r"""The native window: the Strata view, and — when research asks for it — a
browser pane beside it.

The two are separate ``QWebEngineView``\ s on separate profiles, in a splitter.
They are not allowed to become one thing: the Strata view hosts the bridge and
refuses off-origin navigation; the browser pane goes anywhere on the web and has
no bridge at all (see ``app.desktop.browser_pane``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow, QSplitter

from app.desktop.auto_lock import AutoLock
from app.desktop.browser_pane import BrowserPane, EmbeddedSource, build_browser_profile
from app.desktop.taskbar import set_window_in_taskbar
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
# Toggles media blur in the browser pane. Application-scoped, so it fires while
# the researched page has keyboard focus — where a web-layer shortcut cannot.
BLUR_HOTKEY = "Ctrl+Shift+X"


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
        # Built before anything below can show the window (the tray can):
        # showEvent registers for session notifications through it.
        settings_now = services.settings
        self._auto_lock = AutoLock(
            lock_all=self._auto_lock_all,
            idle_minutes=lambda: settings_now.settings.auto_lock_minutes,
            on_system_lock=lambda: settings_now.settings.auto_lock_on_system_lock,
            parent=self,
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
        # for the page whenever an editable element has focus, so the native
        # shortcut alone went missing exactly when someone was working. The web
        # UI handles the same chord (see `shortcuts.ts`), and the pane carries a
        # button that no focus can intercept.
        blur_shortcut = QShortcut(QKeySequence(BLUR_HOTKEY), self, self._toggle_blur)
        blur_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._browser_pane.blurToggleRequested.connect(self._toggle_blur)

        url = QUrl(dev_server) if dev_server else QUrl(APP_URL)
        logger.info("window.loading", dev=bool(dev_server))
        self._view.load(url)

        self._build_tray()

    def _build_browser_pane(self, services: Services) -> BrowserPane:
        """The in-window research pane."""
        from PySide6.QtWidgets import QApplication

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

    def _toggle_blur(self) -> None:
        """Flip media blur in the pane.

        No support check here: the service owns that decision and answers on the
        same event either way, so the panel hears about a press that could not
        take effect instead of the key appearing to do nothing at all.
        """
        self._services.browser.toggle_blur()

    def _auto_lock_all(self) -> int:
        """Lock every private layer, then tell the UI to redraw them locked."""
        workspace = self._services.workspace
        if not workspace.is_open:
            return 0
        count = workspace.lock_all_layers()
        if count:
            self._services.watcher.announce("strata")
        return count

    def nativeEvent(self, event_type: Any, message: Any) -> Any:  # Qt override
        """Session lock/disconnect and suspend arrive as window messages."""
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG":
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            auto_lock = getattr(self, "_auto_lock", None)  # messages can precede __init__'s end
            if auto_lock is not None:
                auto_lock.handle_native(int(msg.message), int(msg.wParam or 0))
        return super().nativeEvent(event_type, message)

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

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._auto_lock.register(int(self.winId()))
        self._sync_taskbar()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
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
        self._auto_lock.unregister()
        self._services.browser.close()
        self._services.workspace.close()
        super().closeEvent(event)
