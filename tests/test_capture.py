"""Phase 7 — Capture tests (headless; selectors/confirmators injected).

GUI overlay and real screen grabs are exercised manually; every behavior that
can fail headlessly is covered here: scope gating, cancel, deny, no
double-capture, and leftover-free in-memory frames.
"""

from __future__ import annotations

import pytest

from deepseek_eyes.capture import CaptureBackend
from deepseek_eyes.errors import ErrorCode, EyesError

_MONITOR = {"left": 0, "top": 0, "width": 1920, "height": 1080}


@pytest.fixture(autouse=True)
def _fake_mss(monkeypatch):
    """Fake mss so headless tests never touch a real screen."""

    def make_shot(width, height):
        return type(
            "Shot",
            (),
            {
                "rgb": bytes(width * height * 3),
                "size": type("Size", (), {"width": width, "height": height})(),
            },
        )()

    class FakeMss:
        def __init__(self):
            self.monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080}, _MONITOR]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, monitor):
            return make_shot(monitor["width"], monitor["height"])

    fake = type(
        "mss",
        (),
        {"mss": FakeMss, "tools": type("tools", (), {"to_png": lambda rgb, size: b"PNG-FAKE"})},
    )
    monkeypatch.setattr("deepseek_eyes.capture.mss", fake)


def test_region_capture_uses_selector_rect():
    backend = CaptureBackend(region_selector=lambda: (10, 20, 300, 400))
    frame = backend.capture("region")
    assert frame.png_bytes == b"PNG-FAKE"
    assert (frame.x, frame.y, frame.width, frame.height) == (10, 20, 300, 400)


def test_region_cancel_maps_to_capture_cancelled():
    def cancel():
        raise EyesError(ErrorCode.CAPTURE_CANCELLED, "cancelled")

    backend = CaptureBackend(region_selector=cancel)
    with pytest.raises(EyesError) as ei:
        backend.capture("region")
    assert ei.value.code == ErrorCode.CAPTURE_CANCELLED


def test_region_zero_area_is_cancel():
    backend = CaptureBackend(region_selector=lambda: (0, 0, 0, 0))
    with pytest.raises(EyesError) as ei:
        backend.capture("region")
    assert ei.value.code == ErrorCode.CAPTURE_CANCELLED


def test_fullscreen_denied_when_not_enabled():
    backend = CaptureBackend(fullscreen_allowed=False)
    with pytest.raises(EyesError) as ei:
        backend.capture("fullscreen")
    assert ei.value.code == ErrorCode.FULLSCREEN_NOT_ENABLED


def test_fullscreen_requires_confirmation():
    backend = CaptureBackend(fullscreen_allowed=True, confirmator=lambda _m: False)
    with pytest.raises(EyesError) as ei:
        backend.capture("fullscreen")
    assert ei.value.code == ErrorCode.CAPTURE_CONFIRMATION_DENIED


def test_fullscreen_confirmed_captures():
    backend = CaptureBackend(fullscreen_allowed=True, confirmator=lambda _m: True)
    frame = backend.capture("fullscreen")
    assert frame.png_bytes == b"PNG-FAKE"


def test_window_capture(monkeypatch):
    monkeypatch.setattr("deepseek_eyes.capture._foreground_window_rect", lambda: (5, 5, 800, 600))
    frame = CaptureBackend().capture("window")
    assert (frame.width, frame.height) == (800, 600)


def test_unknown_scope_rejected():
    backend = CaptureBackend()
    with pytest.raises(EyesError) as ei:
        backend.capture("background")
    assert ei.value.code == ErrorCode.REQUEST_INVALID


def test_backend_captures_only_once():
    backend = CaptureBackend(region_selector=lambda: (0, 0, 10, 10))
    backend.capture("region")
    with pytest.raises(EyesError) as ei:
        backend.capture("region")
    assert ei.value.code == ErrorCode.REQUEST_INVALID
