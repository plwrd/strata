"""AI conversations — multi-turn threads that survive a restart.

A conversation is an ordered list of turns, each pointing at the execution that
produced it. The *backend* owns the thread: the frontend sends a conversation
id, and Python replays the stored turns into the request — a client cannot
forge a history it was never given.

The privacy rule of docs/ai-memory-design.md §3 applies to turns exactly as it
does to execution records: a turn whose request involved a private layer is
persisted redacted (the thread keeps its shape; the content stays off disk).
Replaying a redacted turn contributes nothing to the model context — honest
amnesia, not silent leakage.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str = ""
    created_at: str = ""
    prompt: str = ""
    response_text: str = ""
    redacted: bool = False


class AIConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: str = ""
    updated_at: str = ""
    title: str = ""
    provider: str = ""
    model: str = ""
    turns: list[ConversationTurn] = Field(default_factory=list)

    @property
    def turn_count(self) -> int:
        return len(self.turns)
