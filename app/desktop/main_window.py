r"""The native window: the Strata view, and — when research asks for it — a
browser pane beside it.

The two are separate ``QWebEngineView``\ s on separate profiles, in a splitter.
They are not allowed to become one thing: the Strata view hosts the bridge and
refuses off-origin navigation; the browser pane goes anywhere on the web and has
no bridge at all (see ``app.desktop.browser_pane``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QCloseEvent,
    QKeySequence,
    QPlatformSurfaceEvent,
    QShortcut,
    QShowEvent,
    QWindow,
)
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow, QSplitter, QWidget

from app.bootstrap import resource_root
from app.desktop.browser_pane import BrowserPane, EmbeddedSource, build_browser_profile
from app.desktop.screen_security import (
    CaptureGuard,
    CaptureState,
    capture_control_available,
    set_own_windows_excluded_from_capture,
    set_window_excluded_from_capture,
    set_windows_excluded_from_capture,
    weakest,
)
from app.desktop.taskbar import set_window_in_taskbar
from app.desktop.tray import TrayController, should_hide_to_tray
from app.desktop.webchannel import build_channel
from app.desktop.webengine import APP_URL, StrataPage, build_profile
from app.infrastructure.logging.logger import get_logger
from app.services.container import Services

if TYPE_CHECKING:
    # Imported for typing only: the WebView2 binding must not be loaded on a
    # machine that will never use it, but mypy still checks the pane against
    # the `ResearchPane` protocol the service expects.
    from app.desktop.webview2.pane import WebView2Pane

logger = get_logger(__name__)

MINIMUM_SIZE = (1024, 640)
DEFAULT_SIZE = (1600, 980)
# Splitter split when the browser pane opens: the workspace keeps the larger half.
BROWSER_SPLIT = (960, 640)
# Toggles media blur in the browser pane. Application-scoped, so it fires while
# the researched page has keyboard focus — where a web-layer shortcut cannot.
BLUR_HOTKEY = "Ctrl+Shift+X"
# Save the page in the browser pane to the encrypted archive (see
# `app.services.web_archive_service`). Kept in step with the pane's own copy.
ARCHIVE_HOTKEY = "Ctrl+Alt+F"
# How often to re-sweep every window this process owns while "hidden for
# sharing" is on. The per-window hooks below are the fast path; this is the net
# under them, for a window Qt never told us about (see `_sweep_own_windows`).
# It is a backstop, not the mechanism: `CaptureGuard` catches a new window on
# the message-loop turn it appears, because anything on a timer is late by up
# to its interval — which is how a dropdown opened and dismissed between ticks
# used to reach a recording.
CAPTURE_SWEEP_MS = 1500


class MainWindow(QMainWindow):
    def __init__(
        self,
        services: Services,
        frontend_root: Path,
        *,
        dev_server: str | None = None,
    ) -> None:
        super().__init__()
        self._services = services
        # Last value passed to the OS; settings toggle and window events share it.
        self._hide_for_sharing = services.settings.settings.hide_for_sharing
        # Installed here, before anything below can open a window: building the
        # tray can show the window, which re-enters the capture path.
        self._capture_guard = CaptureGuard()
        self._capture_guard.set_enabled(self._hide_for_sharing)
        self._capture_guard.watch(os.getpid())
        # What the OS last granted, as opposed to what the user asked for. The
        # settings bridge reports this so the dialog can say "hidden" only when
        # the window actually is — a privacy control that overstates itself is
        # worse than one that is missing, because the user acts on it.
        self._capture_state: CaptureState = (
            CaptureState.OFF if not self._hide_for_sharing else CaptureState.UNSUPPORTED
        )
        # Set true only when the user chooses Quit; a plain close hides to tray
        # instead when the tray is on, and must never tear the workspace down.
        self._quitting = False
        self._tray: TrayController | None = None
        self._hide_from_taskbar = services.settings.settings.hide_from_taskbar
        # Re-entrancy guard: hiding the taskbar button cycles the window, which
        # re-fires showEvent; the apply is idempotent, but this stops even the
        # wasted re-entry.
        self._syncing_taskbar = False

        # The window title must never contain a note title: a locked layer's
        # content must not leak through the task bar. It is static by design.
        self.setWindowTitle("Strata")
        self.setMinimumSize(*MINIMUM_SIZE)
        self.resize(*DEFAULT_SIZE)

        # The profile is parented to the application, not the window: Qt requires a
        # profile to outlive every page that uses it, and a profile owned by the
        # window can be destroyed while its own page is still alive.
        from PySide6.QtWidgets import QApplication

        self._profile, self._handler = build_profile(
            frontend_root,
            persistent_path=services.paths.data_dir / "webengine",
            parent=QApplication.instance(),
        )
        self._page = StrataPage(self._profile, self, allow_dev_server=dev_server)
        self._channel = build_channel(services, self)
        self._page.setWebChannel(self._channel)

        settings = self._page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,
            True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.WebGLEnabled,
            True,
        )

        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        self._view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.DefaultContextMenu
            if services.is_development
            else Qt.ContextMenuPolicy.NoContextMenu
        )

        # The browser pane: built now, hidden until research opens it. Nothing
        # is loaded into it until then, so an unused pane costs a widget, not a
        # renderer process.
        self._browser_pane = self._build_browser_pane(services)
        self._browser_pane.hide()

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.addWidget(self._view)
        self._splitter.addWidget(self._browser_pane)
        self._splitter.setCollapsible(0, False)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self.setCentralWidget(self._splitter)

        # Qt-land hands the pane to the Qt-free service, rather than the service
        # reaching for Qt.
        services.browser.attach(EmbeddedSource(self._browser_pane, self.show_browser_pane, self))

        if services.is_development:
            QShortcut(QKeySequence("F12"), self, self._toggle_devtools)
            QShortcut(QKeySequence("F5"), self, self._view.reload)

        # Three ways in, because one is not enough. Qt WebEngine claims a chord
        # for the page whenever an editable element has focus, and the WebView2
        # pane's keyboard focus belongs to an Edge window Qt never sees keys
        # from — so the native shortcut alone went missing exactly when someone
        # was working. The web UI handles the same chord (see `shortcuts.ts`),
        # and the pane carries a button that no focus can intercept.
        blur_shortcut = QShortcut(QKeySequence(BLUR_HOTKEY), self, self._toggle_blur)
        blur_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        request_blur = getattr(self._browser_pane, "blurToggleRequested", None)
        if request_blur is not None:
            request_blur.connect(self._toggle_blur)

        # Save to the encrypted archive. The WebView2 pane sees the chord itself
        # while the page has focus (an accelerator — Qt never gets those keys);
        # this covers the address bar and the rest of the window. Only the
        # WebView2 pane can save: it is the engine with a snapshot API.
        archive_shortcut = QShortcut(QKeySequence(ARCHIVE_HOTKEY), self, self._save_to_archive)
        archive_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)

        url = QUrl(dev_server) if dev_server else QUrl(APP_URL)
        logger.info("window.loading", dev=bool(dev_server))
        self._view.load(url)

        self._build_tray()

        # Capture exclusion is per top-level window, so a popup — a native
        # <select> dropdown, a menu, a dialog — appears as its own window and
        # would leak into a recording. Catch each as it is shown.
        from PySide6.QtWidgets import QApplication

        filter_app = QApplication.instance()
        assert filter_app is not None
        filter_app.installEventFilter(self)

        # Only where there is something to sweep. On Linux every call in this
        # path returns immediately (there is no per-window capture control to
        # make), so the timer would be a wake-up every 1.5 s, for the life of
        # the process, that cannot change anything — and on a laptop that is
        # battery spent on a no-op.
        self._capture_sweep = QTimer(self)
        self._capture_sweep.setInterval(CAPTURE_SWEEP_MS)
        self._capture_sweep.timeout.connect(self._tick_capture_sweep)
        if capture_control_available():
            self._capture_sweep.start()
        else:
            logger.info("window.capture_sweep_skipped", platform=sys.platform)

    def _build_browser_pane(self, services: Services) -> BrowserPane | WebView2Pane:
        """The research pane, on whichever engine the settings ask for.

        WebView2 is a *request*, not a guarantee — the runtime may be absent and
        the redistributable may not have shipped. Rather than lose research
        entirely, fall back to the Qt pane and let the status line say which
        engine is actually running; the difference decides whether video plays,
        so it is not something to hide.
        """
        from PySide6.QtWidgets import QApplication

        if services.settings.settings.browser_backend == "webview2":
            pane = self._try_webview2_pane(services)
            if pane is not None:
                return pane

        self._browser_profile = build_browser_profile(
            services.paths.data_dir / "browser-pane",
            parent=QApplication.instance(),
        )
        settings = services.settings.settings
        return BrowserPane(
            self._browser_profile,
            self,
            # Qt cannot load an extension at all, so these are what it has
            # instead: injected user scripts and a host blocklist.
            user_scripts=tuple(Path(script) for script in settings.browser_user_scripts),
            blocked_hosts=tuple(settings.browser_blocked_hosts),
        )

    def _try_webview2_pane(self, services: Services) -> WebView2Pane | None:
        """Build the WebView2 pane, or None with the reason logged.

        Imported here rather than at module scope so a machine without WebView2
        — or a platform without it at all — does not pay for the binding just by
        opening a window.
        """
        try:
            from app.desktop.webview2 import sdk
            from app.desktop.webview2.pane import WebView2Pane
        except ImportError as exc:  # pragma: no cover - the package is committed
            logger.warning("browser_pane.webview2_import_failed", error=str(exc))
            return None

        loader = sdk.loader_path(resource_root())
        if loader is None:
            logger.warning("browser_pane.webview2_loader_missing")
            return None
        return WebView2Pane(
            user_data_dir=services.paths.data_dir / "webview2-pane",
            loader=loader,
            hide_for_sharing=self._hide_for_sharing,
            extensions=tuple(
                Path(folder) for folder in services.settings.settings.browser_extensions
            ),
            web_archive=services.web_archive,
            parent=self,
        )

    def _toggle_blur(self) -> None:
        """Flip media blur in the pane.

        No support check here: the service owns that decision and answers on the
        same event either way, so the panel hears about a press that could not
        take effect instead of the key appearing to do nothing at all.
        """
        self._services.browser.toggle_blur()

    def _save_to_archive(self) -> None:
        save = getattr(self._browser_pane, "save_page", None)
        if callable(save) and self._browser_pane.isVisible():
            save()
        else:
            logger.info("web_archive.shortcut_unavailable")

    def show_browser_pane(self, visible: bool) -> None:
        """Open or close the browser pane. Qt thread only."""
        if visible == self._browser_pane.isVisible():
            return
        self._browser_pane.setVisible(visible)
        if visible:
            self._splitter.setSizes(list(BROWSER_SPLIT))
        logger.info("browser_pane.visible", visible=visible)

    def _build_tray(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        assert isinstance(app, QApplication)  # a widget cannot exist without one
        icon = self.windowIcon()
        if icon.isNull():
            icon = app.windowIcon()
        settings = self._services.settings.settings
        self._tray = TrayController(
            icon=icon,
            window=self,
            on_show=self.show_from_tray,
            on_quit=self.request_quit,
            parent=app,
        )
        self._tray.set_enabled(settings.minimize_to_tray or settings.hide_from_taskbar)

    def apply_minimize_to_tray(self, enabled: bool) -> None:
        """Turn the tray behaviour on or off. Called from the settings bridge."""
        self._refresh_tray_enabled()

    def apply_hide_from_taskbar(self, enabled: bool) -> None:
        """Show or drop the taskbar button. Called from the settings bridge."""
        self._hide_from_taskbar = enabled
        # No taskbar button means the tray is the only way back — keep it up.
        self._refresh_tray_enabled()
        self._sync_taskbar()

    def _refresh_tray_enabled(self) -> None:
        if self._tray is None:
            return
        settings = self._services.settings.settings
        self._tray.set_enabled(settings.minimize_to_tray or settings.hide_from_taskbar)

    def _sync_taskbar(self) -> None:
        if self._syncing_taskbar:
            return
        if self.windowHandle() is None and not self.isVisible():
            return  # no native window yet; showEvent will apply it
        self._syncing_taskbar = True
        try:
            set_window_in_taskbar(self, shown=not self._hide_from_taskbar)
        finally:
            self._syncing_taskbar = False
        # Cycling the window to change its taskbar style can drop the capture
        # affinity; re-assert it so "hidden for sharing" survives the toggle.
        self._reapply_hide_for_sharing()

    def start_hidden(self) -> bool:
        """Whether launch should skip showing the window (start_in_tray).

        Only honoured when the tray is actually up to restore it from — a first
        launch that hid the window with no way back would be a trap.
        """
        settings = self._services.settings.settings
        return settings.start_in_tray and self._tray_active()

    def show_from_tray(self) -> None:
        """Bring the window back from the tray and give it focus."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def request_quit(self) -> None:
        """A real quit, from the tray menu — bypasses hide-to-tray."""
        self._quitting = True
        self.close()

    def _tray_active(self) -> bool:
        return self._tray is not None and self._tray.enabled

    def apply_hide_for_sharing(self, enabled: bool) -> None:
        """Signal-style: exclude every Strata window from screen capture."""
        self._hide_for_sharing = enabled
        self._reapply_hide_for_sharing()

    def _top_level_windows(self) -> list[QWidget | QWindow]:
        """Every surface Qt currently has a native window for.

        Both lists are needed. ``topLevelWidgets()`` misses anything Qt models as
        a bare ``QWindow`` — which is what Qt WebEngine uses for some of its own
        popups — and those were the surfaces still reaching a recording.
        """
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        others = app.topLevelWidgets() if isinstance(app, QApplication) else []
        seen: set[int] = set()
        windows: list[QWidget | QWindow] = []
        for widget in [self, *others]:
            if (
                isinstance(widget, QWidget)
                and widget.isWindow()
                and id(widget) not in seen
                and (widget.windowHandle() is not None or widget.isVisible())
            ):
                seen.add(id(widget))
                windows.append(widget)
        # A widget's own QWindow resolves to the same HWND as the widget, so mark
        # it seen rather than setting the same affinity twice.
        for surface in windows:
            handle = surface.windowHandle() if isinstance(surface, QWidget) else None
            if handle is not None:
                seen.add(id(handle))
        for window in QApplication.topLevelWindows():
            # Visible only: `winId()` would *create* the native window for one
            # that has none yet. A popup that exists but has not been shown is
            # the sweep's job (`_sweep_own_windows`), not this list's.
            if id(window) not in seen and window.isVisible():
                seen.add(id(window))
                windows.append(window)
        return windows

    def capture_state(self) -> CaptureState:
        """What screen-capture protection this window actually has right now."""
        return self._capture_state

    def _reapply_hide_for_sharing(self) -> None:
        """Re-assert affinity across every window after an HWND / state change.

        Not just the main window: a minimize/restore, the taskbar ex-style cycle,
        or a newly shown dialog can each leave a surface uncovered, so every
        current top-level window is re-excluded.

        The reported state is the weakest of two answers: our own windows,
        and whatever the engine process has on screen that is not ours to
        exclude. A WebView2 popup or a Chrome window in a recording is a
        ``failed`` protection even while every Strata window is excluded.
        """
        state = set_windows_excluded_from_capture(
            self._top_level_windows(), enabled=self._hide_for_sharing
        )
        uncovered = self._sweep_own_windows()
        if self._hide_for_sharing and uncovered:
            state = weakest([state, CaptureState.FAILED])
        self._set_capture_state(CaptureState.OFF if not self._hide_for_sharing else state)

    def _set_capture_state(self, state: CaptureState) -> None:
        """Record the state, logging only when it actually changes.

        The affinity is re-asserted on every activation change and on a 1.5 s
        heartbeat; logging each assertion buried the one line that matters —
        the moment protection was lost — under thousands that said it was fine.
        """
        if state is self._capture_state:
            return
        self._capture_state = state
        if state in (CaptureState.FAILED, CaptureState.UNSUPPORTED):
            logger.warning("window.capture_protection_lost", state=state.value)
        else:
            logger.info("window.capture_protection", state=state.value)

    def _watch_engine_process(self) -> None:
        """Hook the research engine's process once it has one.

        WebView2 renders in a browser process of its own, and the Chrome backend
        is a whole separate browser; in both cases the menus and dropdowns are
        that process's windows, which the hook on ours cannot see. Asking the
        browser service rather than the pane means the Chrome backend gets the
        same instant coverage the embedded engines get — before, it had only
        the 1.5 s sweep, so a menu opened and dismissed between ticks was never
        covered at all.

        The pid is not known until the engine is up, so this is checked as part
        of the sweep rather than at construction.
        """
        pid = self._services.browser.engine_process_id
        if pid > 0:
            self._capture_guard.watch(
                pid, popups_only=self._services.browser.engine_popups_closable
            )

    def _prune_dead_engines(self) -> None:
        """Unhook engine processes that have exited.

        Process ids are reused. A hook left on a dead WebView2 or Chrome would
        eventually fire for whatever inherits the number, and Strata would then
        be setting a capture affinity on another application's windows.
        """
        self._capture_guard.drop_dead_processes(keep=os.getpid())

    def _sweep_own_windows(self) -> int:
        """Exclude every window this process owns, whatever created it.

        The Qt-object hooks only see what Qt models. The bundled Chromium makes
        Win32 windows of its own for menus and dropdowns that never surface as a
        ``QWidget`` or a ``QWindow``, so they were never excluded and showed up in
        a recording on their own. A PID sweep does not need to know what a window
        is, only that it is ours.

        Returns how many *engine* windows were on screen uncovered — windows of
        another process, which no sweep of ours can exclude (see
        ``screen_security``); the caller folds that into the reported state.
        """
        self._capture_guard.set_enabled(self._hide_for_sharing)
        self._prune_dead_engines()
        self._watch_engine_process()
        set_own_windows_excluded_from_capture(enabled=self._hide_for_sharing)
        # The research engine may render in a process of its own — WebView2's
        # browser process, or a launched Chrome. Its menus and dropdowns are
        # that process's windows, so a sweep of ours cannot reach them.
        return int(self._services.browser.apply_capture_exclusion(self._hide_for_sharing) or 0)

    def _tick_capture_sweep(self) -> None:
        """Heartbeat: only while hiding.

        The full re-apply, not the own-window sweep alone, so the reported
        state *recovers* once an engine popup that downgraded it has gone.
        Steady state is reads only — a window already at the right affinity is
        never written again.
        """
        if self._hide_for_sharing:
            self._reapply_hide_for_sharing()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # Cheap early-outs first — this runs for every app event.
        if not self._hide_for_sharing:
            return super().eventFilter(obj, event)
        kind = event.type()
        if kind == QEvent.Type.PlatformSurface and isinstance(obj, QWindow):
            # The earliest possible moment: the native window now exists and has
            # not been shown, so the affinity is in place before its first frame.
            # It also covers a bare QWindow — which is what Qt WebEngine uses for
            # some of its popups — where the QWidget branch below never fires.
            created = QPlatformSurfaceEvent.SurfaceEventType.SurfaceCreated
            if isinstance(event, QPlatformSurfaceEvent) and event.surfaceEventType() == created:
                self._note_capture_state(set_window_excluded_from_capture(obj, enabled=True))
        elif kind == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            self._note_capture_state(set_window_excluded_from_capture(obj, enabled=True))
        return super().eventFilter(obj, event)

    def _note_capture_state(self, state: CaptureState) -> None:
        """Fold one window's result into the reported state.

        A new surface that could not be covered downgrades the answer: the
        status has to describe the weakest window on screen, not the last one
        that happened to succeed.
        """
        self._set_capture_state(weakest([self._capture_state, state]))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # Sync from persisted settings and assert affinity now that HWND exists.
        self._hide_for_sharing = self._services.settings.settings.hide_for_sharing
        self._reapply_hide_for_sharing()
        self._sync_taskbar()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.WindowStateChange,
            QEvent.Type.ActivationChange,
        ):
            self._reapply_hide_for_sharing()
        # A minimize, with the tray on, means "get out of the taskbar" — hide to
        # the tray instead of shrinking to a taskbar button. Deferred a tick so
        # the state change finishes before the window vanishes under it.
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self._tray_active()
            and self.isMinimized()
        ):
            QTimer.singleShot(0, self._tray.hide_to_tray)  # type: ignore[union-attr]

    def _toggle_devtools(self) -> None:
        """Developer tools exist only in development builds."""
        if not self._services.is_development:
            return
        if self._page.devToolsPage() is None:
            tools = StrataPage(self._profile, self)
            self._page.setDevToolsPage(tools)
            view = QWebEngineView()
            view.setPage(tools)
            view.setWindowTitle("Strata — developer tools")
            view.resize(1100, 700)
            view.show()
            self._devtools_view = view

    def closeEvent(self, event: QCloseEvent) -> None:
        # Closing with the tray on hides the window; it does not quit. The
        # workspace and browser stay up behind the tray icon until the user
        # picks Quit from its menu.
        if should_hide_to_tray(tray_enabled=self._tray_active(), is_quitting=self._quitting):
            event.ignore()
            self._tray.hide_to_tray()  # type: ignore[union-attr]
            return
        # The browser goes with the window that justified it: a debugging port
        # (or a signed-in pane) must not outlive Strata — THREAT_MODEL.md T-34.
        if self._tray is not None:
            self._tray.dispose()
        # The WebView2 controller has to be closed while its host window still
        # exists; leaving it to teardown means closing it against a dead HWND.
        self._capture_guard.dispose()
        shutdown = getattr(self._browser_pane, "shutdown", None)
        if callable(shutdown):
            shutdown()
        self._services.browser.close()
        self._services.workspace.close()
        super().closeEvent(event)
