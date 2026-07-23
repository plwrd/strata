"""Exclude the main window from screenshots and screen shares (Signal-style).

On Windows this uses ``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`` so the
window stays visible on the physical display but is omitted from capture
pipelines (Zoom, Teams, OBS, Snipping Tool, Windows Recall, etc.).

Other platforms: best-effort or no-op. Capture exclusion is OS-enforced; the UI
only toggles the request.
"""

from __future__ import annotations

import sys
from typing import Protocol

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

# Win32 — Windows 10 2004+ (build 19041). Older builds treat this as WDA_MONITOR
# (black box in captures) which is still privacy-preserving.
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


class _HasWinId(Protocol):
    def winId(self) -> object: ...


def set_window_excluded_from_capture(window: _HasWinId, *, enabled: bool) -> bool:
    """Ask the OS to hide ``window`` from screen capture when ``enabled``.

    Returns True when the platform call succeeded (or was a deliberate no-op on
    an unsupported OS). Returns False when the call was attempted and failed.
    """
    if sys.platform == "win32":
        return _windows_set_display_affinity(window, enabled=enabled)
    # macOS has NSWindow.sharingType = .none; Qt's winId is an NSView and the
    # Cocoa bridge is fragile without PyObjC. Leave a clear log rather than a
    # half-working path — Windows (Signal's primary desktop capture block) is
    # fully supported.
    logger.info(
        "screen_security.unsupported_platform",
        platform=sys.platform,
        enabled=enabled,
    )
    return True


def _windows_set_display_affinity(window: _HasWinId, *, enabled: bool) -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

    hwnd = int(window.winId())
    affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
    ok = bool(user32.SetWindowDisplayAffinity(hwnd, affinity))
    if not ok:
        err = ctypes.GetLastError()
        logger.warning(
            "screen_security.windows_affinity_failed",
            enabled=enabled,
            win_error=err,
        )
        return False
    logger.info("screen_security.windows_affinity", enabled=enabled)
    return True
