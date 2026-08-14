"""Phase 6 — host bridge protocol tests.

Spawns the real ``deepseek-eyes adapter`` process (fake provider injected via
env) and exercises the JSON-lines protocol the OpenCode plugin will use:
ping, capabilities, register (data URL), observe, and error framing.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import subprocess
import sys

import pytest
from PIL import Image

from deepseek_eyes.contracts import VisionObservation

pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)  # Windows asyncio subprocess transport teardown noise


def _red_png() -> bytes:
    img = Image.new("RGB", (32, 32), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _data_url(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


class _Bridge:
    """Tiny JSON-lines client for the bridge subprocess."""

    def __init__(self, proc: subprocess.Popen, writer: asyncio.StreamWriter) -> None:
        self._proc = proc
        self._writer = writer
        self._next_id = 0

    async def call(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        req_id = self._next_id
        self._writer.write(
            (json.dumps({"id": req_id, "method": method, "params": params or {}}) + "\n").encode()
        )
        await self._writer.drain()
        return await self._reader.readline()

    async def close(self) -> None:
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()


@pytest.fixture()
async def bridge(tmp_path, monkeypatch):
    """Launch the bridge with a fake provider (no network, no real credential)."""
    fake_mod = tmp_path / "fake_provider_mod.py"
    fake_mod.write_text(
        """
from deepseek_eyes.contracts import VisionObservation

class FakeProvider:
    model = "mimo-v2.5"
    def __init__(self):
        self._loop = None
    async def observe(self, request, media):
        loop = __import__("asyncio").get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("provider reused across event loops")
        return VisionObservation(
            evidence=[{"source_ref": request.sources[0], "kind": "text",
                       "text": "fake observation", "confidence": 1.0}]
        )
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["DEEPSEEK_EYES_FAKE_PROVIDER"] = str(fake_mod)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "deepseek_eyes.host_bridge",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
        cwd=os.path.dirname(__file__) + os.sep + ".." + os.sep + "src",
    )
    bridge = _Bridge(proc, proc.stdin)
    bridge._reader = proc.stdout
    try:
        yield bridge
    finally:
        await bridge.close()


async def test_ping(bridge):
    line = await bridge.call("ping")
    msg = json.loads(line)
    assert msg["id"] == 1
    assert msg["result"] == {"pong": True}


async def test_capabilities(bridge):
    line = await bridge.call("capabilities")
    msg = json.loads(line)
    assert msg["result"]["provider"] == "mimo-v2.5"
    assert msg["result"]["max_sources"] == 8


async def test_register_and_observe(bridge):
    line = await bridge.call("register", {"data_url": _data_url(_red_png())})
    ref = json.loads(line)["result"]["source_ref"]
    assert ref.startswith("src_")

    line = await bridge.call(
        "observe", {"sources": [ref], "question": "what color?", "mode": "extract"}
    )
    msg = json.loads(line)
    assert "error" not in msg
    obs = VisionObservation(**msg["result"]["observation"])
    assert obs.tainted is True
    assert obs.may_authorize_actions is False
    assert obs.evidence[0].text == "fake observation"

    # A second uncached observation must reuse the same long-lived bridge loop.
    # Persistent HTTP clients and asyncio locks cannot safely cross loops.
    line = await bridge.call(
        "observe", {"sources": [ref], "question": "same loop?", "mode": "extract"}
    )
    msg = json.loads(line)
    assert "error" not in msg


async def test_bad_base64_rejected(bridge):
    line = await bridge.call("register", {"data_url": "data:image/png;base64,not!base64"})
    msg = json.loads(line)
    assert msg["error"]["code"] == "REQUEST_INVALID"


async def test_malformed_line_gets_error_frame(bridge):
    bridge._writer.write(b"this is not json\n")
    await bridge._writer.drain()
    line = await bridge._reader.readline()
    msg = json.loads(line)
    assert msg["id"] is None
    assert msg["error"]["code"] == "REQUEST_INVALID"


async def test_unknown_method_rejected(bridge):
    line = await bridge.call("frobnicate")
    msg = json.loads(line)
    assert msg["error"]["code"] == "REQUEST_INVALID"
