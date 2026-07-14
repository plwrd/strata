"""QApplication setup."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.bootstrap import APP_NAME, build_services, dev_server, frontend_root, resource_root
from app.desktop.main_window import MainWindow
from app.desktop.webengine import CONTENT_SECURITY_POLICY, register_scheme
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


def _chromium_flags() -> str:
    flags = [
        # No renderer may reach the network; every request goes through Python.
        "--disable-background-networking",
        "--disable-sync",
        "--disable-speech-api",
        "--no-first-run",
        "--disable-remote-fonts",
    ]
    return " ".join(flags)


def create_application(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    import os

    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", _chromium_flags())

    # Must happen before QApplication exists.
    register_scheme()

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationVersion("0.1.0")
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
