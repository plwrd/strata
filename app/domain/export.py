"""AI context export domain model (see docs/export-format/README.md, ADR-0009)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

STRATA_EXPORT_VERSION = 1

ExportTarget = Literal["chatgpt", "claude", "gemini", "generic", "local"]
ExportShape = Literal["single-file", "package"]
ContextDepth = Literal[
    "selected-only",
    "plus-links",
    "plus-backlinks",
    "one-hop",
    "two-hops",
]
ContentMode = Literal["full", "summary", "titles-only"]


class ExportSource(BaseModel):
    """One knowledge object rendered into an export package.

    ``source_id`` (``STRATA-SOURCE-001``) is stable *within an export* and is the
    only identifier the model ever sees. Internal object ids are never exported.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    object_id: str
    layer_id: str
    layer_name: str
    is_private: bool = False
    title: str
    path: str
    tags: list[str] = Field(default_factory=list)
    properties: dict[str, str] = Field(default_factory=dict)
    updated_at: str = ""
    content: str = ""
    truncated: bool = False


class ExportRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    relationship: str = "references"


class ContextPlan(BaseModel):
    """What *would* be exported. Rendered in the UI before anything leaves.

    This is the object the privacy review screen is built from: it is computed
    first, shown to the user, and only then turned into files or a request.
    """

    model_config = ConfigDict(extra="forbid")

    export_id: str
    target: ExportTarget = "generic"
    shape: ExportShape = "single-file"
    depth: ContextDepth = "selected-only"
    content_mode: ContentMode = "full"
    prompt: str = ""
    workspace_name: str = ""
    created_at: str = ""
    sources: list[ExportSource] = Field(default_factory=list)
    relationships: list[ExportRelationship] = Field(default_factory=list)
    excluded_locked_count: int = 0
    private_source_count: int = 0
    private_layer_names: list[str] = Field(default_factory=list)
    estimated_tokens: int = 0
    token_budget: int | None = None
    part_count: int = 1
    warnings: list[str] = Field(default_factory=list)

    @property
    def includes_private_content(self) -> bool:
        return self.private_source_count > 0


class ExportPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    content: str
    source_ids: list[str] = Field(default_factory=list)
    estimated_tokens: int = 0


class ExportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_id: str
    target: ExportTarget
    shape: ExportShape
    parts: list[ExportPart] = Field(default_factory=list)
    manifest: dict[str, object] = Field(default_factory=dict)
    estimated_tokens: int = 0
    private_source_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class PrivacyReceipt(BaseModel):
    """Written after every decrypted export or remote AI request (Milestone 6+).

    Contains *what left the device*, never the content itself.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: str
    kind: Literal["export", "ai-request"]
    provider: str = "none"
    model: str = "none"
    is_remote: bool = False
    layer_ids: list[str] = Field(default_factory=list)
    object_count: int = 0
    private_object_count: int = 0
    attachment_count: int = 0
    estimated_tokens: int = 0
    destination: str = ""
    encrypted_in_transit: bool = False
    files_written: int = 0
    result: Literal["completed", "cancelled", "failed"] = "completed"
    undo_reference: str | None = None
