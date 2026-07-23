"""Graph loading and neighbour expansion."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import bridge_method
from app.domain.graph import GraphSnapshot
from app.services.connection_service import ConnectionSuggestion
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
    semantic_edges: bool = False
    semantic_threshold: float = Field(default=0.5, ge=0.1, le=1.0)
    cluster: bool = False
    cluster_count: int = Field(default=6, ge=2, le=32)


class ShortestPathRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=128)


class PathResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: list[str] = Field(default_factory=list)


class ClusterNodesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=128)


class GraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: GraphSnapshot


class NeighboursRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=128)


class SuggestConnectionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=8, ge=1, le=25)


class SuggestionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[ConnectionSuggestion] = Field(default_factory=list)


class NeighboursResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: list[str] = Field(default_factory=list)


class GraphBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)
    @bridge_method(LoadGraphRequest)
    def load_graph(self, request: LoadGraphRequest) -> GraphResponse:
        clusters = (
            self._services.search.clusters(request.layer_ids, request.cluster_count)
            if request.cluster
            else None
        )
        snapshot = self._services.graph.build(
            layer_ids=request.layer_ids,
            include_tags=request.include_tags,
            include_folders=request.include_folders,
            focus_note_id=request.focus_note_id,
            neighbour_depth=request.neighbour_depth,
            node_limit=request.node_limit,
            semantic_edges=request.semantic_edges,
            semantic_threshold=request.semantic_threshold,
            cluster_assignments=clusters,
        )
        return GraphResponse(graph=snapshot)

    @Slot(str, result=str)
    @bridge_method(NeighboursRequest)
    def expand_neighbours(self, request: NeighboursRequest) -> NeighboursResponse:
        snapshot = self._services.graph.build()
        return NeighboursResponse(
            node_ids=self._services.graph.neighbours(request.node_id, snapshot)
        )

    @Slot(str, result=str)
    @bridge_method(ShortestPathRequest)
    def shortest_path(self, request: ShortestPathRequest) -> PathResponse:
        snapshot = self._services.graph.build(include_tags=False, include_folders=False)
        return PathResponse(
            node_ids=self._services.graph.shortest_path(
                snapshot, request.source_id, request.target_id
            )
        )

    @Slot(str, result=str)
    @bridge_method(ClusterNodesRequest)
    def cluster_of(self, request: ClusterNodesRequest) -> NeighboursResponse:
        """Every node in the same semantic cluster as the given one."""
        clusters = self._services.search.clusters()
        target_cluster = clusters.get(request.node_id)
        if target_cluster is None:
            return NeighboursResponse(node_ids=[request.node_id])
        return NeighboursResponse(
            node_ids=[
                object_id for object_id, cluster in clusters.items() if cluster == target_cluster
            ]
        )

    @Slot(str, result=str)
    @bridge_method(SuggestConnectionsRequest)
    def suggest_connections(self, request: SuggestConnectionsRequest) -> SuggestionsResponse:
        """Computed connection suggestions for one note — similarity and
        unlinked mentions, each with a score and an inspectable reason. Nothing
        is applied here; accepting one goes through the operation flow."""
        return SuggestionsResponse(
            suggestions=self._services.connections.suggest_for_note(
                request.note_id, limit=request.limit
            )
        )
