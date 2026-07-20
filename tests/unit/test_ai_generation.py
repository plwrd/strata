"""AIGenerationService — prompt assembly per mode, and defensive parsing.

The service is tested against a stub AI, because what matters here is *what is
asked* (which instruction set, which count directive) and *how the answer is
treated* (bad JSON → empty plan, never a crash), not any real model.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain.ai import AIEvent
from app.services.ai_generation_service import (
    NOTES_INSTRUCTIONS,
    PLAN_INSTRUCTIONS,
    AIGenerationService,
)


class StubAI:
    """Records the kwargs of every run() call and streams a canned reply."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> AsyncIterator[AIEvent]:
        self.calls.append(kwargs)
        yield AIEvent(kind="delta", text=self.reply)


NOTES_REPLY = json.dumps(
    {
        "summary": "Two notes",
        "operations": [
            {
                "type": "create_note",
                "layer_id": "layer_a",
                "title": "First",
                "content": "# First\n\nBody.\n\nderived_from:: [[Seed]]",
                "rationale": "first topic",
            },
            {
                "type": "create_note",
                "layer_id": "layer_a",
                "title": "Second",
                "content": "# Second\n\nBody.\n\nderived_from:: [[Seed]]",
                "rationale": "second topic",
            },
        ],
    }
)


def generate(ai: StubAI, **overrides: Any):
    kwargs: dict[str, Any] = {
        "provider_id": "ollama",
        "model": "llama3",
        "prompt": "Split my note",
        "context": "ctx",
        "layer_ids": ["layer_a"],
        **overrides,
    }
    return AIGenerationService(ai).generate_plan_sync(**kwargs)


def test_notes_mode_uses_the_note_writing_instructions() -> None:
    ai = StubAI(NOTES_REPLY)

    plan = generate(ai, mode="notes")

    sources = ai.calls[0]["sources"]
    assert NOTES_INSTRUCTIONS in sources
    assert PLAN_INSTRUCTIONS not in sources
    assert [operation.type for operation in plan.operations] == ["create_note", "create_note"]


def test_plan_mode_keeps_the_reorganisation_instructions() -> None:
    ai = StubAI(NOTES_REPLY)

    generate(ai)

    assert PLAN_INSTRUCTIONS in ai.calls[0]["sources"]
    assert NOTES_INSTRUCTIONS not in ai.calls[0]["sources"]


def test_a_fixed_note_count_becomes_an_exact_directive() -> None:
    ai = StubAI(NOTES_REPLY)

    generate(ai, mode="notes", note_count=3)

    assert "Create exactly 3 note(s)" in ai.calls[0]["prompt"]


def test_count_zero_lets_the_model_decide() -> None:
    ai = StubAI(NOTES_REPLY)

    generate(ai, mode="notes", note_count=0)

    prompt = ai.calls[0]["prompt"]
    assert "Create exactly" not in prompt
    assert "Decide how many notes" in prompt


def test_an_absurd_count_is_clamped() -> None:
    ai = StubAI(NOTES_REPLY)

    generate(ai, mode="notes", note_count=10_000)

    assert "Create exactly 20 note(s)" in ai.calls[0]["prompt"]


@pytest.mark.parametrize("reply", ["not json at all", "{broken", '{"operations": "nope"}'])
def test_garbage_in_notes_mode_is_an_empty_plan_not_a_crash(reply: str) -> None:
    ai = StubAI(reply)

    plan = generate(ai, mode="notes")

    assert plan.operations == []
