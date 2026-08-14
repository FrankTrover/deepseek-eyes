"""Phase 5 MCP stdio server tests (no subprocess — direct server inspection).

In the OpenCode desktop flow, ``deepseek_eyes_observe`` is a PLUGIN tool (not an
MCP tool), so this server only exposes capabilities + capture. observe is tested
at the Runtime level (test_runtime.py) and the bridge level (test_host_bridge.py).
"""

from __future__ import annotations

import pytest

from deepseek_eyes.mcp_server import build_server
from deepseek_eyes.runtime import Runtime

from .conftest import FakeProvider


async def test_tools_list() -> None:
    server = build_server(Runtime(provider=FakeProvider()))
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert {
        "deepseek_eyes_capabilities",
        "deepseek_eyes_capture",
    } <= names
    # observe is NOT an MCP tool — it is exposed by the plugin (single registry).
    assert "deepseek_eyes_observe" not in names


async def test_zcode_observe_accepts_cached_image(monkeypatch, tmp_path) -> None:
    from PIL import Image

    image = tmp_path / "attachment.png"
    Image.new("RGB", (8, 8), "red").save(image)
    monkeypatch.setenv("DEEPSEEK_EYES_MCP_OBSERVE", "1")
    monkeypatch.setenv("DEEPSEEK_EYES_ALLOWED_ROOTS", str(tmp_path))

    server = build_server(Runtime(provider=FakeProvider()))
    result = await server.call_tool(
        "deepseek_eyes_observe",
        {"sources": [str(image)], "question": "What is visible?", "mode": "describe"},
    )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["tainted"] is True


async def test_zcode_observe_rejects_mimo_before_provider(monkeypatch, tmp_path) -> None:
    from mcp.server.mcpserver.exceptions import ToolError
    from PIL import Image

    cache = tmp_path / "image-cache"
    session = cache / "sess_mimo"
    session.mkdir(parents=True)
    image = session / "attachment.png"
    Image.new("RGB", (8, 8), "red").save(image)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "today.log").write_text(
        '[host] done {"sessionId":"sess_mimo","modelCurrent":"xiaomi/mimo-v2.5"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_EYES_MCP_OBSERVE", "1")
    monkeypatch.setenv("DEEPSEEK_EYES_MCP_ATTACHMENTS_ONLY", "1")
    monkeypatch.setenv("DEEPSEEK_EYES_ALLOWED_ROOTS", str(cache))
    monkeypatch.setenv("DEEPSEEK_EYES_ZCODE_LOG_DIR", str(logs))
    provider = FakeProvider()

    server = build_server(Runtime(provider=provider))
    with pytest.raises(ToolError, match="disabled for active ZCode model"):
        await server.call_tool(
            "deepseek_eyes_observe",
            {"sources": [str(image)], "question": "What is visible?"},
        )

    assert provider.calls == []


async def test_zcode_observe_accepts_deepseek_session(monkeypatch, tmp_path) -> None:
    from PIL import Image

    cache = tmp_path / "image-cache"
    session = cache / "sess_deepseek"
    session.mkdir(parents=True)
    image = session / "attachment.png"
    Image.new("RGB", (8, 8), "red").save(image)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "today.log").write_text(
        '[host] done {"sessionId":"sess_deepseek",'
        '"modelCurrent":"provider/deepseek-v4-flash"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_EYES_MCP_OBSERVE", "1")
    monkeypatch.setenv("DEEPSEEK_EYES_MCP_ATTACHMENTS_ONLY", "1")
    monkeypatch.setenv("DEEPSEEK_EYES_ALLOWED_ROOTS", str(cache))
    monkeypatch.setenv("DEEPSEEK_EYES_ZCODE_LOG_DIR", str(logs))
    provider = FakeProvider()

    server = build_server(Runtime(provider=provider))
    result = await server.call_tool(
        "deepseek_eyes_observe",
        {"sources": [str(image)], "question": "What is visible?"},
    )

    assert result.is_error is False
    assert len(provider.calls) == 1


async def test_capabilities_call() -> None:
    server = build_server(Runtime(provider=FakeProvider()))
    result = await server.call_tool("deepseek_eyes_capabilities", {})
    assert result.is_error is False
    assert result.structured_content is not None


async def test_capture_call_returns_error(monkeypatch) -> None:
    # Capture must never open a real overlay during tests: stub the backend so
    # the tool returns a structured CAPTURE_CANCELLED instead of grabbing the
    # screen or showing a selection window.
    from deepseek_eyes.errors import ErrorCode, EyesError

    def cancel_selector():
        raise EyesError(ErrorCode.CAPTURE_CANCELLED, "cancelled")

    monkeypatch.setattr(
        "deepseek_eyes.runtime.CaptureBackend",
        lambda **kw: __import__(
            "deepseek_eyes.capture", fromlist=["CaptureBackend"]
        ).CaptureBackend(region_selector=cancel_selector, **kw),
    )
    server = build_server(Runtime(provider=FakeProvider()))
    result = await _call_tool_wire(server, "deepseek_eyes_capture", {"scope": "region"})
    assert result.is_error is True
    # The structured error code survives in the is_error text content.
    text = "".join(c.text for c in result.content)
    assert "CAPTURE_CANCELLED" in text


def _call_tool_wire(server, name: str, arguments: dict):
    """Invoke a tool through the wire-level handler (what a stdio client hits)."""
    from mcp.server.request_state import ServerRequestContext
    from mcp.types import CallToolRequestParams

    ctx = ServerRequestContext.__new__(ServerRequestContext)
    ctx.session = None
    ctx.lifespan_context = None
    ctx.protocol_version = "2026-07-28"
    ctx.method = "tools/call"
    ctx.params = arguments
    ctx.request_id = None
    ctx.meta = None
    ctx.request = None
    ctx.close_sse_stream = None
    ctx.close_standalone_sse_stream = None
    params = CallToolRequestParams(name=name, arguments=arguments)
    return server._handle_call_tool(ctx, params)
