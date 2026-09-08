"""Local-model ids: Qwythos by default, Distill Qwen as a recognised fallback.

Kept in the domain so provider adapters can recognise a thinking model without
importing the service layer.
"""

from __future__ import annotations

from collections.abc import Sequence

# Official Ollama tag used by the blueteam Qwythos harness, and the product
# default for Strata's local providers.
DEFAULT_LOCAL_MODEL = "qwythos"

# Previous product default. Still a valid model; treated as "unset" when
# preferring an installed Qwythos copy.
LEGACY_LOCAL_DEFAULTS = frozenset({"", "default", "deepseek-r1:7b"})

_QWYTHOS_MARKERS = ("qwythos", "claude-mythos", "mythos-5")

_DISTILL_QWEN_7B_MARKERS = (
    "deepseek-r1:7b",
    "7b-qwen-distill",
    "distill-qwen-7b",
    "deepseek-r1-distill-qwen-7b",
)

_THINKING_MARKERS = (
    "qwythos",
    "claude-mythos",
    "mythos-5",
    "deepseek-r1",
    "qwq-",
    "qwen3.5",
    "qwen3-5",
)

# Qwen3.5 thinking-mode recommendations (and blueteam's Qwythos harness).
THINKING_TEMPERATURE = 0.6
THINKING_TOP_P = 0.95
THINKING_TOP_K = 20
THINKING_REPEAT_PENALTY = 1.18
THINKING_PRESENCE_PENALTY = 0.3
THINKING_FREQUENCY_PENALTY = 0.4
# <think> plus the answer; 2048 is too tight and invites truncated CoT.
THINKING_OUTPUT_TOKENS = 8192


def is_qwythos(model: str) -> bool:
    """True when ``model`` names Qwythos / Claude-Mythos (Ollama tag or GGUF file)."""
    name = model.strip().lower()
    if not name:
        return False
    return any(marker in name for marker in _QWYTHOS_MARKERS)


def is_distill_qwen_7b(model: str) -> bool:
    """True when ``model`` names Distill Qwen 7B (any common Ollama/HF id)."""
    name = model.strip().lower()
    if not name:
        return False
    return any(marker in name for marker in _DISTILL_QWEN_7B_MARKERS)


def is_thinking_model(model: str) -> bool:
    """True when the model emits a reasoning block before the answer."""
    name = model.strip().lower()
    if not name:
        return False
    if is_qwythos(name) or is_distill_qwen_7b(name):
        return True
    return any(marker in name for marker in _THINKING_MARKERS)


def uses_strata_identity(model: str) -> bool:
    """True when ``SystemPrompt.md`` should be prepended for this local model."""
    return is_qwythos(model) or is_distill_qwen_7b(model)


def resolve_model(model: str, *, default_model: str = "") -> str:
    """Fill in empty / placeholder model ids with the local default."""
    cleaned = model.strip()
    if not cleaned or cleaned.lower() == "default":
        return default_model.strip() or DEFAULT_LOCAL_MODEL
    return cleaned


def prefer_installed_model(available: Sequence[str], preferred: str = "") -> str:
    """Pick a model that is actually installed, preferring Qwythos.

    If ``preferred`` is a real choice (not the old Distill Qwen default) and it
    appears in ``available``, it wins. Otherwise Qwythos wins when present, then
    the first listed model. An empty list falls back to ``preferred`` or the
    product default so the user can still type an id before health lands.
    """
    ids = [item.strip() for item in available if item and item.strip()]
    pref = preferred.strip()
    legacy = pref.lower() in LEGACY_LOCAL_DEFAULTS

    def _match(target: str) -> str | None:
        needle = target.lower()
        for item in ids:
            if item.lower() == needle:
                return item
        return None

    def _qwythos() -> str | None:
        for item in ids:
            if is_qwythos(item):
                return item
        return None

    if not ids:
        return pref or DEFAULT_LOCAL_MODEL

    if pref and not legacy:
        matched = _match(pref)
        if matched:
            return matched

    found = _qwythos()
    if found:
        return found

    matched = _match(pref) if pref else None
    if matched:
        return matched
    return ids[0]
