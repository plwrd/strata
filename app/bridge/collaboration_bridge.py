"""Collaboration bridge (M9, ADR-0006).

The renderer never holds a key, so the authoritative CRDT lives in Python. This
bridge is the seam: the renderer shares/joins a layer, pushes the Yjs updates its
editor produces, pulls the authoritative state, syncs through the relay, and
sees conflicts and presence. Binary Yjs updates cross the JSON envelope as
base64.

Every method validates and delegates to :class:`CollaborationService`; policy
(role checks, lock state) is enforced there, in Python — never here, and never in
the renderer.
"""

from __future__ import annotations

import base64
import json

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Signal, Slot

from app.bridge.envelope import bridge_method
from app.domain.collaboration import (
    CollaborationState,
    ConflictRecord,
    PresencePeer,
    ShareRole,
)
from app.services.container import Services

_MAX_UPDATE_B64 = 8 * 1024 * 1024  # a single sealed batch; large edits still fit


class LayerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)


class ShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    role: ShareRole = "owner"


class JoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    doc_id: str = Field(min_length=1, max_length=64)
    role: ShareRole = "editor"


class ApplyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    update: str = Field(min_length=1, max_length=_MAX_UPDATE_B64)


class SetBodyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    note_id: str = Field(min_length=1, max_length=128)
    body: str = Field(max_length=2_000_000)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    conflict_id: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=32)


class AnnounceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    peer: PresencePeer


class StateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: CollaborationState


class ConflictsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: CollaborationState
    conflicts: list[ConflictRecord] = Field(default_factory=list)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    update: str  # base64 Yjs update carrying the whole document state


class PresenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peers: list[PresencePeer] = Field(default_factory=list)


class CompactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reclaimed: int = 0


class CollaborationBridge(QObject):
    # Push channel for remote changes, conflicts, and presence updates.
    collabEvent = Signal(str)

    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._services.collaboration.set_emitter(self._on_event)

    def _on_event(self, kind: str, payload: dict[str, object]) -> None:
        self.collabEvent.emit(json.dumps({"kind": kind, **payload}))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(LayerRequest)
    def get_status(self, request: LayerRequest) -> StateResponse:
        return StateResponse(state=self._services.collaboration.status(request.layer_id))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(ShareRequest)
    def share_layer(self, request: ShareRequest) -> StateResponse:
        state = self._services.collaboration.share_layer(request.layer_id, role=request.role)
        return StateResponse(state=state)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(JoinRequest)
    def join_layer(self, request: JoinRequest) -> StateResponse:
        state = self._services.collaboration.join_layer(
            request.layer_id, request.doc_id, role=request.role
        )
        return StateResponse(state=state)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(LayerRequest)
    def leave_layer(self, request: LayerRequest) -> StateResponse:
        self._services.collaboration.forget_layer(request.layer_id)
        return StateResponse(state=self._services.collaboration.status(request.layer_id))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(LayerRequest)
    def sync(self, request: LayerRequest) -> ConflictsResponse:
        conflicts = self._services.collaboration.sync(request.layer_id)
        return ConflictsResponse(
            state=self._services.collaboration.status(request.layer_id),
            conflicts=conflicts,
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(LayerRequest)
    def get_document(self, request: LayerRequest) -> DocumentResponse:
        update = self._services.collaboration.document_update(request.layer_id)
        return DocumentResponse(update=base64.b64encode(update).decode("ascii"))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(ApplyUpdateRequest)
    def apply_update(self, request: ApplyUpdateRequest) -> ConflictsResponse:
        try:
            # binascii.Error is a ValueError subclass, so this covers both.
            update = base64.b64decode(request.update, validate=True)
        except ValueError as exc:
            from app.domain.errors import InvalidRequestError

            raise InvalidRequestError("Update is not valid base64.") from exc
        conflicts = self._services.collaboration.apply_local_update(request.layer_id, update)
        return ConflictsResponse(
            state=self._services.collaboration.status(request.layer_id),
            conflicts=conflicts,
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(SetBodyRequest)
    def set_body(self, request: SetBodyRequest) -> StateResponse:
        self._services.collaboration.set_body(request.layer_id, request.note_id, request.body)
        return StateResponse(state=self._services.collaboration.status(request.layer_id))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(LayerRequest)
    def list_conflicts(self, request: LayerRequest) -> ConflictsResponse:
        return ConflictsResponse(
            state=self._services.collaboration.status(request.layer_id),
            conflicts=self._services.collaboration.conflicts(request.layer_id),
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(ResolveRequest)
    def resolve_conflict(self, request: ResolveRequest) -> StateResponse:
        state = self._services.collaboration.resolve_conflict(
            request.layer_id, request.conflict_id, request.action
        )
        return StateResponse(state=state)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(AnnounceRequest)
    def announce_presence(self, request: AnnounceRequest) -> PresenceResponse:
        self._services.collaboration.announce(request.layer_id, request.peer)
        return PresenceResponse(peers=self._services.collaboration.presence(request.layer_id))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(LayerRequest)
    def get_presence(self, request: LayerRequest) -> PresenceResponse:
        return PresenceResponse(peers=self._services.collaboration.presence(request.layer_id))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(LayerRequest)
    def compact(self, request: LayerRequest) -> CompactResponse:
        return CompactResponse(reclaimed=self._services.collaboration.compact(request.layer_id))
