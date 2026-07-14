"""Collaboration status.

The bridge object exists from Milestone 1 so that the WebChannel surface and its
permission checks are fixed early, but it reports the honest state: this device
is in personal mode and nothing synchronises. The CRDT engine, identities, roles
and revocation arrive in Milestone 9 (ADR-0006).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.errors import UnsupportedError
from app.services.container import Services


class CollaborationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "personal"
    enabled: bool = False
    peers_online: int = 0
    devices: list[str] = Field(default_factory=list)
    milestone: int = 9
    note: str = "Collaboration arrives in Milestone 9. This device is in personal mode."


class InviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    role: str = Field(default="viewer", max_length=32)


class CollaborationBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def get_status(self, _request: EmptyRequest) -> CollaborationStatusResponse:
        return CollaborationStatusResponse()

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(InviteRequest)
    def invite(self, _request: InviteRequest) -> CollaborationStatusResponse:
        raise UnsupportedError(
            "Collaboration arrives in Milestone 9.",
            details={"milestone": 9},
        )
