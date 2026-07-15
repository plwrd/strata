"""Structured views and saved-view management over the WebChannel."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.views import ViewConfig, ViewResult
from app.services.container import Services


class RunViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: ViewConfig


class RunViewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: ViewResult


class SavedViewsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[ViewConfig] = Field(default_factory=list)


class SaveViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: ViewConfig


class SaveViewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: ViewConfig


class DeleteViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_id: str = Field(min_length=1, max_length=128)


class DeleteViewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: bool = True


class ViewsBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)
    @bridge_method(RunViewRequest)
    def run_view(self, request: RunViewRequest) -> RunViewResponse:
        return RunViewResponse(result=self._services.views.run(request.config))

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def list_saved_views(self, _request: EmptyRequest) -> SavedViewsResponse:
        return SavedViewsResponse(views=self._services.workspace.saved_views())

    @Slot(str, result=str)
    @bridge_method(SaveViewRequest)
    def save_view(self, request: SaveViewRequest) -> SaveViewResponse:
        return SaveViewResponse(view=self._services.workspace.save_view(request.view))

    @Slot(str, result=str)
    @bridge_method(DeleteViewRequest)
    def delete_view(self, request: DeleteViewRequest) -> DeleteViewResponse:
        self._services.workspace.delete_view(request.view_id)
        return DeleteViewResponse()
