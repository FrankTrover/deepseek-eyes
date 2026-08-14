# PHASE_ACCEPTANCE_CRITERIA — V4.3

> 每一 Phase 都有“可运行交付物 + 自动测试 + Exit Gate”。未通过不得宣称 Phase 完成。

# Spike 0 — 可行性冻结

## 目标
用最小代码验证外部依赖，不做产品功能。

## 必做实验

### S0-1 MiMo Token Plan 图像

使用用户真实 `tp-` + Token Plan Base URL：
- `mimo-v2.5`
- Chat Completions
- Base64 image
- `thinking=disabled`
- non-stream
- JSON mode

验证：
- 能返回合法 JSON；
- usage 中实际能读：
  - prompt_tokens
  - completion_tokens
  - cached_tokens（若存在）
  - image_tokens（若存在）
  - reasoning_tokens；
- reasoning_tokens 在简单视觉请求中为 0 或与 disabled 语义一致；
- 不打印 Key/Base64。

### S0-2 MCP SDK

安装：
```text
mcp >=2,<3
```

完成一个 `MCPServer` stdio server：
- Pydantic input；
- Pydantic output；
- tool list 中有 output schema；
- call result 有 structured_content；
- in-memory/stdio client test 通过。

记录**实际解析后的精确 SDK 版本**到 `uv.lock` 和 Spike report。

### S0-3 Attachment ingress

至少选择一个首发 Coding Host。

若首发 OpenCode：
- 验证 plugin/API 具体版本；
- 验证图片 hook 是否能在 resize 前获取原图；
- 4K fixture 前后尺寸/hash 记录；
- 若不能，明确 workaround 或将 adapter 标记 unsupported。

### S0-4 Capture PoC

Windows：
- PySide6 overlay；
- mss capture；
- pywin32 foreground window；
- 用户取消时无文件残留。

## Exit Gate

必须生成：
```text
spikes/SPIKE_RESULT.md
uv.lock
selected_host_adapter.json
```

任何一项失败：
- 不继续假装原架构可用；
- 回到 Architecture Decision Record。

---

# Phase 0 — Contract & Security Skeleton

## Deliverables

```text
src/deepseek_eyes/contracts.py
src/deepseek_eyes/interfaces.py
src/deepseek_eyes/errors.py
src/deepseek_eyes/ids.py
schemas/*
```

## Tests

- extra fields rejected；
- focus oneOf enforced；
- invalid bbox rejected；
- source/region IDs high entropy；
- trust boundary 固定 `may_authorize_actions=false`；
- all error codes serializable；
- Pydantic schema 与 checked-in JSON schema consistency test。

## Exit Gate

```text
pytest unit+contract = 100% pass
ruff = pass
mypy = pass
network calls = 0
```

---

# Phase 1 — Media Intake + Registry

## Deliverables

- SourceRegistry；
- RegionRegistry；
- MIME sniff；
- safe decode；
- orientation；
- EXIF strip；
- canonical image；
- approved path resolver。

## Fixed registry limits

```text
per source encoded max: 50 MiB
max decoded pixels: 60M
max live sources: 32
max registry canonical bytes: 256 MiB
idle TTL: 20 min
hard TTL: 60 min
```

Active observe pins source; pinned source cannot be evicted.

Eviction：
```text
expired first -> unpinned LRU
```

Concurrency：
- registry metadata protected by `asyncio.Lock`；
- media bytes immutable after register；
- resolve returns immutable/read-only media descriptor。

## Tests

- symlink/junction/path escape；
- EXIF removed；
- 60M pixel guard；
- GIF animation rejected by default；
- TTL；
- LRU；
- pinned eviction；
- concurrent register/resolve/revoke；
- region source digest mismatch -> `REGION_STALE`。

## Exit Gate

4K PNG local register/hash/canonicalize benchmark on target Windows:
```text
p95 local processing <= 400 ms
```
This is local-only target; provider latency excluded.

---

# Phase 2 — MiMo Provider

## Deliverables

- persistent OpenAI-compatible client；
- Token Plan host validation；
- no environment proxy；
- `mimo-v2.5`；
- `thinking=disabled`；
- no tools；
- no web search；
- structured JSON；
- usage extraction；
- finish_reason handling；
- retry classifier。

## Tests

Mock：
- 400/401/402/403/404/421/429/500/503；
- connect timeout；
- ambiguous read timeout；
- `finish_reason=length`；
- unexpected tool_calls；
- malformed JSON；
- giant output。

Live:
- one built-in safe fixture；
- no secret in logs。

## Exit Gate

- Mock matrix 100%；
- Live S0 fixture success 10/10 manually/release environment；
- simple visual calls do not enable thinking；
- provider client reused, not rebuilt each request。

---

# Phase 3 — Fast Observe

## Deliverables

- one-pass path；
- exact in-memory result cache；
- single-flight；
- local validators；
- output compaction；
- taint propagation。

## Cache policy

Default:
```text
memory only
max entries: 128
max serialized result bytes: 64 MiB
TTL: 60 min
disk result cache: OFF
```

No cross-process visual cache in MVP.

## Tests

- exact duplicate -> zero provider call；
- concurrent identical 10 calls -> one provider execution；
- cache result remains tainted；
- cache key includes model/prompt/schema/mode/question/media digest；
- no semantic cache。

## Exit Gate

Golden routine fixtures:
```text
>=80% complete in one provider call
```

Local cache lookup:
```text
p95 <= 10 ms
```

---

# Phase 4 — Accuracy Planner

## Deliverables

- official MiMo image token estimator；
- full vs crop planner；
- original-resolution ROI；
- verify path；
- conflict detector；
- exact-text local validators；
- deterministic image diff for compare.

## Tests

- official token estimator examples；
- 1080p/4K/8K；
- crop consistency；
- no-anchor verify；
- exact chars；
- compare two/four images；
- conflict not silently resolved。

## Exit Gate

Curated test targets：
```text
clear exact-field match >=98%
unsupported critical claim <=1%
material conflict silently ignored = 0
```

---

# Phase 5 — MCP stdio

## Deliverables

- 3 tools；
- MCPServer v2 line；
- Pydantic schemas；
- structured_content；
- annotations read-only/idempotence where appropriate；
- version handshake/capability result；
- absolute executable path integration.

## Tests

- tools list；
- exact input/output schema；
- stdio subprocess；
- malformed input；
- `is_error`；
- compatibility with selected Host。

## Exit Gate

100 consecutive local tool calls:
```text
adapter/protocol failures = 0
```

---

# Phase 6 — Host Attachment Adapter

## Deliverables

- selected Host adapter；
- source registration；
- safe marker injection；
- lazy visual invocation；
- raw-attachment fidelity report；
- Host capability matrix；
- Action Guard if Host supports tool interception。

## Tests

- image + text reaches text-only DeepSeek as marker；
- source_ref valid；
- user attachment not duplicated to unsupported text provider；
- 4K fidelity；
- malicious user text cannot forge valid source_ref；
- adapter uninstall restores config.

## Exit Gate

End-to-end：
```text
attach image -> DeepSeek receives marker -> calls Eyes -> gets evidence
```
20/20 test sessions.

---

# Phase 7 — Capture

Windows only.

## Deliverables

- interactive region；
- foreground window；
- full screen with per-call local human confirmation；
- no background capture。

## Tests

- cancel；
- multi-monitor；
- high DPI；
- window closes；
- confirmation deny；
- no temp leftovers。

## Exit Gate

No unauthorized capture in adversarial suite.

---

# Phase 8 — Control Center

PySide6.

## Deliverables

Tabs：
- Status；
- Integration；
- Permissions；
- Token Plan/Usage；
- Security/Diagnostics。

## Tests

- credential create/delete；
- config backup/rollback；
- full-screen switch；
- integration diff；
- redacted diagnostics；
- corrupted integration repair。

## Exit Gate

Fresh Windows VM：
```text
install -> configure -> integrate -> smoke test -> uninstall
```
without manual file editing.

---

# Phase 9 — Release / Red Team

Run all:
- P0 Security Gates；
- Golden set；
- injection；
- path；
- cache；
- supply chain；
- bundle integrity；
- host-specific regressions。

Any P0 failure blocks release.
