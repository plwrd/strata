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


# --- catching a window the moment it appears ---------------------------------

# Win32 accessibility events. A window appearing is an event the OS will tell us
# about; polling for it is what leaves a gap.
EVENT_OBJECT_CREATE = 0x8000
EVENT_OBJECT_SHOW = 0x8002
OBJID_WINDOW = 0
CHILDID_SELF = 0
WINEVENT_OUTOFCONTEXT = 0x0000


class CaptureGuard:
    """Excludes each new window as it is created, rather than on a timer.

    The per-window Qt hooks cover what Qt models, and a periodic PID sweep
    covers the rest — but "the rest" is the important part and a sweep is,
    by construction, late. The bundled Chromium (and WebView2's browser
    process) create raw Win32 windows for menus, ``<select>`` dropdowns and
    autofill popups; none of them is a ``QWidget`` or a ``QWindow``, so until
    the next tick of the sweep they are on screen and in the recording. A
    dropdown opened and dismissed inside one interval was never covered at all.

    ``SetWinEventHook`` closes that gap: the OS calls us when a window in a
    named process is created or shown, so the affinity is set on the same
    message-loop turn rather than up to a poll interval later. Hooks are
    ``WINEVENT_OUTOFCONTEXT``, so the callback arrives on this thread's message
    queue — which Qt pumps — and nothing is injected into the other process.

    The sweep stays as a backstop for windows that existed before we started
    watching, and for anything a hook misses.
    """

    def __init__(self) -> None:
        self._enabled = False
        self._hooks: dict[int, Any] = {}
        # ctypes callbacks must outlive the hook; a collected thunk would be a
        # jump into freed memory on the next window that opens anywhere.
        self._proc: Any = None

    @property
    def watched_pids(self) -> tuple[int, ...]:
        return tuple(sorted(self._hooks))

    def set_enabled(self, enabled: bool) -> None:
        """Turn hiding on or off. Hooks stay installed either way.

        Keeping them lets "off" still be authoritative: a window that opens
        while hiding is off gets ``WDA_NONE`` set explicitly rather than
        inheriting whatever it happened to have.
        """
        self._enabled = enabled

    def watch(self, pid: int) -> bool:
        """Start catching new windows in ``pid``. Idempotent per process."""
        if not _is_windows() or pid <= 0 or pid in self._hooks:
            return False
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        if self._proc is None:
            self._proc = self._make_callback()
        user32.SetWinEventHook.restype = wintypes.HANDLE
        hook = user32.SetWinEventHook(
            EVENT_OBJECT_CREATE,
            EVENT_OBJECT_SHOW,
            None,
            self._proc,
            pid,
            0,
            WINEVENT_OUTOFCONTEXT,
        )
        if not hook:
            logger.warning("screen_security.hook_failed", pid=pid)
            return False
        self._hooks[pid] = hook
        logger.info("screen_security.hook_installed", pid=pid)
        return True

    def forget(self, pid: int) -> None:
        """Drop the hook for a process that has gone."""
        hook = self._hooks.pop(pid, None)
        if hook is None or not _is_windows():
            return
        import ctypes

        ctypes.windll.user32.UnhookWinEvent(hook)

    def dispose(self) -> None:
        for pid in list(self._hooks):
            self.forget(pid)

    def _make_callback(self) -> Any:
        import ctypes
        from ctypes import wintypes

        proto = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,  # hWinEventHook
            wintypes.DWORD,  # event
            wintypes.HWND,
            wintypes.LONG,  # idObject
            wintypes.LONG,  # idChild
            wintypes.DWORD,  # idEventThread
            wintypes.DWORD,  # dwmsEventTime
        )

        def _on_event(
            _hook: int,
            _event: int,
            hwnd: int,
            id_object: int,
            id_child: int,
            _thread: int,
            _time: int,
        ) -> None:
            # The hook fires for every accessible object, not only windows.
            if id_object != OBJID_WINDOW or id_child != CHILDID_SELF or not hwnd:
                return
            try:
                _apply_affinity_to_hwnd(int(hwnd), enabled=self._enabled)
            except Exception:  # pragma: no cover - a callback must never raise
                return

        return proto(_on_event)


def _apply_affinity_to_hwnd(hwnd: int, *, enabled: bool) -> bool:
    """Set the affinity on ``hwnd``'s top-level window."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    root = int(user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT) or 0) or hwnd
    affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
    if user32.SetWindowDisplayAffinity(wintypes.HWND(root), affinity):
        return True
    if not enabled:
        return False
    return bool(user32.SetWindowDisplayAffinity(wintypes.HWND(root), WDA_MONITOR))
