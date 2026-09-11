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


def _foreign(monkeypatch: pytest.MonkeyPatch, *, on_screen: int) -> tuple[list[int], list[int]]:
    """Stand in for the two things `screen_security` can do to another process's
    windows: count the visible ones, and ask them to close."""
    counted: list[int] = []
    closed: list[int] = []

    def _count(pid: int) -> int:
        counted.append(pid)
        return on_screen

    def _close(pid: int) -> int:
        closed.append(pid)
        return on_screen

    monkeypatch.setattr("app.services.browser_service.foreign_windows_uncovered", _count)
    monkeypatch.setattr("app.services.browser_service.close_foreign_windows", _close)
    return counted, closed


def test_engine_popups_are_closed_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WebView2's popups live in its own process, which Windows will not let us
    exclude from capture (``SetWindowDisplayAffinity`` is refused for a window
    another process owns). A dropdown that is on screen is therefore in the
    recording: it is closed, and — because it *was* there — reported."""
    counted, closed = _foreign(monkeypatch, on_screen=2)
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("webview2", browser_process_id=4321))

    assert service.apply_capture_exclusion(True) == 2

    assert counted == [4321]
    assert closed == [4321]


def test_nothing_on_screen_means_nothing_to_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The steady state: no popup, no close, nothing to report."""
    counted, closed = _foreign(monkeypatch, on_screen=0)
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("webview2", browser_process_id=4321))

    assert service.apply_capture_exclusion(True) == 0

    assert counted == [4321]
    assert closed == []


def test_exclusion_is_a_noop_before_the_engine_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counted, closed = _foreign(monkeypatch, on_screen=5)
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("webview2", browser_process_id=0))

    assert service.apply_capture_exclusion(True) == 0

    assert counted == []
    assert closed == []


def test_hiding_off_leaves_the_engine_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off means off: a popup is the user's business when nothing is hidden."""
    counted, closed = _foreign(monkeypatch, on_screen=5)
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("webview2", browser_process_id=4321))

    assert service.apply_capture_exclusion(False) == 0

    assert counted == []
    assert closed == []


def test_the_qt_pane_needs_no_process_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It draws into a Strata window, which the window's own affinity covers."""
    counted, closed = _foreign(monkeypatch, on_screen=5)
    service = _service(tmp_path, backend="embedded")
    service.attach(FakePane("embedded"))

    assert service.apply_capture_exclusion(True) == 0

    assert counted == []
    assert closed == []


def test_only_webview2_popups_are_disposable(tmp_path: Path) -> None:
    """Closing is right for an engine whose page lives in *our* window, so any
    window of its own is a popup. Chrome's windows are the browser; the Qt
    pane's popups are ours and get the affinity instead."""
    webview2 = _service(tmp_path, backend="webview2")
    webview2.attach(FakePane("webview2"))
    assert webview2.engine_popups_closable is True

    qt = _service(tmp_path, backend="embedded")
    qt.attach(FakePane("embedded"))
    assert qt.engine_popups_closable is False

    chrome = _service(tmp_path, backend="chrome")
    assert chrome.engine_popups_closable is False


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


def test_the_adapter_forwards_the_engine_pid() -> None:
    """The window asks the *service* for the engine pid, and the service asks
    whatever was attached — which in the running app is this adapter, not the
    pane. Without the forward, the pid read as 0 and the popup guard never
    watched the browser process at all."""
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from app.desktop.browser_pane import EmbeddedSource

    pane = FakeWidgetPane("webview2")
    pane.browser_process_id = 4321  # type: ignore[attr-defined]
    assert EmbeddedSource(pane, lambda visible: None).browser_process_id == 4321

    # The Qt pane has no process of its own and no such attribute.
    assert EmbeddedSource(FakeWidgetPane("embedded"), lambda visible: None).browser_process_id == 0


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


# -- which process the window hooks should watch --------------------------------
#
# The periodic sweep is the backstop; the instant hook is the mechanism, and it
# needs a pid. Chrome used to be left out of it — only the 1.5 s sweep covered
# that backend, so a menu opened and dismissed between two ticks was never
# excluded at all.


def test_the_engine_pid_is_the_webview2_browser_process(tmp_path: Path) -> None:
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("webview2", browser_process_id=4321))

    assert service.engine_process_id == 4321


def test_the_engine_pid_is_zero_before_the_engine_starts(tmp_path: Path) -> None:
    service = _service(tmp_path, backend="webview2")
    service.attach(FakePane("webview2", browser_process_id=0))

    assert service.engine_process_id == 0


def test_the_chrome_backend_reports_the_browser_it_launched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, backend="chrome")

    class _Running:
        pid = 777

        def poll(self) -> None:
            return None

    monkeypatch.setattr(service._chrome, "_process", _Running(), raising=False)

    assert service.engine_process_id == 777


def test_a_chrome_that_has_exited_reports_no_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead pid is worse than none: Windows reuses process ids, and a hook on
    a recycled one would reach another application's windows."""
    service = _service(tmp_path, backend="chrome")

    class _Exited:
        pid = 777

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(service._chrome, "_process", _Exited(), raising=False)

    assert service.engine_process_id == 0


# -- the popup-free <select> ---------------------------------------------------


def test_the_select_guard_opens_in_page_and_puts_the_size_back() -> None:
    """A dropdown's option list is a window of the browser process — one Strata
    cannot exclude and therefore closes on sight. The in-page listbox is what a
    mouse user gets instead, and it must leave the element as it found it."""
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from app.desktop.webview2.pane import popup_free_select_source

    source = popup_free_select_source()
    assert "preventDefault()" in source  # the native popup never opens
    assert 'removeAttribute("size")' in source  # a select without a size gets none back
    assert "el.size = size" in source  # one with a size gets its own back
    assert "__strataSelect" in source  # installed once per document
