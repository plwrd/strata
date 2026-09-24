"""QApplication setup."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.bootstrap import APP_NAME, build_services, dev_server, frontend_root, resource_root
from app.desktop.capture_flags import capture_flags
from app.desktop.main_window import MainWindow
from app.desktop.webengine import CONTENT_SECURITY_POLICY, register_scheme
from app.infrastructure.logging.logger import get_logger
from app.services.container import APP_VERSION

logger = get_logger(__name__)


def _touch_requested() -> bool:
    """Whether mobile mode is on, read before QApplication exists.

    Touch-events are a process-global Chromium flag, so they can only be decided
    at launch — a runtime mobile-mode toggle swaps the user-agent immediately but
    its touch half waits for the next start. Read defensively: a missing or
    corrupt settings file must never stop the app from booting.
    """
    try:
        from app.bootstrap import user_paths
        from app.services.settings_service import SettingsService

        return SettingsService(user_paths().settings_file).settings.browser_mobile_mode
    except Exception:  # pragma: no cover - launch must be robust to any settings error
        return False


def _hide_for_sharing_requested() -> bool:
    """Whether capture exclusion is on, read before QApplication exists.

    Same launch-time constraint as `_touch_requested`: the compositor flags this
    decides are process-global to Chromium, so they can only be set at start.
    """
    try:
        from app.bootstrap import user_paths
        from app.services.settings_service import SettingsService

        return SettingsService(user_paths().settings_file).settings.hide_for_sharing
    except Exception:  # pragma: no cover - launch must be robust to any settings error
        return True  # the setting's own default; fail towards hiding, not exposing


def _chromium_flags() -> str:
    flags = [
        # No renderer may reach the network; every request goes through Python.
        "--disable-background-networking",
        "--disable-sync",
        "--disable-speech-api",
        "--no-first-run",
        "--disable-remote-fonts",
        # Keep every frame on the path `SetWindowDisplayAffinity` can reach.
        # The reasoning, and why this is one shared list across all three
        # Chromium engines, is in `app.desktop.capture_flags`.
        *capture_flags(hiding=_hide_for_sharing_requested()),
    ]
    if _touch_requested():
        # Advertise touch so sites serve their touch/mobile UI. Safe for Strata's
        # own UI, which has no hover/pointer media queries to flip.
        flags.append("--touch-events=enabled")
    return " ".join(flags)


def create_application(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    import os

    # setdefault, not assignment: an explicit QTWEBENGINE_CHROMIUM_FLAGS in the
    # environment wins, which is how the compositor flags above get A/B tested
    # against a real recorder without a rebuild.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", _chromium_flags())

    # Must happen before QApplication exists.
    register_scheme()

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationVersion(APP_VERSION)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(argv if argv is not None else sys.argv)

    icon_path = resource_root() / "packaging" / "icons" / "strata.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    services = build_services()

    root = frontend_root()
    if not root.is_file() and not (root / "index.html").is_file():
        logger.error("frontend.missing", hint="run: npm --prefix frontend run build")

    window = MainWindow(services, root, dev_server=dev_server())
    logger.info("application.ready", csp_len=len(CONTENT_SECURITY_POLICY))
    return app, window
