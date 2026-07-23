"""Structured knowledge extraction (the "Process into knowledge" action).

The model's answer is parsed into these schemas and *validated, never trusted*:
a concept with a hallucinated field fails validation and is dropped; a related
note id that does not exist in the workspace is discarded. Nothing here writes
anything — the proposal becomes an :class:`app.domain.operations.OperationPlan`
that goes through the normal review → approve → transactional apply flow.

Every AI-authored page the proposal creates carries provenance properties
(`review_status: ai-inferred`, `generated_by`, `confidence`) — an AI assumption
is never stored as a confirmed fact.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.operations import OperationPlan

EntityKind = Literal["person", "organization", "project", "tool", "other"]


class ExtractedConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExtractedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: EntityKind = "other"
    description: str = Field(default="", max_length=2000)


class ExtractedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(default="", max_length=2000)
    owner: str = Field(default="", max_length=200)
    date: str = Field(default="", max_length=40)
    # The passage the decision was extracted from — quoted on the decision
    # record so every extracted item links back to its transcript evidence.
    excerpt: str = Field(default="", max_length=1000)


class ExtractedActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=2000)
    owner: str = Field(default="", max_length=200)
    deadline: str = Field(default="", max_length=40)
    excerpt: str = Field(default="", max_length=1000)


class KnowledgeExtraction(BaseModel):
    """The model's schema-validated answer for one source note."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=4000)
    concepts: list[ExtractedConcept] = Field(default_factory=list)
    entities: list[ExtractedEntity] = Field(default_factory=list)
    decisions: list[ExtractedDecision] = Field(default_factory=list)
    action_items: list[ExtractedActionItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    suggested_tags: list[str] = Field(default_factory=list)
    related_note_ids: list[str] = Field(default_factory=list)
    claims_to_verify: list[str] = Field(default_factory=list)
    # Meeting profile only.
    participants: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class KnowledgeProposal(BaseModel):
    """What processing proposes: the extraction(s) plus the plan that would
    materialise them. The plan is applied only after human review."""

    model_config = ConfigDict(extra="forbid")

    source_note_ids: list[str] = Field(default_factory=list)
    extractions: list[KnowledgeExtraction] = Field(default_factory=list)
    plan: OperationPlan
    warnings: list[str] = Field(default_factory=list)
