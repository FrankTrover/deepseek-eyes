"use strict"

const { spawn } = require("node:child_process")

const reasoningEfforts = ["minimal", "low", "medium", "high", "xhigh", "max"]
const reasoningEffortLabels = ["Minimal", "Low", "Medium", "High", "XHigh", "Max"]
const DEEPSEEK_MAX_INPUT_TOKENS = 995904
const DEEPSEEK_MAX_OUTPUT_TOKENS = 4096

function groundedObservationQuestion(userText = "") {
  const request = userText.trim().slice(0, 2000) || "Describe exactly what is visible."
  return (
    "Inspect every supplied image before identifying anything. First report exact visible text, " +
    "layout, colors, shapes, icon geometry, and overlays such as shortcut arrows. Then answer " +
    `this user request from those facts only: ${request}`
  )
}

function deepSeekModelInfos(models) {
  return models
    .filter((model) => !model.id?.startsWith("__provider__") && /deepseek/i.test(model.id ?? ""))
    .map((model) => {
      const id = model.configId ? `${model.id}::${model.configId}` : model.id
      const configuredOutputTokens = model.max_completion_tokens ?? model.max_tokens ?? 4096
      const maxOutputTokens = Math.min(configuredOutputTokens, DEEPSEEK_MAX_OUTPUT_TOKENS)
      const reasoningEffort = reasoningEfforts.includes(model.reasoning_effort)
        ? model.reasoning_effort
        : undefined
      return {
        id,
        name: `${model.displayName ?? model.id} + Eyes`,
        family: model.family || "deepseek",
        version: "1.0.0",
        detail: "DeepSeek Eyes → OAICopilot",
        tooltip: "Images are observed locally through DeepSeek Eyes, then sent as text evidence.",
        maxInputTokens: Math.min(
          DEEPSEEK_MAX_INPUT_TOKENS,
          Math.max(
            1,
            (model.context_length ?? DEEPSEEK_MAX_INPUT_TOKENS + maxOutputTokens) -
              maxOutputTokens,
          ),
        ),
        maxOutputTokens,
        isUserSelectable: true,
        ...(reasoningEffort
          ? {
              configurationSchema: {
                properties: {
                  reasoningEffort: {
                    type: "string",
                    title: "Reasoning Effort",
                    enum: reasoningEfforts,
                    enumItemLabels: reasoningEffortLabels,
                    default: reasoningEffort,
                    group: "navigation",
                  },
                },
              },
            }
          : {}),
        capabilities: { imageInput: true, toolCalling: true },
      }
    })
}

class EyesBridge {
  constructor(command, log = () => {}) {
    this.command = command
    this.log = log
    this.proc = null
    this.starting = null
    this.buffer = ""
    this.nextId = 0
    this.waiters = new Map()
  }

  async start() {
    if (this.proc) return
    if (this.starting) return this.starting
    this.starting = this._start().finally(() => {
      this.starting = null
    })
    return this.starting
  }

  async _start() {
    if (!Array.isArray(this.command) || this.command.length === 0) {
      throw new Error("deepseekEyes.adapterCommand is empty")
    }
    const proc = spawn(this.command[0], this.command.slice(1), {
      stdio: ["pipe", "pipe", "pipe"],
      shell: false,
    })
    this.proc = proc
    this.buffer = ""
    proc.stdout.on("data", (chunk) => this._onData(chunk))
    proc.stderr.on("data", (chunk) => this.log(String(chunk).trimEnd()))
    proc.once("error", (error) => {
      if (this.proc === proc) this._fail(error)
    })
    proc.once("exit", (code) => {
      if (this.proc === proc) this._fail(new Error(`Eyes bridge exited (${code})`))
    })
    await this.call("ping")
  }

  _onData(chunk) {
    this.buffer += chunk.toString("utf8")
    let newline
    while ((newline = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, newline).trim()
      this.buffer = this.buffer.slice(newline + 1)
      if (!line) continue
      let message
      try {
        message = JSON.parse(line)
      } catch {
        continue
      }
      const waiter = this.waiters.get(message.id)
      if (!waiter) continue
      this.waiters.delete(message.id)
      if (message.error) waiter.reject(new Error(JSON.stringify(message.error)))
      else waiter.resolve(message.result)
    }
  }

  _fail(error) {
    this.proc = null
    for (const waiter of this.waiters.values()) waiter.reject(error)
    this.waiters.clear()
  }

  call(method, params = {}) {
    if (!this.proc) return Promise.reject(new Error("Eyes bridge is not running"))
    const id = ++this.nextId
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.waiters.delete(id)
        reject(new Error(`Eyes bridge ${method} timed out`))
        this.stop()
      }, method === "observe" ? 310_000 : 15_000)
      this.waiters.set(id, {
        resolve: (value) => {
          clearTimeout(timeout)
          resolve(value)
        },
        reject: (error) => {
          clearTimeout(timeout)
          reject(error)
        },
      })
      this.proc.stdin.write(`${JSON.stringify({ id, method, params })}\n`, (error) => {
        if (!error) return
        this.waiters.delete(id)
        reject(error)
      })
    })
  }

  async observe(images, question) {
    await this.start()
    const sources = []
    for (const image of images) {
      const dataUrl = `data:${image.mimeType};base64,${Buffer.from(image.data).toString("base64")}`
      const registered = await this.call("register", { data_url: dataUrl, origin: "host:vscode" })
      sources.push(registered.source_ref)
    }
    const result = await this.call("observe", { sources, question, mode: "describe" })
    return result.observation
  }

  stop() {
    const proc = this.proc
    this.proc = null
    proc?.kill()
    this._fail(new Error("Eyes bridge stopped"))
  }
}

module.exports = { deepSeekModelInfos, groundedObservationQuestion, EyesBridge }
