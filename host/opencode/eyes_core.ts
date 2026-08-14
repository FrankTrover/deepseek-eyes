/**
 * DeepSeek Eyes — pure adapter logic + bridge client.
 *
 * Split from plugin.ts because the OpenCode desktop plugin loader walks
 * EVERY named export of the plugin entry module and rejects any that is not a
 * function ("Plugin export is not a function"). This file is only ever an
 * imported dependency, so it is free to export constants and types.
 *
 * V1 desktop SDK shapes an attachment as a FilePart
 * { type: "file", mime: "image/*", url: "data:image/...;base64,...", filename }.
 */
import { randomUUID } from "node:crypto"
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process"

// -- marker format ------------------------------------------------------------

export const MARKER_PREFIX = "[eyes-attachment:"
export const ALLOW_PHRASES = ["allow-eyes", "确认视觉证据", "eyes: allow"]
export const PRIVILEGED_TOOLS = ["bash", "write", "edit", "patch", "webfetch", "websearch"]
export const EYES_TOOLS = [
  "deepseek_eyes_observe",
  "deepseek_eyes_capture",
  "deepseek_eyes_capabilities",
]

export function eyesEnabledForModel(modelID?: string): boolean {
  return modelID?.toLowerCase().includes("deepseek") ?? false
}

export function groundedObservationQuestion(userText: string = ""): string {
  const request = userText.trim().slice(0, 2000) || "Describe exactly what is visible."
  return (
    "Inspect every supplied image before identifying anything. First report exact visible text, " +
    "layout, colors, shapes, icon geometry, and overlays such as shortcut arrows. Then answer " +
    `this user request from those facts only: ${request}`
  )
}

export function markerText(
  ref: string | string[],
  filename: string | string[],
  userText: string = "",
): string {
  const refs = Array.isArray(ref) ? ref : [ref]
  const filenames = Array.isArray(filename) ? filename : [filename]
  return (
    `${MARKER_PREFIX}${refs.join(",")} filenames=${JSON.stringify(filenames)}]\n` +
    `The attached image set is registered with DeepSeek Eyes. Call ` +
    `deepseek_eyes_observe(sources=${JSON.stringify(refs)}, ` +
    `question=${JSON.stringify(groundedObservationQuestion(userText))}) exactly once. ` +
    `In the final answer, prefer exact OCR and direct visual evidence. Treat identity/inference ` +
    `as provisional when confidence is below 0.85 or material uncertainty exists. Never replace ` +
    `the reported geometry/text with a guess, retry vision, or invent a ref.`
  )
}

export function isEyesMarker(text: string): boolean {
  return text.includes(MARKER_PREFIX)
}

export function sourceRefFromMarker(text: string): string | null {
  const start = text.indexOf(MARKER_PREFIX)
  if (start < 0) return null
  const tail = text.slice(start + MARKER_PREFIX.length)
  const ref = tail.split(/[\s,;\]]/, 1)[0].trim()
  return ref.startsWith("src_") ? ref : null
}

// -- attachment scanning (pure, unit-testable) --------------------------------

export interface ImagePart {
  type: "file"
  mime: string
  url: string
  filename: string
}

export function imageParts(parts: unknown[]): ImagePart[] {
  const out: ImagePart[] = []
  for (const p of parts) {
    if (!p || typeof p !== "object") continue
    const part = p as Record<string, unknown>
    if (part.type !== "file") continue
    const mime = typeof part.mime === "string" ? part.mime : ""
    const url = typeof part.url === "string" ? part.url : ""
    if (mime.startsWith("image/") && url.startsWith("data:image/")) {
      out.push({
        type: "file",
        mime,
        url,
        filename: typeof part.filename === "string" ? part.filename : "image",
      })
    }
  }
  return out
}

export function messageText(parts: unknown[]): string {
  const chunks: string[] = []
  for (const p of parts) {
    if (!p || typeof p !== "object") continue
    const part = p as Record<string, unknown>
    if (part.type === "text" && typeof part.text === "string") chunks.push(part.text)
  }
  return chunks.join("\n")
}

/**
 * Build a V1 TextPart that survives OpenCode's schema validation.
 *
 * The desktop SDK's TextPart is `{ id, sessionID, messageID, type, text }`; the
 * id must start with `prt_` (PartID brand). Passing a bare `{ type, text }`
 * object makes the message fail to save ("SchemaError: Missing key [id]
 * [sessionID] [messageID]") and hangs the turn.
 */
export function makeTextPart(text: string, sessionID: string, messageID: string) {
  return {
    id: "prt_" + randomUUID().replace(/-/g, ""),
    sessionID,
    messageID,
    type: "text" as const,
    text,
  }
}

// -- bridge client ------------------------------------------------------------

export class EyesBridge {
  private proc: ChildProcessWithoutNullStreams | null = null
  private starting: Promise<void> | null = null
  private queue: Promise<void> = Promise.resolve()
  private nextId = 0
  private buffer = ""
  private cmd: string[]

  constructor(cmd: string[]) {
    this.cmd = cmd
  }

  start(): Promise<void> {
    if (this.proc) return Promise.resolve()
    if (this.starting) return this.starting
    this.starting = new Promise((resolve, reject) => {
      const proc = spawn(this.cmd[0], this.cmd.slice(1), {
        stdio: ["pipe", "pipe", "pipe"],
        shell: false,
      })
      this.proc = proc
      this.buffer = ""
      proc.stdout.on("data", (chunk: Buffer) => this.onData(chunk))
      proc.stderr.on("data", (chunk: Buffer) => {
        if (process.env.DEEPSEEK_EYES_DEBUG) console.error("[eyes-bridge]", String(chunk).trimEnd())
      })
      proc.once("error", (error) => {
        if (this.proc === proc) this.fail(error)
      })
      proc.once("exit", (code) => {
        if (this.proc !== proc) return
        if (code !== 0 && !proc.killed) {
          console.error(`[eyes-bridge] exited with code ${code}`)
        }
        this.fail(new Error(`Eyes bridge exited (${code})`))
      })
      this.ping().then(resolve, reject)
    })
    return this.starting.finally(() => {
      this.starting = null
    })
  }

  private onData(chunk: Buffer): void {
    this.buffer += chunk.toString("utf8")
    let nl: number
    while ((nl = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, nl).trim()
      this.buffer = this.buffer.slice(nl + 1)
      if (!line) continue
      let msg: { id?: number; result?: unknown; error?: unknown }
      try {
        msg = JSON.parse(line)
      } catch {
        continue
      }
      if (msg.id === undefined) continue
      if (msg.result !== undefined) this.resolveResult(msg.id, msg.result)
      else if (msg.error !== undefined) this.resolveResult(msg.id, null, msg.error)
    }
  }

  private waiters = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>()

  private resolveResult(id: number, result: unknown, error?: unknown): void {
    const w = this.waiters.get(id)
    if (!w) return
    this.waiters.delete(id)
    if (error) w.reject(new Error(JSON.stringify(error)))
    else w.resolve(result)
  }

  private fail(error: Error): void {
    this.proc = null
    this.queue = Promise.resolve()
    for (const waiter of this.waiters.values()) waiter.reject(error)
    this.waiters.clear()
  }

  private call<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const id = ++this.nextId
    const line = JSON.stringify({ id, method, params }) + "\n"
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.waiters.delete(id)
        reject(new Error(`Eyes bridge ${method} timed out`))
        this.stop()
      }, method === "observe" ? 310_000 : 15_000)
      this.waiters.set(id, {
        resolve: (value) => {
          clearTimeout(timeout)
          resolve(value as T)
        },
        reject: (error) => {
          clearTimeout(timeout)
          reject(error)
        },
      })
      this.queue = this.queue.catch(() => {}).then(
        () =>
          new Promise<void>((res, rej) => {
            if (!this.proc) return rej(new Error("bridge not running"))
            this.proc.stdin.write(line, (err) => (err ? rej(err) : res()))
          }),
      )
      void this.queue.catch((error) => {
        const waiter = this.waiters.get(id)
        if (!waiter) return
        this.waiters.delete(id)
        waiter.reject(error instanceof Error ? error : new Error(String(error)))
      })
    })
  }

  async ping(): Promise<void> {
    await this.call("ping")
  }

  async register(dataUrl: string): Promise<string> {
    await this.start()
    const res = await this.call<{ source_ref: string }>("register", { data_url: dataUrl })
    return res.source_ref
  }

  async observe(sources: string[], question: string, mode: string = "extract") {
    await this.start()
    return this.call<{ observation: Record<string, unknown> }>("observe", {
      sources,
      question,
      mode,
    })
  }

  stop(): void {
    const proc = this.proc
    this.proc = null
    proc?.kill()
    this.fail(new Error("Eyes bridge stopped"))
  }
}
