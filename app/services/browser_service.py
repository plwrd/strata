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
from typing import Protocol, cast
from urllib.parse import quote_plus, urlsplit

from app.desktop.capture_flags import capture_flags
from app.desktop.screen_security import close_foreign_windows, foreign_windows_uncovered
from app.domain.browser import (
    IN_WINDOW_BACKENDS,
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

# Chrome keeps browsing history in these files inside its profile. Removing
# them (cookies, logins and site data untouched) is how the research browser
# "keeps the session, not the history". Globs, because names vary by version.
_HISTORY_GLOBS = (
    "History",
    "History-journal",
    "History-wal",
    "History-shm",
    "Archived History",
    "Archived History-journal",
    "Visited Links",
    "Top Sites",
    "Top Sites-journal",
)

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
        self._clear_history()  # start each session with no prior browsing history
        # The same compositor flags the two embedded engines get. This backend
        # is the one a user picks *because* it plays video, and a video promoted
        # to a hardware overlay is scanned out beside DWM — so it appears in a
        # recording of a window Strata has excluded. The exclusion is applied to
        # this browser's windows below; without these flags it would only be
        # true of everything except the video.
        arguments = [
            executable,
            f"--remote-debugging-port={self._port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            *capture_flags(hiding=self._settings.settings.hide_for_sharing),
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
                # The launched window now exists; hide it from capture if asked.
                self.apply_capture_exclusion(self._settings.settings.hide_for_sharing)
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

    @property
    def process_id(self) -> int:
        """The launched browser's pid, or 0 when nothing of ours is running."""
        process = self._process
        if process is None or process.poll() is not None:
            return 0
        return int(process.pid)

    def apply_capture_exclusion(self, enabled: bool) -> int:
        """How many of the launched Chrome's windows are in a recording.

        Not "hide them": Windows refuses a display affinity on a window owned
        by another process, so the sweep this used to run never excluded one.
        Chrome's windows *are* the browser, so closing them is not an option
        either. What is left is the truth — the count feeds the reported
        capture state, so "hidden for sharing" reads ``failed`` while a Chrome
        window is on screen instead of a tick over a window in every recording.
        """
        pid = self.process_id
        if not pid or not enabled:
            return 0
        return foreign_windows_uncovered(pid)

    def _clear_history(self) -> None:
        """Drop browsing history from the Strata-owned profile; keep cookies.

        Only the profile Strata owns — if the user pointed `browser_profile_path`
        at their everyday Chrome profile, that is their data and is left alone.
        """
        if self._settings.settings.browser_profile_path.strip():
            return
        default = self.profile_path / "Default"
        removed = 0
        for name in _HISTORY_GLOBS:
            target = default / name
            try:
                if target.exists():
                    target.unlink()
                    removed += 1
            except OSError:
                pass  # locked or gone — best-effort
        if removed:
            logger.info("browser.history_cleared", files=removed)

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
            self._clear_history()  # leave nothing behind once the window is gone
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
        """What the *window actually built*, which is not always what is set.

        A WebView2 pane can be asked for and not be possible — no runtime, no
        loader — in which case the window falls back to the Qt pane and attaches
        that. Reporting the setting rather than the attached pane would tell the
        user they are on an engine they are not, and the difference decides
        whether video plays.
        """
        configured = self._settings.settings.browser_backend
        if configured == "chrome":
            return "chrome"
        attached = getattr(self._embedded, "backend", None)
        if attached in IN_WINDOW_BACKENDS:
            return cast(BrowserBackend, attached)
        return "embedded"

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
        # Either in-window pane can be restyled; a separate real Chrome is not
        # Strata's to reach into and repaint.
        return self.backend in IN_WINDOW_BACKENDS and self._embedded is not None

    def blur_state(self) -> tuple[bool, int]:
        return self._blur_enabled, self._blur_amount

    def set_blur(self, enabled: bool) -> bool:
        """Set blur, and report what the state actually *is* afterwards.

        A backend that cannot be restyled does not get its state flipped. The
        Chrome backend is a browser Strata does not own, so there is nothing to
        blur there — and recording "blurred" for it would put a badge on the
        panel over a page that is not blurred at all. The same rule as the
        capture status: never claim a protection that was not applied.
        """
        if not self.blur_supported:
            logger.info("browser.blur_unsupported", backend=self.backend)
            self._notify_blur()
            return self._blur_enabled
        self._blur_enabled = bool(enabled)
        self._apply_blur()
        self._notify_blur()
        return self._blur_enabled

    def toggle_blur(self) -> bool:
        """Flip blur at its source.

        The one place the state lives, so a hotkey and a button pressed at the
        same moment cannot each compute ``not (what I last saw)`` from a
        different copy and cancel out.
        """
        return self.set_blur(not self._blur_enabled)

    def set_blur_amount(self, amount: int) -> None:
        """Re-apply a changed radius to a live pane.

        The amount itself lives in settings (it survives a restart, unlike the
        on/off, which is a live toggle). ``amount`` is accepted so the caller
        reads naturally and so a future caller cannot pass one that is silently
        ignored — it must match what was persisted.
        """
        if int(amount) != self._blur_amount:
            logger.info("browser.blur_amount_mismatch", passed=int(amount))
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

    # -- capture exclusion (Chrome backend only) -----------------------------

    def apply_capture_exclusion(self, enabled: bool) -> int:
        """Deal with the browser windows that are not Strata's own.

        Returns how many were on screen, uncovered, when this ran — the number
        the window folds into the reported capture state, so one such window
        makes the status say ``failed`` rather than ``excluded``.

        The Qt pane is drawn into a Strata window and is covered by that
        window's exclusion. The other two are not, and neither can be excluded
        from here: Windows refuses ``SetWindowDisplayAffinity`` on a window
        another process owns. What differs is what can be done about it.
        WebView2 renders the page in our window and only ever puts *popups* —
        dropdowns, tooltips, dialogs, bubbles — in its ``msedgewebview2.exe``,
        so those are closed (the hook in ``CaptureGuard`` does it as they
        appear; this sweep catches what the hook missed). Chrome's windows are
        the browser itself, so they are counted and left alone.
        """
        if self.backend == "chrome":
            return self._chrome.apply_capture_exclusion(enabled)
        pid = self.engine_process_id
        if not pid or not enabled:
            return 0
        uncovered = foreign_windows_uncovered(pid)
        if uncovered:
            close_foreign_windows(pid)
        return uncovered

    @property
    def engine_popups_closable(self) -> bool:
        """Whether every top-level window of the engine process is disposable.

        True for WebView2: its page lives in our window, so a window of its own
        is a popup, and one we cannot exclude — closing it is the only way to
        keep it out of a recording. False for Chrome (its windows are the
        browser) and for the Qt pane (its popups are ours and get the affinity).
        """
        return self.backend == "webview2"

    @property
    def engine_process_id(self) -> int:
        """The pid of the browser process behind the current backend, or 0.

        The window hooks it to catch a new popup *as it appears*; the periodic
        sweep is the backstop under that. Both need to know which process is
        actually rendering, and that differs per backend — which is why this
        answers for all of them rather than each caller reaching for whichever
        attribute its backend happens to have.
        """
        if self.backend == "chrome":
            return self._chrome.process_id
        pid = getattr(self._embedded, "browser_process_id", 0)
        if callable(pid):  # pragma: no cover - defensive against a property/method mix-up
            pid = pid()
        try:
            return int(pid or 0)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return 0

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
