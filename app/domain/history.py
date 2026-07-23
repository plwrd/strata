"""Persistent AI memory records (see docs/ai-memory-design.md).

Every provider call becomes an :class:`AIExecutionRecord` — the durable answer to
"what did the AI do, with what, and what came back". Together with the privacy
receipts and the applied-plan audit log it forms the AI history that survives a
restart, which is what lets an output be traced, re-examined, or later promoted
into a permanent note.

The redaction rule lives here as pure functions so it can be tested without a
workspace: a record that involved a private layer keeps its metadata (provider,
model, tokens, counts, timestamps) but loses every content-bearing field before
it is written to disk. "No decrypted private content ever touches disk" applies
to memory records exactly as it applies to indexes and temp files.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.operations import AppliedPlan

ExecutionKind = Literal["ai-request", "plan-generation", "processing"]
ExecutionResult = Literal["completed", "cancelled", "failed"]


class AIExecutionRecord(BaseModel):
    """One model call: what went in, what came out, and under what policy.

    ``redacted`` marks a record whose content fields were emptied because the
    execution involved a private layer. The metadata that remains matches what a
    privacy receipt already discloses.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: ExecutionKind = "ai-request"
    created_at: str = ""
    provider: str = ""
    model: str = ""
    is_remote: bool = False
    layer_ids: list[str] = Field(default_factory=list)
    prompt: str = ""
    response_text: str = ""
    source_object_ids: list[str] = Field(default_factory=list)
    source_count: int = 0
    private_source_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    result: ExecutionResult = "completed"
    error_message: str = ""
    duration_ms: int = 0
    redacted: bool = False


def redact_execution(record: AIExecutionRecord) -> AIExecutionRecord:
    """Strip every content-bearing field; keep the metadata a receipt discloses."""
    return record.model_copy(
        update={
            "prompt": "",
            "response_text": "",
            "source_object_ids": [],
            "redacted": True,
        }
    )


def redact_applied_plan(plan: AppliedPlan) -> AppliedPlan:
    """Strip note titles and model prose from an applied plan before persisting.

    ``summary`` and per-result ``detail`` quote titles and content previews;
    ``prompt`` may quote private material. Counts, ids, and the snapshot handle
    stay — undo needs them and they reveal nothing.
    """
    return plan.model_copy(
        update={
            "prompt": "",
            "summary": "",
            "results": [result.model_copy(update={"detail": ""}) for result in plan.results],
            "redacted": True,
        }
    )
