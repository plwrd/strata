"""Application bootstrap: paths, logging, services, window.

Kept apart from ``main.py`` so that tests can build the service graph without a
QApplication and packaging can import it without side effects.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from app.domain.ai import is_local_endpoint
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
    """Which build this is. A packaged build is always production.

    Development mode is not cosmetic: it opens the developer tools onto the page
    that holds the WebChannel and restores its context menu. Letting an
    environment variable turn that on in a shipped build would mean a shortcut
    with one extra line in it hands someone a console inside the privileged
    window — so the variable is honoured only from a source checkout.
    """
    if is_frozen():
        return "production"
    return os.environ.get(ENV_ENV, "development")


def dev_server() -> str | None:
    """When set, the window loads Vite instead of the bundled files.

    The bridge is identical either way — dev mode does not mock Python. That is
    exactly why this is refused in a packaged build: the window that loads this
    URL is the window with the WebChannel on it, so whatever answers gets every
    bridge — notes, unlocked layers, settings, the browser. An environment
    variable is not an authorisation (anyone who can set `HKCU\Environment` or
    edit a shortcut can set one), so a shipped Strata ignores it entirely and
    only a source checkout can point the window somewhere else.

    Even there it must be a loopback URL: a dev server is ``localhost``, and
    "load this origin into the privileged window" is not a thing to accept for
    an arbitrary host.
    """
    if is_frozen():
        # A packaged build has a frontend of its own; there is no legitimate
        # reason for one to load a different origin.
        if os.environ.get(DEV_SERVER_ENV):
            get_logger(__name__).warning("bootstrap.dev_server_ignored_in_packaged_build")
        return None
    configured = (os.environ.get(DEV_SERVER_ENV) or "").strip()
    if not configured:
        return None
    if not is_local_endpoint(configured) or urlsplit(configured).scheme not in ("http", "https"):
        get_logger(__name__).warning("bootstrap.dev_server_refused_not_loopback")
        return None
    return configured


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
