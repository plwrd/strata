"""The controlled browser: a pane in the window, or the Chrome you already have.

Research needs the pages a plain fetch cannot reach — the ones behind a login,
the ones that are empty until JavaScript runs. Two backends reach them, and the
rest of the app cannot tell which one answered:

* :class:`EmbeddedSource` — the browser pane inside the Strata window. The
  default. It lives in Qt-land, so it is *injected* here rather than
  constructed (see ``attach``): this module stays free of Qt and testable with
  a fake.
* :class:`ChromeSource` — a real Chrome on a loopback DevTools port. Kept for
  what the pane cannot do: extensions, and sign-in flows that refuse an
  embedded browser.

The rules that keep both narrow:

- **Off by default.** ``settings.browser_control_enabled`` is the kill switch,
  and nothing here runs until the user turns it on (docs/security-and-privacy.md §3).
- **Search is navigation.** A query becomes a URL and the browser goes there.
  Strata makes no outbound call of its own; the engine sees an ordinary browser.
- **http(s) only.** Every other scheme is refused before anything is opened.
- **Pages are data.** Extracted text is stored as an untrusted capture, exactly
  like URL import, and reaches a model only through the usual policy gate.
- **Reading blocks.** ``read`` is called on a worker thread by the bridge, never
  on the Qt event loop — a slow page must not freeze the editor.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import quote_plus, urlsplit

from app.domain.browser import (
    SEARCH_URLS,
    BrowserBackend,
    BrowserStatus,
    BrowserTab,
    ScrapedPage,
)
from app.domain.errors import InvalidRequestError, PermissionDeniedError, ProviderError
from app.infrastructure.browser.cdp import BrowserUnavailableError, CDPClient
from app.infrastructure.logging.logger import get_logger
from app.services.settings_service import SettingsService

logger = get_logger(__name__)

MAX_PAGE_CHARS = 400_000
MAX_QUERY_CHARS = 500
LAUNCH_TIMEOUT_SECONDS = 20.0
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Candidate executables, best first, per platform. The setting overrides all of
# them; this list only exists so the common case needs no configuration.
_CANDIDATES: dict[str, tuple[str, ...]] = {
    "win32": (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Chromium\Application\chrome.exe",
    ),
    "darwin": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ),
    "linux": (
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/microsoft-edge",
    ),
}
_PATH_NAMES = ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge")

# Extraction runs in the page and returns text, not markup. It prefers the
# article/main element when the page has one — a research capture should be the
# piece worth keeping, not the navigation chrome around it.
#
# Both backends run this same source, so a page reads the same either way.
EXTRACT_JS = """
(() => {
  const MAX = %d;
  const pick = () => {
    for (const selector of ["article", "main", "[role=main]"]) {
      const found = document.querySelector(selector);
      if (found && (found.innerText || "").trim().length > 200) return found;
    }
    return document.body;
  };
  const root = pick();
  if (!root) return JSON.stringify({ url: location.href, title: document.title, text: "" });
  const clone = root.cloneNode(true);
  for (const node of clone.querySelectorAll(
    "script,style,noscript,template,svg,iframe,nav,header,footer,aside,form"
  )) {
    node.remove();
  }
  const raw = (clone.innerText || clone.textContent || "").replace(/\\n{3,}/g, "\\n\\n").trim();
  return JSON.stringify({
    url: location.href,
    title: (document.title || "").slice(0, 300),
    text: raw.slice(0, MAX),
    truncated: raw.length > MAX,
  });
})()
""".strip()


def extraction_script() -> str:
    """The extraction source, capped. Qt-land needs this too."""
    return EXTRACT_JS % MAX_PAGE_CHARS


def decode_extraction(raw: object, *, fallback_url: str = "", target_id: str = "") -> ScrapedPage:
    """One page's JSON answer → a validated :class:`ScrapedPage`.

    Shared by both backends so a malformed answer fails the same way whichever
    browser produced it."""
    if not isinstance(raw, str):
        raise ProviderError("The page could not be read.")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ProviderError("The page could not be read.") from exc
    if not isinstance(payload, dict):
        raise ProviderError("The page could not be read.")

    text = str(payload.get("text", ""))[:MAX_PAGE_CHARS]
    url = str(payload.get("url", fallback_url))[:2000]
    title = str(payload.get("title", "")).strip()[:200]
    return ScrapedPage(
        url=url,
        title=title or urlsplit(url).netloc,
        text=text,
        char_count=len(text),
        truncated=bool(payload.get("truncated")),
        target_id=target_id,
    )


class PageSource(Protocol):
    """One way of driving a browser. Implemented twice; used through the facade."""

    backend: BrowserBackend

    def status(self) -> BrowserStatus: ...

    def ensure_ready(self) -> BrowserStatus: ...

    def open_url(self, url: str) -> BrowserTab: ...

    def tabs(self) -> list[BrowserTab]: ...

    def read(self, target_id: str) -> ScrapedPage:
        """Blocking. Called on a worker thread, never on the Qt event loop."""
        ...

    def close(self) -> None: ...


class ChromeSource:
    """A real Chrome, driven over a loopback DevTools port.

    Strata launches it against a profile directory it owns, so a debugging port
    is never opened onto the user's everyday session unless they deliberately
    point ``browser_profile_path`` at one. ``--disable-extensions`` is
    deliberately absent: extensions are the whole reason this backend exists.
    """

    backend: BrowserBackend = "chrome"

    def __init__(self, settings: SettingsService, profile_root: Path) -> None:
        self._settings = settings
        self._profile_root = profile_root
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def _port(self) -> int:
        return int(self._settings.settings.browser_debug_port)

    @property
    def profile_path(self) -> Path:
        override = self._settings.settings.browser_profile_path.strip()
        return Path(override) if override else self._profile_root / "browser-profile"

    def _client(self) -> CDPClient:
        return CDPClient(self._port)

    def status(self) -> BrowserStatus:
        base = BrowserStatus(
            backend="chrome",
            supports_extensions=True,
            port=self._port,
            executable=self._settings.settings.browser_executable_path,
            profile_path=str(self.profile_path),
        )
        try:
            version = self._client().version()
            tabs = self._client().pages()
        except (BrowserUnavailableError, ProviderError):
            return base.model_copy(update={"detail": "The Strata browser window is not open yet."})
        product = str(version.get("Product", "")) or "Chrome"
        return base.model_copy(
            update={
                "running": True,
                "browser_version": product,
                "tab_count": len(tabs),
                "detail": f"{product} is running with {len(tabs)} tab(s).",
            }
        )

    def ensure_ready(self) -> BrowserStatus:
        current = self.status()
        if current.running:
            return current

        executable = self._resolve_executable()
        profile = self.profile_path
        profile.mkdir(parents=True, exist_ok=True)
        arguments = [
            executable,
            f"--remote-debugging-port={self._port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        try:
            self._process = subprocess.Popen(  # noqa: S603 - path is validated above
                arguments,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            logger.info("browser.launch_failed")
            raise ProviderError("The browser could not be started.") from exc

        deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status = self.status()
            if status.running:
                logger.info("browser.launched", port=self._port)
                return status
            time.sleep(0.25)
        raise ProviderError(
            "The browser started but never opened its debugging port. "
            "Close any window already using this profile and try again."
        )

    def _resolve_executable(self) -> str:
        configured = self._settings.settings.browser_executable_path.strip()
        if configured:
            if not Path(configured).is_file():
                raise InvalidRequestError("The configured browser executable does not exist.")
            return configured
        for candidate in _CANDIDATES.get(sys.platform, ()):
            if Path(candidate).is_file():
                return candidate
        for name in _PATH_NAMES:
            found = shutil.which(name)
            if found:
                return found
        raise InvalidRequestError(
            "No Chrome or Chromium install was found. Set the browser path in Settings."
        )

    def open_url(self, url: str) -> BrowserTab:
        self.ensure_ready()  # a no-op when it is already running
        opened = self._client().open_tab(url)
        target_id = str(opened.get("id", ""))
        if target_id:
            self._client().activate(target_id)
        return BrowserTab(
            target_id=target_id,
            title=str(opened.get("title", "")),
            url=str(opened.get("url", url)),
            active=True,
        )

    def tabs(self) -> list[BrowserTab]:
        return [
            BrowserTab(
                target_id=str(page.get("id", "")),
                title=str(page.get("title", ""))[:300],
                url=str(page.get("url", ""))[:2000],
                # Chrome lists the most recently focused page first.
                active=index == 0,
            )
            for index, page in enumerate(self._client().pages())
        ]

    def read(self, target_id: str) -> ScrapedPage:
        pages = self._client().pages()
        if not pages:
            raise ProviderError("There is no page open in the Strata browser.")
        page = self._select(pages, target_id)
        socket_url = str(page.get("webSocketDebuggerUrl", ""))
        if not socket_url:
            raise ProviderError("That tab cannot be read — open it in a normal window first.")

        raw = self._client().evaluate(socket_url, extraction_script())
        return decode_extraction(
            raw,
            fallback_url=str(page.get("url", "")),
            target_id=str(page.get("id", "")),
        )

    @staticmethod
    def _select(pages: list[dict[str, object]], target_id: str) -> dict[str, object]:
        if not target_id:
            return pages[0]
        for page in pages:
            if str(page.get("id", "")) == target_id:
                return page
        raise ProviderError("That tab is no longer open.")

    def close(self) -> None:
        """Stop the browser *Strata* started — never one the user launched.

        The debugging port is only defensible while the window that justified it
        is open (THREAT_MODEL.md T-34), so quitting Strata must take it with us.
        """
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
            logger.info("browser.closed")
        except OSError:
            logger.info("browser.close_failed")


class BrowserService:
    """The facade the bridge talks to: the gate, the guards, and the dispatch."""

    def __init__(self, settings: SettingsService, profile_root: Path) -> None:
        self._settings = settings
        self._chrome = ChromeSource(settings, profile_root)
        self._embedded: PageSource | None = None
        # Runtime blur state. Amount is a setting; on/off starts from the setting
        # but is then toggled live (hotkey or panel), so it lives here, not there.
        self._blur_enabled = settings.settings.browser_blur_media
        # Mobile mode (serve a mobile user-agent) starts from its setting too.
        self._mobile = settings.settings.browser_mobile_mode
        # Notified whenever blur changes by any path, so the panel can reflect a
        # hotkey toggle it did not make. Set by the bridge.
        self.on_blur_changed: Callable[[], None] | None = None

    def attach(self, source: PageSource) -> None:
        """Register the embedded pane, once the Qt window has built it.

        Injected rather than imported: this module must not depend on Qt, so the
        pane arrives from ``app.desktop`` at startup and is a fake in tests.
        """
        self._embedded = source
        self._apply_blur()
        self._apply_mobile()

    # -- state ---------------------------------------------------------------

    @property
    def backend(self) -> BrowserBackend:
        configured = self._settings.settings.browser_backend
        return "chrome" if configured == "chrome" else "embedded"

    def _source(self) -> PageSource:
        if self.backend == "chrome":
            return self._chrome
        if self._embedded is None:
            raise ProviderError(
                "The browser pane is not available in this window. "
                "Switch the research browser to Chrome in Settings."
            )
        return self._embedded

    def require_enabled(self) -> None:
        """The gate. Public because the bridge checks it before starting a read
        on a worker thread — a refusal must be an error the caller sees, not an
        event nobody is listening for yet."""
        if not self._settings.settings.browser_control_enabled:
            raise PermissionDeniedError(
                "Browser research is switched off in this workspace's settings."
            )

    # -- media blur ----------------------------------------------------------

    @property
    def _blur_amount(self) -> int:
        return int(self._settings.settings.browser_blur_amount)

    @property
    def blur_supported(self) -> bool:
        # The pane can be restyled; a separate real Chrome is not Strata's to
        # reach into and repaint.
        return self.backend == "embedded" and self._embedded is not None

    def blur_state(self) -> tuple[bool, int]:
        return self._blur_enabled, self._blur_amount

    def set_blur(self, enabled: bool) -> bool:
        self._blur_enabled = bool(enabled)
        self._apply_blur()
        self._notify_blur()
        return self._blur_enabled

    def toggle_blur(self) -> bool:
        return self.set_blur(not self._blur_enabled)

    def set_blur_amount(self, amount: int) -> None:
        """The amount lives in settings; this re-applies it to a live pane."""
        self._apply_blur()
        self._notify_blur()

    def _apply_blur(self) -> None:
        embedded = self._embedded
        apply = getattr(embedded, "apply_blur", None)
        if self.blur_supported and callable(apply):
            apply(self._blur_enabled, self._blur_amount)

    def _notify_blur(self) -> None:
        if self.on_blur_changed is not None:
            self.on_blur_changed()

    # -- mobile mode ---------------------------------------------------------

    def mobile_state(self) -> bool:
        return self._mobile

    def set_mobile(self, enabled: bool) -> bool:
        self._mobile = bool(enabled)
        self._apply_mobile()
        return self._mobile

    def _apply_mobile(self) -> None:
        embedded = self._embedded
        apply = getattr(embedded, "apply_mobile", None)
        if self.blur_supported and callable(apply):
            apply(self._mobile)

    def status(self) -> BrowserStatus:
        """Never raises: "can I use this?" is a question, not an operation."""
        enabled = self._settings.settings.browser_control_enabled
        if not enabled:
            return BrowserStatus(
                enabled=False,
                backend=self.backend,
                supports_extensions=self.backend == "chrome",
                detail="Turn on browser research in Settings to use it.",
            )
        try:
            status = self._source().status()
        except (ProviderError, PermissionDeniedError) as exc:
            return BrowserStatus(
                enabled=True,
                backend=self.backend,
                detail=getattr(exc, "message", "The browser is not available."),
            )
        return status.model_copy(
            update={
                "enabled": True,
                "blur_enabled": self._blur_enabled,
                "blur_amount": self._blur_amount,
                "blur_supported": self.blur_supported,
                "mobile_mode": self._mobile,
            }
        )

    # -- navigation ----------------------------------------------------------

    def launch(self) -> BrowserStatus:
        """Open the pane, or start Chrome. Idempotent either way."""
        self.require_enabled()
        return self._source().ensure_ready().model_copy(update={"enabled": True})

    def search(self, query: str, engine: str = "") -> BrowserTab:
        """Turn a query into a URL and put the browser on it."""
        self.require_enabled()
        cleaned = query.strip()
        if not cleaned:
            raise InvalidRequestError("Type something to search for.")
        if len(cleaned) > MAX_QUERY_CHARS:
            raise InvalidRequestError("That search query is too long.")
        chosen = engine.strip() or self._settings.settings.browser_search_engine
        template = SEARCH_URLS.get(chosen)
        if template is None:
            raise InvalidRequestError("That search engine is not one Strata knows.")
        return self.open_url(template.format(query=quote_plus(cleaned)))

    def _validate_web_url(self, url: str) -> str:
        """A cleaned http(s) URL, or a refusal. Shared by pane-open and hand-off."""
        cleaned = url.strip()
        parts = urlsplit(cleaned)
        if parts.scheme.lower() not in _ALLOWED_SCHEMES:
            raise PermissionDeniedError("Only http and https pages can be opened.")
        if not parts.hostname:
            raise InvalidRequestError("That is not a valid URL.")
        if parts.username or parts.password:
            raise PermissionDeniedError("URLs with embedded credentials are not opened.")
        return cleaned

    def external_url(self, url: str) -> str:
        """Validate a page URL for handing to the OS browser.

        The pane cannot play H.264 video (its Qt build has no such codec), so the
        escape hatch is to open the page in the user's real browser, which can.
        The action itself (QDesktopServices) lives in the bridge, on the Qt
        thread; this just gates and cleans the URL, reusing the pane's own guard.
        """
        self.require_enabled()
        return self._validate_web_url(url)

    def open_url(self, url: str) -> BrowserTab:
        self.require_enabled()
        cleaned = self._validate_web_url(url)
        parts = urlsplit(cleaned)
        source = self._source()
        source.ensure_ready()
        tab = source.open_url(cleaned)
        logger.info("browser.opened_tab", host=parts.hostname, backend=self.backend)
        return tab

    def tabs(self) -> list[BrowserTab]:
        self.require_enabled()
        return self._source().tabs()

    # -- reading -------------------------------------------------------------

    def read_page(self, target_id: str = "") -> ScrapedPage:
        """Read one page as text. **Blocking** — the bridge runs it on a thread.

        The result is data, never instructions."""
        self.require_enabled()
        page = self._source().read(target_id)
        logger.info("browser.scraped", chars=page.char_count, backend=self.backend)
        return page

    # -- shutdown ------------------------------------------------------------

    def close(self) -> None:
        """Called when the window closes. Takes any browser Strata started with it."""
        self._chrome.close()
        if self._embedded is not None:
            self._embedded.close()
