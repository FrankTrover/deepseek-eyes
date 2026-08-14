/**
 * DeepSeek Eyes — OpenCode DESKTOP host adapter (V1 plugin entry).
 *
 * This file is the plugin entry point the desktop loader reads. It must have
 * ZERO named exports (the loader walks `Object.values(mod)` and throws
 * "Plugin export is not a function" on any non-function export). All real
 * logic lives in eyes_core.ts and is imported here for the server function.
 *
 * V1 module format expected by `readV1Plugin`:
 *   export default { id, server }   (file-path plugins MUST carry id)
 */
import { tool } from "@opencode-ai/plugin"

import {
  EyesBridge,
  isEyesMarker,
  markerText,
  imageParts,
  messageText,
  makeTextPart,
  eyesEnabledForModel,
  ALLOW_PHRASES,
  PRIVILEGED_TOOLS,
  EYES_TOOLS,
} from "./eyes_core.ts"

export interface EyesOptions {
  adapterCommand?: string[]
  actionGuard?: boolean
}

export default {
  id: "deepseek-eyes",
  server: async function plugin(_input: unknown, options: EyesOptions = {}) {
    const cmd = options.adapterCommand ?? ["deepseek-eyes", "adapter"]
    const bridge = new EyesBridge(cmd)
    try {
      await bridge.start()
    } catch (err) {
      console.error("[eyes] failed to start bridge:", err)
      return { dispose: async () => {} }
    }

    const state = new Map<string, { tainted: boolean; userText: string }>()

    return {
      dispose: async () => bridge.stop(),

      // Agent-callable observe tool, backed by the SAME bridge process that
      // registered the image. This keeps register + observe in one runtime, so
      // source_refs resolve (the MCP server is a separate process and cannot
      // see bridge-registered sources).
      tool: {
        deepseek_eyes_observe: tool({
          description:
            "Observe all images from one [eyes-attachment:...] marker in one call. Ground the final answer in exact OCR/direct evidence; identity is provisional below 0.85 confidence or with material uncertainty. Never retry for the same user request.",
          args: {
            sources: tool.schema
              .array(tool.schema.string().startsWith("src_"))
              .min(1)
              .describe("source_ref values from the eyes-attachment marker"),
            question: tool.schema.string().min(1).describe("Observation question"),
            mode: tool.schema
              .enum(["describe", "extract", "verify", "compare", "qa"])
              .optional()
              .describe("Observation mode"),
          },
          execute: async (args: { sources: string[]; question: string; mode?: string }) => {
            try {
              const res = await bridge.observe(args.sources, args.question, args.mode ?? "extract")
              return JSON.stringify(res.observation)
            } catch (error) {
              console.error("[eyes] observe failed:", error)
              return JSON.stringify({
                evidence: [],
                conflicts: [],
                uncertainty: [
                  {
                    text: "Visual observation failed; no visual identity or content was observed.",
                    severity: "material",
                  },
                ],
                summary: null,
                error: "VISION_UNAVAILABLE",
              })
            }
          },
        }),
      },

      "chat.message": async (
        input: {
          sessionID?: string
          messageID?: string
          model?: { providerID: string; modelID: string }
        },
        output: {
          message: {
            model: { providerID: string; modelID: string }
            tools?: Record<string, boolean>
          }
          parts: Record<string, unknown>[]
        },
      ) => {
        const sessionID = input.sessionID ?? ""
        const messageID = input.messageID ?? ""
        const parts = output.parts ?? []
        const modelID = input.model?.modelID ?? output.message.model.modelID
        if (!eyesEnabledForModel(modelID)) {
          output.message.tools = {
            ...output.message.tools,
            ...Object.fromEntries(EYES_TOOLS.map((name) => [name, false])),
          }
          state.set(sessionID, { tainted: false, userText: "" })
          return
        }
        const userText = messageText(parts)
        const images = imageParts(parts)

        // A DeepSeek text-only turn must not start a speculative vision/tool loop.
        // Follow-ups can use the evidence already present in prior assistant text.
        output.message.tools = {
          ...output.message.tools,
          deepseek_eyes_observe: images.length > 0,
          deepseek_eyes_capture: false,
          deepseek_eyes_capabilities: false,
        }

        if (images.length > 0) {
          const alreadyMarked = parts.some(
            (p) => p && p.type === "text" && typeof p.text === "string" && isEyesMarker(p.text),
          )
          if (!alreadyMarked) {
            const refs: string[] = []
            const filenames: string[] = []
            for (const img of images) {
              try {
                const ref = await bridge.register(img.url)
                refs.push(ref)
                filenames.push(img.filename)
              } catch (err) {
                console.error("[eyes] register failed:", err)
              }
            }
            if (refs.length > 0) {
              parts.push(makeTextPart(markerText(refs, filenames, userText), sessionID, messageID))
            } else {
              output.message.tools.deepseek_eyes_observe = false
            }
          }
          state.set(sessionID, { tainted: true, userText })
        } else {
          state.set(sessionID, { tainted: false, userText })
        }
      },

      "permission.ask": (
        input: { sessionID?: string; type?: string },
        output: { status?: "ask" | "deny" | "allow" },
      ) => {
        if (options.actionGuard !== true) return
        const s = state.get(input.sessionID ?? "")
        if (!s || !s.tainted) return
        if (!PRIVILEGED_TOOLS.includes(input.type ?? "")) return
        if (ALLOW_PHRASES.some((phrase) => s.userText.includes(phrase))) return
        output.status = "deny"
      },
    }
  },
}
