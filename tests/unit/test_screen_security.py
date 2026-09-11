"""Screen-capture exclusion (Signal-style hide for sharing)."""

from __future__ import annotations

import ctypes
import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.desktop import screen_security
from app.desktop.screen_security import (
    GA_ROOT,
    WDA_EXCLUDEFROMCAPTURE,
    WDA_MONITOR,
    WDA_NONE,
    CaptureState,
    set_window_excluded_from_capture,
)
from app.services.settings_service import AppSettings


def test_hide_for_sharing_defaults_on() -> None:
    assert AppSettings().hide_for_sharing is True


def test_an_unsupported_platform_says_so_rather_than_claiming_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This used to return True — the value the UI reads to say "hidden".

    A macOS or Linux user was shown a window that was in every recording,
    over a tick that said it was not.
    """
    monkeypatch.setattr(screen_security.sys, "platform", "linux")
    window = SimpleNamespace(winId=lambda: 1)

    assert set_window_excluded_from_capture(window, enabled=True) is CaptureState.UNSUPPORTED
    # Asking to *stop* hiding, on a platform that never hid anything, is honest.
    assert set_window_excluded_from_capture(window, enabled=False) is CaptureState.OFF


def _affinity_user32(*, set_result: Any = 1, initial: int = WDA_NONE) -> MagicMock:
    """A user32 stand-in that behaves like Windows: what you set, it reports.

    The read-back matters enough to model. `SetWindowDisplayAffinity` returning
    TRUE is not the same claim as "this window is out of a recording", and the
    second is the one the UI repeats to the user — so the fake has to be able to
    disagree with the setter.
    """
    import ctypes

    state = {"affinity": initial}
    fake = MagicMock()

    def _set(_hwnd: object, affinity: int) -> int:
        ok = set_result(affinity) if callable(set_result) else set_result
        if ok:
            state["affinity"] = int(affinity)
        return ok

    def _get(_hwnd: object, out: Any) -> int:
        ctypes.cast(out, ctypes.POINTER(ctypes.c_ulong))[0] = state["affinity"]
        return 1

    fake.SetWindowDisplayAffinity.side_effect = _set
    fake.GetWindowDisplayAffinity.side_effect = _get
    return fake


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_windows_sets_exclude_from_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    window = SimpleNamespace(winId=lambda: 0x1234)
    fake_user32 = _affinity_user32()
    fake_user32.GetAncestor.return_value = 0xABCD
    fake_windll = MagicMock(user32=fake_user32)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll)
    assert set_window_excluded_from_capture(window, enabled=True) is CaptureState.EXCLUDED

    fake_user32.GetAncestor.assert_called_once()
    assert fake_user32.GetAncestor.call_args[0][0] == 0x1234
    assert fake_user32.GetAncestor.call_args[0][1] == GA_ROOT
    fake_user32.SetWindowDisplayAffinity.assert_called_once()
    hwnd, affinity = fake_user32.SetWindowDisplayAffinity.call_args[0]
    assert hwnd == 0xABCD
    assert affinity == WDA_EXCLUDEFROMCAPTURE


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_windows_falls_back_to_monitor_when_exclude_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = SimpleNamespace(winId=lambda: 0x10)
    # The exclude affinity is refused (an older build); the blackout is not.
    fake_user32 = _affinity_user32(set_result=lambda a: 0 if a == WDA_EXCLUDEFROMCAPTURE else 1)
    fake_user32.GetAncestor.return_value = 0x10
    fake_windll = MagicMock(user32=fake_user32)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll)
    # The blackout fallback is protection, but it is not the same protection,
    # and the status says which one the user actually has.
    assert set_window_excluded_from_capture(window, enabled=True) is CaptureState.BLACKED_OUT

    assert fake_user32.SetWindowDisplayAffinity.call_count == 2
    assert fake_user32.SetWindowDisplayAffinity.call_args_list[0][0][1] == WDA_EXCLUDEFROMCAPTURE
    assert fake_user32.SetWindowDisplayAffinity.call_args_list[1][0][1] == WDA_MONITOR


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_windows_clears_affinity_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    window = SimpleNamespace(winId=lambda: 0x99)
    # Start from a hidden window: clearing an affinity that is already clear is
    # correctly a no-op, so the interesting case is the one with something to
    # undo.
    fake_user32 = _affinity_user32(initial=WDA_EXCLUDEFROMCAPTURE)
    fake_user32.GetAncestor.return_value = 0x99
    fake_windll = MagicMock(user32=fake_user32)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll)
    assert set_window_excluded_from_capture(window, enabled=False) is CaptureState.OFF
    assert fake_user32.SetWindowDisplayAffinity.call_args[0][1] == WDA_NONE


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_windows_uses_raw_hwnd_when_get_ancestor_returns_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = SimpleNamespace(winId=lambda: 0x55)
    fake_user32 = _affinity_user32()
    fake_user32.GetAncestor.return_value = 0
    fake_windll = MagicMock(user32=fake_user32)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll)
    assert set_window_excluded_from_capture(window, enabled=True) is CaptureState.EXCLUDED
    assert fake_user32.SetWindowDisplayAffinity.call_args[0][0] == 0x55


def test_exclusion_covers_every_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Popups, menus and dropdowns are separate top-level windows; each must be
    excluded in its own right, not just the main window."""
    from app.desktop.screen_security import set_windows_excluded_from_capture

    calls: list[tuple[int, bool]] = []

    def _record(window: Any, *, enabled: bool) -> CaptureState:
        calls.append((window.winId(), enabled))
        return CaptureState.EXCLUDED

    monkeypatch.setattr(screen_security, "set_window_excluded_from_capture", _record)
    windows = [SimpleNamespace(winId=lambda i=i: i) for i in range(3)]

    assert set_windows_excluded_from_capture(windows, enabled=True) is CaptureState.EXCLUDED

    assert calls == [(0, True), (1, True), (2, True)]


def test_one_uncovered_window_decides_the_reported_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The aggregate is the weakest window, not the most common one.

    Three excluded surfaces and one that refused is not "excluded" — the one
    that refused is the one in the recording.
    """
    from app.desktop.screen_security import set_windows_excluded_from_capture

    results = iter([CaptureState.EXCLUDED, CaptureState.FAILED, CaptureState.EXCLUDED])
    monkeypatch.setattr(
        screen_security,
        "set_window_excluded_from_capture",
        lambda _window, *, enabled: next(results),
    )
    windows = [SimpleNamespace(winId=lambda i=i: i) for i in range(3)]

    assert set_windows_excluded_from_capture(windows, enabled=True) is CaptureState.FAILED


def test_process_exclusion_is_noop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.desktop.screen_security import set_process_windows_excluded_from_capture

    monkeypatch.setattr(screen_security.sys, "platform", "linux")
    assert set_process_windows_excluded_from_capture(1234, enabled=True) == 0


def test_process_exclusion_ignores_a_bad_pid() -> None:
    from app.desktop.screen_security import set_process_windows_excluded_from_capture

    assert set_process_windows_excluded_from_capture(0, enabled=True) == 0


def test_own_window_exclusion_is_noop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.desktop.screen_security import set_own_windows_excluded_from_capture

    monkeypatch.setattr(screen_security.sys, "platform", "linux")
    assert set_own_windows_excluded_from_capture(enabled=True) == 0


def _fake_enum(monkeypatch: pytest.MonkeyPatch, windows: dict[int, tuple[int, bool]]) -> MagicMock:
    """Stand in for EnumWindows over ``{hwnd: (owning_pid, is_visible)}``.

    Per-window affinity is modelled, not stubbed: what the sweep writes is what
    the next read reports. Without that the fake cannot tell a first pass from a
    second, which is exactly the distinction the flicker fix turns on.
    """
    import ctypes

    fake_user32 = MagicMock()
    fake_user32.IsWindowVisible.side_effect = lambda hwnd: windows[int(hwnd)][1]
    affinities: dict[int, int] = dict.fromkeys(windows, WDA_NONE)

    def _set(hwnd: object, affinity: int) -> int:
        affinities[int(cast(int, hwnd))] = int(affinity)
        return 1

    def _get_affinity(hwnd: object, out: Any) -> int:
        ctypes.cast(out, ctypes.POINTER(ctypes.c_ulong))[0] = affinities[int(cast(int, hwnd))]
        return 1

    fake_user32.SetWindowDisplayAffinity.side_effect = _set
    fake_user32.GetWindowDisplayAffinity.side_effect = _get_affinity

    def _get_pid(hwnd: object, out: Any) -> int:
        out._obj.value = windows[int(cast(int, hwnd))][0]
        return 1

    fake_user32.GetWindowThreadProcessId.side_effect = _get_pid

    def _enum(callback: object, _lparam: int) -> int:
        for hwnd in windows:
            callback(hwnd, 0)  # type: ignore[operator]
        return 1

    fake_user32.EnumWindows.side_effect = _enum
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))
    return fake_user32


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_process_sweep_skips_other_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.desktop.screen_security import set_process_windows_excluded_from_capture

    user32 = _fake_enum(monkeypatch, {0x10: (42, True), 0x20: (99, True)})

    assert set_process_windows_excluded_from_capture(42, enabled=True) == 1
    hwnd, affinity = user32.SetWindowDisplayAffinity.call_args[0]
    assert hwnd == 0x10
    assert affinity == WDA_EXCLUDEFROMCAPTURE


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_process_sweep_covers_a_window_that_is_not_shown_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A popup is created before it is shown.

    A visible-only sweep can only reach a menu or a ``<select>`` dropdown after it
    has already painted an unprotected frame, which is exactly the leak.
    """
    from app.desktop.screen_security import set_process_windows_excluded_from_capture

    windows = {0x10: (42, True), 0x11: (42, False)}

    _fake_enum(monkeypatch, windows)
    assert set_process_windows_excluded_from_capture(42, enabled=True) == 1

    _fake_enum(monkeypatch, windows)
    assert set_process_windows_excluded_from_capture(42, enabled=True, include_hidden=True) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_process_sweep_does_not_count_a_window_it_could_not_cover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither affinity took. Reporting that as covered would be the one lie
    this feature cannot afford."""
    from app.desktop.screen_security import set_process_windows_excluded_from_capture

    user32 = _fake_enum(monkeypatch, {0x10: (42, True)})
    user32.SetWindowDisplayAffinity.return_value = 0
    user32.SetWindowDisplayAffinity.side_effect = None

    assert set_process_windows_excluded_from_capture(42, enabled=True) == 0
    # Tried the exclude, then the blackout fallback, then gave up.
    assert user32.SetWindowDisplayAffinity.call_count == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_own_window_sweep_uses_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes

    from app.desktop.screen_security import set_own_windows_excluded_from_capture

    seen: list[tuple[int, bool, bool]] = []

    def _record(pid: int, *, enabled: bool, include_hidden: bool = False) -> int:
        seen.append((pid, enabled, include_hidden))
        return 0

    monkeypatch.setattr(screen_security, "set_process_windows_excluded_from_capture", _record)
    monkeypatch.setattr(
        ctypes, "windll", MagicMock(kernel32=MagicMock(GetCurrentProcessId=lambda: 4321))
    )

    set_own_windows_excluded_from_capture(enabled=True)

    assert seen == [(4321, True, True)]


# -- catching a window as it appears -------------------------------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")


def test_guard_does_nothing_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.desktop.screen_security import CaptureGuard

    monkeypatch.setattr(screen_security.sys, "platform", "linux")
    guard = CaptureGuard()

    assert guard.watch(1234) is False
    assert guard.watched_pids == ()


def test_guard_refuses_a_bad_pid() -> None:
    from app.desktop.screen_security import CaptureGuard

    assert CaptureGuard().watch(0) is False


@windows_only
def test_guard_hooks_a_process_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Watching twice must not stack hooks; the sweep calls it every tick."""
    from app.desktop.screen_security import CaptureGuard

    fake_user32 = MagicMock()
    fake_user32.SetWinEventHook.return_value = 0xABC
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))
    guard = CaptureGuard()

    assert guard.watch(42) is True
    assert guard.watch(42) is False

    assert guard.watched_pids == (42,)
    assert fake_user32.SetWinEventHook.call_count == 1


@windows_only
def test_guard_watches_the_engine_process_as_well(monkeypatch: pytest.MonkeyPatch) -> None:
    """WebView2's popups are its browser process's windows, not ours."""
    from app.desktop.screen_security import CaptureGuard

    fake_user32 = MagicMock()
    fake_user32.SetWinEventHook.return_value = 0xABC
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))
    guard = CaptureGuard()

    guard.watch(42)
    guard.watch(99)

    assert guard.watched_pids == (42, 99)


@windows_only
def test_guard_reports_a_hook_it_could_not_install(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.desktop.screen_security import CaptureGuard

    fake_user32 = MagicMock()
    fake_user32.SetWinEventHook.return_value = 0
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))

    assert CaptureGuard().watch(42) is False


@windows_only
def test_guard_unhooks_on_dispose(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hook outliving the window it protects is a callback into a dead object."""
    from app.desktop.screen_security import CaptureGuard

    fake_user32 = MagicMock()
    fake_user32.SetWinEventHook.return_value = 0xABC
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))
    guard = CaptureGuard()
    guard.watch(42)
    guard.watch(99)

    guard.dispose()

    assert guard.watched_pids == ()
    assert fake_user32.UnhookWinEvent.call_count == 2


@windows_only
def test_a_hooked_window_is_excluded_at_its_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child HWND's affinity has to be set on the top-level that owns it."""
    from app.desktop.screen_security import _apply_affinity_to_hwnd

    fake_user32 = _affinity_user32()
    fake_user32.GetAncestor.return_value = 0xF00
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))

    assert _apply_affinity_to_hwnd(0x123, enabled=True) is True
    hwnd, affinity = fake_user32.SetWindowDisplayAffinity.call_args[0]
    assert hwnd == 0xF00
    assert affinity == WDA_EXCLUDEFROMCAPTURE


# -- the Win32 prototypes ------------------------------------------------------
#
# `ctypes` assumes a C `int` for anything it has not been told about. On a
# 64-bit build that silently truncates an HWND, and the affinity then lands on
# a window that does not exist — the feature reports success and protects
# nothing. These pin the declarations rather than the behaviour they prevent,
# because the behaviour they prevent is unobservable until it is too late.


@windows_only
def test_every_prototype_this_module_uses_is_declared() -> None:
    from ctypes import wintypes

    from app.desktop.screen_security import _user32

    user32 = _user32()

    # The return type that matters most: a truncated ancestor is a different
    # window, and GetAncestor defaults to a 32-bit signed int.
    assert user32.GetAncestor.restype is wintypes.HWND
    assert user32.GetAncestor.argtypes[0] is wintypes.HWND
    assert user32.SetWindowDisplayAffinity.argtypes[0] is wintypes.HWND
    assert user32.IsWindowVisible.argtypes[0] is wintypes.HWND
    assert user32.GetWindowThreadProcessId.argtypes[0] is wintypes.HWND
    assert user32.SetWinEventHook.restype is wintypes.HANDLE


@windows_only
def test_a_64_bit_window_handle_does_not_raise() -> None:
    """An HWND above 2^32 is an ``ArgumentError`` against an undeclared prototype.

    These are the calls the per-process sweep makes for every window on the
    desktop, and it makes them from inside an ``EnumWindows`` callback — where
    an exception ends the enumeration and silently skips every window after it.
    """
    from ctypes import byref, wintypes

    from app.desktop.screen_security import _apply_affinity_to_hwnd, _user32

    user32 = _user32()
    big = 0x0000_0007_DEAD_BEEF  # not a real window; the point is the argument

    user32.IsWindowVisible(big)
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(big, byref(owner))
    # And the whole path: a bogus handle must fail as a *result*, not an
    # exception.
    assert _apply_affinity_to_hwnd(big, enabled=True) is False


@windows_only
def test_the_sweep_finishes_even_when_one_window_misbehaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising callback aborts EnumWindows, silently skipping the rest."""
    from app.desktop.screen_security import set_process_windows_excluded_from_capture

    user32 = _fake_enum(monkeypatch, {0x10: (42, True), 0x20: (42, True), 0x30: (42, True)})

    def _sometimes_explodes(hwnd: object) -> bool:
        if int(cast(int, hwnd)) == 0x20:
            raise OSError("this window is having a bad day")
        return True

    user32.IsWindowVisible.side_effect = _sometimes_explodes

    # The two healthy windows are still covered — the bad one does not take
    # the windows enumerated after it down with it.
    assert set_process_windows_excluded_from_capture(42, enabled=True) == 2


# -- hooks on processes that have gone -----------------------------------------


@windows_only
def test_a_hook_is_dropped_when_its_process_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows reuses process ids.

    A hook left on a dead engine would eventually fire for whatever inherits
    the number, and Strata would set a capture affinity on another
    application's windows.
    """
    from app.desktop.screen_security import CaptureGuard

    fake_user32 = MagicMock()
    fake_user32.SetWinEventHook.return_value = 0xABC
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))
    guard = CaptureGuard()
    guard.watch(42)  # us
    guard.watch(99)  # an engine process that is about to exit

    monkeypatch.setattr(screen_security, "process_is_running", lambda pid: pid != 99)
    assert guard.drop_dead_processes(keep=42) == 1

    assert guard.watched_pids == (42,)
    fake_user32.UnhookWinEvent.assert_called_once()


@windows_only
def test_our_own_process_is_never_probed_away(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.desktop.screen_security import CaptureGuard

    fake_user32 = MagicMock()
    fake_user32.SetWinEventHook.return_value = 0xABC
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))
    guard = CaptureGuard()
    guard.watch(42)

    monkeypatch.setattr(screen_security, "process_is_running", lambda _pid: False)

    assert guard.drop_dead_processes(keep=42) == 0
    assert guard.watched_pids == (42,)


# -- confirming, rather than assuming ------------------------------------------


@windows_only
def test_a_window_the_os_does_not_confirm_is_not_reported_as_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`SetWindowDisplayAffinity` returned TRUE; the OS reports no exclusion.

    That is the one case the user must hear about, because everything upstream
    of it looks like a success.
    """
    window = SimpleNamespace(winId=lambda: 0x20)
    fake_user32 = _affinity_user32()
    fake_user32.GetAncestor.return_value = 0x20
    # It "worked", but the window is still ordinary.
    fake_user32.GetWindowDisplayAffinity.side_effect = None
    fake_user32.GetWindowDisplayAffinity.return_value = 1  # TRUE, and leaves WDA_NONE
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))

    assert set_window_excluded_from_capture(window, enabled=True) is CaptureState.FAILED


@windows_only
def test_a_window_the_os_reports_as_blacked_out_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked to exclude, granted the older blackout. The user gets the smaller
    claim, not the one we asked for."""
    window = SimpleNamespace(winId=lambda: 0x21)
    fake_user32 = _affinity_user32()
    fake_user32.GetAncestor.return_value = 0x21

    def _report_monitor(_hwnd: object, out: Any) -> int:
        ctypes.cast(out, ctypes.POINTER(ctypes.c_ulong))[0] = WDA_MONITOR
        return 1

    fake_user32.GetWindowDisplayAffinity.side_effect = _report_monitor
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))

    assert set_window_excluded_from_capture(window, enabled=True) is CaptureState.BLACKED_OUT


@windows_only
def test_an_os_that_will_not_answer_is_taken_at_its_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read-back that fails is not evidence of a *failure* — the setter
    succeeded, and inventing a refusal would be its own kind of dishonesty."""
    window = SimpleNamespace(winId=lambda: 0x22)
    fake_user32 = _affinity_user32()
    fake_user32.GetAncestor.return_value = 0x22
    fake_user32.GetWindowDisplayAffinity.side_effect = None
    fake_user32.GetWindowDisplayAffinity.return_value = 0  # the query itself failed
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))

    assert set_window_excluded_from_capture(window, enabled=True) is CaptureState.EXCLUDED


# -- platforms with no such control --------------------------------------------


def test_capture_control_is_windows_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.desktop.screen_security import capture_control_available

    monkeypatch.setattr(screen_security.sys, "platform", "linux")
    assert capture_control_available() is False

    monkeypatch.setattr(screen_security.sys, "platform", "darwin")
    assert capture_control_available() is False

    monkeypatch.setattr(screen_security.sys, "platform", "win32")
    assert capture_control_available() is True


# -- the flicker, and the leak inside it ----------------------------------------
#
# Reported symptom: on Windows the whole window flickers now and then, and a
# capture taken at that moment gets the real contents.
#
# Cause: every `SetWindowDisplayAffinity` call makes DWM rebuild the window's
# redirection surface. The affinity was re-asserted unconditionally from three
# places — a 1.5 s sweep over every window, a re-assert on every activation
# change, and a window hook that fires on every show — so an already-excluded
# window was torn down and rebuilt several times a second. The flicker *is* the
# rebuild, and the rebuild is when a frame can be composed outside the
# exclusion.
#
# The fix is that a correct affinity is never written again. These hold it.


@windows_only
def test_a_window_that_is_already_excluded_is_not_written_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = SimpleNamespace(winId=lambda: 0x30)
    fake_user32 = _affinity_user32()
    fake_user32.GetAncestor.return_value = 0x30
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))

    assert set_window_excluded_from_capture(window, enabled=True) is CaptureState.EXCLUDED
    writes_after_first = fake_user32.SetWindowDisplayAffinity.call_count
    assert writes_after_first == 1

    # Re-asserting — an activation change, a show event, a sweep tick — must
    # not touch the window at all.
    for _ in range(5):
        assert set_window_excluded_from_capture(window, enabled=True) is CaptureState.EXCLUDED

    assert fake_user32.SetWindowDisplayAffinity.call_count == writes_after_first


@windows_only
def test_the_sweep_writes_once_and_then_stops_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 1.5 s heartbeat, over the life of the process. It used to rewrite
    every window on every tick."""
    from app.desktop.screen_security import set_process_windows_excluded_from_capture

    user32 = _fake_enum(monkeypatch, {0x10: (42, True), 0x11: (42, True)})

    assert set_process_windows_excluded_from_capture(42, enabled=True) == 2
    assert user32.SetWindowDisplayAffinity.call_count == 2

    for _ in range(10):
        # Still reported as covered — because they are.
        assert set_process_windows_excluded_from_capture(42, enabled=True) == 2

    assert user32.SetWindowDisplayAffinity.call_count == 2


@windows_only
def test_the_window_hook_does_not_rewrite_on_every_show(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`EVENT_OBJECT_SHOW` fires for the main window every time it is shown."""
    from app.desktop.screen_security import _apply_affinity_to_hwnd

    fake_user32 = _affinity_user32()
    fake_user32.GetAncestor.return_value = 0xF00
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))

    assert _apply_affinity_to_hwnd(0x123, enabled=True) is True
    assert _apply_affinity_to_hwnd(0x123, enabled=True) is True
    assert _apply_affinity_to_hwnd(0x123, enabled=True) is True

    assert fake_user32.SetWindowDisplayAffinity.call_count == 1


@windows_only
def test_a_new_window_is_still_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not writing when it is unnecessary must not become not writing at all.

    A recreated HWND starts at WDA_NONE, so the read that suppresses a
    redundant write is the same read that catches a window needing one.
    """
    from app.desktop.screen_security import set_process_windows_excluded_from_capture

    user32 = _fake_enum(monkeypatch, {0x10: (42, True)})
    assert set_process_windows_excluded_from_capture(42, enabled=True) == 1
    assert user32.SetWindowDisplayAffinity.call_count == 1

    # A second window appears (Qt recreated the surface, a popup opened).
    user32 = _fake_enum(monkeypatch, {0x10: (42, True), 0x20: (42, True)})
    assert set_process_windows_excluded_from_capture(42, enabled=True) == 2
    assert user32.SetWindowDisplayAffinity.call_count == 2
