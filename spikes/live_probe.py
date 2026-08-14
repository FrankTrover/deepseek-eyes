"""S0-1 live MiMo Token Plan probe — runnable once credentials are provided.

Usage:
    $env:DEEPSEEK_EYES_BASE_URL = "https://...token-plan-base-url..."
    $env:DEEPSEEK_EYES_TOKEN    = "tp-..."
    .venv\\Scripts\\python.exe spikes\\live_probe.py

Makes 10 real requests to ``mimo-v2.5`` with a built-in safe fixture (a
synthetic in-memory PNG — never a user image), verifies JSON-mode output and
usage extraction, and writes a redacted report to ``spikes/LIVE_PROBE_RESULT.md``.

Guarantees:
- the token and the base64 payload are never printed or written;
- no fixture file is created on disk.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import time
from pathlib import Path

from PIL import Image


def _safe_fixture() -> bytes:
    """A synthetic PNG: a red square with a black '42' area. No real content."""
    img = Image.new("RGB", (256, 256), (255, 0, 0))
    for y in range(100, 140):
        for x in range(100, 140):
            img.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _require_credentials() -> tuple[str, str]:
    base = os.environ.get("DEEPSEEK_EYES_BASE_URL", "").strip()
    token = os.environ.get("DEEPSEEK_EYES_TOKEN", "").strip()
    if not base or not token:
        raise SystemExit("set DEEPSEEK_EYES_BASE_URL and DEEPSEEK_EYES_TOKEN first")
    return base, token


async def _one_call(provider, question: str, fixture: bytes) -> dict:
    from deepseek_eyes.contracts import ObserveRequest

    start = time.perf_counter()
    request = ObserveRequest(
        sources=["src_probe"],
        question=question,
        mode="extract",
    )

    class _FakeMedia:
        ref = "src_probe"
        canonical_bytes = fixture
        mime_type = "image/png"

    obs = await provider.observe(request, [_FakeMedia()])
    latency_ms = (time.perf_counter() - start) * 1000
    usage = obs.usage
    return {
        "ok": True,
        "latency_ms": round(latency_ms, 1),
        "evidence_count": len(obs.evidence),
        "summary": (obs.summary or "")[:120],
        "usage": {
            "prompt_tokens": usage.prompt_tokens if usage else None,
            "completion_tokens": usage.completion_tokens if usage else None,
            "cached_tokens": usage.cached_tokens if usage else None,
            "image_tokens": usage.image_tokens if usage else None,
            "reasoning_tokens": usage.reasoning_tokens if usage else None,
            "total_tokens": usage.total_tokens if usage else None,
        },
    }


async def main() -> int:
    base, token = _require_credentials()

    from deepseek_eyes.provider import MiMoConfig, MiMoProvider

    config = MiMoConfig(base_url=base, token=token)
    provider = MiMoProvider(config, timeout=120.0)
    fixture = _safe_fixture()

    questions = [
        "What is the dominant color of the image? Answer with a single word.",
    ] * 10

    results = []
    for i, q in enumerate(questions, start=1):
        try:
            results.append(await _one_call(provider, q, fixture))
        except Exception as exc:
            results.append({"ok": False, "error": str(exc).split("|")[0][:200]})
        print(f"  {i:2}/10 done")

    passed = sum(1 for r in results if r.get("ok"))
    report = {
        "provider": "mimo-v2.5",
        "requests": len(results),
        "passed": passed,
        "results": results,
        "summary": {
            "all_valid_json": passed == len(results),
            "reasoning_zero_check": all(
                (r.get("usage") or {}).get("reasoning_tokens") in (None, 0) for r in results
            ),
        },
    }
    out = Path(__file__).resolve().parent / "LIVE_PROBE_RESULT.md"  # noqa: ASYNC240
    body = (
        "# S0-1 Live MiMo Probe Result\n\n"
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"`mimo-v2.5`, 10 requests, `thinking=disabled`, JSON mode.\n\n"
        "```json\n" + json.dumps(report, indent=2, ensure_ascii=False) + "\n```\n"
    )
    out.write_text(body, encoding="utf-8")
    print(f"passed {passed}/{len(results)} -> {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
