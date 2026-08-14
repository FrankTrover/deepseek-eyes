# Spike 0 — 可行性冻结结果（完整）

> 记录开工时的真实环境与解析后的依赖版本。MVP-A 核心链路已实现并通过门禁；
> Phase 6/7/8 交付物已就绪。

## 工具链

```text
Python: 3.12.13 (uv 管理的 CPython)
uv:     0.12.3
Node:   v24.16.0
平台:   Windows 11 (win32)
```

## 解析后的依赖版本（由 uv.lock 锁定，非手写）

```text
mcp          2.0.0
openai       1.109.1
pydantic     2.13.4
pillow       11.3.0
httpx        0.28.1
keyring      25.7.0
platformdirs 4.11.2
typer        0.27.1
mss          10.2.0
pywin32      312
PySide6      6.11.1
```

## S0 逐项状态

| 项 | 状态 | 说明 |
|---|---|---|
| S0-1 MiMo Token Plan 图像 | ✅ 通过 | `mimo-v2.5`，10/10 合法 JSON；`thinking=disabled` 生效（reasoning=0）；usage 字段齐全。详见 `LIVE_PROBE_RESULT.md` |
| S0-2 MCP SDK | ✅ 通过 | `mcp==2.0.0`；3 工具；input/output schema；structured_content；stdio 语义 |
| S0-3 Attachment ingress | ✅ 调研完成 | OpenCode V2 插件 API（beta）；原图可在 `ctx.session.hook("context")` 取到；适配器已实现（见下） |
| S0-4 Capture PoC | ✅ 实现 | `mss` + pywin32 + PySide6 overlay；无背景截图；全屏需逐次人工确认 |

## S0-3 调研结论（OpenCode 桌面端 — V1 插件 API）

> **关键更正（2026-08-14）**：目标宿主是 **OpenCode 桌面端**（Electron，
> `LOCALAPPDATA\Programs\@opencode-aidesktop\OpenCode.exe`），它运行的是 **V1
> 插件 API**，不是 `@opencode-ai/plugin` 的 V2 `Plugin.define`。对桌面端
> `app.asar` 的静态检查：`Plugin.define` / `session.hook` 出现 **0 次**，而
> V1 hook 名 `chat.message` / `permission.ask` / `tool.execute.before` /
> `experimental.chat.system.transform` 全部存在。已加载的 `ponytail.mjs` 也是
> V1 格式（`export default async ({client}) => ({...hooks})`）。

- 插件加载：`~/.config/opencode/opencode.json` 的 `plugin` 数组，引用插件文件
  绝对路径（`.mjs` / `.ts`，Bun 运行时原生支持 TS）。
- 附件流：用户贴/拖图 → 桌面端生成 `FilePart`（`{ type: "file", mime: "image/*",
  url: "data:image/...;base64,...", filename }`），**原始字节**，未缩放。
- 附件入口 hook：**`chat.message`**（`output.parts` 在模型 dispatch 前可修改；
  V1 `Message` 是 `{ id, role, content }`，`FilePart` 是 `type:"file"` 不是 `media`）。
- Action Guard 入口 hook：**`permission.ask`**（`output.status = "deny"` 可拒绝；
  V1 `tool.execute.before` 的 `output` 只有 `args`，无 deny 通道）。
- 适配器交付：`host/opencode/plugin.ts`（V1 `export default async`，含 Action Guard
  开关）+ `deepseek-eyes adapter` stdio bridge（JSON-lines，复用 Core Runtime；
  用 `node:child_process.spawn` 起 bridge）。
- 版本 pin：桌面端插件运行时为 Bun（`PluginInput.$` 是 BunShell）；plugin 源为
  `.ts`，`npm run smoke` 用 `node --experimental-strip-types` 验证纯逻辑。

## 架构决策变更

无。MVP-A 严格按 `ARCHITECTURE.md` 实现：taint 恒定、
`may_authorize_actions=false`。新增模块不改变核心契约：
- `config.py` / `credentials.py`（keyring 存凭证，不落盘明文）
- `capture.py`（Phase 7）
- `integration.py` / `diagnostics.py` / `control_center.py`（Phase 8）
- `host_bridge.py` + `host/opencode/plugin.ts`（Phase 6）

## 阻塞项

1. **OpenCode 适配器实机验证**（Phase 6 Exit Gate）：需要桌面端上端到端 20/20
   会话（附件 → marker → observe）。插件文件需加入 `opencode.json` 的 `plugin`
   数组（`integration.py` 目前只写 MCP 段，尚未写 plugin 段）。
2. **Action Guard 实装**：插件已实现 `permission.ask` deny 逻辑（V1），但
   `host_action_guard` capability 仍恒 `false`，需桌面端实机确认
   `permission.ask` deny 真实生效后才能翻真（见 `SECURITY_GUARANTEES` §4-5）。
3. **bridge 可执行路径**：桌面端进程 PATH 未必包含 `deepseek-eyes`，需确认
   `options.pythonPath` 指向 venv 内的 console script 或绝对路径。
