import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"

const require = createRequire(import.meta.url)
const { deepSeekModelInfos, groundedObservationQuestion, EyesBridge } = require("./core.js")
const packageJson = require("./package.json")

assert(packageJson.enabledApiProposals.includes("languageModelSystem"))

const models = deepSeekModelInfos([
  {
    id: "deepseek-v4-flash",
    displayName: "DeepSeek V4 Flash",
    configId: "flash",
    reasoning_effort: "medium",
  },
  { id: "mimo-v2.5", displayName: "MiMo V2.5", vision: true },
])
assert.deepEqual(models.map((model) => model.id), ["deepseek-v4-flash::flash"])
assert.equal(models[0].capabilities.imageInput, true)
assert.equal(models[0].maxInputTokens, 995904)
assert.equal(models[0].maxOutputTokens, 4096)
assert.equal(
  models[0].configurationSchema.properties.reasoningEffort.default,
  "medium",
)
assert.deepEqual(models[0].configurationSchema.properties.reasoningEffort.enum, [
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
])
assert(groundedObservationQuestion("identify it").includes("shortcut arrows"))
assert(groundedObservationQuestion("identify it").includes("facts only"))

const extensionSource = readFileSync(new URL("./extension.js", import.meta.url), "utf8")
assert(!extensionSource.includes("newestImageMessage"))
assert(extensionSource.includes("far more expensive prefix cache"))
assert(extensionSource.includes("token.onCancellationRequested"))
assert(extensionSource.includes("Visual observation failed"))

const fake = String.raw`
let buffer = ""
process.stdin.setEncoding("utf8")
process.stdin.on("data", chunk => {
  buffer += chunk
  let newline
  while ((newline = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, newline).trim()
    buffer = buffer.slice(newline + 1)
    if (!line) continue
    const request = JSON.parse(line)
    let result
    if (request.method === "ping") result = { pong: true }
    else if (request.method === "register") result = { source_ref: "src_test" }
    else result = { observation: { summary: "seen" } }
    process.stdout.write(JSON.stringify({ id: request.id, result }) + "\n")
  }
})
`
const bridge = new EyesBridge([process.execPath, "-e", fake])
assert.equal(
  (await bridge.observe([{ mimeType: "image/png", data: Uint8Array.of(1, 2, 3) }], "q")).summary,
  "seen",
)
bridge.stop()
console.log("smoke: OK")
