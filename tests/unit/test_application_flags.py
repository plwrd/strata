"""Chromium launch flags.

The compositor flags are not a preference: `SetWindowDisplayAffinity` is enforced
by DWM, so anything Chromium hands to a hardware overlay plane is composed beside
DWM's output and escapes "Hidden for sharing" entirely. These pin the flags that
keep every frame on the path the affinity can reach.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets")

from app.desktop import application


def test_video_never_takes_a_hardware_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application, "_hide_for_sharing_requested", lambda: False)
    monkeypatch.setattr(application, "_touch_requested", lambda: False)

    flags = application._chromium_flags()

    # Unconditional: an overlay plane also causes the flicker, which is a bug
    # even for someone who is not hiding anything.
    assert "--disable-direct-composition-video-overlays" in flags
    assert "--disable-accelerated-video-decode" not in flags


def test_hiding_also_forces_software_video_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application, "_hide_for_sharing_requested", lambda: True)
    monkeypatch.setattr(application, "_touch_requested", lambda: False)

    assert "--disable-accelerated-video-decode" in application._chromium_flags()


def test_unreadable_settings_fail_towards_hiding(monkeypatch: pytest.MonkeyPatch) -> None:
    """A corrupt settings file must not silently expose the window."""

    def boom() -> object:
        raise OSError("settings unreadable")

    monkeypatch.setattr(application, "user_paths", boom, raising=False)
    monkeypatch.setattr("app.bootstrap.user_paths", boom)

    assert application._hide_for_sharing_requested() is True
