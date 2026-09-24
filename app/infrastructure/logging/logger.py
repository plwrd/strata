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
from logging.handlers import RotatingFileHandler
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

LOG_FILE_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUPS = 4

_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^\s\"']+")
_POSIX_PATH = re.compile(r"/(?:home|Users|mnt|var|tmp)/[^\s\"']+")

_configured = False
_log_file: Path | None = None


def _redact_paths(value: str) -> str:
    def basename(match: re.Match[str]) -> str:
        return f"<path:{Path(match.group(0)).name}>"

    value = _WINDOWS_PATH.sub(basename, value)
    return _POSIX_PATH.sub(basename, value)


_MAX_REDACT_DEPTH = 6


def _redact_value(key: str, value: Any, depth: int = 0) -> Any:
    """Redact one value, recursing into the containers a caller may pass.

    Structured logging invites ``details={...}`` and ``layers=[...]``. A redactor
    that only looked at the top level would mean the rule held for
    ``password=...`` and quietly lapsed for ``details={"password": ...}`` — which
    is the shape a bridge error actually has.
    """
    if key.lower() in _SENSITIVE_KEYS:
        return "<redacted>"
    if depth >= _MAX_REDACT_DEPTH:
        return value
    if isinstance(value, str):
        return _redact_paths(value)
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        redacted = [_redact_value(key, item, depth + 1) for item in value]
        return tuple(redacted) if isinstance(value, tuple) else redacted
    return value


def _redactor(
    _logger: object,
    _name: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    for key, value in list(event_dict.items()):
        event_dict[key] = _redact_value(key, value)
    return event_dict


def configure_logging(*, level: str = "INFO", log_file: Path | None = None) -> None:
    """Configure structlog for the process.

    Idempotent for a repeat of the same request, but a call that names a log
    file is *not* a repeat of the import-time default that named none. The
    first ``get_logger`` — a module-level ``logger = get_logger(__name__)`` in
    whichever module imports first — configures stderr-only logging before
    ``bootstrap`` runs, and until this distinction was made that early call
    won: ``strata.log`` was never created, on any install, and the one line
    that would have said when screen protection was lost had nowhere to go.
    """
    global _configured, _log_file
    if _configured and (log_file is None or log_file == _log_file):
        return
    _log_file = log_file

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        # Rotating, not plain: this process logs from timers and from every
        # frontend console message, so an install that runs for months would
        # otherwise grow one file without bound — and a log nobody can open is
        # a log nobody can audit. Five files of 5 MiB is enough history to
        # investigate a session and small enough to attach to a bug report.
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=LOG_FILE_BYTES,
                backupCount=LOG_FILE_BACKUPS,
                encoding="utf-8",
            )
        )

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
            # Tracebacks are rendered to a string *before* the redactor runs, not
            # after. `format_exc_info` is where an exception becomes text, and
            # that text is full of absolute paths (and whatever a message
            # interpolated). Redacting first would have left every `.exception()`
            # call writing the user's home directory into the log — the one rule
            # this module exists to enforce.
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redactor,
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
