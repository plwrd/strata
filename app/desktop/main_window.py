"""The native window. It is a frame around the web view and nothing else."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow

from app.desktop.screen_security import set_window_excluded_from_capture
from app.desktop.webchannel import build_channel
from app.desktop.webengine import APP_URL, StrataPage, build_profile
from app.infrastructure.logging.logger import get_logger
from app.services.container import Services

logger = get_logger(__name__)

MINIMUM_SIZE = (1024, 640)
DEFAULT_SIZE = (1600, 980)


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
        self.setCentralWidget(self._view)

        if services.is_development:
            QShortcut(QKeySequence("F12"), self, self._toggle_devtools)
            QShortcut(QKeySequence("F5"), self, self._view.reload)

        url = QUrl(dev_server) if dev_server else QUrl(APP_URL)
        logger.info("window.loading", dev=bool(dev_server))
        self._view.load(url)

    def apply_hide_for_sharing(self, enabled: bool) -> None:
        """Signal-style: exclude the whole Strata window from screen capture."""
        set_window_excluded_from_capture(self, enabled=enabled)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # winId is only valid after the native window exists.
        self.apply_hide_for_sharing(
            self._services.settings.settings.hide_for_sharing
        )

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
        self._services.workspace.close()
        super().closeEvent(event)
