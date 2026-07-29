"""Strata's identity prompt for the default local model (Distill Qwen 7B).

``SystemPrompt.md`` at the repo (or bundle) root is the source of truth. It is
composed with the untrusted-sources framing in :mod:`app.services.ai_service`
so security boundaries are never dropped when the identity prompt is applied.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

# Official Ollama tag for DeepSeek-R1-Distill-Qwen-7B.
DEFAULT_LOCAL_MODEL = "deepseek-r1:7b"

_DISTILL_QWEN_7B_MARKERS = (
    "deepseek-r1:7b",
    "7b-qwen-distill",
    "distill-qwen-7b",
    "deepseek-r1-distill-qwen-7b",
)


def _resource_root() -> Path:
    """Mirror :func:`app.bootstrap.resource_root` without importing bootstrap.

    Importing bootstrap here would cycle through the service container.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # app/services/system_prompt.py → repo root
    return Path(__file__).resolve().parent.parent.parent


def is_distill_qwen_7b(model: str) -> bool:
    """True when ``model`` names Distill Qwen 7B (any common Ollama/HF id)."""
    name = model.strip().lower()
    if not name:
        return False
    return any(marker in name for marker in _DISTILL_QWEN_7B_MARKERS)


def resolve_model(model: str, *, default_model: str = "") -> str:
    """Fill in empty / placeholder model ids with the Distill Qwen 7B default."""
    cleaned = model.strip()
    if not cleaned or cleaned.lower() == "default":
        return default_model.strip() or DEFAULT_LOCAL_MODEL
    return cleaned


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
