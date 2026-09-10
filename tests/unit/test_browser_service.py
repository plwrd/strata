"""The controlled browser — the gate, the guards, and both backends.

No browser is started here. The embedded pane is a fake ``PageSource`` (the real
one is Qt-land, injected at startup) and the DevTools endpoint is stubbed,
because what needs testing is Strata's side of the contract: the feature refuses
to do anything while it is switched off, search becomes a URL rather than an
outbound request, only http(s) is opened, a page comes back as text, and
whichever backend answers, the answer has the same shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.browser import BrowserStatus, BrowserTab, ScrapedPage
from app.domain.errors import InvalidRequestError, PermissionDeniedError, ProviderError
from app.services.browser_service import BrowserService
from app.services.settings_service import SettingsService

PAGE_JSON = json.dumps(
    {
        "url": "https://example.com/paper",
        "title": "A research page",
        "text": "Body text.",
        "truncated": False,
    }
)


class FakeCDP:
    """Stands in for a Chrome that is already listening."""

    def __init__(self, pages: list[dict[str, Any]] | None = None, value: str | None = None) -> None:
        self.pages_list = (
            pages
            if pages is not None
            else [
                {
                    "id": "tab_1",
                    "type": "page",
                    "title": "A research page",
                    "url": "https://example.com/paper",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/page/tab_1",
                }
            ]
        )
        self.value = value
        self.opened: list[str] = []
        self.activated: list[str] = []

    def version(self) -> dict[str, Any]:
        return {"Product": "Chrome/140.0"}

    def pages(self) -> list[dict[str, Any]]:
        return self.pages_list

    def open_tab(self, url: str) -> dict[str, Any]:
        self.opened.append(url)
        return {"id": "tab_new", "title": "", "url": url}

    def activate(self, target_id: str) -> None:
        self.activated.append(target_id)

    def evaluate(self, websocket_url: str, expression: str) -> Any:
        return self.value


class DeadCDP:
    """A port with nothing behind it — what "the browser is not open" looks like."""

    def version(self) -> dict[str, Any]:
        raise ProviderError("The browser is not reachable.")

    def pages(self) -> list[dict[str, Any]]:
        raise ProviderError("The browser is not reachable.")


class FakePane:
    """Stands in for the Qt pane, which cannot exist in a headless test."""

    backend = "embedded"

    def __init__(self, *, url: str = "https://example.com/paper") -> None:
        self.url = url
        self.shown = False
        self.loaded: list[str] = []
        self.closed = False
        self.reads = 0
        self.blur: tuple[bool, int] | None = None
        self.mobile: bool | None = None

    def status(self) -> BrowserStatus:
        return BrowserStatus(
            backend="embedded",
            running=self.shown,
            supports_extensions=False,
            mobile_mode=bool(self.mobile),
            tab_count=1 if self.url else 0,
            detail="The browser pane is open." if self.shown else "The browser pane is closed.",
        )

    def ensure_ready(self) -> BrowserStatus:
        self.shown = True
        return self.status()

    def open_url(self, url: str) -> BrowserTab:
        self.shown = True
        self.url = url
        self.loaded.append(url)
        return BrowserTab(target_id="pane", title="", url=url, active=True)

    def tabs(self) -> list[BrowserTab]:
        return [BrowserTab(target_id="pane", title="A research page", url=self.url, active=True)]

    def read(self, target_id: str) -> ScrapedPage:
        self.reads += 1
        return ScrapedPage(
            url=self.url,
            title="A research page",
            text="Body text.",
            char_count=10,
            target_id="pane",
        )

    def apply_blur(self, enabled: bool, amount: int) -> None:
        self.blur = (enabled, amount)

    def apply_mobile(self, enabled: bool) -> None:
        self.mobile = enabled

    def close(self) -> None:
        self.closed = True
        self.shown = False


def _service(
    tmp_path: Path,
    *,
    enabled: bool = True,
    backend: str = "embedded",
    engine: str = "duckduckgo",
) -> BrowserService:
    settings = SettingsService(tmp_path / "settings.json")
    settings.update(
        {
            "browser_control_enabled": enabled,
            "browser_backend": backend,
            "browser_search_engine": engine,
        }
    )
    return BrowserService(settings, tmp_path)


def _embedded(tmp_path: Path, **kwargs: Any) -> tuple[BrowserService, FakePane]:
    service = _service(tmp_path, **kwargs)
    pane = FakePane()
    service.attach(pane)
    return service, pane


def _chrome(tmp_path: Path, fake: Any, **kwargs: Any) -> BrowserService:
    service = _service(tmp_path, backend="chrome", **kwargs)
    service._chrome._client = lambda: fake  # type: ignore[assignment]
    return service


# -- the gate ---------------------------------------------------------------


@pytest.mark.parametrize("backend", ["embedded", "chrome"])
def test_everything_is_refused_while_the_feature_is_off(tmp_path: Path, backend: str) -> None:
    service, _pane = _embedded(tmp_path, enabled=False, backend=backend)

    for call in (
        lambda: service.launch(),
        lambda: service.search("anything"),
        lambda: service.open_url("https://example.com"),
        lambda: service.tabs(),
        lambda: service.read_page(),
        lambda: service.require_enabled(),
    ):
        with pytest.raises(PermissionDeniedError):
            call()


def test_status_explains_itself_when_off_instead_of_raising(tmp_path: Path) -> None:
    service, _pane = _embedded(tmp_path, enabled=False)
    status = service.status()

    assert isinstance(status, BrowserStatus)
    assert status.enabled is False
    assert status.running is False
    assert "Settings" in status.detail


# -- the embedded pane (the default) ----------------------------------------


def test_embedded_is_the_default_backend(tmp_path: Path) -> None:
    service, _pane = _embedded(tmp_path)

    assert service.backend == "embedded"
    assert service.status().supports_extensions is False


def test_opening_a_page_shows_the_pane_and_loads_it(tmp_path: Path) -> None:
    service, pane = _embedded(tmp_path)

    tab = service.open_url("https://example.com/paper")

    assert pane.shown is True
    assert pane.loaded == ["https://example.com/paper"]
    assert tab.target_id == "pane"


def test_search_navigates_the_pane(tmp_path: Path) -> None:
    service, pane = _embedded(tmp_path)

    service.search("vector index tradeoffs")

    # The query left as a URL for the browser, not as a request from Strata.
    assert pane.loaded == ["https://duckduckgo.com/?q=vector+index+tradeoffs"]


def test_reading_the_pane_returns_text(tmp_path: Path) -> None:
    service, pane = _embedded(tmp_path)

    page = service.read_page()

    assert page.text == "Body text."
    assert page.target_id == "pane"
    assert pane.reads == 1


def test_the_embedded_backend_says_so_when_there_is_no_pane(tmp_path: Path) -> None:
    """Headless, or a window that never built one. It must not pretend."""
    service = _service(tmp_path)  # nothing attached

    with pytest.raises(ProviderError):
        service.read_page()
    assert "not available" in service.status().detail


# -- the Chrome backend ------------------------------------------------------


def test_chrome_status_reports_a_running_browser(tmp_path: Path) -> None:
    status = _chrome(tmp_path, FakeCDP()).status()

    assert status.backend == "chrome"
    assert status.running is True
    assert status.supports_extensions is True
    assert status.browser_version == "Chrome/140.0"
    assert status.tab_count == 1


def test_chrome_search_opens_and_focuses_a_tab(tmp_path: Path) -> None:
    fake = FakeCDP()
    service = _chrome(tmp_path, fake)

    tab = service.search("vector index tradeoffs")

    assert fake.opened == ["https://duckduckgo.com/?q=vector+index+tradeoffs"]
    assert fake.activated == ["tab_new"]
    assert tab.target_id == "tab_new"


def test_chrome_reads_a_tab_as_text(tmp_path: Path) -> None:
    service = _chrome(tmp_path, FakeCDP(value=PAGE_JSON))

    page = service.read_page()

    assert page.title == "A research page"
    assert page.text == "Body text."
    assert page.char_count == 10
    assert page.target_id == "tab_1"


def test_both_backends_produce_the_same_shape(tmp_path: Path) -> None:
    """The research pipeline downstream must not be able to tell them apart."""
    embedded_service, _pane = _embedded(tmp_path)
    chrome_service = _chrome(tmp_path / "chrome", FakeCDP(value=PAGE_JSON))

    from_pane = embedded_service.read_page()
    from_chrome = chrome_service.read_page()

    assert from_pane.text == from_chrome.text
    assert from_pane.title == from_chrome.title
    assert from_pane.url == from_chrome.url


def test_chrome_scraping_a_tab_that_closed_says_so(tmp_path: Path) -> None:
    service = _chrome(tmp_path, FakeCDP(value="{}"))

    with pytest.raises(ProviderError):
        service.read_page(target_id="tab_that_went_away")


def test_a_page_that_answers_with_nonsense_fails_closed(tmp_path: Path) -> None:
    service = _chrome(tmp_path, FakeCDP(value="not json at all"))

    with pytest.raises(ProviderError):
        service.read_page()


def test_no_open_page_is_an_explanation_not_a_crash(tmp_path: Path) -> None:
    service = _chrome(tmp_path, FakeCDP(pages=[]))

    with pytest.raises(ProviderError):
        service.read_page()


def test_a_configured_executable_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    settings = SettingsService(tmp_path / "settings.json")
    settings.update(
        {
            "browser_control_enabled": True,
            "browser_backend": "chrome",
            "browser_executable_path": str(tmp_path / "no-such-chrome.exe"),
        }
    )
    service = BrowserService(settings, tmp_path)
    service._chrome._client = lambda: DeadCDP()  # type: ignore[assignment]

    # Nothing is listening, so launch() must resolve an executable — and refuse.
    with pytest.raises(InvalidRequestError):
        service.launch()


def test_the_chrome_profile_lives_outside_the_workspace_by_default(tmp_path: Path) -> None:
    service = _service(tmp_path, backend="chrome")

    assert service._chrome.profile_path == tmp_path / "browser-profile"


# -- navigation guards (both backends) --------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///c:/windows/win.ini",
        "javascript:alert(1)",
        "chrome://settings",
        "data:text/html,<h1>hi</h1>",
        "strata://app/index.html",
        "https://user:secret@example.com/",
    ],
)
def test_only_plain_http_and_https_pages_can_be_opened(tmp_path: Path, url: str) -> None:
    service, pane = _embedded(tmp_path)

    with pytest.raises(PermissionDeniedError):
        service.open_url(url)
    assert pane.loaded == []


def test_an_unknown_engine_is_refused(tmp_path: Path) -> None:
    service, pane = _embedded(tmp_path)

    with pytest.raises(InvalidRequestError):
        service.search("hnsw", engine="totally-made-up")
    assert pane.loaded == []


def test_the_engine_can_be_chosen_per_search(tmp_path: Path) -> None:
    service, pane = _embedded(tmp_path)

    service.search("hnsw", engine="google")

    assert pane.loaded == ["https://www.google.com/search?q=hnsw"]


# -- shutdown ----------------------------------------------------------------


def test_closing_takes_the_browser_with_it(tmp_path: Path) -> None:
    """A pane holding a signed-in session, or a Chrome holding a debugging port,
    must not outlive the window that justified it (THREAT_MODEL.md T-34)."""
    service, pane = _embedded(tmp_path)
    service.launch()

    service.close()

    assert pane.closed is True


def test_closing_terminates_only_a_chrome_strata_started(tmp_path: Path) -> None:
    service = _service(tmp_path, backend="chrome")

    class FakeProcess:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float | None = None) -> int:
            return 0

    process = FakeProcess()
    service._chrome._process = process  # type: ignore[assignment]
    service.close()
    assert process.terminated is True

    # A browser Strata never started is not ours to kill.
    service._chrome._process = None
    service.close()  # must not raise


# -- media blur --------------------------------------------------------------


def test_blur_is_off_by_default_and_supported_on_the_pane(tmp_path: Path) -> None:
    service, _pane = _embedded(tmp_path)
    enabled, amount = service.blur_state()

    assert enabled is False
    assert amount == 12
    assert service.blur_supported is True


def test_toggling_blur_applies_it_to_the_pane(tmp_path: Path) -> None:
    service, pane = _embedded(tmp_path)

    assert service.toggle_blur() is True
    assert pane.blur == (True, 12)

    assert service.toggle_blur() is False
    assert pane.blur == (False, 12)


def test_blur_change_notifies_the_listener(tmp_path: Path) -> None:
    """The bridge relies on this to push a hotkey toggle to the panel."""
    service, _pane = _embedded(tmp_path)
    seen: list[tuple[bool, int]] = []
    service.on_blur_changed = lambda: seen.append(service.blur_state())

    service.toggle_blur()
    service.set_blur_amount(20)

    assert seen and seen[0][0] is True


def test_the_amount_comes_from_settings_and_reaches_the_pane(tmp_path: Path) -> None:
    settings = SettingsService(tmp_path / "settings.json")
    settings.update(
        {
            "browser_control_enabled": True,
            "browser_backend": "embedded",
            "browser_blur_amount": 25,
        }
    )
    service = BrowserService(settings, tmp_path)
    pane = FakePane()
    service.attach(pane)

    service.set_blur(True)

    assert pane.blur == (True, 25)


def test_the_chrome_backend_does_not_support_blur(tmp_path: Path) -> None:
    service = _chrome(tmp_path, FakeCDP())

    assert service.blur_supported is False
    # Toggling is a harmless no-op — there is no pane to restyle.
    assert service.set_blur(True) is True


def test_a_freshly_attached_pane_gets_the_starting_blur(tmp_path: Path) -> None:
    settings = SettingsService(tmp_path / "settings.json")
    settings.update(
        {
            "browser_control_enabled": True,
            "browser_backend": "embedded",
            "browser_blur_media": True,
            "browser_blur_amount": 8,
        }
    )
    service = BrowserService(settings, tmp_path)
    pane = FakePane()
    service.attach(pane)

    assert pane.blur == (True, 8)


# -- mobile mode -------------------------------------------------------------


def test_mobile_is_off_by_default(tmp_path: Path) -> None:
    service, _pane = _embedded(tmp_path)
    assert service.mobile_state() is False
    assert service.status().mobile_mode is False


def test_toggling_mobile_applies_it_to_the_pane(tmp_path: Path) -> None:
    service, pane = _embedded(tmp_path)

    assert service.set_mobile(True) is True
    assert pane.mobile is True
    assert service.status().mobile_mode is True

    assert service.set_mobile(False) is False
    assert pane.mobile is False


def test_a_freshly_attached_pane_gets_the_starting_mobile_state(tmp_path: Path) -> None:
    settings = SettingsService(tmp_path / "settings.json")
    settings.update(
        {
            "browser_control_enabled": True,
            "browser_backend": "embedded",
            "browser_mobile_mode": True,
        }
    )
    service = BrowserService(settings, tmp_path)
    pane = FakePane()
    service.attach(pane)

    assert pane.mobile is True


def test_chrome_backend_ignores_mobile(tmp_path: Path) -> None:
    service = _chrome(tmp_path, FakeCDP())
    # No embedded pane to restyle; the call is a harmless no-op.
    assert service.set_mobile(True) is True


# -- open in the real browser ------------------------------------------------


def test_external_url_accepts_http_and_https(tmp_path: Path) -> None:
    service, _pane = _embedded(tmp_path)
    assert service.external_url("https://x.com/i/status/1") == "https://x.com/i/status/1"


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "https://u:p@x.com/"],
)
def test_external_url_refuses_non_web_and_credentialed(tmp_path: Path, url: str) -> None:
    service, _pane = _embedded(tmp_path)
    with pytest.raises((PermissionDeniedError, InvalidRequestError)):
        service.external_url(url)


def test_external_url_is_gated_on_the_feature(tmp_path: Path) -> None:
    service, _pane = _embedded(tmp_path, enabled=False)
    with pytest.raises(PermissionDeniedError):
        service.external_url("https://x.com/")
