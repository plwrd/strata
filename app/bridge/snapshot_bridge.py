"""Workspace snapshots: create, list, restore, delete."""

from __future__ import annotations

from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.services.container import Services


class SnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    created_at: str
    kind: str = "manual"
    layer_count: int = 0
    note_count: int = 0


class SnapshotListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[SnapshotRecord] = Field(default_factory=list)


class CreateSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class SnapshotIdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=1, max_length=128)


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: SnapshotRecord


class DeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: bool = True


class SnapshotBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def list_snapshots(self, _request: EmptyRequest) -> SnapshotListResponse:
        return SnapshotListResponse(
            snapshots=[SnapshotRecord(**asdict(info)) for info in self._services.snapshots.list()]
        )

    @Slot(str, result=str)
    @bridge_method(CreateSnapshotRequest)
    def create_snapshot(self, request: CreateSnapshotRequest) -> SnapshotResponse:
        info = self._services.snapshots.create(request.name)
        return SnapshotResponse(snapshot=SnapshotRecord(**asdict(info)))

    @Slot(str, result=str)
    @bridge_method(SnapshotIdRequest)
    def restore_snapshot(self, request: SnapshotIdRequest) -> SnapshotResponse:
        info = self._services.snapshots.restore(request.snapshot_id)
        return SnapshotResponse(snapshot=SnapshotRecord(**asdict(info)))

    @Slot(str, result=str)
    @bridge_method(SnapshotIdRequest)
    def delete_snapshot(self, request: SnapshotIdRequest) -> DeleteResponse:
        self._services.snapshots.delete(request.snapshot_id)
        return DeleteResponse()
