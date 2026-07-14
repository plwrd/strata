"""Workspace snapshots.

Milestone 1 lists snapshots (there are none) and refuses to create or restore
them. Snapshots exist to make AI reorganisations recoverable, so they land with
the transactional AI change engine in Milestone 8.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.errors import UnsupportedError
from app.services.container import Services


class SnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    created_at: str
    kind: str = "manual"


class SnapshotListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[SnapshotRecord] = Field(default_factory=list)
    milestone: int = 8


class CreateSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class SnapshotBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def list_snapshots(self, _request: EmptyRequest) -> SnapshotListResponse:
        return SnapshotListResponse()

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(CreateSnapshotRequest)
    def create_snapshot(self, _request: CreateSnapshotRequest) -> SnapshotListResponse:
        raise UnsupportedError(
            "Snapshots arrive with the transactional AI change engine in Milestone 8.",
            details={"milestone": 8},
        )
