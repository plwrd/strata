"""The native window: the Strata view, and — when research asks for it — a
browser pane beside it.

The two are separate ``QWebEngineView``\ s on separate profiles, in a splitter.
They are not allowed to become one thing: the Strata view hosts the bridge and
refuses off-origin navigation; the browser pane goes anywhere on the web and has
no bridge at all (see ``app.desktop.browser_pane``).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow, QSplitter

from app.desktop.browser_pane import BrowserPane, EmbeddedSource, build_browser_profile
from app.desktop.screen_security import set_window_excluded_from_capture
from app.desktop.tray import TrayController, should_hide_to_tray
from app.desktop.webchannel import build_channel
from app.desktop.webengine import APP_URL, StrataPage, build_profile
from app.infrastructure.logging.logger import get_logger
from app.services.container import Services

logger = get_logger(__name__)

MINIMUM_SIZE = (1024, 640)
DEFAULT_SIZE = (1600, 980)
# Splitter split when the browser pane opens: the workspace keeps the larger half.
BROWSER_SPLIT = (960, 640)


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
        # Set true only when the user chooses Quit; a plain close hides to tray
        # instead when the tray is on, and must never tear the workspace down.
        self._quitting = False
        self._tray: TrayController | None = None

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
        self._browser_profile = build_browser_profile(
            services.paths.data_dir / "browser-pane",
            parent=QApplication.instance(),
        )
        self._browser_pane = BrowserPane(self._browser_profile, self)
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

        url = QUrl(dev_server) if dev_server else QUrl(APP_URL)
        logger.info("window.loading", dev=bool(dev_server))
        self._view.load(url)

        self._build_tray()

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
        self._tray.set_enabled(settings.minimize_to_tray)

    def apply_minimize_to_tray(self, enabled: bool) -> None:
        """Turn the tray behaviour on or off. Called from the settings bridge."""
        if self._tray is not None:
            self._tray.set_enabled(enabled)

    def start_hidden(self) -> bool:
        """Whether launch should skip showing the window (start_in_tray).

        Only honoured when the tray is actually up to restore it from — a first
        launch that hid the window with no way back would be a trap.
        """
        settings = self._services.settings.settings
        return settings.minimize_to_tray and settings.start_in_tray and self._tray_active()

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
        """Signal-style: exclude the whole Strata window from screen capture."""
        self._hide_for_sharing = enabled
        # winId() materialises the native HWND if needed; affinity needs it.
        if self.windowHandle() is None and not self.isVisible():
            return
        set_window_excluded_from_capture(self, enabled=enabled)

    def _reapply_hide_for_sharing(self) -> None:
        """Re-assert affinity after HWND / state changes (minimize, restore, …)."""
        if self.windowHandle() is None and not self.isVisible():
            return
        set_window_excluded_from_capture(self, enabled=self._hide_for_sharing)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # Sync from persisted settings and assert affinity now that HWND exists.
        self._hide_for_sharing = self._services.settings.settings.hide_for_sharing
        self._reapply_hide_for_sharing()

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
        self._services.browser.close()
        self._services.workspace.close()
        super().closeEvent(event)
