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


# -- one list, three engines ---------------------------------------------------
#
# The launched Chrome used to get none of these, which is backwards: it is the
# backend a user picks *because* it plays video, and a video on a hardware
# overlay is the one thing a window's display affinity cannot cover.


def test_every_chromium_engine_gets_the_same_compositor_flags(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    from app.desktop.capture_flags import CAPTURE_SAFE_ARGUMENTS, SOFTWARE_DECODE_ARGUMENT
    from app.desktop.webview2 import sdk
    from app.domain.errors import ProviderError
    from app.services.browser_service import ChromeSource
    from app.services.settings_service import SettingsService

    # The app's own Qt WebEngine.
    monkeypatch.setattr(application, "_hide_for_sharing_requested", lambda: True)
    monkeypatch.setattr(application, "_touch_requested", lambda: False)
    qt_flags = application._chromium_flags()

    # The WebView2 pane.
    assert set(CAPTURE_SAFE_ARGUMENTS) <= set(sdk.CAPTURE_SAFE_ARGUMENTS)
    assert sdk.SOFTWARE_DECODE_ARGUMENT == SOFTWARE_DECODE_ARGUMENT

    # The launched Chrome.
    root = Path(str(tmp_path))
    settings = SettingsService(root / "settings.json")
    settings.update({"browser_control_enabled": True, "hide_for_sharing": True})
    chrome = ChromeSource(settings, root)
    launched: list[list[str]] = []

    def _popen(arguments: list[str], **_kwargs: object) -> object:
        launched.append(arguments)
        raise OSError("not really launching a browser in a test")

    monkeypatch.setattr(chrome, "_resolve_executable", lambda: "chrome.exe")
    monkeypatch.setattr("app.services.browser_service.subprocess.Popen", _popen)
    with pytest.raises(ProviderError):
        chrome.ensure_ready()

    for flag in (*CAPTURE_SAFE_ARGUMENTS, SOFTWARE_DECODE_ARGUMENT):
        assert flag in qt_flags, flag
        assert flag in launched[0], flag


def test_chrome_pays_for_software_decode_only_while_hiding(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It costs real CPU on playback; it is not charged to someone who did not
    ask to be hidden."""
    from pathlib import Path

    from app.desktop.capture_flags import SOFTWARE_DECODE_ARGUMENT
    from app.domain.errors import ProviderError
    from app.services.browser_service import ChromeSource
    from app.services.settings_service import SettingsService

    root = Path(str(tmp_path))
    settings = SettingsService(root / "settings.json")
    settings.update({"browser_control_enabled": True, "hide_for_sharing": False})
    chrome = ChromeSource(settings, root)
    launched: list[list[str]] = []

    def _popen(arguments: list[str], **_kwargs: object) -> object:
        launched.append(arguments)
        raise OSError("not really launching a browser in a test")

    monkeypatch.setattr(chrome, "_resolve_executable", lambda: "chrome.exe")
    monkeypatch.setattr("app.services.browser_service.subprocess.Popen", _popen)
    with pytest.raises(ProviderError):
        chrome.ensure_ready()

    assert SOFTWARE_DECODE_ARGUMENT not in launched[0]
    assert "--disable-direct-composition-video-overlays" in launched[0]
