"""Saved prompts — reusable AI instructions with history and usage stats.

A saved prompt is workspace data (``.strata/ai/prompts.jsonl``), not
configuration: it versions like content (saving over an existing id appends a
new record with ``version + 1``; the trail is the file), counts its uses, and
is rendered only into the *instruction* channel of a request — never mixed into
the untrusted sources block.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PromptCategory = Literal[
    "research",
    "summarization",
    "meeting-processing",
    "project-planning",
    "weekly-review",
    "decision-extraction",
    "writing",
    "technical-analysis",
    "learning",
    "content-generation",
    "other",
]


class SavedPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    category: PromptCategory = "other"
    prompt_text: str = Field(min_length=1, max_length=32_000)
    # Preferences, not commands: the composer pre-selects these when the prompt
    # is chosen, and the user can still override them.
    model_preference: str = Field(default="", max_length=128)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    version: int = Field(default=1, ge=1)
    usage_count: int = Field(default=0, ge=0)
    created_at: str = ""
    updated_at: str = ""
    last_used_at: str = ""
