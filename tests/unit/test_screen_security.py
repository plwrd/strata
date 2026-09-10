"""Screen-capture exclusion (Signal-style hide for sharing)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
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
