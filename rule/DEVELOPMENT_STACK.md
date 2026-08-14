# DEVELOPMENT_STACK — V4.3 冻结技术栈

## 1. Language

```text
Python 3.12.x
```

项目 `requires-python`：
```text
>=3.12,<3.13
```

理由：
- 避免首版同时覆盖多个 Python minor；
- MCP SDK 官方支持 Python 3.10+；
- Python 3.12 对 asyncio/typing/Pydantic 支持成熟；
- Windows 生态稳定。

`.python-version` 固定：
```text
3.12
```

---

## 2. Package / Environment

```text
uv
```

必须：
```text
pyproject.toml
uv.lock
```

规则：
- `pyproject.toml` 放兼容范围；
- `uv.lock` 锁实际精确版本；
- Release 使用 `uv sync --frozen`；
- 禁 production 启动时联网安装依赖。

当前包只提供 `pyproject.toml`；`uv.lock` 必须在 Spike 的联网开发机生成并提交，**不得手写/伪造 lockfile**。

---

## 3. Async

```text
asyncio
```

使用：
- `async def` Runtime；
- `asyncio.Lock` Registry metadata；
- `asyncio.Task/Future` single-flight；
- `asyncio.Semaphore` provider concurrency。

MCP SDK 内部 AnyIO 不改变 Eyes 的应用层并发模型。

---

## 4. MCP

PyPI package：
```text
mcp
```

Target line：
```text
mcp >= 2, < 3
```

当前官方 Python SDK 文档把 v2 作为 stable line，并明确支持 MCP 2026-07-28。

高层 server：
```python
from mcp.server import MCPServer
```

Tool：
- 使用 Pydantic return type；
- SDK 自动生成 output schema；
- 同时返回 structured content；
- contract tests 还要检查 wire schema 与本项目 JSON Schema 一致。

### 为什么不在文档里写死一个猜测的 patch 版本？

精确版本由：
```text
uv.lock
```
决定。

Spike 必须记录：
```text
mcp resolved version
SDK docs revision
protocol conformance result
```

如果官方发布节奏变化，不需要改架构文档。

---

## 5. Core dependencies

```text
pydantic 2.x       contract/config validation
openai SDK         MiMo OpenAI-compatible calls
httpx              explicit safe network client
Pillow             media decode/canonicalization
keyring            OS credential store
platformdirs       user config/cache paths
mss                screen pixel capture
pywin32            Windows window/handle APIs
typer              doctor/integrity CLI
```

---

## 6. UI

```text
PySide6 6.x
```

用途：
- Control Center；
- interactive region overlay；
- per-call full-screen confirmation；
- integration diff/permission UI。

UI 为 optional extra，在 Core unit tests 中不需要启动 Qt。

---

## 7. Test

```text
pytest
pytest-asyncio
pytest-cov
```

Async mode：
```ini
asyncio_mode = auto
```

---

## 8. Quality

```text
ruff
mypy
pip-audit
```

Release：
- ruff check；
- ruff format --check；
- mypy；
- pytest；
- security bundle audit；
- dependency audit；
- SBOM generation（release CI）。

---

## 9. Build

开发：
```bash
uv sync --group dev
```

测试：
```bash
uv run pytest
```

Lint：
```bash
uv run ruff check .
uv run ruff format --check .
```

Mypy：
```bash
uv run mypy src
```

---

## 10. 不采用

首版不引入：
- FastAPI；
- Redis；
- SQL database；
- Docker hard dependency；
- Celery；
- Electron；
- Tauri；
- vector DB；
- remote HTTP MCP。

本项目不需要这些基础设施即可完成核心目标。
