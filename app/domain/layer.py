"""Layer domain model.

A *layer* is an independent content, permission, encryption, synchronisation and
AI-access boundary. Every knowledge object belongs to exactly one layer.

Milestone 1 ships public layers only; the private-layer fields below are part of
the persisted descriptor from day one so that Milestone 3 does not require a
storage migration.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LayerVisibility = Literal["public", "private"]
LayerState = Literal["mounted", "unmounted", "locked", "unlocked"]
LayerSharingMode = Literal["personal", "shared-password", "identity-managed"]

# How a layer's bytes are kept on disk. Deliberately *not* the same axis as
# visibility: "private" is a policy (who may read it, what the AI may do with it,
# whether an export needs a confirmation), while storage is a mechanism. Keeping
# them separate means the privacy rules are enforced and tested against the
# descriptor today, and Milestone 3 only has to add a second storage backend
# rather than re-thread every privacy check through the codebase.
LayerStorage = Literal["markdown", "encrypted-objects"]

AIAccess = Literal[
    "disabled",
    "local-only",
    "remote-with-confirmation",
    "remote-always",
]
EmbeddingAccess = Literal["disabled", "local-only", "remote-allowed"]

LAYER_STORAGE_VERSION = 1


class LayerAIPolicy(BaseModel):
    """Per-layer AI permissions.

    Enforced in the service layer, never in the UI: the UI only mirrors it.
    A locked layer is never available to AI regardless of this policy.
    """

    model_config = ConfigDict(extra="forbid")

    access: AIAccess = "local-only"
    embeddings: EmbeddingAccess = "local-only"
    may_read: bool = True
    may_summarize: bool = True
    may_propose_edits: bool = True
    may_apply_approved_edits: bool = False
    may_create_links: bool = False
    may_reorganize_structure: bool = False
    may_process_attachments: bool = False

    def allows_remote(self) -> bool:
        return self.access in ("remote-with-confirmation", "remote-always")

    def requires_confirmation_for_remote(self) -> bool:
        return self.access == "remote-with-confirmation"


class LayerDescriptor(BaseModel):
    """The public description of a layer.

    Safe to send to the frontend in any lock state: for a private layer the
    ``display_name`` is user-chosen metadata stored in the *workspace* file, not
    inside the encrypted layer, so it is available while locked. Nothing else
    about a locked layer's contents is exposed.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    visibility: LayerVisibility = "public"
    state: LayerState = "mounted"
    sharing_mode: LayerSharingMode = "personal"
    storage: LayerStorage = "markdown"
    storage_version: int = LAYER_STORAGE_VERSION
    created_at: str
    updated_at: str
    color: str = "layer-public"
    ai_policy: LayerAIPolicy = Field(default_factory=LayerAIPolicy)

    @property
    def is_readable(self) -> bool:
        """True when the app currently holds what it needs to read the layer."""
        if self.visibility == "public":
            return self.state in ("mounted", "unlocked")
        return self.state == "unlocked"

    @property
    def is_locked(self) -> bool:
        return self.visibility == "private" and self.state != "unlocked"


class KnowledgeObjectRef(BaseModel):
    """A cross-layer reference. Always opaque: never a path, never a title."""

    model_config = ConfigDict(extra="forbid")

    layer_id: str
    object_id: str

    def key(self) -> str:
        return f"{self.layer_id}:{self.object_id}"
