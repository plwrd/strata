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

    def status(self) -> BrowserStatus:
        return BrowserStatus(
            backend="embedded",
            running=self.shown,
            supports_extensions=False,
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
