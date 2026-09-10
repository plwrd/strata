"""Dropping the taskbar button (Windows ex-style).

The behaviour that matters without a display: non-Windows is a clean no-op, the
call is idempotent (it only cycles the window when the style actually changes,
which is what stops a showEvent re-apply from looping), and it sets the right
bits — WS_EX_TOOLWINDOW on and WS_EX_APPWINDOW off when hiding, the reverse when
showing.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.desktop import taskbar
from app.desktop.taskbar import (
    WS_EX_APPWINDOW,
    WS_EX_TOOLWINDOW,
    set_window_in_taskbar,
)
from app.services.settings_service import AppSettings


def test_defaults_to_shown() -> None:
    assert AppSettings().hide_from_taskbar is False


def test_unsupported_platform_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(taskbar.sys, "platform", "linux")
    window = SimpleNamespace(winId=lambda: 1, isVisible=lambda: True)
    assert set_window_in_taskbar(window, shown=False) is True


def _fake_user32(ex_style: int) -> MagicMock:
    state = {"ex": ex_style}
    user32 = MagicMock()
    user32.GetWindowLongPtrW.side_effect = lambda hwnd, idx: state["ex"]
    user32.GetWindowLongW.side_effect = lambda hwnd, idx: state["ex"]

    def _set(hwnd: int, idx: int, value: object) -> int:
        # The real setter takes a LONG_PTR; the code passes ctypes.c_ssize_t, so
        # unwrap to the plain int the assertions compare against.
        state["ex"] = int(getattr(value, "value", value))
        return 0

    user32.SetWindowLongPtrW.side_effect = _set
    user32.SetWindowLongW.side_effect = _set
    user32._state = state
    return user32


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 ex-style only")
def test_windows_hiding_sets_toolwindow_and_clears_appwindow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user32 = _fake_user32(WS_EX_APPWINDOW)
    monkeypatch.setattr("ctypes.windll", MagicMock(user32=user32))
    window = SimpleNamespace(winId=lambda: 0x10, isVisible=lambda: True)

    assert set_window_in_taskbar(window, shown=False) is True

    ex = user32._state["ex"]
    assert ex & WS_EX_TOOLWINDOW
    assert not (ex & WS_EX_APPWINDOW)
    # Visible window had to be cycled for the shell to notice.
    assert user32.ShowWindow.call_count == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 ex-style only")
def test_windows_is_idempotent_when_already_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user32 = _fake_user32(WS_EX_TOOLWINDOW)
    monkeypatch.setattr("ctypes.windll", MagicMock(user32=user32))
    window = SimpleNamespace(winId=lambda: 0x10, isVisible=lambda: True)

    assert set_window_in_taskbar(window, shown=False) is True

    # Already a tool window: nothing to change, so the window is never cycled —
    # this is what keeps a showEvent re-apply from looping through hide/show.
    assert user32.ShowWindow.call_count == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 ex-style only")
def test_windows_showing_restores_appwindow(monkeypatch: pytest.MonkeyPatch) -> None:
    user32 = _fake_user32(WS_EX_TOOLWINDOW)
    monkeypatch.setattr("ctypes.windll", MagicMock(user32=user32))
    window = SimpleNamespace(winId=lambda: 0x10, isVisible=lambda: False)

    assert set_window_in_taskbar(window, shown=True) is True

    ex = user32._state["ex"]
    assert ex & WS_EX_APPWINDOW
    assert not (ex & WS_EX_TOOLWINDOW)
    # Not visible: no cycle needed, the style is adopted on the next show.
    assert user32.ShowWindow.call_count == 0
