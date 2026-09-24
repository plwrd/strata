"""Chromium launch flags."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets")

from app.desktop import application


def test_video_never_takes_a_hardware_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application, "_touch_requested", lambda: False)

    flags = application._chromium_flags()

    assert "--disable-direct-composition-video-overlays" in flags
    assert "--disable-accelerated-video-decode" not in flags
    assert "--disable-direct-composition " not in flags + " "


def test_touch_events_follow_mobile_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application, "_touch_requested", lambda: True)
    assert "--touch-events=enabled" in application._chromium_flags()

    monkeypatch.setattr(application, "_touch_requested", lambda: False)
    assert "--touch-events=enabled" not in application._chromium_flags()
