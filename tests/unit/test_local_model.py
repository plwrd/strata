"""Qwythos / thinking-model id helpers and the stream filter."""

from __future__ import annotations

from app.domain.local_model import (
    DEFAULT_LOCAL_MODEL,
    is_qwythos,
    is_thinking_model,
    prefer_installed_model,
    resolve_model,
    uses_strata_identity,
)
from app.infrastructure.ai_providers.thinking import ThinkingStreamFilter, thinking_request_extras


def test_product_default_is_qwythos() -> None:
    assert DEFAULT_LOCAL_MODEL == "qwythos"
    assert is_qwythos("qwythos")
    assert is_qwythos("Qwythos-9B-Claude-Mythos-5-1M-Q4_K_M.gguf")
    assert is_thinking_model("qwythos")
    assert uses_strata_identity("qwythos")


def test_prefer_installed_picks_qwythos_over_legacy_default() -> None:
    assert (
        prefer_installed_model(["llama3", "qwythos"], preferred="deepseek-r1:7b") == "qwythos"
    )
    assert prefer_installed_model(["llama3", "qwythos"], preferred="qwythos") == "qwythos"
    assert prefer_installed_model(["llama3", "qwythos"], preferred="llama3") == "llama3"
    assert prefer_installed_model(["phi3"], preferred="qwythos") == "phi3"
    assert prefer_installed_model([], preferred="") == "qwythos"


def test_blank_model_resolves_to_qwythos() -> None:
    assert resolve_model("") == "qwythos"
    assert resolve_model("default") == "qwythos"
    assert resolve_model("  ", default_model="deepseek-r1:7b") == "deepseek-r1:7b"
    assert resolve_model("llama3") == "llama3"


def test_think_filter_strips_a_split_block() -> None:
    filt = ThinkingStreamFilter()
    assert filt.push("Intro <thi") == "Intro "
    assert filt.push("nk>hidden plan</th") == ""
    assert filt.push("ink>\nAnswer") == "\nAnswer"
    assert filt.flush() == ""


def test_think_filter_discards_unclosed_think_on_flush() -> None:
    filt = ThinkingStreamFilter()
    assert filt.push("<think>still going") == ""
    assert filt.flush() == ""


def test_think_filter_passes_plain_text() -> None:
    filt = ThinkingStreamFilter()
    assert filt.push("Hel") == "Hel"
    assert filt.push("lo") == "lo"
    assert filt.flush() == ""


def test_ollama_qwythos_request_enables_think() -> None:
    extras = thinking_request_extras("ollama", "qwythos")
    assert extras["think"] is True
    options = extras["options"]
    assert isinstance(options, dict)
    assert options["temperature"] == 0.6
    assert options["top_k"] == 20


def test_llamacpp_qwythos_request_enables_jinja_thinking() -> None:
    extras = thinking_request_extras("llamacpp", "qwythos")
    assert extras["chat_template_kwargs"] == {"enable_thinking": True}
    assert extras["repetition_penalty"] == 1.18


def test_plain_models_do_not_get_think_extras() -> None:
    assert thinking_request_extras("ollama", "llama3") == {}
    assert thinking_request_extras("openai", "gpt-4o") == {}
