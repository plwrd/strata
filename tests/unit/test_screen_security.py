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
    set_window_excluded_from_capture,
)
from app.services.settings_service import AppSettings


def test_hide_for_sharing_defaults_on() -> None:
    assert AppSettings().hide_for_sharing is True


def test_unsupported_platform_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screen_security.sys, "platform", "linux")
    window = SimpleNamespace(winId=lambda: 1)
    assert set_window_excluded_from_capture(window, enabled=True) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_windows_sets_exclude_from_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    window = SimpleNamespace(winId=lambda: 0x1234)
    fake_user32 = MagicMock()
    fake_user32.GetAncestor.return_value = 0xABCD
    fake_user32.SetWindowDisplayAffinity.return_value = 1
    fake_windll = MagicMock(user32=fake_user32)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll)
    assert set_window_excluded_from_capture(window, enabled=True) is True

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
    fake_user32 = MagicMock()
    fake_user32.GetAncestor.return_value = 0x10
    # First call (exclude) fails; second (monitor) succeeds.
    fake_user32.SetWindowDisplayAffinity.side_effect = [0, 1]
    fake_windll = MagicMock(user32=fake_user32)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll)
    assert set_window_excluded_from_capture(window, enabled=True) is True

    assert fake_user32.SetWindowDisplayAffinity.call_count == 2
    assert fake_user32.SetWindowDisplayAffinity.call_args_list[0][0][1] == WDA_EXCLUDEFROMCAPTURE
    assert fake_user32.SetWindowDisplayAffinity.call_args_list[1][0][1] == WDA_MONITOR


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_windows_clears_affinity_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    window = SimpleNamespace(winId=lambda: 0x99)
    fake_user32 = MagicMock()
    fake_user32.GetAncestor.return_value = 0x99
    fake_user32.SetWindowDisplayAffinity.return_value = 1
    fake_windll = MagicMock(user32=fake_user32)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll)
    assert set_window_excluded_from_capture(window, enabled=False) is True
    assert fake_user32.SetWindowDisplayAffinity.call_args[0][1] == WDA_NONE


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_windows_uses_raw_hwnd_when_get_ancestor_returns_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = SimpleNamespace(winId=lambda: 0x55)
    fake_user32 = MagicMock()
    fake_user32.GetAncestor.return_value = 0
    fake_user32.SetWindowDisplayAffinity.return_value = 1
    fake_windll = MagicMock(user32=fake_user32)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll)
    assert set_window_excluded_from_capture(window, enabled=True) is True
    assert fake_user32.SetWindowDisplayAffinity.call_args[0][0] == 0x55


def test_exclusion_covers_every_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Popups, menus and dropdowns are separate top-level windows; each must be
    excluded in its own right, not just the main window."""
    from app.desktop.screen_security import set_windows_excluded_from_capture

    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        screen_security,
        "set_window_excluded_from_capture",
        lambda window, *, enabled: calls.append((window.winId(), enabled)) or True,
    )
    windows = [SimpleNamespace(winId=lambda i=i: i) for i in range(3)]

    set_windows_excluded_from_capture(windows, enabled=True)

    assert calls == [(0, True), (1, True), (2, True)]


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
    """Stand in for EnumWindows over ``{hwnd: (owning_pid, is_visible)}``."""
    import ctypes

    fake_user32 = MagicMock()
    fake_user32.IsWindowVisible.side_effect = lambda hwnd: windows[int(hwnd)][1]
    fake_user32.SetWindowDisplayAffinity.return_value = 1

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
    assert int(hwnd.value) == 0x10
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

    fake_user32 = MagicMock()
    fake_user32.GetAncestor.return_value = 0xF00
    fake_user32.SetWindowDisplayAffinity.return_value = 1
    monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))

    assert _apply_affinity_to_hwnd(0x123, enabled=True) is True
    hwnd, affinity = fake_user32.SetWindowDisplayAffinity.call_args[0]
    assert int(hwnd.value) == 0xF00
    assert affinity == WDA_EXCLUDEFROMCAPTURE
