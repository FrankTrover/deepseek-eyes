"""Windows-first screen capture with explicit human interaction.

Three scopes, matching the MCP contract:

- ``region`` — the user drags a rectangle on an overlay (PySide6);
- ``window`` — the foreground window is captured (pywin32);
- ``fullscreen`` — the primary monitor is captured with a per-call local human
  confirmation dialog, gated by ``fullscreen_capture_allowed`` in the config.

There is deliberately no background/periodic capture anywhere. Every path
returns in-memory bytes; nothing is written to disk unless the caller does so.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .errors import ErrorCode, EyesError

try:
    import mss
    import mss.tools
except ImportError:  # pragma: no cover — optional dependency
    mss = None  # type: ignore[assignment,unused-ignore]

try:
    import win32gui
except ImportError:  # pragma: no cover — optional dependency
    win32gui = None  # type: ignore[assignment,unused-ignore]

# A selector returns a normalized (x, y, w, h) rectangle in screen pixels, or
# raises EyesError(CAPTURE_CANCELLED). Injectable for headless tests.
RegionSelector = Callable[[], tuple[int, int, int, int]]
# A confirmator returns True to allow, False to deny. Injectable for tests.
Confirmator = Callable[[str], bool]


@dataclass(frozen=True)
class CapturedFrame:
    """In-memory screenshot bytes plus geometry (no files, no leftovers)."""

    png_bytes: bytes
    x: int
    y: int
    width: int
    height: int


class CaptureBackend:
    """One object per capture request; never captures twice."""

    def __init__(
        self,
        *,
        region_selector: RegionSelector | None = None,
        confirmator: Confirmator | None = None,
        fullscreen_allowed: bool = False,
    ) -> None:
        self._select = region_selector or _qt_region_selector()
        self._confirm = confirmator or _default_confirmator()
        self._fullscreen_allowed = fullscreen_allowed
        self._captured = False

    def _ensure_once(self) -> None:
        if self._captured:
            raise EyesError(ErrorCode.REQUEST_INVALID, "this CaptureBackend already captured")
        self._captured = True

    def capture(self, scope: str) -> CapturedFrame:
        if scope not in ("region", "window", "fullscreen"):
            raise EyesError(ErrorCode.REQUEST_INVALID, f"unknown capture scope: {scope}")
        self._ensure_once()
        if scope == "region":
            return self._capture_region()
        if scope == "window":
            return self._capture_window()
        return self._capture_fullscreen()

    # -- scopes --------------------------------------------------------------

    def _capture_region(self) -> CapturedFrame:
        try:
            x, y, w, h = self._select()
        except EyesError:
            raise
        except Exception as exc:
            raise EyesError(ErrorCode.CAPTURE_CANCELLED, "region selection aborted") from exc
        if w <= 0 or h <= 0:
            raise EyesError(ErrorCode.CAPTURE_CANCELLED, "empty selection")
        return _grab_rect(x, y, w, h)

    def _capture_window(self) -> CapturedFrame:
        rect = _foreground_window_rect()
        if rect is None:
            raise EyesError(ErrorCode.CAPTURE_CANCELLED, "no foreground window found")
        x, y, w, h = rect
        with mss.mss() as sct:
            shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
        return _frame_from_shot(shot, x, y)

    def _capture_fullscreen(self) -> CapturedFrame:
        if not self._fullscreen_allowed:
            raise EyesError(
                ErrorCode.FULLSCREEN_NOT_ENABLED,
                "full-screen capture is disabled in configuration",
            )
        if not self._confirm(
            "DeepSeek Eyes wants to capture the full screen. Allow this one capture?"
        ):
            raise EyesError(ErrorCode.CAPTURE_CONFIRMATION_DENIED, "full-screen capture denied")
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            shot = sct.grab(monitor)
        return _frame_from_shot(shot, monitor["left"], monitor["top"])


# -- frame helpers ------------------------------------------------------------


def _frame_from_shot(shot, x: int, y: int) -> CapturedFrame:
    png = mss.tools.to_png(shot.rgb, shot.size)
    assert png is not None
    return CapturedFrame(png_bytes=png, x=x, y=y, width=shot.size.width, height=shot.size.height)


def _grab_rect(x: int, y: int, w: int, h: int) -> CapturedFrame:
    with mss.mss() as sct:
        shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
    return _frame_from_shot(shot, x, y)


def _foreground_window_rect() -> tuple[int, int, int, int] | None:
    """Return the foreground window's client rect in screen pixels (pywin32)."""
    if win32gui is None:
        raise EyesError(ErrorCode.CAPTURE_NOT_ALLOWED, "pywin32 not installed")
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    if not win32gui.IsWindowVisible(hwnd):
        return None
    rect = win32gui.GetWindowRect(hwnd)
    x, y, x2, y2 = rect
    return (x, y, x2 - x, y2 - y)


def _default_confirmator() -> Confirmator:
    def confirm(message: str) -> bool:
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
        except ImportError:
            raise EyesError(ErrorCode.CAPTURE_NOT_ALLOWED, "PySide6 not installed") from None
        QApplication.instance() or QApplication([])
        box = QMessageBox()
        box.setWindowTitle("DeepSeek Eyes")
        box.setText(message)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    return confirm


def _qt_region_selector() -> RegionSelector:
    def select() -> tuple[int, int, int, int]:
        try:
            from PySide6.QtCore import QPoint, QRect, Qt
            from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
            from PySide6.QtWidgets import QApplication, QWidget
        except ImportError:
            raise EyesError(ErrorCode.CAPTURE_NOT_ALLOWED, "PySide6 not installed") from None

        app = QApplication.instance() or QApplication([])
        screen_geo = QGuiApplication.primaryScreen().geometry()

        class _Overlay(QWidget):
            def __init__(self) -> None:
                super().__init__(None)
                self._origin: QPoint | None = None
                self._rect: QRect | None = None
                self.result: QRect | None = None
                self.cancelled = False
                self.setWindowFlags(
                    Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                )
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                self.setGeometry(screen_geo)
                self.setCursor(Qt.CursorShape.CrossCursor)
                self.setMouseTracking(True)

            def paintEvent(self, event) -> None:
                painter = QPainter(self)
                painter.fillRect(self.rect(), QColor(0, 0, 0, 60))
                if self._rect is not None:
                    painter.setPen(QPen(Qt.GlobalColor.red, 2))
                    painter.drawRect(self._rect)
                painter.end()

            def mousePressEvent(self, event) -> None:
                self._origin = event.position().toPoint()
                self._rect = QRect(self._origin, self._origin)

            def mouseMoveEvent(self, event) -> None:
                if self._origin is not None:
                    self._rect = QRect(self._origin, event.position().toPoint()).normalized()
                    self.update()

            def mouseReleaseEvent(self, event) -> None:
                if self._rect is not None and self._rect.width() > 0 and self._rect.height() > 0:
                    self.result = self._rect
                self.close()

            def keyPressEvent(self, event) -> None:
                if event.key() == Qt.Key.Key_Escape:
                    self.cancelled = True
                    self.close()

        overlay = _Overlay()
        overlay.show()
        overlay.activateWindow()
        app.exec()
        if overlay.cancelled or overlay.result is None:
            raise EyesError(ErrorCode.CAPTURE_CANCELLED, "region selection cancelled")
        r = overlay.result
        # Clamp to the primary screen to keep coordinates sane under DPI scaling.
        x = max(screen_geo.left(), r.x())
        y = max(screen_geo.top(), r.y())
        w = min(screen_geo.right(), r.right()) - x
        h = min(screen_geo.bottom(), r.bottom()) - y
        return (x, y, max(w, 1), max(h, 1))

    return select
