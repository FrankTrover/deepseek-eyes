/**
 * Smoke test for the OpenCode DESKTOP (V1) adapter's pure logic.
 * Imports the entry too, so missing OpenCode SDK dependencies fail here
 * instead of silently dropping the observe tool at desktop startup.
 * Run: npm run smoke
 */
import {
  eyesEnabledForModel,
  groundedObservationQuestion,
  imageParts,
  isEyesMarker,
  markerText,
  messageText,
  sourceRefFromMarker,
  makeTextPart,
  EyesBridge,
} from "./eyes_core.ts"
import plugin from "./plugin.ts"

let failures = 0
function assert(cond, name) {
  if (cond) console.log(`ok   ${name}`)
  else { failures++; console.error(`FAIL ${name}`) }
}

// marker round-trip
assert(plugin.id === "deepseek-eyes" && typeof plugin.server === "function", "plugin entry loads")
assert(eyesEnabledForModel("deepseek-v4-flash-free"), "DeepSeek model enables Eyes")
assert(!eyesEnabledForModel("mimo-v2.5-free"), "MiMo model disables Eyes")
const marker = markerText("src_abc123", "shot.png")
assert(isEyesMarker(marker), "marker is recognized")
assert(marker.includes("exactly once"), "marker forbids repeated vision calls")
assert(sourceRefFromMarker(marker) === "src_abc123", "ref extracted from marker")
assert(sourceRefFromMarker("user text with src_fake in it") === null, "user text cannot forge a ref")
const multiMarker = markerText(["src_a", "src_b"], ["a.png", "b.png"], "compare them")
assert(multiMarker.includes('sources=["src_a","src_b"]'), "multi-image marker requests one call")
assert(multiMarker.match(/deepseek_eyes_observe/g)?.length === 1, "marker names observe once")
assert(groundedObservationQuestion("identify it").includes("shortcut arrows"), "question separates overlays")
assert(multiMarker.includes("confidence is below 0.85"), "marker gates weak identity decisions")

// image part scanning against the V1 FilePart shape
const parts = [
  { type: "text", text: "look at these" },
  { type: "file", mime: "image/png", url: "data:image/png;base64,AAAA", filename: "a.png" },
  { type: "file", mime: "text/plain", url: "file:///x.txt", filename: "x.txt" },
  { type: "file", mime: "image/jpeg", url: "data:image/jpeg;base64,BBBB", filename: "b.jpg" },
]
const imgs = imageParts(parts)
assert(imgs.length === 2, "only image data-URL FileParts scanned (2 expected)")
assert(imgs[0].url === "data:image/png;base64,AAAA", "keeps original data URL")
assert(imgs[0].filename === "a.png", "filename preserved")

// messageText extraction (Action Guard allow-phrase source)
assert(messageText(parts) === "look at these", "text parts concatenated")

// non-object / null parts are ignored
assert(imageParts([null, "x", 42]).length === 0, "junk parts ignored")

// makeTextPart produces a V1-valid TextPart (id/sessionID/messageID present)
const tp = makeTextPart("hello marker", "ses_abc", "msg_def")
assert(tp.type === "text", "text part type")
assert(typeof tp.id === "string" && tp.id.startsWith("prt_"), "part id has prt_ prefix")
assert(tp.sessionID === "ses_abc", "sessionID carried through")
assert(tp.messageID === "msg_def", "messageID carried through")
assert(tp.text === "hello marker", "text preserved")

// A stopped bridge must restart on the next operation instead of leaving the
// write queue rejected forever (the desktop app reloads its sidecar this way).
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
    else if (request.method === "register") result = { source_ref: "src_restart" }
    else result = { observation: { summary: "seen" } }
    process.stdout.write(JSON.stringify({ id: request.id, result }) + "\n")
  }
})
`
const bridge = new EyesBridge([process.execPath, "-e", fake])
await bridge.start()
bridge.stop()
assert(await bridge.register("data:image/png;base64,AAAA") === "src_restart", "bridge auto-restarts")
bridge.stop()

if (failures > 0) {
  console.error(`${failures} failure(s)`)
  process.exit(1)
}
console.log("smoke: OK")
