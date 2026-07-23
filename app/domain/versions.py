"""Per-note version history.

A version is the full state of a note (content + properties) captured *before*
a mutation replaced it, with the origin of that mutation (``human``,
``ai:<plan id>``, ``restore``). The current state is the file itself; versions
are the trail behind it. Restoring is a normal, itself-versioned write — history
only ever grows until the user clears it.

Scope rule: versions are stored as plaintext under ``.strata/versions/`` and
therefore exist **only for Markdown (public) layers**. A private layer's history
must never appear on disk decrypted; its recovery story remains workspace
snapshots and the encrypted trash.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NoteVersion(BaseModel):
    """One captured prior state of a note."""

    model_config = ConfigDict(extra="forbid")

    index: int = 0
    created_at: str = ""
    # Who caused the mutation that replaced this state: "human", "ai:<plan_id>",
    # or "restore".
    origin: str = "human"
    # What kind of mutation replaced it: "update", "properties", "rename",
    # "move", "delete", "restore".
    change: str = "update"
    title: str = ""
    content: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class NoteVersionSummary(BaseModel):
    """A version without its content — what the history list shows."""

    model_config = ConfigDict(extra="forbid")

    index: int = 0
    created_at: str = ""
    origin: str = "human"
    change: str = "update"
    title: str = ""
    size_chars: int = 0
