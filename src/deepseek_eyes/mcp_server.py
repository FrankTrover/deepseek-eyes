"""MCP stdio server exposing the three tools:

- ``deepseek_eyes_capabilities`` — version + capability handshake;
- ``deepseek_eyes_observe`` — structured, tainted visual observation;
- ``deepseek_eyes_capture`` — screen capture (region / window / fullscreen).

The Runtime is created lazily via lifespan so the same process can serve many
sessions without re-resolving provider config. Credentials are optional for
tool listing (``capabilities``) but required before any ``observe`` call.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from .contracts import (
    CapabilitiesResult,
    CaptureResult,
    ObserveMode,
    ObserveRequest,
    VisionObservation,
)
from .errors import ErrorCode, EyesError
from .ids import is_source_ref
from .provider import MiMoConfig, MiMoProvider
from .runtime import Runtime

CaptureScope = Literal["region", "window", "fullscreen"]

APP_NAME = "deepseek-eyes-mcp"


def _attachment_roots() -> tuple[Path, ...]:
    return tuple(
        Path(value).expanduser().resolve()
        for value in os.environ.get("DEEPSEEK_EYES_ALLOWED_ROOTS", "").split(os.pathsep)
        if value.strip()
    )


def _checked_attachment_path(source: str, roots: tuple[Path, ...]) -> Path:
    path = Path(source).expanduser().resolve()
    if not roots or not any(path.is_relative_to(root) for root in roots):
        raise EyesError(
            ErrorCode.REQUEST_INVALID,
            f"image path is outside configured attachment roots: {source}",
        )
    return path


def _zcode_session_id(path: Path, roots: tuple[Path, ...]) -> str | None:
    for root in roots:
        if path.is_relative_to(root):
            return next(
                (part for part in path.relative_to(root).parts if part.startswith("sess_")),
                None,
            )
    return None


def _zcode_model_for_session(log_dir: Path, session_id: str) -> str | None:
    """Read ZCode's latest model decision for an attachment session.

    ZCode exposes MCP tools globally instead of per model. Its image-cache path
    carries the session id and its local host log records the active model, so
    this is the only fail-closed boundary that prevents MiMo from invoking Eyes.
    """
    try:
        logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    needle = f'"sessionId":"{session_id}"'
    for log in logs[:3]:
        try:
            with log.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - 8 * 1024 * 1024))
                text = stream.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in reversed(text.splitlines()):
            if needle not in line:
                continue
            match = re.search(r'"modelCurrent":"([^"]+)"', line)
            if match is None:
                match = re.search(r'"runtimeModel":\{.*?"model":"([^"]+)"', line)
            if match is not None:
                return match.group(1)
    return None


def _guard_zcode_model(path: Path, roots: tuple[Path, ...]) -> None:
    log_dir = os.environ.get("DEEPSEEK_EYES_ZCODE_LOG_DIR", "").strip()
    if not log_dir:
        return
    session_id = _zcode_session_id(path, roots)
    model = (
        _zcode_model_for_session(Path(log_dir).expanduser().resolve(), session_id)
        if session_id
        else None
    )
    if model is None:
        raise EyesError(
            ErrorCode.REQUEST_INVALID,
            "cannot verify the active ZCode model; DeepSeek Eyes is denied to prevent billing",
        )
    if "deepseek" not in model.lower():
        raise EyesError(
            ErrorCode.REQUEST_INVALID,
            f"DeepSeek Eyes is disabled for active ZCode model: {model}",
        )


def _runtime_from_env() -> Runtime:
    """Build a Runtime from env/keyring config, or a stub when unset.

    A missing credential does not prevent the server from starting — it only
    makes ``observe`` fail with ``CREDENTIAL_MISSING``.

    ``DEEPSEEK_EYES_FAKE_PROVIDER`` (a path to a module exposing a
    ``FakeProvider`` class) swaps in a fake provider for tests; it is never
    honored when the variable is absent.
    """
    from .credentials import load_credential

    fake_path = os.environ.get("DEEPSEEK_EYES_FAKE_PROVIDER", "").strip()
    if fake_path:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_eyes_fake_provider", fake_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return Runtime(provider=module.FakeProvider())

    cred = load_credential()
    if cred is not None:
        config = MiMoConfig(base_url=cred.base_url, token=cred.token)
        provider: Any = MiMoProvider(config)
    else:
        provider = _UnconfiguredProvider()
    return Runtime(provider)


class _UnconfiguredProvider:
    """Placeholder provider that reports a missing credential."""

    model = "mimo-v2.5"

    async def observe(self, request, media):
        raise EyesError(
            ErrorCode.CREDENTIAL_MISSING,
            "MiMo Token Plan credential not configured "
            "(set DEEPSEEK_EYES_BASE_URL and DEEPSEEK_EYES_TOKEN)",
        )


def build_server(runtime: Runtime | None = None) -> MCPServer:
    rt = runtime or _runtime_from_env()
    server = MCPServer(
        name=APP_NAME,
        title="DeepSeek Eyes",
        description="Structured visual evidence from the MiMo Token Plan, tainted as untrusted.",
        version="0.1.0",
    )

    @server.tool(
        name="deepseek_eyes_capabilities",
        description="Return DeepSeek Eyes version and capability flags.",
        structured_output=True,
    )
    async def capabilities() -> CapabilitiesResult:
        return rt.capabilities()

    # OpenCode owns this tool in its plugin so registration and observation use
    # one bridge process. ZCode has no equivalent attachment hook, so it opts in
    # here and passes paths from its own image cache.
    if os.environ.get("DEEPSEEK_EYES_MCP_OBSERVE") == "1":
        attachment_roots = _attachment_roots()

        @server.tool(
            name="deepseek_eyes_observe",
            description=(
                "Inspect all user-attached images in one call for an active DeepSeek model. "
                "Ask for exact text, geometry and overlays before identity. In the final answer, "
                "prefer direct evidence; identity/inference is provisional below 0.85 confidence "
                "or with material uncertainty. Do not guess or retry. The server "
                "rejects MiMo and other non-DeepSeek ZCode sessions before any vision call."
            ),
            structured_output=True,
        )
        async def observe(
            sources: Annotated[list[str], Field(min_length=1, max_length=8)],
            question: Annotated[str, Field(min_length=1, max_length=4096)],
            mode: ObserveMode = "extract",
        ) -> VisionObservation:
            refs: list[str] = []
            for source in sources:
                if is_source_ref(source):
                    if os.environ.get("DEEPSEEK_EYES_MCP_ATTACHMENTS_ONLY") == "1":
                        raise EyesError(
                            ErrorCode.REQUEST_INVALID,
                            "ZCode Eyes accepts attachment paths only",
                        )
                    refs.append(source)
                    continue
                path = _checked_attachment_path(source, attachment_roots)
                _guard_zcode_model(path, attachment_roots)
                refs.append(await rt.register_source_path(str(path), origin="mcp:zcode"))
            return await rt.observe(
                ObserveRequest(sources=refs, question=question, mode=mode)
            )

    if os.environ.get("DEEPSEEK_EYES_MCP_ATTACHMENTS_ONLY") != "1":

        @server.tool(
            name="deepseek_eyes_capture",
            description="Capture the screen (region, window, or fullscreen) and register the frame as a source.",
            structured_output=True,
        )
        async def capture(
            scope: Annotated[CaptureScope, Field(default="region")],
        ) -> CaptureResult:
            ref = await rt.capture(scope)
            return CaptureResult(source_ref=ref)

    return server


def main() -> None:
    import anyio

    server = build_server()
    anyio.run(server.run_stdio_async)


if __name__ == "__main__":
    main()
