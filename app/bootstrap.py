"""Application bootstrap: paths, logging, services, window.

Kept apart from ``main.py`` so that tests can build the service graph without a
QApplication and packaging can import it without side effects.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.infrastructure.logging.logger import configure_logging, get_logger
from app.services.container import Paths, Services

APP_NAME = "Strata"
DEV_SERVER_ENV = "STRATA_DEV_SERVER"
ENV_ENV = "STRATA_ENV"
WORKSPACE_ENV = "STRATA_WORKSPACE"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """Where bundled read-only resources live (differs under PyInstaller)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def frontend_root() -> Path:
    return resource_root() / "frontend" / "dist"


def user_paths() -> Paths:
    """Per-user directories, following the platform conventions."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        config_dir = base / APP_NAME
        data_dir = Path(os.environ.get("LOCALAPPDATA", base)) / APP_NAME
    elif sys.platform == "darwin":  # pragma: no cover - not a target yet
        config_dir = Path.home() / "Library" / "Application Support" / APP_NAME
        data_dir = config_dir
    else:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        config_dir = config_home / "strata"
        data_dir = data_home / "strata"

    return Paths(
        config_dir=config_dir,
        data_dir=data_dir,
        log_dir=data_dir / "logs",
        default_workspace=Path(
            os.environ.get(WORKSPACE_ENV, str(Path.home() / "Documents" / "Strata"))
        ),
    )


def environment() -> str:
    return os.environ.get(ENV_ENV, "development" if not is_frozen() else "production")


def dev_server() -> str | None:
    """When set, the window loads Vite instead of the bundled files.

    The bridge is identical either way — dev mode does not mock Python.
    """
    return os.environ.get(DEV_SERVER_ENV) or None


def build_services() -> Services:
    paths = user_paths()
    env = environment()
    configure_logging(
        level="DEBUG" if env == "development" else "INFO",
        log_file=paths.log_dir / "strata.log",
    )
    logger = get_logger(__name__)
    services = Services(paths, environment=env)
    logger.info("bootstrap.services_ready", environment=env)
    return services
