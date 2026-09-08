"""Thinking-model extras: sampling, think flags, and a stream filter.

Qwythos (Qwen3.5) and similar reasoning models open every reply with a
``<think>...</think>`` block, or — with llama.cpp ``--reasoning-format deepseek``
— a separate ``reasoning_content`` delta. The composer should show the answer,
not the chain of thought.
"""

from __future__ import annotations

from app.domain.local_model import (
    THINKING_FREQUENCY_PENALTY,
    THINKING_PRESENCE_PENALTY,
    THINKING_REPEAT_PENALTY,
    THINKING_TEMPERATURE,
    THINKING_TOP_K,
    THINKING_TOP_P,
    is_thinking_model,
)

_OPEN = "<think>"
_CLOSE = "</think>"


class ThinkingStreamFilter:
    """Hold ``<think>`` … ``</think>`` across SSE chunks; emit only the answer."""

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def push(self, chunk: str) -> str:
        if not chunk:
            return ""
        self._buf += chunk
        out: list[str] = []
        while self._buf:
            if self._in_think:
                end = _find_ci(self._buf, _CLOSE)
                if end < 0:
                    self._buf = self._buf[-len(_CLOSE) + 1 :]
                    return "".join(out)
                self._buf = self._buf[end + len(_CLOSE) :]
                self._in_think = False
                continue
            start = _find_ci(self._buf, _OPEN)
            if start < 0:
                hold = _held_prefix(self._buf, _OPEN)
                if hold:
                    out.append(self._buf[:-hold])
                    self._buf = self._buf[-hold:]
                    return "".join(out)
                out.append(self._buf)
                self._buf = ""
                return "".join(out)
            out.append(self._buf[:start])
            self._buf = self._buf[start + len(_OPEN) :]
            self._in_think = True
        return "".join(out)

    def flush(self) -> str:
        if self._in_think:
            self._buf = ""
            return ""
        rest = self._buf
        self._buf = ""
        return rest


def thinking_request_extras(provider_id: str, model: str) -> dict[str, object]:
    """OpenAI-compatible body fields that turn thinking on with sane sampling."""
    if not is_thinking_model(model):
        return {}

    extras: dict[str, object] = {
        "top_p": THINKING_TOP_P,
        "top_k": THINKING_TOP_K,
    }
    if provider_id == "ollama":
        extras["think"] = True
        extras["options"] = {
            "temperature": THINKING_TEMPERATURE,
            "top_p": THINKING_TOP_P,
            "top_k": THINKING_TOP_K,
            "repeat_penalty": THINKING_REPEAT_PENALTY,
            "presence_penalty": THINKING_PRESENCE_PENALTY,
            "frequency_penalty": THINKING_FREQUENCY_PENALTY,
        }
        return extras

    if provider_id in {"llamacpp", "lmstudio"}:
        extras["repetition_penalty"] = THINKING_REPEAT_PENALTY
        extras["presence_penalty"] = THINKING_PRESENCE_PENALTY
        extras["frequency_penalty"] = THINKING_FREQUENCY_PENALTY
        extras["chat_template_kwargs"] = {"enable_thinking": True}
    return extras


def _find_ci(haystack: str, needle: str) -> int:
    return haystack.lower().find(needle.lower())


def _held_prefix(text: str, tag: str) -> int:
    """How many trailing characters of ``text`` might be a split opening tag."""
    lower = text.lower()
    needle = tag.lower()
    for length in range(len(needle) - 1, 0, -1):
        if lower.endswith(needle[:length]):
            return length
    return 0
