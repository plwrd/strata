"""Filing web research into the graph you already have.

Processing a capture (`app.domain.knowledge`) asks "what is in this material?"
and answers with new pages. Research asks a different question — "where does
this belong?" — and answers with *attachments*: subnodes under an existing
node, and short sections appended to nodes that were already about this.

The difference matters for how it is validated. Every id the model returns must
be one of the candidate nodes Strata retrieved and showed it; a node it was
never offered is not a match, it is a hallucination, and it is dropped rather
than looked up. Nothing here writes: the proposal becomes an
:class:`app.domain.operations.OperationPlan` that goes through the normal
review → approve → transactional apply flow like every other AI mutation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.operations import OperationPlan

SubnodeKind = Literal["concept", "entity", "source", "question", "task", "other"]


class CandidateNode(BaseModel):
    """A node Strata retrieved and offered to the model. The model may only
    reference these — the offer *is* the permission boundary."""

    model_config = ConfigDict(extra="forbid")

    note_id: str
    title: str
    layer_id: str
    folder_path: str = ""
    node_type: str = ""
    tags: list[str] = Field(default_factory=list)
    score: float = 0.0


class NodeMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1, max_length=128)
    relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=500)


class ProposedSubnode(BaseModel):
    """A new page that belongs *under* an existing node."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    # Empty means "no good parent" — it still gets filed, in the target layer's
    # knowledge folder, rather than being dropped.
    parent_note_id: str = Field(default="", max_length=128)
    kind: SubnodeKind = "concept"
    content: str = Field(default="", max_length=20_000)


class ContextAddition(BaseModel):
    """A short section to append to a node that already covers this ground."""

    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1, max_length=128)
    heading: str = Field(default="", max_length=200)
    content: str = Field(min_length=1, max_length=20_000)


class ResearchAnalysis(BaseModel):
    """The model's schema-validated answer for one batch of research material."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=4000)
    key_points: list[str] = Field(default_factory=list)
    node_matches: list[NodeMatch] = Field(default_factory=list)
    subnodes: list[ProposedSubnode] = Field(default_factory=list)
    context_additions: list[ContextAddition] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    claims_to_verify: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ResearchProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_note_ids: list[str] = Field(default_factory=list)
    analysis: ResearchAnalysis = Field(default_factory=ResearchAnalysis)
    candidates: list[CandidateNode] = Field(default_factory=list)
    plan: OperationPlan
    warnings: list[str] = Field(default_factory=list)
