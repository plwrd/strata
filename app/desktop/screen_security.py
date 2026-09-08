"""Exclude the main window from screenshots and screen shares (Signal-style).

On Windows this uses ``SetWindowDisplayAffinity``. Prefer
``WDA_EXCLUDEFROMCAPTURE`` so the window stays visible on the physical display
but is omitted from capture pipelines (Zoom, Teams, OBS, Snipping Tool,
Windows Recall, etc.). If that fails (older builds), fall back to
``WDA_MONITOR`` which blacks the window out in captures — still
privacy-preserving.

Other platforms: best-effort or no-op. Capture exclusion is OS-enforced; the UI
only toggles the request.
"""

from __future__ import annotations

import sys
from typing import Any, Protocol, cast

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

# Win32 — Windows 10 2004+ (build 19041) for WDA_EXCLUDEFROMCAPTURE.
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011
GA_ROOT = 2


class _HasWinId(Protocol):
    def winId(self) -> object: ...


def _is_windows() -> bool:
    """A function, not `sys.platform == "win32"` inline.

    Type checkers narrow that literal to whichever platform they are running
    on, which marks every other branch unreachable and stops checking it. This
    code has to be correct on all three."""
    return sys.platform == "win32"


def set_window_excluded_from_capture(window: _HasWinId, *, enabled: bool) -> bool:
    """Ask the OS to hide ``window`` from screen capture when ``enabled``.

    Returns True when the platform call succeeded (or was a deliberate no-op on
    an unsupported OS). Returns False when the call was attempted and failed.
    """
    if _is_windows():
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
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND

    # winId() is a sip.voidptr on Qt; the Protocol types it as `object`
    # because this module must not import Qt just to name it.
    raw = int(cast(Any, window.winId()))
    hwnd = int(user32.GetAncestor(raw, GA_ROOT) or 0) or raw

    if not enabled:
        ok = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_NONE))
        if not ok:
            err = ctypes.GetLastError()
            logger.warning(
                "screen_security.windows_affinity_failed",
                enabled=False,
                affinity=WDA_NONE,
                win_error=err,
            )
            return False
        logger.info("screen_security.windows_affinity", enabled=False, affinity=WDA_NONE)
        return True

    ok = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    if ok:
        logger.info(
            "screen_security.windows_affinity",
            enabled=True,
            affinity=WDA_EXCLUDEFROMCAPTURE,
        )
        return True

    exclude_err = ctypes.GetLastError()
    logger.warning(
        "screen_security.windows_exclude_failed_trying_monitor",
        win_error=exclude_err,
    )
    ok = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR))
    if not ok:
        err = ctypes.GetLastError()
        logger.warning(
            "screen_security.windows_affinity_failed",
            enabled=True,
            affinity=WDA_MONITOR,
            win_error=err,
        )
        return False
    logger.info(
        "screen_security.windows_affinity",
        enabled=True,
        affinity=WDA_MONITOR,
        fallback=True,
    )
    return True
