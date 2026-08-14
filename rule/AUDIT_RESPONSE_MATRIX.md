# V4.3 — 对外部审核报告逐项判定

> 判定基于完整 V4.2 开发包，而不是只抽取 6 份文件后的残缺目录。

| 审核项 | 判定 | 说明 | V4.3 动作 |
|---|---|---|---|
| P0-1 docs/references 不存在 | ❌ 不存在 | 完整 V4.2 实际有 `docs/00~14` 以及 `skill/vision/references/*` | 增加 bundle audit，防止交付时漏文件 |
| P0-2 Phase 没有逐阶段 DoD | ⚠️ 部分存在 | 已有 Release Gates，但确实缺少每 Phase 的可执行验收 | 新增 `PHASE_ACCEPTANCE_CRITERIA.md` |
| P0-3 技术栈未冻结 | ✅ 存在 | 文档有 Python/uv 暗示，但没有形成权威技术栈 | 冻结 Python 3.12、uv、asyncio、pytest、PySide6、Windows-first |
| P0-4 MCP SDK 版本不明确 | ⚠️ 部分存在 | 规范版本明确，但 package/major line 未冻结 | 冻结 PyPI package `mcp` v2 line；具体 patch 由 `uv.lock` 锁定 |
| H1 source_ref/region_ref 生命周期不完整 | ✅ 存在 | 已有 process-scoped/随机ID，但 TTL、容量、并发、所有者接口不够具体 | 新增 Registry/Cache 规格 |
| H2 Token Estimator 无规格 | ❌ 基本不存在 | 已有官方公式 Python 实现、Credit estimator、usage 字段 | 统一入口并列入 Spike 验证 |
| H3 Cache 规格不完整 | ✅ 存在 | cache key/taint 有，但内存/磁盘/容量/eviction 边界不够明确 | 明确 MVP 仅内存 LRU，不持久化视觉结果 |
| H4 Control Center 形态未定 | ✅ 存在 | UX 有功能，无 UI 技术栈 | 冻结 Windows PySide6 |
| H5 Capture 跨平台未定 | ✅ 存在 | 没必要 V1 同时跨平台 | 明确 V4.3 Windows 10/11 first；mss + pywin32 + PySide6 overlay |
| M1 Agent Bridge 未定义 | ❌ 基本不存在 | V4.2 已明确 MCP stdio + Host Adapter | 新增权威 `ARCHITECTURE.md` 避免信息分散 |
| M2 多图未覆盖 | ❌ 不存在 | Observe schema 已支持 array，mode 有 compare | 增加多图限制和 compare 规则 |
| M3 错误恢复不完整 | ❌ 基本不存在 | V4.2 已有错误矩阵/重试/circuit breaker | 新增统一错误表，方便编码 |
| M4 taint 无法拦截宿主后续行为 | ✅ 存在且很重要 | Runtime 确实无法控制另一个宿主的 bash/file 工具 | 明确两级安全保证 + Host Action Guard capability |

## 最终判断

审核报告不是“全对”。13 项准确分类为：
- 6 项真实存在，需要修复：P0-3、H1、H3、H4、H5、M4；
- 2 项部分存在，需要补强：P0-2、P0-4；
- 5 项属于误报或 V4.2 已经覆盖：P0-1、H2、M1、M2、M3。

真正阻塞开工的不是“缺 docs”，而是：
1. 技术栈与 build contract 没冻结；
2. 每 Phase 没有独立 Acceptance Criteria；
3. SourceRegistry/Cache 生命周期没工程化；
4. Control Center/Capture 平台没有冻结；
5. Host 级视觉注入防护能力边界未写清。

V4.3 专门解决这 5 项。
