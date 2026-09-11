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

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, Signal
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
from app.domain.browser import BrowserBackend, BrowserTab
from app.infrastructure.logging.logger import get_logger
from app.services.browser_service import extraction_script

logger = get_logger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})


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


class WebView2Pane(QWidget):
    """Toolbar plus a WebView2. Owns the controller; knows nothing about research."""

    urlChanged = Signal(str)
    # See the Qt pane: the pane asks, the window decides, and the state stays
    # in one place. It matters more here — keyboard focus inside WebView2
    # belongs to an Edge window, so a Qt shortcut never sees the keys at all.
    blurToggleRequested = Signal()

    backend: BrowserBackend = "webview2"

    def __init__(
        self,
        *,
        user_data_dir: Path,
        loader: Path,
        hide_for_sharing: bool,
        extensions: tuple[Path, ...] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
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
        layout.addLayout(self._stack, 1)

        self._start(user_data_dir=user_data_dir, loader=loader, hide_for_sharing=hide_for_sharing)

    # -- startup -------------------------------------------------------------

    def _start(self, *, user_data_dir: Path, loader: Path, hide_for_sharing: bool) -> None:
        extra = (sdk.SOFTWARE_DECODE_ARGUMENT,) if hide_for_sharing else ()
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
        view.apply_settings(dev_tools=False, context_menus=False, status_bar=False)
        view.on_navigation_starting(self._allow)
        view.on_new_window_requested(self._open_here)
        view.on_source_changed(self._refresh_url)
        view.on_title_changed(self._refresh_title)

        self._apply_mobile_user_agent()
        self._load_extensions()
        self._install_blur()
        self._sync_bounds()
        controller.set_visible(True)
        self._message.hide()
        logger.info("webview2.ready", browser_pid=view.browser_process_id)

        if self._pending_url:
            url, self._pending_url = self._pending_url, ""
            self.load_url(url)

    def _load_extensions(self) -> None:
        """Hand the runtime each configured extension folder, one at a time."""
        if not self._requested_extensions or self._controller is None:
            return
        profile = self._controller.webview.profile()
        if profile is None:
            self.addon_errors.append("This WebView2 runtime is too old to load browser extensions.")
            logger.warning("webview2.extensions_unsupported")
            return
        self._profile = profile
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

    # -- teardown ------------------------------------------------------------

    def shutdown(self) -> None:
        """Close the engine before the window goes.

        Explicit, not ``__del__``: the controller must be closed while its host
        window still exists, and interpreter shutdown is too late for that.
        """
        profile, self._profile = self._profile, None
        if profile is not None:
            profile.release()
        controller, self._controller = self._controller, None
        if controller is not None:
            controller.close()
        environment, self._environment = self._environment, None
        if environment is not None:
            environment.release()
