"""Declarative AI operation plans.

Every change an AI makes to the workspace is a *plan* of typed operations, not a
direct edit. The plan is validated, shown as a diff, approved (whole or per-item),
applied in a transaction, and can be undone. The AI never touches the filesystem;
it proposes, and the user disposes.

Two rules make this safe rather than merely tidy:

1. **The AI cannot invent a target.** Every operation names a layer, and the
   validator checks that layer is one the user actually put in scope. A plan that
   references a layer outside the selection is rejected, not clamped.
2. **Destructive operations are marked and gated.** Delete, decrypt, move-to-public
   and bulk changes carry ``destructive=True`` and require the extra confirmation
   the UI enforces — the model cannot smuggle a deletion past the user by burying it
   in a large plan.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OperationType = Literal[
    "create_folder",
    "create_note",
    "update_note",
    "append_note",
    "move_note",
    "rename_note",
    "set_property",
    "add_tag",
    "remove_tag",
    "add_link",
    "add_relationship",
    "create_task",
    "archive_note",
    "delete_note",
]

# Operations that change or destroy existing content, rather than only adding.
# These get the extra confirmation gate. A plan is not allowed to hide one.
DESTRUCTIVE_TYPES: frozenset[str] = frozenset({"delete_note", "archive_note", "update_note"})


class Operation(BaseModel):
    """One step in a plan. Extra keys are forbidden, so a model cannot smuggle a
    field the applier does not understand and the validator does not check."""

    model_config = ConfigDict(extra="forbid")

    type: OperationType
    layer_id: str = Field(min_length=1, max_length=128)

    # Targets. Which of these is required depends on `type`; the validator checks.
    note_id: str | None = Field(default=None, max_length=128)
    folder_path: str = Field(default="", max_length=1024)
    title: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=512_000)

    # For links and relationships.
    target_note_id: str | None = Field(default=None, max_length=128)
    target_title: str = Field(default="", max_length=200)
    relationship: str = Field(default="references", max_length=64)

    # For properties and tags.
    property_key: str = Field(default="", max_length=120)
    property_value: str = Field(default="", max_length=2000)
    tag: str = Field(default="", max_length=120)

    # Initial frontmatter for create_note / create_task (e.g. a schema `type`,
    # provenance fields). Strings only — richer values go through set_property.
    properties: dict[str, str] = Field(default_factory=dict)

    # A one-line, human-readable statement of intent, written by the model. Shown in
    # the diff so the user reads *why*, not just *what*.
    rationale: str = Field(default="", max_length=400)

    @property
    def is_destructive(self) -> bool:
        return self.type in DESTRUCTIVE_TYPES


class OperationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    summary: str = ""
    operations: list[Operation] = Field(default_factory=list)
    created_at: str = ""
    provider: str = ""
    model: str = ""
    # The prompt that produced it, kept for the audit log.
    prompt: str = ""

    @property
    def destructive_count(self) -> int:
        return sum(1 for operation in self.operations if operation.is_destructive)


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    type: str
    applied: bool
    note_id: str | None = None
    detail: str = ""
    error: str = ""


class AppliedPlan(BaseModel):
    """The record of an applied plan, and the handle used to undo it."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    snapshot_id: str
    applied_at: str
    results: list[OperationResult] = Field(default_factory=list)
    summary: str = ""
    provider: str = ""
    model: str = ""
    prompt: str = ""
    undone: bool = False
    # True when the persisted copy had its content fields stripped because the
    # plan touched a private layer (see docs/ai-memory-design.md §3).
    redacted: bool = False

    @property
    def applied_count(self) -> int:
        return sum(1 for result in self.results if result.applied)


class DiffEntry(BaseModel):
    """One line of the visual diff the user reviews before approving."""

    model_config = ConfigDict(extra="forbid")

    index: int
    type: str
    layer_id: str
    layer_name: str
    is_private: bool
    is_destructive: bool
    title: str = ""
    summary: str = ""
    rationale: str = ""
    before: str = ""
    after: str = ""
    valid: bool = True
    problem: str = ""


class PlanReview(BaseModel):
    """A validated plan, ready for the user to approve or reject piecewise."""

    model_config = ConfigDict(extra="forbid")

    plan: OperationPlan
    entries: list[DiffEntry] = Field(default_factory=list)
    valid_count: int = 0
    invalid_count: int = 0
    destructive_count: int = 0
    private_layers_touched: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
