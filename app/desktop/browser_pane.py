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

import re
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
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

# Blur is applied twice over, because neither mechanism alone is enough.
#
# **A constructed stylesheet, adopted at document-creation.** This is the half
# that makes media blurred *before it is ever painted*: the rule is in the
# document before the parser has produced a single element, so there is no
# first frame where a photo is sharp and no flash when a lazy image loads. It
# is `new CSSStyleSheet()` + `adoptedStyleSheets` rather than an injected
# `<style>` because a strict `style-src` CSP blocks the element and does not
# block CSSOM.
#
# **Inline `filter` with `!important`, written through the CSSOM.** The
# stylesheet loses one specific fight: a site's own `img.css-xyz { filter: …
# !important }` is author-origin `!important` too, and beats us. An inline
# `!important` declaration beats every stylesheet rule there is. So the sheet
# gives instant, universal coverage and the inline pass wins where a page
# fights back.
#
# The observer has to watch more than added nodes. On a timeline like x.com,
# media arrives three ways a childList-only watcher misses:
#   - an avatar/thumbnail <div> is inserted first and its `background-image` is
#     set a tick later (an attribute change, not a child addition);
#   - an <img>/<video> is inserted empty and lazy-loads via a later `src`;
#   - the video player re-renders the <video> and strips our inline filter.
# So we also observe `style`/`src`/`srcset`/`poster` and *re-assert* the blur
# whenever it is missing — self-healing rather than one-shot. `consider` writes
# only when something is actually absent, so our own writes settle in one cycle
# instead of looping (we observe the style attribute, not our marker).
#
# It observes `document`, not `document.documentElement`. At document-creation
# there is no `<html>` yet, so the old code resolved its target to `null`, never
# attached, and left the throttled scroll handler as the only thing that ever
# swept — which is exactly why blur used to appear only once you scrolled.
# `document` exists from the first instruction and its subtree covers everything
# the parser goes on to build.
#
# `%s` placeholders are filled from Python as a bool literal, an int, and a JSON
# string, so a radius can never break out of the script.
_BLUR_SCRIPT = """
(() => {
  const ON = %s;
  const RADIUS = %s;
  const SEL = %s;
  const MARK = "data-strata-blur";
  const value = "blur(" + RADIUS + "px)";

  // -- the pre-paint half --------------------------------------------------
  // Adopted before the parser has built anything, so the rule is already in
  // force for the first element it creates. No JavaScript is in the loop here:
  // no observer latency, no lazy-load flash, nothing sharp waiting on a
  // callback.
  const sheetFor = () => {
    if (typeof CSSStyleSheet !== "function") return null;
    if (!("adoptedStyleSheets" in document)) return null;
    let sheet = window.__strataBlurSheet;
    if (!sheet) {
      try { sheet = new CSSStyleSheet(); } catch (e) { return null; }
      window.__strataBlurSheet = sheet;
    }
    return sheet;
  };

  const applySheet = () => {
    const sheet = sheetFor();
    if (!sheet) return;
    try {
      // Emptied rather than un-adopted when off: replacing the text is one
      // call and cannot leave a stale rule behind on a re-entry.
      sheet.replaceSync(ON ? SEL + "{filter:" + value + " !important}" : "");
    } catch (e) { return; }
    try {
      const adopted = document.adoptedStyleSheets || [];
      if (Array.prototype.indexOf.call(adopted, sheet) === -1) {
        document.adoptedStyleSheets = Array.prototype.concat.call([], adopted, sheet);
      }
    } catch (e) { /* older engine: the inline pass below still covers it */ }
  };

  // -- the specificity half ------------------------------------------------
  const consider = (el) => {
    if (!el || el.nodeType !== 1 || typeof el.matches !== "function") return;
    let hit = false;
    try { hit = el.matches(SEL); } catch (e) { return; }
    if (!ON) {
      if (el.hasAttribute(MARK)) {
        el.style.removeProperty("filter");
        el.style.removeProperty("transform");
        el.removeAttribute(MARK);
      }
      return;
    }
    if (!hit) return;
    // Only write what is missing; if everything is already set we return without
    // touching the DOM, so the style mutation we would have caused never fires.
    const needsFilter = el.style.getPropertyValue("filter").indexOf("blur(") === -1;
    const layered = el.tagName === "VIDEO" || el.tagName === "CANVAS";
    const needsLayer =
      layered && el.style.getPropertyValue("transform").indexOf("translateZ") === -1;
    if (!needsFilter && !needsLayer) return;
    if (needsFilter) el.style.setProperty("filter", value, "important");
    // A blurred <video> fights the GPU video overlay and flickers; promoting it
    // to its own composited layer settles the repaint, and re-asserting it heals
    // the case where the player rewrote the element's transform.
    if (needsLayer) el.style.setProperty("transform", "translateZ(0)", "important");
    el.setAttribute(MARK, "1");
  };

  const sweep = (root) => {
    try {
      consider(root);
      if (root.querySelectorAll) root.querySelectorAll(SEL).forEach(consider);
      if (!ON && root.querySelectorAll) {
        root.querySelectorAll("[" + MARK + "]").forEach(consider);
      }
    } catch (e) {}
  };

  let scheduled = false;
  const soon = () => {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => { scheduled = false; sweep(document); }, 120);
  };

  const prev = window.__strataBlur;
  if (prev) prev.stop();

  const onScroll = () => soon();
  const obs = new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === "childList") {
        for (const n of m.addedNodes) sweep(n);
      } else {
        consider(m.target);
      }
    }
  });

  const start = () => {
    applySheet();
    sweep(document);
    if (!ON) return;
    // `document`, not `document.documentElement`: at document-creation there is
    // no <html> to observe yet, and a subtree observer on the document sees it
    // being created along with everything under it.
    obs.observe(document, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["style", "src", "srcset", "poster"],
    });
    // Infinite scroll that recycles nodes may not fire a useful mutation for
    // every new card; a throttled sweep on scroll is the safety net.
    window.addEventListener("scroll", onScroll, { passive: true, capture: true });
  };

  window.__strataBlur = {
    stop: () => {
      obs.disconnect();
      window.removeEventListener("scroll", onScroll, { capture: true });
    },
  };

  start();
  // A same-document navigation can swap the adopted sheets out from under us,
  // and a page that rewrote its own <html> would lose the first sweep; both are
  // cheap to re-assert once the DOM is there.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => { applySheet(); sweep(document); },
      { once: true });
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


# Userscripts are written against Greasemonkey/Tampermonkey conventions, and
# the one that matters here is `@run-at`. Its default is `document-idle` — the
# DOM exists and the page has settled — which is what almost every script in
# the wild assumes. Injecting everything at document-creation instead would
# break the naive majority on its first line (`document.documentElement` is
# null that early) while helping only the few that deliberately patch globals
# before the page's own scripts run. So: honour what the file asks for, and
# default to what its author expected.
_RUN_AT = {
    "document-start": QWebEngineScript.InjectionPoint.DocumentCreation,
    "document-end": QWebEngineScript.InjectionPoint.DocumentReady,
    "document-idle": QWebEngineScript.InjectionPoint.Deferred,
}


def _run_at(source: str) -> QWebEngineScript.InjectionPoint:
    """Read ``// @run-at`` out of a userscript's metadata block."""
    # Only the head of the file: the metadata block is at the top by
    # convention, and a later mention is a comment about it, not a directive.
    head = source[:4096]
    match = re.search(r"@run-at\s+(document-start|document-end|document-idle)", head)
    if match is None:
        return QWebEngineScript.InjectionPoint.Deferred
    return _RUN_AT[match.group(1)]


def blur_source(enabled: bool, amount: int) -> str:
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


class HostBlockInterceptor(QWebEngineUrlRequestInterceptor):
    """Refuses requests to named hosts — a hosts-file ad blocker for the pane.

    This exists because Qt WebEngine cannot load an extension at any price:
    Chromium's extensions subsystem is not compiled into it and no flag adds
    it. Blocking at the network layer is the part of an ad blocker Qt *can*
    do, and it is the part that matters most — a tracker that never loads
    cannot run, cannot set a cookie and cannot see the page.

    Matching is by host suffix, so ``example.com`` also covers
    ``ads.example.com``. It is not EasyList: there are no cosmetic rules and no
    path patterns, and saying so is better than implying a completeness this
    does not have.

    The main frame is deliberately exempt. Blocking a navigation the user typed
    produces a blank pane with no explanation, which reads as a broken browser
    rather than as a blocklist doing its job; sub-resources are where ads and
    trackers actually live.
    """

    def __init__(self, hosts: Iterable[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._hosts = frozenset(host.lower().strip(".") for host in hosts if host.strip())
        self.blocked = 0

    @property
    def host_count(self) -> int:
        return len(self._hosts)

    def _is_blocked(self, host: str) -> bool:
        if not self._hosts or not host:
            return False
        host = host.lower().strip(".")
        if host in self._hosts:
            return True
        # Suffix match on a label boundary — `notevil.com` must not be caught
        # by a rule for `evil.com`.
        return any(host.endswith("." + blocked) for blocked in self._hosts)

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # Qt override
        if info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame:
            return
        if self._is_blocked(info.requestUrl().host()):
            info.block(True)
            self.blocked += 1


class BrowserPane(QWidget):
    """Toolbar plus view. Owns the page; knows nothing about research."""

    urlChanged = Signal(str)
    # The pane asks; the window decides. Blur state lives in the browser
    # service, so the toolbar button routes through the same place the hotkey
    # and the Research panel do rather than keeping a fourth copy.
    blurToggleRequested = Signal()

    # Which engine is behind this pane. Read by `EmbeddedSource` and reported
    # to the user, because it decides whether video plays (see ADR-0012).
    backend: BrowserBackend = "embedded"

    def __init__(
        self,
        profile: QWebEngineProfile,
        parent: QWidget | None = None,
        *,
        user_scripts: tuple[Path, ...] = (),
        blocked_hosts: tuple[str, ...] = (),
    ) -> None:
        super().__init__(parent)
        # What loaded and what did not, read by `EmbeddedSource.status` so the
        # Research panel can say so. A userscript that silently failed to load
        # looks exactly like one that is working.
        self.loaded_addons: list[str] = []
        self.addon_errors: list[str] = []
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

        # A blur control on the pane's own toolbar, not only in the panel and on
        # a hotkey. This is the one control that is always reachable: a
        # keyboard chord can be claimed by whatever has focus — the page, or in
        # the WebView2 pane an Edge window Qt never sees the keys from — and the
        # Research panel is on the other side of the splitter. A button beside
        # the address bar is a mouse click away from wherever the user is
        # looking.
        self._blur_button = QPushButton("Blur", self)
        self._blur_button.setToolTip("Blur images, video and canvas (Ctrl/Cmd+Shift+X)")
        self._blur_button.setAccessibleName("Blur media")
        self._blur_button.setCheckable(True)
        self._blur_button.clicked.connect(lambda _checked: self.blurToggleRequested.emit())
        toolbar.addWidget(self._blur_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(toolbar)
        layout.addWidget(self._view, 1)

        self._page.urlChanged.connect(self._on_url_changed)

        self._install_user_scripts(user_scripts)
        self._install_block_list(blocked_hosts)

    # -- extensions, as far as Qt can go --------------------------------------

    def _install_user_scripts(self, paths: tuple[Path, ...]) -> None:
        """Inject the user's ``.js`` files, honouring their ``@run-at``.

        The nearest thing Qt WebEngine has to an extension: a userscript runs
        in the page and can do most of what a content script does. What it
        cannot do is anything needing the ``chrome.*`` APIs — no background
        worker, no toolbar UI, no declarative blocking (see
        :class:`HostBlockInterceptor` for that half).

        Main world on purpose: a userscript that cannot see the page's own
        globals is not a userscript. That is the same trust decision the user
        makes by choosing the file.
        """
        for path in paths:
            try:
                source = path.read_text(encoding="utf-8")
            except OSError as exc:
                self.addon_errors.append(f"{path.name} could not be read ({exc.strerror}).")
                logger.warning("browser_pane.user_script_failed", path=str(path))
                continue
            script = QWebEngineScript()
            script.setName(f"strata-user-{path.stem}")
            script.setSourceCode(source)
            script.setInjectionPoint(_run_at(source))
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            self._page.scripts().insert(script)
            self.loaded_addons.append(path.name)
            logger.info("browser_pane.user_script_loaded", name=path.name)

    def _install_block_list(self, hosts: tuple[str, ...]) -> None:
        """Refuse sub-resource requests to these hosts, for this pane only."""
        if not hosts:
            self._interceptor = None
            return
        self._interceptor = HostBlockInterceptor(hosts, self)
        # Set on the *page*, not the profile: the app's own view shares neither
        # and must never have its requests filtered by a research setting.
        self._page.setUrlRequestInterceptor(self._interceptor)
        logger.info("browser_pane.block_list", hosts=len(hosts))

    @property
    def blocked_host_count(self) -> int:
        return self._interceptor.host_count if self._interceptor is not None else 0

    @property
    def blocked_request_count(self) -> int:
        return self._interceptor.blocked if self._interceptor is not None else 0

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
        self._blur_button.setChecked(self._blur_enabled)
        self._blur_button.setText("Blurred" if self._blur_enabled else "Blur")
        self._install_blur_script()
        self._page.runJavaScript(blur_source(self._blur_enabled, self._blur_amount))

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
        script.setSourceCode(blur_source(self._blur_enabled, self._blur_amount))
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        scripts.insert(script)
        self._blur_script = script

    def extract(self, deliver: Any) -> None:
        """Run the shared extraction in the page. Qt thread only."""
        self._page.runJavaScript(extraction_script(), 0, deliver)


class ResearchPane(Protocol):
    """What the service needs of an in-window pane, whatever draws it.

    Both the Qt pane above and the WebView2 pane in ``app.desktop.webview2``
    satisfy this. Stated as a protocol rather than a base class because the two
    share no implementation — one *is* a ``QWebEngineView``, the other owns an
    HWND — only a contract.
    """

    backend: BrowserBackend

    def current(self) -> BrowserTab: ...
    def is_mobile(self) -> bool: ...
    def isVisible(self) -> bool: ...
    def load_url(self, url: str) -> None: ...
    def set_blur(self, enabled: bool, amount: int) -> None: ...
    def set_mobile(self, enabled: bool) -> None: ...
    def extract(self, deliver: Any) -> None: ...


class EmbeddedSource(QObject):
    """Adapts the pane to :class:`app.services.browser_service.PageSource`.

    The service calls this from a worker thread; every touch of the pane is
    marshalled onto the Qt thread first, because neither engine may be driven
    from a thread that does not own it.
    """

    _readRequested = Signal()

    def __init__(self, pane: ResearchPane, show: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pane = pane
        self._show = show
        # An instance attribute, not a class one: which engine is behind the
        # pane is decided per window, and the service reports it to the user.
        self.backend: BrowserBackend = getattr(pane, "backend", "embedded")
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._result: ScrapedPage | None = None
        self._error = ""
        self._readRequested.connect(self._read_on_qt_thread, Qt.ConnectionType.QueuedConnection)

    # -- PageSource ----------------------------------------------------------

    def status(self) -> BrowserStatus:
        tab = self._pane.current()
        showing = self._pane.isVisible()
        # An engine can fail *after* the pane is built — WebView2's controller
        # arrives asynchronously, so "the runtime is missing" is caught before
        # the pane exists but "the controller would not start" is not. Saying
        # so here is the difference between a pane that looks merely empty and
        # one the user knows to switch away from.
        failure = str(getattr(self._pane, "failure_reason", "") or "")
        # "Add-ons" covers both shapes: real extensions in the Edge pane, and
        # the userscripts the Qt pane uses in their place. The status line says
        # what actually loaded either way.
        loaded: list[str] = list(getattr(self._pane, "loaded_addons", []))
        problems: list[str] = list(getattr(self._pane, "addon_errors", []))
        blocked_hosts = int(getattr(self._pane, "blocked_host_count", 0) or 0)
        if failure:
            detail = f"The browser pane could not start its engine. {failure}"
        elif showing:
            detail = f"The browser pane is open on {_host(tab.url) or 'a blank page'}."
        else:
            detail = "The browser pane is closed."
        if loaded:
            label = "Extensions" if self.backend == "webview2" else "User scripts"
            detail += f" {label}: {', '.join(loaded)}."
        if blocked_hosts:
            detail += f" Blocking {blocked_hosts} host(s)."
        # An extension the user added and that did not load is the case worth
        # being loud about — silence here reads as "it is working".
        if problems:
            detail += " " + " ".join(problems)
        return BrowserStatus(
            backend=self.backend,
            running=showing and not failure,
            # Only the Edge pane loads real extensions. A userscript is not
            # one, and claiming otherwise is how a user ends up wondering why
            # their extension's toolbar button never appeared.
            supports_extensions=self.backend == "webview2" and bool(loaded),
            mobile_mode=self._pane.is_mobile(),
            profile_path="",
            tab_count=1 if tab.url else 0,
            detail=detail,
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
