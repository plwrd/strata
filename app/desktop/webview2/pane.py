"""The research pane, backed by WebView2 instead of Qt WebEngine.

Same pane, same toolbar, same contract as ``app.desktop.browser_pane`` — the
service layer cannot tell the two apart, which is the point: ``EmbeddedSource``
drives either one. What differs is underneath, and only in ways the pane has to
absorb here:

* **The engine is not a widget.** WebView2 renders into a bare HWND, so the pane
  keeps a native child widget purely to own that handle and keeps the engine's
  bounds in step with it by hand. Qt never draws the page.
* **Everything is asynchronous, including existing.** The controller arrives some
  hundreds of milliseconds after the pane does, so a navigation asked for before
  then is queued, not lost.
* **Results come back JSON-encoded twice.** ``ExecuteScript`` returns the script's
  value as JSON; our extraction script already returns a JSON *string*, so the
  answer is a JSON string containing JSON. :func:`_unwrap` undoes exactly one
  layer, and then the shared ``decode_extraction`` sees what it sees from Qt.

If WebView2 cannot start, that is not an exception — it is a state. The pane
exists, says why it is empty, and the window falls back to the Qt pane rather
than the research feature disappearing.
"""

from __future__ import annotations

import ctypes
import json
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QMoveEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from app.desktop.browser_pane import MOBILE_USER_AGENT, PANE_TARGET_ID, blur_source
from app.desktop.webview2 import sdk
from app.desktop.webview2.com import ComError
from app.domain.browser import BrowserBackend, BrowserTab
from app.domain.errors import StrataError
from app.infrastructure.logging.logger import get_logger
from app.services.browser_service import extraction_script
from app.services.web_archive_service import (
    VAULT_HOST,
    VAULT_ORIGIN,
    Cookie,
    PageCapture,
    SaveResult,
    classify_media_url,
)

if TYPE_CHECKING:
    from app.services.job_service import JobService
    from app.services.web_archive_service import WebArchiveService

logger = get_logger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Ctrl+Alt+F: save the page (and its plain-file videos) into the encrypted
# archive. Ctrl+Alt+S: open the saved-pages library. Both are seen as a WebView2
# accelerator while the page has focus, and as a Qt shortcut (see MainWindow)
# while anything else in Strata does. The toolbar buttons are hidden by design;
# these chords are the way in.
ARCHIVE_HOTKEY = "Ctrl+Alt+F"
LIBRARY_HOTKEY = "Ctrl+Alt+S"
_VK_F = 0x46
_VK_S = 0x53
_VK_SHIFT, _VK_CONTROL, _VK_MENU = 0x10, 0x11, 0x12
_MAX_MEDIA_PER_PAGE = 20


def media_probe_source() -> str:
    """Find the page's ``<video>``/``<audio>`` sources, without touching them.

    One candidate per element: its playing source if that is a plain http(s)
    file, else its first ``<source>`` that is. An element that only plays a
    ``blob:`` (a player assembling a stream itself) or an HLS/DASH manifest is
    counted as *streamed*, so the save can say what it did not take.
    """
    return """
(() => {
  const files = [];
  let streamed = 0;
  const resolve = (u) => {
    try { return new URL(u, location.href).href; } catch (e) { return ""; }
  };
  const isStream = (u) => u.startsWith("blob:") || /\\.(m3u8|mpd)(\\?|#|$)/i.test(u);
  const pages = [];
  const host = location.hostname.replace(/^(www|m|mobile)\\./, "");
  const onX = host === "x.com" || host === "twitter.com";
  const onYouTube = host === "youtube.com" || host === "youtu.be";
  // The post a video belongs to, on X: its permalink is the link around <time>.
  const postOf = (el) => {
    const article = el.closest("article");
    const link = article && article.querySelector('a[href*="/status/"] time');
    return link ? link.closest("a").href.replace(/\\/(photo|video)\\/\\d+$/, "") : "";
  };
  document.querySelectorAll("video, audio").forEach((el) => {
    const candidates = [el.currentSrc, el.src,
      ...Array.from(el.querySelectorAll("source")).map((s) => s.src)]
      .filter(Boolean).map(resolve).filter(Boolean);
    const file = candidates.find((u) => /^https?:/i.test(u) && !isStream(u));
    if (file && !onYouTube) { if (!files.includes(file)) files.push(file); return; }
    if (!file && !candidates.some(isStream) && !onX && !onYouTube) return;
    // On an X timeline, only what is on screen: a feed holds dozens of videos
    // the user never looked at.
    const onPost = /\\/status\\/\\d+/.test(location.pathname);
    const box = el.getBoundingClientRect();
    if (onX && !onPost && !(box.bottom > 0 && box.top < innerHeight && box.width > 0)) return;
    streamed += 1;
    // YouTube is handled below: only a watch page names one video, and the
    // home feed's previews must not be sent off as "the" video.
    const page = onYouTube ? "" : onX ? (postOf(el) || (onPost ? location.href : ""))
      : location.href;
    if (page && !pages.includes(page)) pages.push(page);
  });
  // A YouTube watch or Shorts page is a video page even before it plays.
  if (onYouTube && /^\\/(watch|shorts\\/|embed\\/)|^\\/[\\w-]{11}$/.test(location.pathname)
      && !pages.includes(location.href)) pages.push(location.href);
  return JSON.stringify({ media: files, streamed, pages: pages.slice(0, 5),
                          ua: navigator.userAgent });
})();
"""


def _modifiers_down() -> tuple[bool, bool, bool]:
    """(ctrl, alt, shift) as the keyboard has them right now."""
    state = ctypes.windll.user32.GetKeyState
    return (
        bool(state(_VK_CONTROL) & 0x8000),
        bool(state(_VK_MENU) & 0x8000),
        bool(state(_VK_SHIFT) & 0x8000),
    )


def popup_free_select_source() -> str:
    """A ``<select>`` that opens in the page instead of in a window of its own.

    Chromium draws a dropdown's option list as a separate top-level window of
    the browser process. Under WebView2 that process is not ours, Windows will
    not let us exclude its windows from capture, and the ``CaptureGuard``
    therefore closes the popup the moment it appears — which would leave a
    mouse user with a dropdown that opens and vanishes. This turns the click
    into an in-page listbox instead (``size`` above one is rendered inline, in
    our window, under our affinity) and collapses it again on a choice, on
    Escape, or when focus leaves. Keyboard selection on a closed ``<select>``
    is untouched. Installed only while "hidden for sharing" is on.
    """
    return """
(() => {
  if (window.__strataSelect) return;
  window.__strataSelect = true;
  const expand = (el) => {
    if (el.multiple || el.disabled || el.dataset.strataOpen) return;
    el.dataset.strataOpen = "1";
    const hadSize = el.hasAttribute("size");
    const size = el.size;
    el.size = Math.min(Math.max(el.options.length, 2), 8);
    const collapse = () => {
      if (!el.dataset.strataOpen) return;
      delete el.dataset.strataOpen;
      if (hadSize) el.size = size; else el.removeAttribute("size");
      el.removeEventListener("change", collapse);
      el.removeEventListener("blur", collapse);
      el.removeEventListener("keydown", onKey);
    };
    const onKey = (e) => { if (e.key === "Escape" || e.key === "Enter") collapse(); };
    el.addEventListener("change", collapse);
    el.addEventListener("blur", collapse);
    el.addEventListener("keydown", onKey);
  };
  const isClosedSelect = (t) =>
    t instanceof HTMLSelectElement && !t.multiple && !t.disabled && !t.dataset.strataOpen;
  document.addEventListener("mousedown", (e) => {
    if (!isClosedSelect(e.target)) return;
    e.preventDefault();
    e.target.focus();
    expand(e.target);
  }, true);
  document.addEventListener("keydown", (e) => {
    if (!isClosedSelect(e.target)) return;
    const opens = e.key === " " || e.key === "F4" ||
      (e.altKey && (e.key === "ArrowDown" || e.key === "ArrowUp"));
    if (opens) { e.preventDefault(); expand(e.target); }
  }, true);
})();
"""


def _unwrap(raw: str) -> object:
    """``ExecuteScript``'s JSON envelope → the string the script returned.

    Qt hands back the script's value; WebView2 hands back that value JSON-encoded.
    Undo exactly one layer so both engines reach ``decode_extraction`` with the
    same thing. A result that is not the expected envelope is passed through
    untouched, so a malformed page fails in the one place that reports it.
    """
    try:
        unwrapped = json.loads(raw)
    except ValueError:
        return raw
    return unwrapped if isinstance(unwrapped, str) else raw


def _with_cookies(capture: PageCapture, raw: str | None) -> PageCapture:
    """Attach the cookies DevTools reported. The service matches them per URL."""
    try:
        listed = json.loads(raw or "{}").get("cookies") or []
    except (TypeError, ValueError, AttributeError):
        listed = []
    cookies = tuple(
        Cookie(
            name=str(item.get("name", "")),
            value=str(item.get("value", "")),
            domain=str(item.get("domain", "")),
            path=str(item.get("path", "/")) or "/",
            secure=bool(item.get("secure", False)),
        )
        for item in listed
        if isinstance(item, dict) and item.get("name")
    )
    return PageCapture(
        url=capture.url,
        title=capture.title,
        mhtml=capture.mhtml,
        media_urls=capture.media_urls,
        streamed_media=capture.streamed_media,
        user_agent=capture.user_agent,
        cookies=cookies,
        stream_pages=capture.stream_pages,
    )


class WebView2Pane(QWidget):
    """Toolbar plus a WebView2. Owns the controller; knows nothing about research."""

    urlChanged = Signal(str)
    # See the Qt pane: the pane asks, the window decides, and the state stays
    # in one place. It matters more here — keyboard focus inside WebView2
    # belongs to an Edge window, so a Qt shortcut never sees the keys at all.
    blurToggleRequested = Signal()
    # Worker thread -> Qt thread. Emitted from the save thread; the automatic
    # connection queues them, so the slots always run where widgets live.
    _archiveProgress = Signal(str)
    _archiveFinished = Signal(object)
    # Any thread (a layer can lock from a bridge worker) -> the Qt thread.
    clearTracesRequested = Signal()

    backend: BrowserBackend = "webview2"

    def __init__(
        self,
        *,
        user_data_dir: Path,
        loader: Path,
        hide_for_sharing: bool,
        extensions: tuple[Path, ...] = (),
        web_archive: WebArchiveService | None = None,
        jobs: JobService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._archive = web_archive
        self._jobs = jobs
        self._saves_in_flight = 0
        self._job_ids: list[str] = []
        self._thread_cancel: threading.Event | None = None
        # The vault answers only a document the *pane* opened. A web page
        # cannot navigate or embed its way in: requests for the vault host are
        # refused unless the pane itself just asked for it, or is already
        # showing a vault page (whose CSP allows no script to do anything).
        self._vault_requested = False
        self._controller: sdk.Controller | None = None
        self._environment: sdk.Environment | None = None
        self._profile: sdk.Profile | None = None
        self._requested_extensions = tuple(extensions)
        # What actually loaded, and what did not. Reported rather than assumed:
        # an extension folder can be moved or deleted between sessions, and a
        # user who thinks their ad blocker is running when it is not is worse
        # off than one who is told.
        self.loaded_addons: list[str] = []
        self.addon_errors: list[str] = []
        self._pending_url = ""
        self._failure = ""
        self._blur_enabled = False
        self._blur_amount = 12
        self._mobile = False
        # Decided at creation, like the software-decode flag: the popup guard
        # is installed into every document from the first one.
        self._hide_for_sharing = hide_for_sharing
        # Updated from the engine's own events, so `current()` answers without a
        # COM round-trip on a path the service calls from a read.
        self._url = ""
        self._title = ""

        self._address = QLineEdit(self)
        self._address.setPlaceholderText("https://…")
        self._address.returnPressed.connect(self._go)
        self._address.setAccessibleName("Page address")

        toolbar = QHBoxLayout()
        for label, tip, handler in (
            ("←", "Back", self._back),
            ("→", "Forward", self._forward),
            ("↻", "Reload", self._reload),
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

        self._save_button = QPushButton("Save", self)
        self._save_button.setToolTip(
            f"Save this page and its videos, encrypted, for offline reading ({ARCHIVE_HOTKEY})"
        )
        self._save_button.setAccessibleName("Save page to encrypted archive")
        self._save_button.clicked.connect(self.save_page)
        # Hidden by design: saving is driven by the ARCHIVE_HOTKEY chord, not a
        # visible button. Kept in the tree so its enabled state still tracks
        # whether the archive is available.
        self._save_button.hide()
        toolbar.addWidget(self._save_button)

        self._cancel_button = QPushButton("Cancel", self)
        self._cancel_button.setToolTip("Cancel the save in progress (queued saves continue)")
        self._cancel_button.setAccessibleName("Cancel saving")
        self._cancel_button.clicked.connect(self.cancel_save)
        self._cancel_button.hide()
        toolbar.addWidget(self._cancel_button)

        self._library_button = QPushButton("Saved", self)
        self._library_button.setToolTip(
            f"Open saved pages (encrypted, readable offline) ({LIBRARY_HOTKEY})"
        )
        self._library_button.setAccessibleName("Open saved pages")
        self._library_button.clicked.connect(self.open_library)
        # Hidden like Save: the library opens with the LIBRARY_HOTKEY chord.
        self._library_button.hide()
        toolbar.addWidget(self._library_button)
        if self._archive is None:
            self._save_button.setEnabled(False)
            self._library_button.setEnabled(False)

        self._archive_status = QLabel(self)
        self._archive_status.setWordWrap(True)
        self._archive_status.hide()
        self._archive_status_timer = QTimer(self)
        self._archive_status_timer.setSingleShot(True)
        self._archive_status_timer.timeout.connect(self._archive_status.hide)
        self.clearTracesRequested.connect(self.clear_traces)
        self._archiveProgress.connect(self._show_archive_status)
        self._archiveFinished.connect(self._on_archive_finished)

        # The engine draws into this widget's HWND. It is native and keeps Qt
        # from creating native ancestors it does not need — the page is not a
        # Qt surface and Qt must not try to compose it.
        self._host = QWidget(self)
        self._host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self._host.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self._host.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        self._message = QLabel("Starting the browser engine…", self)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)

        # Stacked, not hidden: the host widget must keep its size so the bounds
        # we hand the engine are the ones it will actually be shown at.
        self._stack = QStackedLayout()
        self._stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._stack.addWidget(self._message)
        self._stack.addWidget(self._host)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(toolbar)
        layout.addWidget(self._archive_status)
        layout.addLayout(self._stack, 1)

        self._start(user_data_dir=user_data_dir, loader=loader, hide_for_sharing=hide_for_sharing)

    # -- startup -------------------------------------------------------------

    def _start(self, *, user_data_dir: Path, loader: Path, hide_for_sharing: bool) -> None:
        # While hiding: take the engine off the DirectComposition present path
        # (the whole-window overlay that flickers and leaks — see
        # `app.desktop.capture_flags`) and off hardware video decode. The
        # always-on video-overlay flag is prepended by `create_environment`.
        extra = (
            (sdk.DISABLE_DIRECT_COMPOSITION_ARGUMENT, sdk.SOFTWARE_DECODE_ARGUMENT)
            if hide_for_sharing
            else ()
        )
        try:
            sdk.create_environment(
                user_data_folder=user_data_dir,
                loader=loader,
                extra_arguments=extra,
                # Decided once, at creation: the browser process is configured
                # here and there is no later switch. Off unless the user has
                # actually named an extension, so the ordinary pane runs with
                # the extension machinery disabled entirely.
                extensions_enabled=bool(self._requested_extensions),
                on_ready=self._on_environment,
            )
        except sdk.WebView2Unavailable as exc:
            self._fail(str(exc))

    def _on_environment(self, environment: sdk.Environment | None, error: str) -> None:
        if environment is None:
            self._fail(error)
            return
        self._environment = environment
        logger.info("webview2.environment_ready", browser=environment.browser_version)
        # winId() forces the native window into existence; the engine needs a
        # real HWND, not a promise of one.
        environment.create_controller(int(self._host.winId()), self._on_controller)

    def _on_controller(self, controller: sdk.Controller | None, error: str) -> None:
        if controller is None:
            self._fail(error)
            return
        self._controller = controller
        view = controller.webview
        # Every browser-process window this engine could open, switched off:
        # each would be a top-level window Strata cannot exclude from capture.
        view.apply_settings(
            dev_tools=False,
            context_menus=False,
            status_bar=False,
            script_dialogs=False,
            zoom_control=False,
            browser_accelerator_keys=False,
        )
        view.deny_permission_requests()
        if not view.cancel_downloads():
            logger.warning("webview2.downloads_not_cancellable")
        view.on_navigation_starting(self._allow)
        view.on_new_window_requested(self._open_here)
        view.on_source_changed(self._refresh_url)
        view.on_title_changed(self._refresh_title)
        controller.on_accelerator_key(self._on_accelerator)
        if self._archive is not None and self._environment is not None:
            view.serve(self._environment, f"{VAULT_ORIGIN}/*", self._serve_vault)

        self._apply_mobile_user_agent()
        self._quiet_profile()
        # Whatever the last session cached — it may have ended before a clear
        # finished — goes now, before anything new is browsed.
        self.clear_traces()
        self._load_extensions()
        self._install_blur()
        if self._hide_for_sharing:
            view.add_script_on_document_created(popup_free_select_source())
        self._sync_bounds()
        controller.set_visible(True)
        self._message.hide()
        logger.info("webview2.ready", browser_pid=view.browser_process_id)

        if self._pending_url:
            url, self._pending_url = self._pending_url, ""
            self.load_url(url)

    def _quiet_profile(self) -> None:
        """Turn off the profile-level UI that opens browser-process bubbles.

        Autofill and password-save prompts. An old runtime without a profile
        interface keeps its defaults; the capture guard closes those bubbles
        as they appear, which is the fallback for everything on this path.
        """
        profile = self._profile_interface()
        if profile is None:
            return
        try:
            profile.set_autofill(enabled=False)
        except ComError as exc:
            logger.warning("webview2.autofill_not_disabled", hr=exc.hr)

    def _profile_interface(self) -> sdk.Profile | None:
        """The view's profile, fetched once and released at shutdown."""
        if self._profile is None and self._controller is not None:
            self._profile = self._controller.webview.profile()
        return self._profile

    def _load_extensions(self) -> None:
        """Hand the runtime each configured extension folder, one at a time."""
        if not self._requested_extensions or self._controller is None:
            return
        profile = self._profile_interface()
        if profile is None:
            self.addon_errors.append("This WebView2 runtime is too old to load browser extensions.")
            logger.warning("webview2.extensions_unsupported")
            return
        for folder in self._requested_extensions:
            self._add_extension(profile, folder)

    def _add_extension(self, profile: sdk.Profile, folder: Path) -> None:
        if not folder.is_dir():
            # An unpacked extension is a directory with a manifest. Saying which
            # path is missing is the difference between a fixable message and
            # "extensions do not work".
            self.addon_errors.append(f"{folder} is not a folder.")
            logger.warning("webview2.extension_missing", path=str(folder))
            return

        def done(name: str, error: str) -> None:
            if error:
                self.addon_errors.append(f"{folder.name} did not load ({error}).")
                logger.warning("webview2.extension_failed", path=str(folder), error=error)
                return
            self.loaded_addons.append(name or folder.name)
            logger.info("webview2.extension_loaded", name=name or folder.name)

        profile.add_extension(folder, done)

    def _fail(self, reason: str) -> None:
        self._failure = reason
        self._message.setText(
            f"The WebView2 browser engine could not start.\n\n{reason}\n\n"
            "Research can still use the built-in pane or Chrome — choose one in Settings."
        )
        logger.warning("webview2.unavailable", reason=reason)

    @property
    def failure_reason(self) -> str:
        """Empty while starting or once ready; the reason when it will not."""
        return self._failure

    @property
    def ready(self) -> bool:
        return self._controller is not None

    @property
    def browser_process_id(self) -> int:
        """The ``msedgewebview2.exe`` to include in the capture sweep, or 0.

        Its popups and menus are that process's windows, not ours, so the
        window's own display affinity does not reach them.
        """
        if self._controller is None:
            return 0
        try:
            return self._controller.webview.browser_process_id
        except Exception:  # pragma: no cover - the engine died under us
            return 0

    # -- geometry ------------------------------------------------------------

    def _sync_bounds(self) -> None:
        """Keep the engine's rectangle on top of the host widget's.

        WebView2 measures in device pixels relative to the parent HWND; Qt's
        widget geometry is logical. On a scaled display the two differ by the
        device pixel ratio, and getting it wrong shows as a page drawn at the
        wrong size rather than as an error.
        """
        if self._controller is None:
            return
        ratio = self.devicePixelRatioF() or 1.0
        width = max(0, int(self._host.width() * ratio))
        height = max(0, int(self._host.height() * ratio))
        self._controller.set_bounds(0, 0, width, height)

    def resizeEvent(self, event: QResizeEvent) -> None:  # Qt override
        super().resizeEvent(event)
        self._sync_bounds()

    def moveEvent(self, event: QMoveEvent) -> None:  # Qt override
        super().moveEvent(event)
        if self._controller is not None:
            # Without this the engine places its popups against a stale screen
            # position, so a dropdown opens away from the control that owns it.
            self._controller.notify_moved()

    def showEvent(self, event: QShowEvent) -> None:  # Qt override
        super().showEvent(event)
        self._sync_bounds()

    # -- driving -------------------------------------------------------------

    def _go(self) -> None:
        typed = self._address.text().strip()
        if not typed:
            return
        if "://" not in typed:
            typed = f"https://{typed}"
        if urlsplit(typed).scheme.lower() in _ALLOWED_SCHEMES:
            self.load_url(typed)

    def load_url(self, url: str) -> None:
        """Navigate, or remember the request until the engine exists."""
        self._address.setText(url)
        if self._controller is None:
            self._pending_url = url
            return
        self._controller.webview.navigate(url)

    def current(self) -> BrowserTab:
        return BrowserTab(
            target_id=PANE_TARGET_ID,
            title=self._title[:300],
            url=self._url[:2000],
            active=True,
        )

    def _back(self) -> None:
        if self._controller is not None:
            self._controller.webview.go_back()

    def _forward(self) -> None:
        if self._controller is not None:
            self._controller.webview.go_forward()

    def _reload(self) -> None:
        if self._controller is not None:
            self._controller.webview.reload()

    # -- engine events -------------------------------------------------------

    def _allow(self, url: str) -> bool:
        """``http``/``https`` only — never ``strata://``, never the user's disk.

        The same rule the Qt pane enforces, and for the same reason: the app
        origin is where the bridge lives, and a page the web can steer must not
        be able to reach it.
        """
        allowed = urlsplit(url).scheme.lower() in _ALLOWED_SCHEMES
        if not allowed:
            logger.info("webview2.navigation_refused", scheme=urlsplit(url).scheme.lower())
        return allowed

    def _open_here(self, url: str) -> None:
        """A ``target=_blank`` opens in this pane, not in a window of its own.

        A new top-level browser window would be one Strata does not own and
        therefore cannot exclude from a recording, so there is no version of
        this that lets the popup through.
        """
        if urlsplit(url).scheme.lower() in _ALLOWED_SCHEMES:
            self.load_url(url)

    def _refresh_url(self) -> None:
        if self._controller is None:
            return
        self._url = self._controller.webview.source
        self._vault_requested = False
        self._address.setText(self._url)
        self.urlChanged.emit(self._url)

    def _refresh_title(self) -> None:
        if self._controller is not None:
            self._title = self._controller.webview.title

    # -- media blur ----------------------------------------------------------

    def set_blur(self, enabled: bool, amount: int) -> None:
        """Blur (or unblur) media. Qt thread only."""
        self._blur_enabled = bool(enabled)
        self._blur_amount = max(1, min(100, int(amount)))
        self._blur_button.setChecked(self._blur_enabled)
        self._blur_button.setText("Blurred" if self._blur_enabled else "Blur")
        self._install_blur()
        if self._controller is not None:
            # Re-registering only affects the *next* document; restyle this one.
            self._controller.webview.execute_script(
                blur_source(self._blur_enabled, self._blur_amount), lambda _result: None
            )

    def _install_blur(self) -> None:
        """Register the blur for every future document.

        WebView2 has no "remove script" that matches Qt's script collection, so
        re-registering stacks another copy. That is safe by construction: the
        script disables the previous instance (``window.__strataBlur.stop()``)
        before installing itself, so only the newest one observes.
        """
        if self._controller is None:
            return
        self._controller.webview.add_script_on_document_created(
            blur_source(self._blur_enabled, self._blur_amount)
        )

    # -- mobile mode ---------------------------------------------------------

    def set_mobile(self, enabled: bool) -> None:
        self._mobile = bool(enabled)
        if self._apply_mobile_user_agent() and self._url:
            self._reload()

    def is_mobile(self) -> bool:
        return self._mobile

    def _apply_mobile_user_agent(self) -> bool:
        if self._controller is None:
            return False
        agent = MOBILE_USER_AGENT if self._mobile else ""
        # "" restores the engine's own default, which is what leaving mobile
        # mode has to mean — there is no "remember the original" to restore.
        return self._controller.webview.set_user_agent(agent)

    # -- reading -------------------------------------------------------------

    def extract(self, deliver: Any) -> None:
        """Run the shared extraction in the page. Qt thread only."""
        if self._controller is None:
            deliver(None)
            return
        self._controller.webview.execute_script(
            extraction_script(), lambda raw: deliver(_unwrap(raw))
        )

    # -- browsing traces --------------------------------------------------------

    def clear_traces(self) -> None:
        """Drop the cache and history; keep cookies and site storage (sign-ins).

        Called at start, when a layer locks, and at shutdown: a page viewed
        while unlocked must not stay readable in the engine's cache afterwards.
        Qt thread only — from elsewhere, emit ``clearTracesRequested``.
        """
        profile = self._profile_interface()
        if profile is None:
            return

        def done(error: str) -> None:
            if error:
                logger.warning("webview2.clear_traces_failed", hr=error)
            else:
                logger.info("webview2.traces_cleared")

        try:
            profile.clear_browsing_data(sdk.BROWSING_TRACES, done)
        except ComError as exc:
            logger.warning("webview2.clear_traces_failed", hr=exc.hr)

    # -- encrypted archive (Ctrl+Alt+F) ---------------------------------------

    def _on_accelerator(self, key: int) -> bool:
        """The page has focus, so the shortcut arrives here, not at Qt."""
        if key not in (_VK_F, _VK_S):
            return False
        ctrl, alt, shift = _modifiers_down()
        if not (ctrl and alt) or shift:
            return False
        # Out of the COM callback before doing anything with the engine.
        QTimer.singleShot(0, self.save_page if key == _VK_F else self.open_library)
        return True

    def _show_archive_status(self, text: str, *, linger_ms: int = 0) -> None:
        self._archive_status.setText(text)
        self._archive_status.show()
        if linger_ms:
            self._archive_status_timer.start(linger_ms)
        else:
            self._archive_status_timer.stop()

    def open_library(self) -> None:
        """Show the saved pages. Works offline: nothing here touches the network."""
        if self._archive is None:
            return
        self._vault_requested = True
        self.load_url(f"{VAULT_ORIGIN}/")

    @property
    def _saving(self) -> bool:
        """True while any save is being captured, queued or running."""
        return self._saves_in_flight > 0

    def save_page(self) -> None:
        """Capture the page now; the save itself queues behind any running one.

        The capture (snapshot, video list, cookies) is taken at the moment of
        the press, so navigating on while earlier saves finish is safe.
        """
        if self._archive is None or self._controller is None:
            return
        host = urlsplit(self._url).hostname or ""
        if host == VAULT_HOST:
            self._show_archive_status("This is already a saved page.", linger_ms=4000)
            return
        if urlsplit(self._url).scheme.lower() not in _ALLOWED_SCHEMES:
            self._show_archive_status("Open a web page first, then save it.", linger_ms=4000)
            return
        self._saves_in_flight += 1
        self._refresh_save_controls()
        self._show_archive_status(self._queue_text("Saving page…"))
        url, title = self._url, self._title
        view = self._controller.webview
        view.execute_script(
            media_probe_source(), lambda raw: self._after_probe(view, url, title, raw)
        )

    def _queue_text(self, message: str) -> str:
        waiting = self._saves_in_flight - 1
        return message + (f" ({waiting} more queued)" if waiting > 0 else "")

    def _refresh_save_controls(self) -> None:
        self._cancel_button.setVisible(self._saving)

    def cancel_save(self) -> None:
        """Cancel the save that is running now; queued ones continue."""
        if self._jobs is not None and self._job_ids:
            self._jobs.cancel(self._job_ids[0])
        elif self._thread_cancel is not None:
            self._thread_cancel.set()
        self._show_archive_status("Cancelling…")

    def _after_probe(self, view: sdk.WebView, url: str, title: str, raw: str) -> None:
        try:
            probe = json.loads(str(_unwrap(raw)))
        except (TypeError, ValueError):
            probe = {}
        media = [
            str(item)
            for item in (probe.get("media") or [])[:_MAX_MEDIA_PER_PAGE]
            if classify_media_url(str(item)) == "file"
        ]
        streamed = int(probe.get("streamed") or 0)
        agent = str(probe.get("ua") or "")
        pages = [
            str(item)
            for item in (probe.get("pages") or [])[:5]
            if urlsplit(str(item)).scheme in _ALLOWED_SCHEMES
        ]

        def snapshot(result: str | None) -> None:
            if not result:
                self._finish_save(None, "The page could not be captured.")
                return
            try:
                mhtml = str(json.loads(result)["data"])
            except (KeyError, TypeError, ValueError):
                self._finish_save(None, "The page could not be captured.")
                return
            capture = PageCapture(
                url=url,
                title=title,
                mhtml=mhtml,
                media_urls=tuple(media),
                streamed_media=streamed,
                user_agent=agent,
                stream_pages=tuple(pages),
            )
            if not media and not pages:
                self._start_save(capture)
                return
            # The session the extractor and downloads need: cookies for the
            # video files and for the pages behind streamed video (YouTube's
            # bot check is passed by exactly this). Held in memory only.
            view.call_devtools(
                "Network.getCookies",
                json.dumps({"urls": [*media, *pages]}),
                lambda cookies: self._start_save(_with_cookies(capture, cookies)),
            )

        view.call_devtools("Page.captureSnapshot", json.dumps({"format": "mhtml"}), snapshot)

    def _start_save(self, capture: PageCapture) -> None:
        archive = self._archive
        assert archive is not None

        def attempt(progress: Any, cancelled: Any) -> object:
            try:
                return archive.save(capture, progress=progress, cancelled=cancelled)
            except StrataError as exc:
                return str(exc)
            except Exception:
                logger.exception("webview2.archive_save_failed")
                return "The page could not be saved."

        if self._jobs is None:
            # No job service (a bare pane): a plain thread, cancellable by event.
            cancel = threading.Event()
            self._thread_cancel = cancel

            def work_thread() -> None:
                outcome = attempt(
                    lambda m: self._archiveProgress.emit(self._queue_text(m)), cancel.is_set
                )
                self._archiveFinished.emit(outcome)

            threading.Thread(target=work_thread, name="web-archive-save", daemon=True).start()
            return

        # A background job: it shows in Strata's job list with progress and a
        # cancel button. Title and detail stay generic - job details are also
        # logged, and a page title is private content.
        def work(handle: Any) -> dict[str, Any]:
            def progress(message: str) -> None:
                match = re.search(r"(\d+)%", message)
                handle.progress(int(match.group(1)) / 100 if match else 0.0, message)
                self._archiveProgress.emit(self._queue_text(message))

            outcome = attempt(progress, lambda: handle.is_cancelled)
            self._archiveFinished.emit(outcome)
            return {"saved": isinstance(outcome, SaveResult) and not outcome.cancelled}

        record = self._jobs.submit(
            job_type="web_archive",
            title="Save page to encrypted archive",
            work=work,
            privacy="private",
        )
        self._job_ids.append(record.id)

    def _on_archive_finished(self, outcome: object) -> None:
        if self._job_ids:
            self._job_ids.pop(0)
        self._thread_cancel = None
        if isinstance(outcome, SaveResult):
            self._finish_save(outcome.summary(), "")
        else:
            self._finish_save(None, str(outcome))

    def _finish_save(self, message: str | None, error: str) -> None:
        self._saves_in_flight = max(0, self._saves_in_flight - 1)
        self._refresh_save_controls()
        if error:
            self._show_archive_status(f"Not saved: {error}", linger_ms=10000)
        elif self._saving:
            self._show_archive_status(self._queue_text(message or "Saved."))
        else:
            self._show_archive_status(message or "Saved.", linger_ms=8000)

    def _serve_vault(self, method: str, uri: str, range_header: str) -> sdk.VaultReply:
        """Answer a request for the vault — only for a document the pane opened."""
        from app.services.web_archive_service import BytesSource

        showing_vault = (urlsplit(self._url).hostname or "") == VAULT_HOST
        if self._archive is None or not (self._vault_requested or showing_vault):
            logger.info("webview2.vault_request_refused")
            return sdk.VaultReply(403, "Forbidden", "Cache-Control: no-store", BytesSource(b""))
        reply = self._archive.respond(method, uri, range_header)
        return sdk.VaultReply(reply.status, reply.reason, reply.header_block(), reply.body)

    # -- teardown ------------------------------------------------------------

    def shutdown(self) -> None:
        """Close the engine before the window goes.

        Explicit, not ``__del__``: the controller must be closed while its host
        window still exists, and interpreter shutdown is too late for that.
        """
        # Best effort: the clear is asynchronous and may not finish before the
        # engine closes, which is why it runs again at the next start.
        self.clear_traces()
        profile, self._profile = self._profile, None
        if profile is not None:
            profile.release()
        controller, self._controller = self._controller, None
        if controller is not None:
            controller.close()
        environment, self._environment = self._environment, None
        if environment is not None:
            environment.release()
