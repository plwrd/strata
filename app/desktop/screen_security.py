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
from collections.abc import Iterable
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


def set_windows_excluded_from_capture(windows: Iterable[_HasWinId], *, enabled: bool) -> None:
    """Apply capture exclusion to several windows.

    The affinity is per top-level window, so a popup, menu, native ``<select>``
    dropdown, tooltip or dialog — each its own OS window — is *not* covered by
    the main window's exclusion and leaks into a recording unless excluded in its
    own right. Callers pass every current top-level window here.
    """
    for window in windows:
        set_window_excluded_from_capture(window, enabled=enabled)


def set_process_windows_excluded_from_capture(
    pid: int, *, enabled: bool, include_hidden: bool = False
) -> int:
    """Exclude every top-level window owned by ``pid`` from capture.

    For the Chrome backend: Strata launches a *separate* browser process, whose
    windows its own per-HWND exclusion cannot reach. This finds the launched
    process's windows by PID and applies the same affinity, so "Hidden for
    sharing" covers that Chrome too.

    ``include_hidden`` also covers windows that exist but are not on screen yet.
    That matters for our *own* process: a menu or a ``<select>`` dropdown is
    created first and shown a moment later, so a visible-only sweep can only ever
    reach it after it has already painted a frame into someone's recording.

    Best-effort and Windows-only: it covers only the windows of that process
    (not, say, other Chrome windows the user opens), and returns the number of
    windows it excluded — 0 on another platform or when the window has not been
    created yet.
    """
    if not _is_windows() or pid <= 0:
        return 0
    return _windows_exclude_by_pid(pid, enabled=enabled, include_hidden=include_hidden)


def set_own_windows_excluded_from_capture(*, enabled: bool) -> int:
    """Apply the affinity to every top-level window *this* process owns.

    The catch-all behind the per-window calls. Qt's own event stream only reaches
    what Qt models as a ``QWidget``/``QWindow``, and neither covers a raw Win32
    window that the bundled Chromium creates for itself. Sweeping by PID does not
    care who created the window or what object wraps it: if it belongs to Strata,
    it is excluded.
    """
    if not _is_windows():
        return 0
    import ctypes

    return set_process_windows_excluded_from_capture(
        int(ctypes.windll.kernel32.GetCurrentProcessId()),
        enabled=enabled,
        include_hidden=True,
    )


def _windows_exclude_by_pid(pid: int, *, enabled: bool, include_hidden: bool) -> int:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
    touched = 0

    # EnumWindows(callback, lparam): callback returns True to keep enumerating.
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _each(hwnd: int, _lparam: int) -> bool:
        nonlocal touched
        if not include_hidden and not user32.IsWindowVisible(hwnd):
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            if user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), affinity):
                touched += 1
            # Try the blackout fallback, same as the single-window path. Only
            # count a window we actually covered — a silent miss is the one
            # thing this feature must not report as a success.
            elif enabled and user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_MONITOR):
                touched += 1
        return True

    user32.EnumWindows(enum_proc(_each), 0)
    logger.info("screen_security.process_windows", pid=pid, enabled=enabled, windows=touched)
    return touched


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
