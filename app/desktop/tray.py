"""System tray icon, and minimize-to-tray.

When ``settings.minimize_to_tray`` is on, Strata keeps a tray icon and treats
closing or minimizing the window as *hiding* it: the window leaves the taskbar
but the process keeps running, and the workspace stays open behind it. Quitting
is then a deliberate act from the tray menu.

What this is **not**: the process stays fully visible to the OS. A tray app that
also hid itself from the process list would be indistinguishable from a rootkit,
and there is no honest way to do it from user space anyway. Hiding the *window*
from the taskbar is the real, supported thing; hiding the *process* is not on
offer, by design.

The window/quit wiring lives in ``MainWindow``; this module owns only the icon,
its menu, and the notify-once-on-first-hide courtesy. The one piece of policy —
"should this close hide to the tray, or really quit?" — is a pure function so it
can be tested without a display.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


def should_hide_to_tray(*, tray_enabled: bool, is_quitting: bool) -> bool:
    """Does closing the window hide it, or actually quit?

    Hide only when the tray is available to restore it from *and* the user did
    not ask to quit. A close with no tray to fall back to must always really
    close — hiding a window the user can never get back is a trap.
    """
    return tray_enabled and not is_quitting


class TrayController:
    """The tray icon and its menu. Owns no window state — it calls back out."""

    def __init__(
        self,
        *,
        icon: QIcon,
        window: QWidget,
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
        parent: QApplication | None = None,
    ) -> None:
        self._window = window
        self._on_show = on_show
        self._on_quit = on_quit
        self._notified = False
        self._enabled = False

        self._tray = QSystemTrayIcon(icon, parent)
        self._tray.setToolTip("Strata")

        menu = QMenu()
        self._show_action = menu.addAction("Show Strata")
        self._show_action.triggered.connect(self._show)
        menu.addSeparator()
        quit_action = menu.addAction("Quit Strata")
        quit_action.triggered.connect(self._quit)
        self._tray.setContextMenu(menu)

        # A left click (or double click, on Windows) is "bring it back".
        self._tray.activated.connect(self._on_activated)

    # -- availability --------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    @property
    def enabled(self) -> bool:
        """True only when the icon is actually shown and can restore the window."""
        return self._enabled and self._tray.isVisible()

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self.is_available():
            logger.info("tray.unavailable")
            enabled = False
        self._enabled = enabled
        self._tray.setVisible(enabled)
        if not enabled:
            # Turning the feature off must not leave the window stranded off the
            # taskbar with no icon to summon it.
            self._show()

    # -- actions -------------------------------------------------------------

    def hide_to_tray(self) -> None:
        """Take the window off the taskbar, keeping the process alive."""
        self._window.hide()
        if not self._notified:
            self._notified = True
            self._tray.showMessage(
                "Strata is still running",
                "The window is hidden. Click the tray icon to bring it back, "
                "or use Quit Strata to close it.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
        logger.info("tray.hidden")

    def _show(self) -> None:
        self._on_show()

    def _quit(self) -> None:
        self._on_quit()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show()

    def dispose(self) -> None:
        self._tray.hide()
