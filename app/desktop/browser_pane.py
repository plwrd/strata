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
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
)
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

# Media to blur. Beyond <img>/<video>/<canvas>, sites (x.com among them) render
# avatars and thumbnails as <div style="background-image:…">, and photos/players
# as <picture>/<iframe> — all of which the old img-only rule missed. Bare <svg>
# is left out on purpose: blurring every icon makes a page look broken, and
# avatars are not SVGs.
_BLUR_SELECTOR = "img,video,canvas,picture,iframe,[style*='background-image']"

# The blur is applied as an *inline* ``filter`` with ``!important``, set through
# the CSSOM, rather than as an injected ``<style>``. Two reasons the stylesheet
# approach failed on real sites:
#   1. Specificity — a site's own ``img.css-xyz { filter: … !important }`` beats
#      a bare ``img !important`` rule, so the page's filter won and ours didn't.
#      An inline ``!important`` declaration beats every stylesheet rule.
#   2. CSP — a strict ``style-src`` blocks an injected ``<style>`` element
#      outright, so nothing applied at all. Programmatic CSSOM writes
#      (``el.style.setProperty``) are not subject to ``style-src``.
# A MutationObserver re-applies to nodes a single-page app adds after load.
# ``%s`` placeholders are filled from Python as a bool literal, an int, and a
# JSON string, so a radius can never break out of the script.
_BLUR_SCRIPT = """
(() => {
  const ON = %s;
  const RADIUS = %s;
  const SEL = %s;
  const MARK = "data-strata-blur";
  const value = "blur(" + RADIUS + "px)";

  const apply = (el) => {
    if (!el || el.nodeType !== 1 || typeof el.matches !== "function") return;
    if (!el.matches(SEL)) return;
    if (ON) {
      el.style.setProperty("filter", value, "important");
      // A blurred <video> fights the GPU video overlay and flickers; promoting
      // it to its own composited layer settles the repaint.
      if (el.tagName === "VIDEO" || el.tagName === "CANVAS") {
        el.style.setProperty("transform", "translateZ(0)", "important");
      }
      el.setAttribute(MARK, "1");
    } else if (el.hasAttribute(MARK)) {
      el.style.removeProperty("filter");
      el.style.removeProperty("transform");
      el.removeAttribute(MARK);
    }
  };

  const sweep = (root) => {
    try {
      if (root.nodeType === 1 && root.matches && root.matches(SEL)) apply(root);
      if (root.querySelectorAll) root.querySelectorAll(SEL).forEach(apply);
    } catch (e) {}
  };

  const run = () => {
    sweep(document);
    if (window.__strataBlurObs) {
      window.__strataBlurObs.disconnect();
      window.__strataBlurObs = null;
    }
    if (!ON) return;
    // childList+subtree only: a single-page app replaces media nodes on render,
    // and the new node is caught here. We do not observe attributes — our own
    // inline write would retrigger the observer and loop.
    const obs = new MutationObserver((muts) => {
      for (const m of muts) for (const n of m.addedNodes) sweep(n);
    });
    const target = document.documentElement || document.body;
    if (target) {
      obs.observe(target, { childList: true, subtree: true });
      window.__strataBlurObs = obs;
    }
  };

  run();
  // documentElement is bare at document-creation; finish once the DOM exists.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run, { once: true });
  }
})();
""".strip()

# An Android Chrome user-agent. Setting it on the pane's profile makes sites
# serve their mobile/touch layout; combined with the narrow pane width that is
# what "mobile mode" means. True synthetic touch events are a process-global
# Chromium flag (see app/desktop/application.py), applied from the next launch.
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
)


def _blur_source(enabled: bool, amount: int) -> str:
    import json

    radius = max(1, min(100, int(amount)))
    return _BLUR_SCRIPT % (
        "true" if enabled else "false",
        radius,
        json.dumps(_BLUR_SELECTOR),
    )


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

        # Media blur, off until asked. The script is re-registered whenever the
        # state changes so every future navigation is blurred from first paint;
        # the current page is restyled at once by running the same source.
        self._blur_enabled = False
        self._blur_amount = 12
        self._blur_script: QWebEngineScript | None = None
        self._install_blur_script()

        # Mobile mode swaps the profile's user-agent; remember the default so it
        # can be restored. The profile is the pane's own, so this affects nothing
        # else in Strata.
        self._profile = profile
        self._default_user_agent = profile.httpUserAgent()
        self._mobile = False

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

    # -- media blur ----------------------------------------------------------

    def set_blur(self, enabled: bool, amount: int) -> None:
        """Blur (or unblur) images, video and canvas. Qt thread only.

        Two effects: re-register the injected script so the *next* page loads
        already blurred, and restyle the page open *now* so the change is
        immediate rather than waiting for a navigation.
        """
        self._blur_enabled = bool(enabled)
        self._blur_amount = max(1, min(100, int(amount)))
        self._install_blur_script()
        self._page.runJavaScript(_blur_source(self._blur_enabled, self._blur_amount))

    def set_mobile(self, enabled: bool) -> None:
        """Serve sites their mobile layout by swapping the user-agent. Qt thread only.

        Reloads so the open page re-requests under the new UA; a blank pane just
        adopts it for the next navigation.
        """
        self._mobile = bool(enabled)
        self._profile.setHttpUserAgent(
            MOBILE_USER_AGENT if self._mobile else self._default_user_agent
        )
        if self._page.url().isValid() and not self._page.url().isEmpty():
            self._view.reload()

    def is_mobile(self) -> bool:
        return self._mobile

    def _install_blur_script(self) -> None:
        scripts = self._page.scripts()
        if self._blur_script is not None:
            scripts.remove(self._blur_script)
        script = QWebEngineScript()
        script.setName("strata-blur")
        script.setSourceCode(_blur_source(self._blur_enabled, self._blur_amount))
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        scripts.insert(script)
        self._blur_script = script

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
            mobile_mode=self._pane.is_mobile(),
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

    def apply_blur(self, enabled: bool, amount: int) -> None:
        # Called from the Qt thread (a bridge slot or the window's own shortcut),
        # never a worker — the read path is the only thing that crosses threads.
        self._pane.set_blur(enabled, amount)

    def apply_mobile(self, enabled: bool) -> None:
        self._pane.set_mobile(enabled)

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
