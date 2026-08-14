"""Host adapter bridge — JSON-lines over stdio for the OpenCode plugin.

The TypeScript plugin spawns this process once and speaks one JSON object per
line::

    {"id": 1, "method": "ping", "params": {}}
    {"id": 1, "result": {"pong": true}}

    {"id": 2, "method": "register", "params": {"data_url": "data:image/png;base64,..."}}
    {"id": 2, "result": {"source_ref": "src_..."}}

    {"id": 3, "method": "observe", "params": {"source_ref": "...", "question": "..."}}
    {"id": 3, "result": {"observation": {...}}}

Errors come back as ``{"id": ..., "error": {"code": ..., "message": ...}}``.
The bridge reuses the Core Runtime — it never duplicates provider/cache/media
logic — and holds no secrets beyond what the Runtime already resolves from
env/keyring. Stdio only: no ports, no files.

Requests are served serially on one long-lived event loop.  The Runtime owns a
persistent HTTP client and asyncio locks, so moving successive register/observe
calls across short-lived loops can strand the client on a closed loop.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from typing import Any

from . import CONTRACT_VERSION, VISION_SCHEMA_VERSION
from .contracts import ObserveRequest
from .errors import ErrorCode, EyesError
from .mcp_server import _runtime_from_env
from .runtime import Runtime


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    """Split ``data:<mime>;base64,<payload>`` into (bytes, mime)."""
    if not data_url.startswith("data:"):
        raise EyesError(ErrorCode.REQUEST_INVALID, "expected a data: URL")
    head, sep, payload = data_url.partition(",")
    if not sep or not payload:
        raise EyesError(ErrorCode.REQUEST_INVALID, "malformed data: URL")
    mime = head[5:].split(";")[0] or "application/octet-stream"
    try:
        return base64.b64decode(payload), mime
    except (ValueError, TypeError) as exc:
        raise EyesError(ErrorCode.REQUEST_INVALID, "invalid base64 payload") from exc


class BridgeHandler:
    """Request dispatch; one instance per bridge process."""

    def __init__(self, runtime: Runtime | None = None) -> None:
        self._rt = runtime or _runtime_from_env()

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "ping":
            return {"pong": True}
        if method == "capabilities":
            return self._rt.capabilities().model_dump()
        if method == "register":
            raw, _mime = _decode_data_url(params["data_url"])
            ref = await self._rt.register_source(raw, origin=params.get("origin", "host:adapter"))
            return {"source_ref": ref}
        if method == "observe":
            request = ObserveRequest(
                sources=params["sources"],
                question=params["question"],
                mode=params.get("mode", "extract"),
                focus=params.get("focus"),
                contract_version=params.get("contract_version", CONTRACT_VERSION),
                vision_schema_version=params.get("vision_schema_version", VISION_SCHEMA_VERSION),
            )
            obs = await self._rt.observe(request)
            return {"observation": obs.model_dump()}
        raise EyesError(ErrorCode.REQUEST_INVALID, f"unknown method: {method}")


def _write_line(obj: dict[str, Any]) -> None:
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def serve() -> int:
    handler = BridgeHandler()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        for raw in sys.stdin.buffer:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                req_id = msg["id"]
                method = msg["method"]
                params = msg.get("params") or {}
            except (json.JSONDecodeError, KeyError, TypeError):
                _write_line(
                    {
                        "id": None,
                        "error": {
                            "code": ErrorCode.REQUEST_INVALID.value,
                            "message": "malformed request line",
                        },
                    }
                )
                continue
            try:
                result = loop.run_until_complete(handler.dispatch(method, params))
                _write_line({"id": req_id, "result": result})
            except EyesError as exc:
                _write_line(
                    {"id": req_id, "error": {"code": exc.code.value, "message": exc.message}}
                )
            except Exception as exc:  # pragma: no cover — safety net
                _write_line({"id": req_id, "error": {"code": "INTERNAL", "message": str(exc)}})
    finally:
        asyncio.set_event_loop(None)
        loop.close()
    return 0


def main() -> None:
    raise SystemExit(serve())


if __name__ == "__main__":
    main()
