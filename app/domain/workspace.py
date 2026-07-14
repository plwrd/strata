"""Workspace domain model — the root project opened by Strata."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.layer import LayerDescriptor

WORKSPACE_FORMAT_VERSION = 1


class KnowledgeLens(BaseModel):
    """A saved perspective over one or more layers.

    A lens never grants access: hiding a layer in a lens is a *view* decision,
    unlocking one is a *security* decision. ``ai_readable_layers`` narrows what
    the AI Context Composer may include, but the per-layer AI policy still wins.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    visible_layer_ids: list[str] = Field(default_factory=list)
    layer_order: list[str] = Field(default_factory=list)
    folder_scope: list[str] = Field(default_factory=list)
    tag_filters: list[str] = Field(default_factory=list)
    property_filters: dict[str, str] = Field(default_factory=dict)
    relationship_filters: list[str] = Field(default_factory=list)
    node_types: list[str] = Field(default_factory=list)
    search_query: str = ""
    time_range_days: int | None = None
    graph_layout: str = "force"
    graph_camera: dict[str, float] = Field(default_factory=dict)
    color_mapping: str = "layer"
    ai_readable_layer_ids: list[str] = Field(default_factory=list)
    mode: str = "explore"
    is_default: bool = False


class WorkspaceDescriptor(BaseModel):
    """Persisted as ``workspace.json`` at the workspace root."""

    model_config = ConfigDict(extra="forbid")

    format_version: int = WORKSPACE_FORMAT_VERSION
    id: str
    name: str
    created_at: str
    updated_at: str
    layer_order: list[str] = Field(default_factory=list)
    layers: list[LayerDescriptor] = Field(default_factory=list)
    lenses: list[KnowledgeLens] = Field(default_factory=list)

    def layer(self, layer_id: str) -> LayerDescriptor | None:
        return next((layer for layer in self.layers if layer.id == layer_id), None)

    def ordered_layers(self) -> list[LayerDescriptor]:
        index = {layer_id: position for position, layer_id in enumerate(self.layer_order)}
        return sorted(self.layers, key=lambda layer: index.get(layer.id, len(index)))
