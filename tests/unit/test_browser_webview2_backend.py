"""The WebView2 backend as the service sees it.

The engine itself is exercised live in ``tests/unit/test_webview2_binding.py``
and against a real runtime by hand; what matters here is the wiring around it,
where the failure modes are quiet rather than loud:

* the service must report the pane that was *built*, not the one that was asked
  for — a WebView2 pane that could not start falls back, and telling the user
  otherwise tells them video will play when it will not;
* capture exclusion must reach the engine's own process, because that is where
  its menus and dropdowns are.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.domain.browser import IN_WINDOW_BACKENDS, BrowserStatus, BrowserTab, ScrapedPage
from app.services.browser_service import BrowserService
from app.services.settings_service import AppSettings, SettingsService


class FakePane:
    """An in-window pane on whichever engine the test says."""

    def __init__(self, backend: str = "webview2", *, browser_process_id: int = 0) -> None:
        self.backend = backend
        self.browser_process_id = browser_process_id
        self.url = "https://example.com/paper"
        self.shown = False
        self.blur: tuple[bool, int] | None = None
        self.mobile: bool | None = None

    def status(self) -> BrowserStatus:
        return BrowserStatus(backend=self.backend, running=self.shown)

    def ensure_ready(self) -> BrowserStatus:
        self.shown = True
        return self.status()

    def open_url(self, url: str) -> BrowserTab:
        self.url = url
        return BrowserTab(target_id="pane", url=url, active=True)

    def tabs(self) -> list[BrowserTab]:
        return [BrowserTab(target_id="pane", url=self.url, active=True)]

    def read(self, target_id: str) -> ScrapedPage:
        return ScrapedPage(url=self.url, title="A research page", text="Body.", char_count=5)

    def apply_blur(self, enabled: bool, amount: int) -> None:
        self.blur = (enabled, amount)

    def apply_mobile(self, enabled: bool) -> None:
        self.mobile = enabled

    def close(self) -> None:
        self.shown = False


def _service(tmp_path: Path, *, backend: str = "webview2") -> BrowserService:
    settings = SettingsService(tmp_path / "settings.json")
    settings.update({"browser_control_enabled": True, "browser_backend": backend})
    return BrowserService(settings, tmp_path)


# -- the setting ---------------------------------------------------------------


def test_webview2_is_an_accepted_backend() -> None:
    assert AppSettings(browser_backend="webview2").browser_backend == "webview2"


def test_an_unknown_backend_is_still_refused() -> None:
    with pytest.raises(ValueError, match="browser_backend"):
        AppSettings(browser_backend="firefox")


def test_both_panes_count_as_in_window() -> None:
    assert IN_WINDOW_BACKENDS == {"embedded", "webview2"}
    assert "chrome" not in IN_WINDOW_BACKENDS


# -- which engine is actually running ------------------------------------------


def test_service_reports_the_engine_that_was_built(tmp_path: Path) -> None:
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("webview2"))

    assert service.backend == "webview2"


def test_a_webview2_request_that_fell_back_reports_the_fallback(tmp_path: Path) -> None:
    """The window asked for WebView2, could not have it, and attached Qt instead.

    Reporting the *setting* here would promise video that will not play.
    """
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("embedded"))

    assert service.backend == "embedded"


def test_chrome_wins_over_whatever_pane_is_attached(tmp_path: Path) -> None:
    """Chrome is not a pane; an attached pane must not shadow the setting."""
    service = _service(tmp_path, backend="chrome")
    service.attach(FakePane("webview2"))

    assert service.backend == "chrome"


def test_no_pane_at_all_still_answers(tmp_path: Path) -> None:
    service = _service(tmp_path, backend="webview2")

    assert service.backend == "embedded"


# -- features that follow the engine -------------------------------------------


def test_blur_works_in_the_webview2_pane(tmp_path: Path) -> None:
    service = _service(tmp_path, backend="webview2")
    pane = FakePane("webview2")
    service.attach(pane)

    assert service.blur_supported is True
    service.set_blur(True)

    assert pane.blur == (True, service.blur_state()[1])


def test_blur_is_not_offered_for_a_browser_we_do_not_own(tmp_path: Path) -> None:
    service = _service(tmp_path, backend="chrome")
    service.attach(FakePane("webview2"))

    assert service.blur_supported is False


# -- capture exclusion ---------------------------------------------------------


def test_exclusion_reaches_the_engine_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WebView2's popups live in its own process, not in ours.

    The window's display affinity covers the pane's pixels but not a menu the
    browser process opened, so that PID has to be swept in its own right.
    """
    swept: list[tuple[int, bool, bool]] = []
    monkeypatch.setattr(
        "app.services.browser_service.set_process_windows_excluded_from_capture",
        lambda pid, *, enabled, include_hidden=False: swept.append((pid, enabled, include_hidden)),
    )
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("webview2", browser_process_id=4321))

    service.apply_capture_exclusion(True)

    assert swept == [(4321, True, True)]


def test_exclusion_covers_hidden_windows_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A popup exists before it is shown; a visible-only sweep is always late."""
    swept: list[Any] = []
    monkeypatch.setattr(
        "app.services.browser_service.set_process_windows_excluded_from_capture",
        lambda pid, *, enabled, include_hidden=False: swept.append(include_hidden),
    )
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("webview2", browser_process_id=99))

    service.apply_capture_exclusion(True)

    assert swept == [True]


def test_exclusion_is_a_noop_before_the_engine_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swept: list[Any] = []
    monkeypatch.setattr(
        "app.services.browser_service.set_process_windows_excluded_from_capture",
        lambda pid, **kwargs: swept.append(pid),
    )
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("webview2", browser_process_id=0))

    service.apply_capture_exclusion(True)

    assert swept == []


def test_the_qt_pane_needs_no_process_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It draws into a Strata window, which the window's own affinity covers."""
    swept: list[Any] = []
    monkeypatch.setattr(
        "app.services.browser_service.set_process_windows_excluded_from_capture",
        lambda pid, **kwargs: swept.append(pid),
    )
    service = _service(tmp_path, backend="embedded")
    service.attach(FakePane("embedded"))

    service.apply_capture_exclusion(True)

    assert swept == []


# -- the Qt-side adapter -------------------------------------------------------


class FakeWidgetPane:
    """A *pane*, not a PageSource: what ``EmbeddedSource`` actually wraps."""

    def __init__(self, backend: str) -> None:
        self.backend = backend
        self.failure_reason = ""
        self.loaded_addons: list[str] = []
        self.addon_errors: list[str] = []

    def current(self) -> BrowserTab:
        return BrowserTab(target_id="pane", title="A page", url="https://example.com/", active=True)

    def is_mobile(self) -> bool:
        return False

    def isVisible(self) -> bool:
        return True

    def load_url(self, url: str) -> None: ...

    def set_blur(self, enabled: bool, amount: int) -> None: ...

    def set_mobile(self, enabled: bool) -> None: ...

    def extract(self, deliver: Any) -> None: ...


def test_embedded_source_reports_the_pane_it_was_given() -> None:
    """``EmbeddedSource`` is shared by both panes and must not assume one.

    It used to hard-code "embedded" as a class attribute, which was true while
    there was a single engine and is a silent lie now.
    """
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from app.desktop.browser_pane import EmbeddedSource

    source = EmbeddedSource(FakeWidgetPane("webview2"), lambda visible: None)

    assert source.backend == "webview2"
    assert source.status().backend == "webview2"


def test_embedded_source_still_reports_the_qt_pane() -> None:
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from app.desktop.browser_pane import EmbeddedSource

    source = EmbeddedSource(FakeWidgetPane("embedded"), lambda visible: None)

    assert source.status().backend == "embedded"


def test_a_failed_engine_says_so_in_the_status() -> None:
    """The pane exists but its engine never started.

    Reported through the status the panel reads, not only on the placeholder
    inside the pane — a user looking at the Research panel should not have to
    open the pane to find out why it is blank.
    """
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from app.desktop.browser_pane import EmbeddedSource

    pane = FakeWidgetPane("webview2")
    pane.failure_reason = "The Edge WebView2 Runtime is not installed."

    status = EmbeddedSource(pane, lambda visible: None).status()

    assert status.running is False
    assert "could not start its engine" in status.detail
    assert "Runtime is not installed" in status.detail


def test_a_healthy_pane_reports_the_page_not_an_error() -> None:
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from app.desktop.browser_pane import EmbeddedSource

    status = EmbeddedSource(FakeWidgetPane("webview2"), lambda visible: None).status()

    assert status.running is True
    assert "example.com" in status.detail


# -- extensions ----------------------------------------------------------------


def test_extensions_default_to_none() -> None:
    assert AppSettings().browser_extensions == []


def test_extension_paths_are_tidied_not_validated() -> None:
    """Whitespace and duplicates go; a missing folder is *kept*.

    Settings must load on a machine where the folder has since been moved —
    the pane reports that when it tries, rather than the app refusing to start.
    """
    settings = AppSettings(
        browser_extensions=["  C:/tools/ublock  ", "C:/tools/ublock", "", "C:/gone"]
    )

    assert settings.browser_extensions == ["C:/tools/ublock", "C:/gone"]


def test_a_bare_string_is_not_a_list_of_folders() -> None:
    """Otherwise "C:/tools/ublock" silently becomes eighteen one-character paths."""
    with pytest.raises(ValueError, match="browser_extensions"):
        AppSettings(browser_extensions="C:/tools/ublock")


def test_the_extension_list_is_capped() -> None:
    with pytest.raises(ValueError, match="at most"):
        AppSettings(browser_extensions=[f"C:/tools/e{index}" for index in range(50)])


def test_loaded_extensions_are_named_in_the_status() -> None:
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from app.desktop.browser_pane import EmbeddedSource

    pane = FakeWidgetPane("webview2")
    pane.loaded_addons = ["uBlock Origin"]

    status = EmbeddedSource(pane, lambda visible: None).status()

    assert status.supports_extensions is True
    assert "uBlock Origin" in status.detail


def test_an_extension_that_did_not_load_is_not_passed_over() -> None:
    """Silence here reads as "it is working", which is the wrong answer."""
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from app.desktop.browser_pane import EmbeddedSource

    pane = FakeWidgetPane("webview2")
    pane.addon_errors = ["C:/gone is not a folder."]

    status = EmbeddedSource(pane, lambda visible: None).status()

    assert status.supports_extensions is False
    assert "C:/gone is not a folder." in status.detail


def test_a_pane_with_no_extensions_claims_none() -> None:
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from app.desktop.browser_pane import EmbeddedSource

    status = EmbeddedSource(FakeWidgetPane("webview2"), lambda visible: None).status()

    assert status.supports_extensions is False
    assert "Extensions" not in status.detail
