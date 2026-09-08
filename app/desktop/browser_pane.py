"""The embedded browser pane.

A second ``QWebEngineView``, beside the Strata UI in a splitter, on a profile of
its own. Everything about that separation is deliberate:

* **Its own profile.** The app profile is ``NoPersistentCookies`` and must stay
  that way — it hosts the bridge. This one persists cookies, because the point
  is that a sign-in survives a restart. They share no storage path, no cache
  and no cookie jar.
* **Its own navigation policy.** The app page blocks off-origin navigation; a
  browser obviously cannot. Instead this page enforces the inverse rule:
  ``http``/``https`` only, and never ``strata://`` — the app origin, where the
  bridge lives, must not be reachable from a page the web can steer.
* **No bridge.** No ``QWebChannel`` is installed on this page, so nothing it
  loads has a channel to Python even if it escapes its sandbox into the
  renderer.

Reading a page is asynchronous in Qt (``runJavaScript`` takes a callback) but
the service layer wants a blocking call it can make from a worker thread. The
adapter at the bottom bridges the two: it marshals the request onto the Qt
thread, waits on an ``Event``, and returns — so a slow page blocks the worker,
never the editor.
"""

from __future__ import annotations

import threading
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.domain.browser import BrowserBackend, BrowserStatus, BrowserTab, ScrapedPage
from app.domain.errors import ProviderError
from app.infrastructure.logging.logger import get_logger
from app.services.browser_service import decode_extraction, extraction_script

logger = get_logger(__name__)

READ_TIMEOUT_SECONDS = 20.0
_ALLOWED_SCHEMES = frozenset({"http", "https"})
# The only target the pane ever gets: it shows one page at a time, so the id is
# a constant rather than something the frontend has to track.
PANE_TARGET_ID = "pane"


class BrowserPanePage(QWebEnginePage):
    """A page that may go anywhere on the web, and nowhere else."""

    def acceptNavigationRequest(  # Qt override, hence the camelCase
        self, url: QUrl | str, kind: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        target = url if isinstance(url, QUrl) else QUrl(url)
        scheme = target.scheme().lower()
        if scheme in _ALLOWED_SCHEMES:
            return True
        if not is_main_frame:
            return False
        # `strata://` is the app's own origin, and `file://` is the user's disk.
        # A page that could reach either would be a boundary, not a browser.
        logger.info("browser_pane.navigation_refused", scheme=scheme)
        return False

    def javaScriptConsoleMessage(  # Qt override, hence the camelCase
        self, level: Any, message: str, line: int, source: str
    ) -> None:
        """Swallowed on purpose: a researched page's console is not our log."""
        return


def build_browser_profile(storage_path: Any, parent: QObject | None = None) -> QWebEngineProfile:
    """A persistent profile for the pane, sharing nothing with the app's."""
    profile = QWebEngineProfile("strata-browser", parent)
    profile.setPersistentStoragePath(str(storage_path))
    profile.setCachePath(str(storage_path / "cache"))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    # The one place in Strata where cookies persist — that *is* the sign-in.
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    return profile


class BrowserPane(QWidget):
    """Toolbar plus view. Owns the page; knows nothing about research."""

    urlChanged = Signal(str)

    def __init__(self, profile: QWebEngineProfile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = BrowserPanePage(profile, self)
        self._view = QWebEngineView(self)
        self._view.setPage(self._page)

        settings = self._page.settings()
        # A researched page gets a browser's capabilities, minus the ones that
        # would let it reach the user's disk or open windows Strata cannot see.
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False)

        self._address = QLineEdit(self)
        self._address.setPlaceholderText("https://…")
        self._address.returnPressed.connect(self._go)
        self._address.setAccessibleName("Page address")

        toolbar = QHBoxLayout()
        for label, tip, handler in (
            ("←", "Back", self._view.back),
            ("→", "Forward", self._view.forward),
            ("↻", "Reload", self._view.reload),
        ):
            button = QPushButton(label, self)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.setFixedWidth(32)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addWidget(self._address, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(toolbar)
        layout.addWidget(self._view, 1)

        self._page.urlChanged.connect(self._on_url_changed)

    # -- driving -------------------------------------------------------------

    def _go(self) -> None:
        typed = self._address.text().strip()
        if not typed:
            return
        if "://" not in typed:
            typed = f"https://{typed}"
        if urlsplit(typed).scheme.lower() in _ALLOWED_SCHEMES:
            self._view.load(QUrl(typed))

    def _on_url_changed(self, url: QUrl) -> None:
        self._address.setText(url.toString())
        self.urlChanged.emit(url.toString())

    def load_url(self, url: str) -> None:
        self._view.load(QUrl(url))
        self._address.setText(url)

    def current(self) -> BrowserTab:
        return BrowserTab(
            target_id=PANE_TARGET_ID,
            title=self._page.title()[:300],
            url=self._page.url().toString()[:2000],
            active=True,
        )

    def extract(self, deliver: Any) -> None:
        """Run the shared extraction in the page. Qt thread only."""
        self._page.runJavaScript(extraction_script(), 0, deliver)


class EmbeddedSource(QObject):
    """Adapts the pane to :class:`app.services.browser_service.PageSource`.

    The service calls this from a worker thread; every touch of the pane is
    marshalled onto the Qt thread first, because a ``QWebEnginePage`` may only
    be driven from the thread that owns it.
    """

    backend: BrowserBackend = "embedded"

    _readRequested = Signal()

    def __init__(self, pane: BrowserPane, show: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pane = pane
        self._show = show
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._result: ScrapedPage | None = None
        self._error = ""
        self._readRequested.connect(self._read_on_qt_thread, Qt.ConnectionType.QueuedConnection)

    # -- PageSource ----------------------------------------------------------

    def status(self) -> BrowserStatus:
        tab = self._pane.current()
        showing = self._pane.isVisible()
        return BrowserStatus(
            backend="embedded",
            running=showing,
            supports_extensions=False,
            profile_path="",
            tab_count=1 if tab.url else 0,
            detail=(
                f"The browser pane is open on {_host(tab.url) or 'a blank page'}."
                if showing
                else "The browser pane is closed."
            ),
        )

    def ensure_ready(self) -> BrowserStatus:
        # Called from the Qt thread (a bridge slot) — showing a widget from a
        # worker thread would be a crash waiting to happen, and nothing here
        # needs to.
        self._show(True)
        return self.status()

    def open_url(self, url: str) -> BrowserTab:
        self._show(True)
        self._pane.load_url(url)
        return self._pane.current()

    def tabs(self) -> list[BrowserTab]:
        return [self._pane.current()]

    def read(self, target_id: str) -> ScrapedPage:
        """Blocking, for the worker thread. One read at a time."""
        if target_id and target_id != PANE_TARGET_ID:
            raise ProviderError("The browser pane shows one page at a time.")
        with self._lock:
            self._done.clear()
            self._readRequested.emit()
            if not self._done.wait(READ_TIMEOUT_SECONDS):
                raise ProviderError("The page did not answer in time.")
            if self._error:
                raise ProviderError(self._error)
            if self._result is None:
                raise ProviderError("The page could not be read.")
            return self._result

    @Slot()
    def _read_on_qt_thread(self) -> None:
        self._result = None
        self._error = ""
        tab = self._pane.current()
        if not tab.url or urlsplit(tab.url).scheme.lower() not in _ALLOWED_SCHEMES:
            self._error = "There is no page open in the browser pane."
            self._done.set()
            return

        def deliver(raw: object) -> None:
            try:
                self._result = decode_extraction(
                    raw, fallback_url=tab.url, target_id=PANE_TARGET_ID
                )
            except ProviderError as exc:
                self._error = exc.message
            finally:
                self._done.set()

        # A page that never calls back (navigated away mid-read, crashed
        # renderer) must not strand the worker on its full timeout.
        QTimer.singleShot(int(READ_TIMEOUT_SECONDS * 1000) - 500, self._expire)
        self._pane.extract(deliver)

    @Slot()
    def _expire(self) -> None:
        if not self._done.is_set():
            self._error = "The page did not answer in time."
            self._done.set()

    def close(self) -> None:
        self._show(False)


def _host(url: str) -> str:
    return urlsplit(url).netloc if url else ""
