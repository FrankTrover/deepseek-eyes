# ARCHITECTURE — V4.3 唯一权威架构文档

> 若其他文档与本文冲突，以本文 + JSON Schema + `PHASE_ACCEPTANCE_CRITERIA.md` 为准。

## 1. 目标部署

V4.3 是 **Windows-first、本地 Coding Agent 视觉扩展**。

```text
User
  │
  ├─ text
  └─ image attachment
        │
        ▼
┌──────────────────────────────┐
│ Supported Coding Host        │
│ DeepSeek primary Agent       │
├──────────────────────────────┤
│ Attachment Ingress Adapter   │
│ Vision Skill                 │
│ Host Action Guard*           │
└──────────────┬───────────────┘
               │ MCP stdio / host adapter
               ▼
┌──────────────────────────────┐
│ DeepSeek Eyes Core Runtime   │
├──────────────────────────────┤
│ Contract Gate                │
│ Permission Gate              │
│ SourceRegistry               │
│ RegionRegistry               │
│ In-memory Cache              │
│ Media Security Pipeline      │
│ MiMo View Planner            │
│ Output Firewall              │
│ Usage/Credit Meter           │
└──────────────┬───────────────┘
               │
               ▼
       Xiaomi MiMo Token Plan
             mimo-v2.5
```

`Host Action Guard*` 是宿主能力，只有支持工具执行拦截的 Adapter 才有。

---

## 2. 组件职责

### Attachment Ingress Adapter

负责：
- 在宿主可用的最早稳定附件 hook 获取图片；
- 把原图注册给 Runtime；
- 获取 `source_ref`；
- 给文本 DeepSeek 注入不可伪造的附件 metadata marker；
- 不自动调用 MiMo。

不负责：
- 视觉推理；
- 权限升级；
- 保存永久图片历史。

### Vision Skill

负责：
- 判断什么时候看；
- 把最终问题缩成 observation question；
- 决定是否需要第二次 Agent-level observe；
- 读懂 taint / uncertainty / conflicts；
- 决定停止。

### MCP stdio Bridge

默认通用工具传输层：
- `deepseek_eyes_capabilities`
- `deepseek_eyes_observe`
- `deepseek_eyes_capture`

MCP 不是媒体数据库，也不是状态存储。

### Core Runtime

唯一业务核心。

所有 Host Adapter / MCP / 诊断 CLI 必须复用 Core Runtime，不复制 Provider/Cache/Media 逻辑。

### MiMo Provider

只接收：
- 已批准/最小化视觉媒体；
- stable vision constitution；
- mode contract；
- observation question；
- output contract。

永远不给 tools/web/filesystem。

---

## 3. 接口方向

```text
Host Adapter
  -> Runtime.register_source(raw_media)
  <- source_ref

Host/Agent
  -> MCP eyes_observe(source_ref, question...)
  -> Runtime.observe(...)
  -> Provider
  <- VisionObservation
  <- MCP structuredContent
```

### source_ref 谁生成？

**只有 Runtime SourceRegistry 生成。**

Adapter 不允许自己构造 `src_*`。

### region_ref 谁生成？

**只有 Runtime RegionRegistry 生成。**

VLM 只输出 bbox/region candidate；Runtime 校验后分配 `reg_*`。

---

## 4. 运行时状态

MCP 2026-07-28 核心协议 stateless，但 Eyes 应用允许本地短生命周期状态。

Eyes 状态不依赖 MCP transport session。

```text
Runtime Process
├─ SourceRegistry
├─ RegionRegistry
├─ ExactObservationCache
├─ PreprocessCache
└─ SingleFlightMap
```

进程退出：
- source_ref 全部失效；
- visual result cache 清空；
- media bytes 清空；
- 不恢复旧视觉会话。

---

## 5. Token Plan 两种模式

### A. host_integrated（推荐）

Token Plan credential 由 Coding Host 持有。

适合宿主允许：
- custom provider；
- model routing；
- visual subagent；
- plugin model invocation。

Eyes 只做本地媒体/权限/结构化感知编排。

### B. direct_local_extension（受限）

Eyes Runtime 自己持有 `tp-` 并调用专属 Base URL。

限制：
- 无 remote HTTP visual API；
- 无 generic batch visual backend；
- 仅 Coding Host 启动；
- 仅 Coding/Debugging 场景；
- 不宣称 Xiaomi 已确认该具体架构合规。

Spike 后由项目负责人选定一种作为首发路径。

---

## 6. 安全保证分级

### Level E — Eyes-local hard guarantees

Runtime 能硬保证：
- 不给 MiMo 工具；
- 路径 allowlist；
- 截图权限；
- full-screen 本地人工确认；
- 输入/输出大小限制；
- taint 不丢失；
- 不把视觉结果当 Runtime 权限；
- 不执行 bash/file/browser；
- 不自动扩大视觉 source。

这些可自动测试。

### Level H — Host-mediated guarantees

要防止：
```text
恶意截图 -> DeepSeek -> bash
```
必须由宿主工具层实施 Action Guard。

如果 Host Adapter 能拦截 privileged tool execution：
- 检查请求是否仅由 `UNTRUSTED_VISUAL_EVIDENCE` 驱动；
- 若用户没有独立明确授权，则 request confirmation / deny。

如果 Host Adapter 不能拦截：
- Eyes 只能提供 Skill + taint；
- 产品必须标记 `host_action_guard=false`；
- 不得宣称“端到端阻止视觉注入触发外部工具”。

---

## 7. Windows-first

V4.3 支持目标：
```text
Windows 10 22H2+
Windows 11
x64
```

首版不承诺：
- macOS capture；
- Wayland/X11 capture；
- Linux window enumeration。

Core Runtime 尽量跨平台，但 Capture/Control Center 首发只验 Windows。

---

## 8. Control Center

技术：
```text
PySide6 desktop application
```

原因：
- 区域框选 overlay 需要 GUI；
- 凭据/权限设置不适合纯 TUI；
- Windows Credential Manager；
- 集成 wizard/diff；
- full-screen 本地确认。

CLI 只保留：
- doctor；
- integrity；
- config export；
- 非视觉 smoke test。

Token Plan Profile 默认不暴露 generic `eyes observe` CLI。

---

## 9. Async 模型

应用层：
```text
asyncio
```

MCP Python SDK 内部使用 AnyIO，但本项目只测试 asyncio backend。

I/O：
- MiMo network async；
- Registry operations async-safe；
- Capture GUI 在 Qt main thread；
- Runtime 与 GUI 通过明确 thread/async bridge，不让 Qt event loop 混乱接管 provider pipeline。

---

## 10. 多图

`eyes_observe.sources`：
```text
1..8
```

默认：
- 普通 observe: 1；
- compare: 2..4 推荐；
- 上限 8 是 contract guard，不代表应该每次传 8 张。

Planner 同时限制：
- 总 encoded bytes；
- 总 estimated image tokens；
- 单图 max bytes；
- source count。

多图 compare：
1. 保持 source_index/ref；
2. 不把跨图 inference 写成 direct evidence；
3. 每条 evidence 必须绑定 source_ref；
4. 对同布局图片可使用 deterministic diff 辅助定位。

---

## 11. 文档优先级

1. JSON Schema / Pydantic Contracts
2. `ARCHITECTURE.md`
3. `PHASE_ACCEPTANCE_CRITERIA.md`
4. Security docs
5. Provider/MiMo docs
6. UX/Integration docs
7. examples

历史 V4.0/V4.1 审计文档保留用于追溯，不作为最终实现优先级。
