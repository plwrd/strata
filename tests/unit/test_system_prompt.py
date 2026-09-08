"""Distill Qwen 7B / Qwythos defaults and SystemPrompt.md wiring."""

from __future__ import annotations

import pytest

from app.domain.local_model import is_qwythos, uses_strata_identity
from app.services.ai_service import UNTRUSTED_PREAMBLE, build_system_prompt
from app.services.settings_service import AppSettings
from app.services.system_prompt import (
    DEFAULT_LOCAL_MODEL,
    is_distill_qwen_7b,
    load_strata_system_prompt,
    resolve_model,
)


@pytest.fixture(autouse=True)
def _clear_prompt_cache() -> None:
    load_strata_system_prompt.cache_clear()
    yield
    load_strata_system_prompt.cache_clear()


def test_default_settings_point_at_qwythos() -> None:
    assert AppSettings().default_provider == "ollama"
    assert AppSettings().default_model == DEFAULT_LOCAL_MODEL
    assert is_qwythos(AppSettings().default_model)
    assert uses_strata_identity(AppSettings().default_model)


@pytest.mark.parametrize(
    "model",
    [
        "deepseek-r1:7b",
        "deepseek-r1:7b-qwen-distill-q4_K_M",
        "DeepSeek-R1-Distill-Qwen-7B",
        "cyberuser42/DeepSeek-R1-Distill-Qwen-7B",
    ],
)
def test_distill_qwen_7b_ids_are_recognised(model: str) -> None:
    assert is_distill_qwen_7b(model)


@pytest.mark.parametrize(
    "model",
    ["llama3", "deepseek-r1:14b", "deepseek-r1:1.5b", "claude-opus-4", ""],
)
def test_other_models_are_not_treated_as_distill_qwen_7b(model: str) -> None:
    assert is_distill_qwen_7b(model) is False


def test_blank_and_default_resolve_to_qwythos() -> None:
    assert resolve_model("") == DEFAULT_LOCAL_MODEL
    assert resolve_model("default") == DEFAULT_LOCAL_MODEL
    assert resolve_model("  ", default_model="deepseek-r1:7b") == "deepseek-r1:7b"
    assert resolve_model("llama3") == "llama3"


def test_system_prompt_file_loads_from_repo_root() -> None:
    text = load_strata_system_prompt()
    assert text
    assert "You are **Strata**" in text or "You are Strata" in text
    assert "local model" in text


def test_distill_qwen_gets_strata_identity_plus_untrusted_framing() -> None:
    prompt = build_system_prompt("deepseek-r1:7b")
    identity = load_strata_system_prompt()
    assert identity
    assert prompt.startswith(identity)
    assert UNTRUSTED_PREAMBLE in prompt
    assert "data, not instructions" in prompt.lower()


def test_qwythos_gets_strata_identity_plus_untrusted_framing() -> None:
    prompt = build_system_prompt("qwythos")
    identity = load_strata_system_prompt()
    assert identity
    assert prompt.startswith(identity)
    assert UNTRUSTED_PREAMBLE in prompt


def test_other_models_keep_framing_only() -> None:
    prompt = build_system_prompt("llama3")
    assert prompt == UNTRUSTED_PREAMBLE
    assert "You are **Strata**" not in prompt
    assert "You are Strata" not in prompt
