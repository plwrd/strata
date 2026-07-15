"""Collaboration domain models (M9).

Real-time collaboration in Strata is a property of the *data*, not of a server:
peers exchange sealed Yjs updates through an untrusted relay and converge. The
relay never sees plaintext (ADR-0006). These models are the vocabulary the
service and bridge speak — the CRDT machinery itself lives in
``app.infrastructure.crdt``.

Two things here carry the product's weight:

1. **The conflict record.** A CRDT guarantees convergence, not correctness. When
   a merge produces a semantically wrong tree (a move cycle, a note orphaned
   under a deleted folder, an edit to a deleted note), we do not resolve it
   silently — we *rescue* the data and surface a ``ConflictRecord``. Nothing is
   ever lost to a merge.
2. **Roles are advisory in the renderer, enforced in Python.** A viewer's edits
   are refused by the service, not merely hidden by the UI.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ShareRole = Literal["owner", "editor", "viewer"]

# The three semantic conflicts a converging tree can produce (ADR-0006). Each is
# detected after every merge and rescued into Conflicts/ rather than merged away.
ConflictKind = Literal["move_cycle", "move_vs_delete", "edit_vs_delete"]


class TreeNode(BaseModel):
    """One node in the parent-pointer folder tree.

    Deliberately flat: a move is a single-key write (``parent`` change), which is
    the smallest unit of concurrency and the only shape that makes the
    pathological merges *detectable*.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=128)
    parent: str | None = None
    name: str = Field(max_length=255)
    # Fractional-index string for sibling ordering, so concurrent inserts between
    # two siblings do not collide.
    order: str = Field(default="a0", max_length=64)
    is_note: bool = False
    deleted: bool = False


class ConflictRecord(BaseModel):
    """A merge outcome that was rescued rather than silently applied."""

    model_config = ConfigDict(extra="forbid")

    conflict_id: str = Field(min_length=1, max_length=64)
    kind: ConflictKind
    node_ids: list[str] = Field(default_factory=list)
    # Pseudonymous peer ids involved, best-effort — the relay is untrusted, so
    # this is provenance, not identity.
    peers: list[str] = Field(default_factory=list)
    detected_at: str = ""
    previous_parent: str | None = None
    # A one-line, plain-language explanation the UI shows verbatim.
    summary: str = ""
    resolved: bool = False


class PresencePeer(BaseModel):
    """A peer currently connected to a shared layer (from Yjs awareness)."""

    model_config = ConfigDict(extra="forbid")

    peer_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="peer", max_length=64)
    color: str = Field(default="peer-1", max_length=32)
    active_note_id: str | None = None
    # Character offset of the peer's cursor in the active note, if shared.
    cursor: int | None = None


class CollaborationState(BaseModel):
    """The honest status of collaboration for a layer or the workspace."""

    model_config = ConfigDict(extra="forbid")

    layer_id: str | None = None
    mode: Literal["personal", "shared"] = "personal"
    enabled: bool = False
    role: ShareRole = "owner"
    doc_id: str | None = None
    peers: list[PresencePeer] = Field(default_factory=list)
    pending_conflicts: int = 0
    # Bytes of sealed update log not yet compacted — a compaction/privacy signal.
    uncompacted_updates: int = 0
