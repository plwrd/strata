"""Graph loading and neighbour expansion."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import bridge_method
from app.domain.graph import GraphSnapshot
from app.services.container import Services
from app.services.graph_service import DEFAULT_NODE_LIMIT


class LoadGraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_ids: list[str] | None = Field(default=None, max_length=200)
    include_tags: bool = True
    include_folders: bool = True
    focus_note_id: str | None = Field(default=None, max_length=128)
    neighbour_depth: int = Field(default=1, ge=0, le=5)
    node_limit: int = Field(default=DEFAULT_NODE_LIMIT, ge=1, le=200_000)


class GraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: GraphSnapshot


class NeighboursRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=128)


class NeighboursResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: list[str] = Field(default_factory=list)


class GraphBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(LoadGraphRequest)
    def load_graph(self, request: LoadGraphRequest) -> GraphResponse:
        snapshot = self._services.graph.build(
            layer_ids=request.layer_ids,
            include_tags=request.include_tags,
            include_folders=request.include_folders,
            focus_note_id=request.focus_note_id,
            neighbour_depth=request.neighbour_depth,
            node_limit=request.node_limit,
        )
        return GraphResponse(graph=snapshot)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(NeighboursRequest)
    def expand_neighbours(self, request: NeighboursRequest) -> NeighboursResponse:
        snapshot = self._services.graph.build()
        return NeighboursResponse(
            node_ids=self._services.graph.neighbours(request.node_id, snapshot)
        )
