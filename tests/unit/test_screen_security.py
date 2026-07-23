"""Screen-capture exclusion (Signal-style hide for sharing)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.desktop import screen_security
from app.desktop.screen_security import (
    WDA_EXCLUDEFROMCAPTURE,
    WDA_NONE,
    set_window_excluded_from_capture,
)
from app.services.settings_service import AppSettings


def test_hide_for_sharing_defaults_off() -> None:
    assert AppSettings().hide_for_sharing is False


def test_unsupported_platform_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screen_security.sys, "platform", "linux")
    window = SimpleNamespace(winId=lambda: 1)
    assert set_window_excluded_from_capture(window, enabled=True) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_windows_sets_exclude_from_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    window = SimpleNamespace(winId=lambda: 0x1234)
    fake_user32 = MagicMock()
    fake_user32.SetWindowDisplayAffinity.return_value = 1
    fake_windll = MagicMock(user32=fake_user32)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll)
    assert set_window_excluded_from_capture(window, enabled=True) is True

    fake_user32.SetWindowDisplayAffinity.assert_called_once()
    hwnd, affinity = fake_user32.SetWindowDisplayAffinity.call_args[0]
    assert hwnd == 0x1234
    assert affinity == WDA_EXCLUDEFROMCAPTURE


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 affinity only")
def test_windows_clears_affinity_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    window = SimpleNamespace(winId=lambda: 0x99)
    fake_user32 = MagicMock()
    fake_user32.SetWindowDisplayAffinity.return_value = 1
    fake_windll = MagicMock(user32=fake_user32)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll)
    assert set_window_excluded_from_capture(window, enabled=False) is True
    assert fake_user32.SetWindowDisplayAffinity.call_args[0][1] == WDA_NONE
