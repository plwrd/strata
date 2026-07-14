"""Search over readable layers. Locked layers contribute nothing at all."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.services.container import Services
from app.services.search_service import SearchResult


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=1000)
    layer_ids: list[str] | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=50, ge=1, le=500)
    semantic: bool = True
    # When set, notes near this one in the graph are boosted — "what else is relevant
    # to what I am reading", rather than "what matches this string".
    near_object_id: str | None = Field(default=None, max_length=128)


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[SearchResult] = Field(default_factory=list)
    # The count is over readable layers only, and is never adjusted for locked ones:
    # "3 results, 1 hidden" would leak the hidden one's existence.
    total: int = 0
    locked_layers_excluded: int = 0


class SimilarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=10, ge=1, le=100)


class ClusterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_ids: list[str] | None = Field(default=None, max_length=200)
    count: int = Field(default=6, ge=2, le=32)


class ClusterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clusters: dict[str, int] = Field(default_factory=dict)


class IndexStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sizes: dict[str, int] = Field(default_factory=dict)


class SearchBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(SearchRequest)
    def search(self, request: SearchRequest) -> SearchResponse:
        results = self._services.search.search(
            request.query,
            layer_ids=request.layer_ids,
            tags=request.tags,
            limit=request.limit,
            semantic=request.semantic,
            near_object_id=request.near_object_id,
        )
        return SearchResponse(
            results=results,
            total=len(results),
            locked_layers_excluded=len(self._services.workspace.locked_layers()),
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(SimilarRequest)
    def similar(self, request: SimilarRequest) -> SearchResponse:
        results = self._services.search.similar_to(request.object_id, request.limit)
        return SearchResponse(results=results, total=len(results))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(ClusterRequest)
    def clusters(self, request: ClusterRequest) -> ClusterResponse:
        return ClusterResponse(
            clusters=self._services.search.clusters(request.layer_ids, request.count)
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def index_status(self, _request: EmptyRequest) -> IndexStatusResponse:
        """Index sizes, for diagnostics.

        A locked layer has no index and therefore no entry — its size is *absent*,
        not reported as zero, because "0 documents" and "no such layer" should be
        indistinguishable from outside.
        """
        return IndexStatusResponse(sizes=self._services.search.index_sizes())
