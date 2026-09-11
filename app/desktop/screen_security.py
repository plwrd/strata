"""Exclude the main window from screenshots and screen shares (Signal-style).

On Windows this uses ``SetWindowDisplayAffinity``. Prefer
``WDA_EXCLUDEFROMCAPTURE`` so the window stays visible on the physical display
but is omitted from capture pipelines (Zoom, Teams, OBS, Snipping Tool,
Windows Recall, etc.). If that fails (older builds), fall back to
``WDA_MONITOR`` which blacks the window out in captures — still
privacy-preserving.

Other platforms report ``UNSUPPORTED`` and mean it. macOS has
``NSWindow.sharingType`` and Linux has nothing at all (X11 lets any client read
the root window by design); neither is implemented, so neither is claimed — the
settings dialog shows the refusal rather than a tick. Capture exclusion is
OS-enforced; the UI only asks for it.

Three rules govern everything in this module, and all three were learned the
hard way:

**A correct affinity is never written again.** Every
``SetWindowDisplayAffinity`` call makes DWM rebuild the window's redirection
surface, and the window flickers as it does. That flicker is not cosmetic: the
rebuild is the moment a frame can be composed outside the exclusion and reach a
recording — a whole-window flash that a screen capture catches in full. The
affinity used to be re-asserted unconditionally by a 1.5 s sweep, by every
activation change, and by a window hook firing on every show, so an
already-excluded window was rebuilt several times a second. There is now exactly
one write site (:func:`_set_affinity_if_needed`), it reads first, and steady
state performs no writes at all. The single unavoidable write happens before the
window is first shown (the Qt surface-created event, or ``EVENT_OBJECT_CREATE``),
so there is no visible frame to catch.

**Every ``ctypes`` prototype is declared before use.** ``ctypes`` assumes a C
``int`` for anything it has not been told about, so an unconfigured
``GetAncestor`` returns a *32-bit* value — a top-level ``HWND`` above 2^31 comes
back sign-extended, and the affinity is then set on a window that does not
exist. The same default turns an ``HWND`` argument above 2^32 into an
``ArgumentError`` raised *inside* an ``EnumWindows`` callback, which ends the
enumeration early and silently leaves every remaining window unprotected.
Declaring the prototypes in one place (:func:`_user32`) is what makes the
result independent of which function happened to run first.

**Nothing here reports a success it did not get.** A window that could not be
excluded is not counted, an unsupported platform says ``UNSUPPORTED`` rather
than ``True``, and the aggregate of several windows is the *weakest* of them.
A screen-privacy control that overstates itself is worse than one that is
absent: the user acts on it.

**Another process's window cannot be excluded — only closed or reported.**
``SetWindowDisplayAffinity`` is refused with ``ERROR_ACCESS_DENIED`` for any
window the calling process does not own (measured on Windows 11 26200 against
a process this one had launched; the affinity *read* works, the write does
not). So the "sweep the engine process by PID" strategy that used to cover
WebView2's browser process and the launched Chrome never excluded a single
window: every ``<select>`` dropdown, tooltip, ``alert()`` and permission bubble
the research pane opened was in the recording, and the sweep's return value
said nothing about it. What *can* be done from outside is
``PostMessage(WM_CLOSE)`` — not subject to the ownership check — and counting
what is on screen. For WebView2, whose browser process has no top-level window
worth keeping (the page itself draws inside our window), a popup that appears
is closed on the message-loop turn we hear about it (:class:`CaptureGuard`),
and every window the :func:`foreign_windows_uncovered` sweep still finds is
reported as a failure. For the Chrome backend, whose windows *are* the
browser, they are reported and left alone: that backend cannot be hidden, and
the status must say so.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from enum import Enum
from typing import Any, Protocol, cast

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

# Win32 — Windows 10 2004+ (build 19041) for WDA_EXCLUDEFROMCAPTURE.
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011
GA_ROOT = 2
WM_CLOSE = 0x0010


class CaptureState(str, Enum):
    """What the OS actually granted — not what the user asked for.

    The distinction is the whole point. ``hide_for_sharing`` is a *request*;
    this is the answer, and the UI must render the answer.
    """

    EXCLUDED = "excluded"  # WDA_EXCLUDEFROMCAPTURE: invisible to capture
    BLACKED_OUT = "blacked-out"  # WDA_MONITOR fallback: a black rectangle
    OFF = "off"  # not hiding, by the user's choice
    FAILED = "failed"  # asked to hide; the OS refused
    UNSUPPORTED = "unsupported"  # this platform has no such control


# Worst first. `_weakest` reports the lowest rank present, so one uncovered
# window cannot be averaged away by several covered ones.
_RANK: dict[CaptureState, int] = {
    CaptureState.FAILED: 0,
    CaptureState.UNSUPPORTED: 1,
    CaptureState.BLACKED_OUT: 2,
    CaptureState.EXCLUDED: 3,
    CaptureState.OFF: 4,
}


def weakest(states: Iterable[CaptureState]) -> CaptureState:
    """The least-protected state in ``states`` (``OFF`` when there are none)."""
    return min(states, key=lambda state: _RANK[state], default=CaptureState.OFF)


def capture_control_available() -> bool:
    """Whether this platform has a per-window capture control at all.

    Windows does (``SetWindowDisplayAffinity``). Linux does not: X11 lets any
    client read the root window by design, and no Wayland protocol offers a
    client-side exclusion — Electron's equivalent is a documented no-op there
    too. macOS has one (``NSWindow.sharingType``) that Strata does not
    implement.

    Callers use this for two things: to phrase the truth for the user, and to
    stop doing work that cannot have an effect — the capture sweep is a timer
    that would otherwise wake the machine every 1.5 s on a platform where every
    call it makes returns immediately.
    """
    return _is_windows()


class _HasWinId(Protocol):
    def winId(self) -> object: ...


def _is_windows() -> bool:
    """A function, not `sys.platform == "win32"` inline.

    Type checkers narrow that literal to whichever platform they are running
    on, which marks every other branch unreachable and stops checking it. This
    code has to be correct on all three."""
    return sys.platform == "win32"


def _read_affinity(user32: Any, hwnd: int) -> int | None:
    """The affinity the OS currently reports for ``hwnd``, or None if it cannot say.

    "The call returned TRUE" is not the same claim as "this window is out of a
    recording", and the second one is what the UI repeats to the user. Where
    Windows will tell us, ask.
    """
    import ctypes
    from ctypes import wintypes

    try:
        current = wintypes.DWORD()
        if not user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(current)):
            return None
        return int(current.value)
    except Exception:  # pragma: no cover - a stand-in library in tests
        return None


def _set_affinity_if_needed(user32: Any, hwnd: int, affinity: int) -> bool:
    """Set ``hwnd``'s affinity only when it is not already that. Returns success.

    The conditional is the point, and it is a correctness fix rather than an
    optimisation. Every ``SetWindowDisplayAffinity`` call makes DWM rebuild the
    window's redirection surface — the window flickers as it does, and that
    rebuild is precisely the moment a frame can be composed outside the
    exclusion and land in a recording.

    The affinity used to be written unconditionally from three places: a 1.5 s
    sweep over every window, a re-assert on every activation change, and a
    window hook that fires on every show. A window that was *already* excluded
    was therefore torn down and rebuilt several times a second — which is the
    flicker, and each rebuild its own chance to leak a frame. Steady state is
    now a read and no write at all.

    A window whose affinity cannot be read is still written: skipping a window
    that might need it would be the worse error.
    """
    current = _read_affinity(user32, hwnd)
    if current == affinity:
        return True
    if not user32.SetWindowDisplayAffinity(hwnd, affinity):
        return False
    # Confirm rather than assume. "The call returned TRUE" is not the same
    # claim as "this window is out of a recording", and the second is what the
    # UI repeats to the user. An OS that will not answer leaves the setter's
    # own success standing.
    settled = _read_affinity(user32, hwnd)
    return settled is None or settled == affinity


def _user32() -> Any:
    """``user32`` with every prototype this module uses declared.

    Fetched (and re-declared) per call rather than cached at import: the
    declarations are a handful of attribute writes, and binding them here means
    no function in this module depends on another having run first to make its
    own call 64-bit-correct. See the module docstring for what that costs when
    it is not done.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        # HWND, not the default c_int: a truncated ancestor is a different
        # window, and setting an affinity on it protects nothing.
        user32.GetAncestor.restype = wintypes.HWND
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowDisplayAffinity.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
        user32.EnumWindows.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.SetWinEventHook.restype = wintypes.HANDLE
        user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        user32.UnhookWinEvent.restype = wintypes.BOOL
    except (AttributeError, TypeError):  # pragma: no cover - a stand-in library in tests
        pass
    return user32


def set_window_excluded_from_capture(window: _HasWinId, *, enabled: bool) -> CaptureState:
    """Ask the OS to hide ``window`` from screen capture when ``enabled``.

    Returns what the OS granted. ``UNSUPPORTED`` on a platform with no such
    control is deliberately **not** a success: this used to return True there,
    which meant a macOS or Linux user saw "Hidden for sharing ✓" over a window
    that was in every recording.
    """
    if _is_windows():
        return _windows_set_display_affinity(window, enabled=enabled)
    logger.debug(
        "screen_security.unsupported_platform",
        platform=sys.platform,
        enabled=enabled,
    )
    return CaptureState.UNSUPPORTED if enabled else CaptureState.OFF


def set_windows_excluded_from_capture(
    windows: Iterable[_HasWinId], *, enabled: bool
) -> CaptureState:
    """Apply capture exclusion to several windows; report the weakest result.

    The affinity is per top-level window, so a popup, menu, native ``<select>``
    dropdown, tooltip or dialog — each its own OS window — is *not* covered by
    the main window's exclusion and leaks into a recording unless excluded in its
    own right. Callers pass every current top-level window here.
    """
    return weakest(
        [set_window_excluded_from_capture(window, enabled=enabled) for window in windows]
    )


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
    return set_process_windows_excluded_from_capture(
        current_process_id(),
        enabled=enabled,
        include_hidden=True,
    )


def current_process_id() -> int:
    import ctypes

    return int(ctypes.windll.kernel32.GetCurrentProcessId())


def process_is_running(pid: int) -> bool:
    """Whether ``pid`` still names a live process.

    Used to drop a hook on a process that has exited. Windows reuses process
    ids, and a hook left on a dead one would eventually fire for whatever
    inherits the number — at which point Strata would be setting a
    capture affinity on *another application's* windows.
    """
    if not _is_windows() or pid <= 0:
        return False
    import ctypes

    kernel32 = ctypes.windll.kernel32
    # PROCESS_QUERY_LIMITED_INFORMATION: enough to ask whether it is alive,
    # and grantable for a process we did not create.
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def foreign_windows_uncovered(pid: int) -> int:
    """How many visible top-level windows of ``pid`` are in a recording right now.

    For a process that is not ours. Its windows cannot be excluded from here
    (the write is refused; see the module docstring), so the only honest thing
    to do with them is count the ones that are on screen without an affinity
    and let the reported state carry that number: one such window is a
    ``FAILED`` protection, however well covered our own windows are.
    """
    if not _is_windows() or pid <= 0:
        return 0
    user32 = _user32()
    return sum(
        1
        for hwnd in _visible_top_level_windows(user32, pid)
        if _read_affinity(user32, hwnd) not in (WDA_EXCLUDEFROMCAPTURE, WDA_MONITOR)
    )


def close_foreign_windows(pid: int) -> int:
    """Ask every visible top-level window of ``pid`` to close. Returns how many.

    The backstop under :class:`CaptureGuard` for an engine process whose
    top-level windows are all popups — WebView2's browser process draws the
    page inside *our* window, so anything it puts on screen in its own right is
    a dropdown, a tooltip, a dialog or a bubble, and each is a window Windows
    will not let us exclude. ``WM_CLOSE`` is posted, not sent: a synchronous
    send into another process's message loop is a hang waiting for a stuck
    renderer.
    """
    if not _is_windows() or pid <= 0:
        return 0
    user32 = _user32()
    closed = 0
    for hwnd in _visible_top_level_windows(user32, pid):
        if _close_window(user32, hwnd, pid=pid):
            closed += 1
    return closed


def _close_window(user32: Any, hwnd: int, *, pid: int) -> bool:
    if not user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
        return False
    # Warning, not debug: this is the "strange event" a user asks about after
    # a recording. The class name says what it was (``Chrome_WidgetWin_1`` for
    # a Chromium popup) without reading the window's contents.
    logger.warning("screen_security.foreign_window_closed", pid=pid, cls=_class_name(user32, hwnd))
    return True


def _class_name(user32: Any, hwnd: int) -> str:
    import ctypes

    try:
        buffer = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buffer, 64)
        return str(buffer.value)
    except Exception:  # pragma: no cover - a stand-in library in tests
        return ""


def _visible_top_level_windows(user32: Any, pid: int) -> list[int]:
    """Every visible top-level window ``pid`` owns (``EnumWindows`` is top-level only)."""
    import ctypes
    from ctypes import wintypes

    found: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _each(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid:
                found.append(int(hwnd))
        except Exception:  # pragma: no cover - a callback must never raise
            return True
        return True

    user32.EnumWindows(enum_proc(_each), 0)
    return found


def _windows_exclude_by_pid(pid: int, *, enabled: bool, include_hidden: bool) -> int:
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
    touched = 0

    # EnumWindows(callback, lparam): callback returns True to keep enumerating.
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _each(hwnd: int, _lparam: int) -> bool:
        nonlocal touched
        # Nothing in here may raise. An exception out of a ctypes callback
        # aborts the enumeration, so one odd window would leave every window
        # after it unprotected — and the sweep would still look like it ran.
        try:
            if not include_hidden and not user32.IsWindowVisible(hwnd):
                return True
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != pid:
                return True
            # Conditional: a window already at this affinity is left alone.
            # Rewriting it rebuilds its DWM surface — which is the flicker, and
            # the one moment a frame can escape — and this runs over every
            # window of the process every 1.5 s.
            if _set_affinity_if_needed(user32, hwnd, affinity):
                touched += 1
            # Try the blackout fallback, same as the single-window path. Only
            # count a window we actually covered — a silent miss is the one
            # thing this feature must not report as a success.
            elif enabled and _set_affinity_if_needed(user32, hwnd, WDA_MONITOR):
                touched += 1
        except Exception:  # pragma: no cover - defensive; see above
            logger.warning("screen_security.sweep_window_failed")
        return True

    user32.EnumWindows(enum_proc(_each), 0)
    return touched


def _windows_set_display_affinity(window: _HasWinId, *, enabled: bool) -> CaptureState:
    import ctypes

    user32 = _user32()

    # winId() is a sip.voidptr on Qt; the Protocol types it as `object`
    # because this module must not import Qt just to name it.
    raw = int(cast(Any, window.winId()))
    hwnd = int(user32.GetAncestor(raw, GA_ROOT) or 0) or raw

    if not enabled:
        if _set_affinity_if_needed(user32, hwnd, WDA_NONE):
            logger.debug("screen_security.windows_affinity", enabled=False, affinity=WDA_NONE)
            return CaptureState.OFF
        logger.warning(
            "screen_security.windows_affinity_failed",
            enabled=False,
            affinity=WDA_NONE,
            win_error=ctypes.GetLastError(),
        )
        return CaptureState.FAILED

    if _set_affinity_if_needed(user32, hwnd, WDA_EXCLUDEFROMCAPTURE):
        # Debug, not info: this is re-asserted across every window on every
        # activation change, and a heartbeat in the log buries the failures
        # that matter. The *transitions* are logged once, by the window.
        logger.debug(
            "screen_security.windows_affinity",
            enabled=True,
            affinity=WDA_EXCLUDEFROMCAPTURE,
        )
        return CaptureState.EXCLUDED

    # The exclusion did not take. What the window has *now* decides what is
    # claimed: an older build may have granted the blackout instead, and a
    # window left ordinary is one the user has to be told about.
    if _read_affinity(user32, hwnd) == WDA_MONITOR:
        logger.debug("screen_security.windows_affinity", enabled=True, affinity=WDA_MONITOR)
        return CaptureState.BLACKED_OUT

    logger.warning(
        "screen_security.windows_exclude_failed_trying_monitor",
        win_error=ctypes.GetLastError(),
    )
    if _set_affinity_if_needed(user32, hwnd, WDA_MONITOR):
        logger.debug(
            "screen_security.windows_affinity",
            enabled=True,
            affinity=WDA_MONITOR,
            fallback=True,
        )
        return CaptureState.BLACKED_OUT

    logger.warning(
        "screen_security.windows_affinity_failed",
        enabled=True,
        affinity=WDA_MONITOR,
        win_error=ctypes.GetLastError(),
    )
    return CaptureState.FAILED


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
        # Processes whose every top-level window is a popup we would rather
        # close than show: see `watch(..., popups_only=True)`.
        self._popups_only: set[int] = set()
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

    def watch(self, pid: int, *, popups_only: bool = False) -> bool:
        """Start catching new windows in ``pid``. Idempotent per process.

        ``popups_only`` says the process is an engine whose page is drawn in
        *our* window, so a top-level window of its own can only be a popup —
        and, being another process's, one we cannot exclude. Those are closed
        as they appear rather than left in the recording. Our own process is
        never that: its windows get the affinity.
        """
        if not _is_windows() or pid <= 0 or pid in self._hooks:
            return False
        if popups_only:
            self._popups_only.add(pid)
        user32 = _user32()
        if self._proc is None:
            self._proc = self._make_callback()
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

    def drop_dead_processes(self, *, keep: int) -> int:
        """Unhook every watched process that has exited. Returns how many.

        ``keep`` is this process, which is always alive and must never be
        probed away. The rest are engine processes that come and go — and a
        hook outliving one is not merely useless: process ids are reused, so it
        would eventually fire for an unrelated application and Strata would set
        a capture affinity on windows that are not its own.
        """
        dropped = 0
        for pid in [p for p in self._hooks if p != keep]:
            if not process_is_running(pid):
                self.forget(pid)
                dropped += 1
        if dropped:
            logger.info("screen_security.hooks_dropped", count=dropped)
        return dropped

    def forget(self, pid: int) -> None:
        """Drop the hook for a process that has gone."""
        hook = self._hooks.pop(pid, None)
        self._popups_only.discard(pid)
        if hook is None or not _is_windows():
            return
        _user32().UnhookWinEvent(hook)

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
            event: int,
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
                self._handle_window(int(event), int(hwnd))
            except Exception:  # pragma: no cover - a callback must never raise
                return

        return proto(_on_event)

    def _handle_window(self, event: int, hwnd: int) -> None:
        if self._popups_only and self._enabled and event == EVENT_OBJECT_SHOW:
            user32 = _user32()
            owner = _owner_pid(user32, hwnd)
            root = int(user32.GetAncestor(hwnd, GA_ROOT) or 0) or hwnd
            # Top-level and the engine's own: a popup. (The engine's *child*
            # window inside our pane has our window as its root, and that root
            # takes the affinity like any window of ours.)
            if owner in self._popups_only and root == hwnd:
                _close_window(user32, hwnd, pid=owner)
                return
        _apply_affinity_to_hwnd(hwnd, enabled=self._enabled)


def _owner_pid(user32: Any, hwnd: int) -> int:
    import ctypes
    from ctypes import wintypes

    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    return int(owner.value)


def _apply_affinity_to_hwnd(hwnd: int, *, enabled: bool) -> bool:
    """Set the affinity on ``hwnd``'s top-level window."""
    user32 = _user32()
    root = int(user32.GetAncestor(hwnd, GA_ROOT) or 0) or hwnd
    affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
    # This fires on every window create *and* show event in a watched process,
    # which for the main window is often. Conditional, so an already-excluded
    # window is not rebuilt — and made to flicker — on every show.
    if _set_affinity_if_needed(user32, root, affinity):
        return True
    if not enabled:
        return False
    return _set_affinity_if_needed(user32, root, WDA_MONITOR)
