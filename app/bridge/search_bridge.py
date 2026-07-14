"""Search over readable layers. Locked layers contribute nothing at all."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import bridge_method
from app.services.container import Services
from app.services.search_service import SearchResult


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=1000)
    layer_ids: list[str] | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=50, ge=1, le=500)


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[SearchResult] = Field(default_factory=list)
    # The count is over readable layers only. It is never adjusted for locked
    # layers, because "3 results, 1 hidden" would leak the hidden one's existence.
    total: int = 0
    locked_layers_excluded: int = 0


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
        )
        return SearchResponse(
            results=results,
            total=len(results),
            locked_layers_excluded=len(self._services.workspace.locked_layers()),
        )
