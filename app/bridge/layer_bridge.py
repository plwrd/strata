"""Layer management over the WebChannel."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.errors import UnsupportedError
from app.domain.layer import LayerAIPolicy, LayerDescriptor, LayerVisibility
from app.services.container import Services


class LayerListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layers: list[LayerDescriptor] = Field(default_factory=list)
    layer_order: list[str] = Field(default_factory=list)


class CreateLayerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    visibility: LayerVisibility = "public"


class RenameLayerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)


class ReorderLayersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_order: list[str] = Field(min_length=1, max_length=500)


class SetAIPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    policy: LayerAIPolicy


class LayerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer: LayerDescriptor


class UnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    # The password never appears in a log, a job detail, an error or a receipt.
    password: str = Field(min_length=1, max_length=1024)
    remember_on_this_device: bool = False


class LayerBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def list_layers(self, _request: EmptyRequest) -> LayerListResponse:
        descriptor = self._services.workspace.descriptor
        return LayerListResponse(
            layers=descriptor.ordered_layers(),
            layer_order=descriptor.layer_order,
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(CreateLayerRequest)
    def create_layer(self, request: CreateLayerRequest) -> LayerResponse:
        layer = self._services.workspace.create_layer(
            request.display_name, visibility=request.visibility
        )
        return LayerResponse(layer=layer)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(RenameLayerRequest)
    def rename_layer(self, request: RenameLayerRequest) -> LayerResponse:
        layer = self._services.workspace.rename_layer(request.layer_id, request.display_name)
        return LayerResponse(layer=layer)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(ReorderLayersRequest)
    def reorder_layers(self, request: ReorderLayersRequest) -> LayerListResponse:
        descriptor = self._services.workspace.reorder_layers(request.layer_order)
        return LayerListResponse(
            layers=descriptor.ordered_layers(), layer_order=descriptor.layer_order
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(SetAIPolicyRequest)
    def set_ai_policy(self, request: SetAIPolicyRequest) -> LayerResponse:
        layer = self._services.workspace.require_layer(request.layer_id)
        layer.ai_policy = request.policy
        return LayerResponse(layer=layer)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(UnlockRequest)
    def unlock_layer(self, _request: UnlockRequest) -> LayerResponse:
        # The endpoint exists so the UI contract is fixed, but it refuses rather
        # than pretending: there is no key hierarchy to unlock until Milestone 3.
        raise UnsupportedError(
            "Private encrypted layers arrive in Milestone 3.",
            details={"milestone": 3},
        )
