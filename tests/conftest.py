"""Shared test fixtures: in-memory image builders and a fake provider."""

from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image

from deepseek_eyes.contracts import Evidence, ObserveRequest, Usage, VisionObservation


def png_bytes(
    width: int = 64, height: int = 48, color: tuple[int, int, int] = (255, 0, 0)
) -> bytes:
    """Build a deterministic solid-color PNG in memory."""
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def jpeg_bytes(width: int = 64, height: int = 48) -> bytes:
    img = Image.new("RGB", (width, height), (0, 128, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def gif_animated_bytes() -> bytes:
    frame1 = Image.new("RGB", (32, 32), (255, 0, 0))
    frame2 = Image.new("RGB", (32, 32), (0, 0, 255))
    buf = io.BytesIO()
    frame1.save(buf, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)
    return buf.getvalue()


class FakeProvider:
    """Records calls and returns a canned observation."""

    def __init__(self, model: str = "mimo-v2.5") -> None:
        self.model = model
        self.calls: list[ObserveRequest] = []
        self.observation = VisionObservation(
            evidence=[Evidence(source_ref="", kind="text", text="fake evidence", confidence=0.9)],
            summary="fake",
            usage=Usage(prompt_tokens=10, completion_tokens=2, total_tokens=12),
        )

    async def observe(self, request: ObserveRequest, media: list[Any]) -> VisionObservation:
        self.calls.append(request)
        # Bind evidence to the first source if present.
        if request.sources:
            evidence = [
                Evidence(
                    source_ref=request.sources[0],
                    kind=e.kind,
                    text=e.text,
                    confidence=e.confidence,
                )
                for e in self.observation.evidence
            ]
            return self.observation.model_copy(update={"evidence": evidence})
        return self.observation


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()
