"""Layer management, including the private-layer lifecycle.

Password rules enforced here:

* a password crosses the bridge, is used, and is never stored, logged, echoed back,
  or put in an error;
* every failed unlock returns the same generic error, so a caller cannot use the
  response as an oracle for whether the layer exists or what is inside it;
* the recovery key is returned exactly once, at creation. There is no
  "show me my recovery key" endpoint, because there is no copy to show.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.layer import LayerAIPolicy, LayerDescriptor, LayerVisibility
from app.services.container import Services

# Passwords are neither truncated nor normalised — that would silently change what
# the user typed. The cap only bounds the allocation.
MAX_PASSWORD = 1024


class LayerListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layers: list[LayerDescriptor] = Field(default_factory=list)
    layer_order: list[str] = Field(default_factory=list)


class CreateLayerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    visibility: LayerVisibility = "public"
    password: str | None = Field(default=None, max_length=MAX_PASSWORD)
    with_recovery_key: bool = True
    padding: bool = True


class CreateLayerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer: LayerDescriptor
    recovery_key: str | None = None


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
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    remember_on_this_device: bool = False


class RecoveryUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    recovery_key: str = Field(min_length=1, max_length=256)


class LayerIdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    old_password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    new_password: str = Field(min_length=1, max_length=MAX_PASSWORD)


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


class RecoveryKeyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recovery_key: str


class RotationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objects_reencrypted: int
    layer: LayerDescriptor


class LockAllResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: int


class LayerBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def list_layers(self, _request: EmptyRequest) -> LayerListResponse:
        descriptor = self._services.workspace.descriptor
        return LayerListResponse(
            layers=descriptor.ordered_layers(),
            layer_order=descriptor.layer_order,
        )

    @Slot(str, result=str)
    @bridge_method(CreateLayerRequest)
    def create_layer(self, request: CreateLayerRequest) -> CreateLayerResponse:
        layer, recovery_key = self._services.workspace.create_layer(
            request.display_name,
            visibility=request.visibility,
            password=request.password,
            with_recovery_key=request.with_recovery_key,
            padding=request.padding,
        )
        return CreateLayerResponse(layer=layer, recovery_key=recovery_key)

    @Slot(str, result=str)
    @bridge_method(RenameLayerRequest)
    def rename_layer(self, request: RenameLayerRequest) -> LayerResponse:
        layer = self._services.workspace.rename_layer(request.layer_id, request.display_name)
        return LayerResponse(layer=layer)

    @Slot(str, result=str)
    @bridge_method(ReorderLayersRequest)
    def reorder_layers(self, request: ReorderLayersRequest) -> LayerListResponse:
        descriptor = self._services.workspace.reorder_layers(request.layer_order)
        return LayerListResponse(
            layers=descriptor.ordered_layers(), layer_order=descriptor.layer_order
        )

    @Slot(str, result=str)
    @bridge_method(SetAIPolicyRequest)
    def set_ai_policy(self, request: SetAIPolicyRequest) -> LayerResponse:
        layer = self._services.workspace.require_layer(request.layer_id)
        layer.ai_policy = request.policy
        return LayerResponse(layer=layer)

    # -- private layers ------------------------------------------------------

    @Slot(str, result=str)
    @bridge_method(UnlockRequest)
    def unlock_layer(self, request: UnlockRequest) -> LayerResponse:
        layer = self._services.workspace.unlock_layer(request.layer_id, request.password)
        return LayerResponse(layer=layer)

    @Slot(str, result=str)
    @bridge_method(RecoveryUnlockRequest)
    def unlock_with_recovery_key(self, request: RecoveryUnlockRequest) -> LayerResponse:
        layer = self._services.workspace.unlock_layer_with_recovery_key(
            request.layer_id, request.recovery_key
        )
        return LayerResponse(layer=layer)

    @Slot(str, result=str)
    @bridge_method(LayerIdRequest)
    def lock_layer(self, request: LayerIdRequest) -> LayerResponse:
        layer = self._services.workspace.lock_layer(request.layer_id)
        return LayerResponse(layer=layer)

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def lock_all_layers(self, _request: EmptyRequest) -> LockAllResponse:
        return LockAllResponse(locked=self._services.workspace.lock_all_layers())

    @Slot(str, result=str)
    @bridge_method(ChangePasswordRequest)
    def change_password(self, request: ChangePasswordRequest) -> LayerResponse:
        """Rewrap the layer key. Cheap — and it does *not* revoke anyone who
        already holds the key. That is rotation, below."""
        self._services.workspace.change_layer_password(
            request.layer_id, request.old_password, request.new_password
        )
        return LayerResponse(layer=self._services.workspace.require_layer(request.layer_id))

    @Slot(str, result=str)
    @bridge_method(PasswordRequest)
    def reissue_recovery_key(self, request: PasswordRequest) -> RecoveryKeyResponse:
        key = self._services.workspace.reissue_recovery_key(request.layer_id, request.password)
        return RecoveryKeyResponse(recovery_key=key)

    @Slot(str, result=str)
    @bridge_method(PasswordRequest)
    def rotate_key(self, request: PasswordRequest) -> RotationResponse:
        """Generate a new layer key and re-encrypt every object under it.

        This is the operation that actually revokes someone who kept the old key.
        It rewrites the entire layer, so the UI warns before calling it.
        """
        rewritten = self._services.workspace.rotate_layer_key(request.layer_id, request.password)
        return RotationResponse(
            objects_reencrypted=rewritten,
            layer=self._services.workspace.require_layer(request.layer_id),
        )
