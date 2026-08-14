# SPIKE_PLAN — 开工前外部依赖验证

预计是“先做最小探针”，不是实现完整产品。

## S1 MiMo Token Plan

输入：
- one built-in PNG；
- user-selected Token Plan Base URL；
- `tp-` credential。

请求：
- `mimo-v2.5`；
- image Base64；
- JSON mode；
- `thinking=disabled`；
- no tools；
- stream false。

保存 redacted result：
```text
status
finish_reason
usage prompt/completion
cached_tokens
image_tokens
reasoning_tokens
latency
```

不得保存：
- Key；
- Base64；
- 用户真实图片。

Pass：
- 10/10 valid response；
- JSON schema parse；
- no reasoning for simple extraction if disabled behaves as documented。

---

## S2 MCP SDK

官方当前 Python SDK 文档：
- package `mcp`；
- v2 current stable line；
- Python 3.10+；
- `MCPServer`；
- structured output from typed/Pydantic return；
- 2026-07-28 protocol support。

项目：
- Python 3.12；
- constraint `mcp>=2,<3`；
- `uv lock` resolves exact package version；
- exact resolved version recorded in report。

Pass：
- tools list；
- output schema present；
- structured_content returned；
- stdio call 100/100；
- no dependency on MCP session id。

---

## S3 OpenCode/selected Host

If OpenCode:
- record exact `opencode/opencode2` version；
- Plugin API version；
- attachment config；
- tool execution hook；
- raw image accessibility；
- resize point。

Use:
```text
3840x2160 CubeMX fixture
```

Record:
```text
original width/height
adapter registered width/height
provider-bound width/height
```

Pass:
- no unexpected resize before Eyes registration；
OR
- documented configuration produces acceptable fidelity。

Because OpenCode V2 plugin API is currently beta, exact adapter support is version-pinned.

---

## S4 Windows Capture

Pass:
- multi-monitor region select；
- 125%/150% DPI；
- cancel；
- foreground window；
- local full-screen confirm deny/allow；
- no persisted screenshot after Runtime cleanup。

---

## Output

Create:
```text
spikes/SPIKE_RESULT.md
```

Include:
- exact dependency versions；
- exact Host version；
- pass/fail；
- architectural decision changes。

No Spike result => no Phase 1 implementation.
