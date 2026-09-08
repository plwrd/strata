"""Strata's identity prompt for the default local model (Qwythos / Distill Qwen).

``SystemPrompt.md`` at the repo (or bundle) root is the source of truth. It is
composed with the untrusted-sources framing in :mod:`app.services.ai_service`
so security boundaries are never dropped when the identity prompt is applied.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from app.domain.local_model import (
    DEFAULT_LOCAL_MODEL,
    is_distill_qwen_7b,
    is_qwythos,
    resolve_model,
    uses_strata_identity,
)
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

__all__ = [
    "DEFAULT_LOCAL_MODEL",
    "is_distill_qwen_7b",
    "is_qwythos",
    "load_strata_system_prompt",
    "resolve_model",
    "strata_system_prompt_path",
    "uses_strata_identity",
]

logger = get_logger(__name__)


def _resource_root() -> Path:
    """Mirror :func:`app.bootstrap.resource_root` without importing bootstrap.

    Importing bootstrap here would cycle through the service container.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # app/services/system_prompt.py → repo root
    return Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=1)
def load_strata_system_prompt() -> str:
    """Load ``SystemPrompt.md``. Empty string if missing (callers must tolerate that)."""
    path = strata_system_prompt_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("system_prompt.missing", path=str(path))
        return ""
    if not text:
        logger.warning("system_prompt.empty", path=str(path))
        return ""
    return text


def strata_system_prompt_path() -> Path:
    return _resource_root() / "SystemPrompt.md"
