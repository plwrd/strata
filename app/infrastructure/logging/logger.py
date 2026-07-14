"""Structured, privacy-preserving logging.

Rules (SECURITY.md, non-negotiable):

* passwords, keys and decrypted private content are never logged;
* filesystem paths are redacted to their basename outside debug builds so that
  a shared log file never leaks a folder structure or a user name;
* the redactor is applied to *every* event, not to individual call sites, so a
  forgetful caller cannot bypass it.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import structlog

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passphrase",
        "key",
        "secret",
        "token",
        "api_key",
        "apikey",
        "recovery_key",
        "content",
        "body",
        "prompt",
        "title",
        "snippet",
    }
)

_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^\s\"']+")
_POSIX_PATH = re.compile(r"/(?:home|Users|mnt|var|tmp)/[^\s\"']+")

_configured = False


def _redact_paths(value: str) -> str:
    def basename(match: re.Match[str]) -> str:
        return f"<path:{Path(match.group(0)).name}>"

    value = _WINDOWS_PATH.sub(basename, value)
    return _POSIX_PATH.sub(basename, value)


def _redactor(
    _logger: object,
    _name: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    for key, value in list(event_dict.items()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "<redacted>"
        elif isinstance(value, str):
            event_dict[key] = _redact_paths(value)
    return event_dict


def configure_logging(*, level: str = "INFO", log_file: Path | None = None) -> None:
    """Configure structlog once for the process."""
    global _configured
    if _configured:
        return

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )

    dev_mode = os.environ.get("STRATA_ENV", "production") == "development"
    renderer: Any = (
        structlog.dev.ConsoleRenderer(colors=False)
        if dev_mode
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redactor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    if not _configured:
        configure_logging()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
