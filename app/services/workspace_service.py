"""Workspace and layer lifecycle.

Holds the *one* open workspace and the per-layer stores. Every other service asks
this one for a readable layer, which is where the lock check lives — so a lock
check cannot be forgotten by a caller.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.errors import (
    InvalidRequestError,
    LayerLockedError,
    NotFoundError,
    UnsupportedError,
)
from app.domain.ids import new_layer_id, new_workspace_id
from app.domain.layer import LayerAIPolicy, LayerDescriptor, LayerVisibility
from app.domain.workspace import KnowledgeLens, WorkspaceDescriptor
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.storage.markdown_store import MarkdownLayerStore, now_iso
from app.infrastructure.storage.workspace_store import WorkspaceStore
from app.services.demo_content import seed_demo_workspace

logger = get_logger(__name__)


class WorkspaceService:
    def __init__(self) -> None:
        self._store: WorkspaceStore | None = None
        self._descriptor: WorkspaceDescriptor | None = None
        self._layer_stores: dict[str, MarkdownLayerStore] = {}

    # -- state ---------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._descriptor is not None

    @property
    def descriptor(self) -> WorkspaceDescriptor:
        if self._descriptor is None:
            raise NotFoundError("No workspace is open.")
        return self._descriptor

    @property
    def root(self) -> Path:
        if self._store is None:
            raise NotFoundError("No workspace is open.")
        return self._store.root

    def _require_store(self) -> WorkspaceStore:
        if self._store is None:
            raise NotFoundError("No workspace is open.")
        return self._store

    # -- lifecycle -----------------------------------------------------------

    def create(self, root: Path, name: str, *, seed_demo: bool = False) -> WorkspaceDescriptor:
        store = WorkspaceStore(root)
        if store.exists():
            raise InvalidRequestError("A workspace already exists at this location.")

        timestamp = now_iso()
        descriptor = WorkspaceDescriptor(
            id=new_workspace_id(),
            name=name,
            created_at=timestamp,
            updated_at=timestamp,
        )
        store.initialise(descriptor)
        self._store = store
        self._descriptor = descriptor
        self._layer_stores = {}

        layer = self.create_layer("Knowledge", visibility="public")
        if seed_demo:
            seed_demo_workspace(self.layer_store(layer.id))
        self._install_default_lenses()
        logger.info("workspace.created", workspace_id=descriptor.id)
        return self.descriptor

    def open(self, root: Path) -> WorkspaceDescriptor:
        store = WorkspaceStore(root)
        descriptor = store.load()
        self._store = store
        self._descriptor = descriptor
        self._layer_stores = {}
        logger.info("workspace.opened", workspace_id=descriptor.id, layers=len(descriptor.layers))
        return descriptor

    def open_or_create(self, root: Path, name: str) -> WorkspaceDescriptor:
        """Open the workspace at ``root``, creating a seeded one if absent."""
        if WorkspaceStore(root).exists():
            return self.open(root)
        return self.create(root, name, seed_demo=True)

    def close(self) -> None:
        self._store = None
        self._descriptor = None
        self._layer_stores = {}

    def _save(self) -> None:
        descriptor = self.descriptor
        descriptor.updated_at = now_iso()
        self._require_store().save(descriptor)

    # -- layers --------------------------------------------------------------

    def create_layer(
        self,
        display_name: str,
        *,
        visibility: LayerVisibility = "public",
        ai_policy: LayerAIPolicy | None = None,
    ) -> LayerDescriptor:
        if visibility == "private":
            # Refusing is the honest answer: a "private" layer without the
            # Milestone 3 encryption engine would be a public layer wearing a
            # padlock icon, which is worse than not offering it.
            raise UnsupportedError(
                "Private encrypted layers arrive in Milestone 3.",
                details={"milestone": 3},
            )
        descriptor = self.descriptor
        timestamp = now_iso()
        layer = LayerDescriptor(
            id=new_layer_id(),
            display_name=display_name,
            visibility=visibility,
            state="mounted",
            created_at=timestamp,
            updated_at=timestamp,
            color="layer-public",
            ai_policy=ai_policy or LayerAIPolicy(),
        )
        store = self._require_store()
        MarkdownLayerStore(layer.id, store.layer_root(layer.id)).ensure()
        descriptor.layers.append(layer)
        descriptor.layer_order.append(layer.id)
        self._save()
        logger.info("layer.created", layer_id=layer.id, visibility=visibility)
        return layer

    def rename_layer(self, layer_id: str, display_name: str) -> LayerDescriptor:
        layer = self.require_layer(layer_id)
        layer.display_name = display_name
        layer.updated_at = now_iso()
        self._save()
        return layer

    def reorder_layers(self, layer_order: list[str]) -> WorkspaceDescriptor:
        descriptor = self.descriptor
        known = {layer.id for layer in descriptor.layers}
        if set(layer_order) != known:
            raise InvalidRequestError("The layer order must list every layer exactly once.")
        descriptor.layer_order = layer_order
        self._save()
        return descriptor

    def require_layer(self, layer_id: str) -> LayerDescriptor:
        layer = self.descriptor.layer(layer_id)
        if layer is None:
            raise NotFoundError("Layer not found.")
        return layer

    def require_readable_layer(self, layer_id: str) -> LayerDescriptor:
        """The single place a lock check happens. Callers cannot skip it."""
        layer = self.require_layer(layer_id)
        if layer.is_locked:
            # Deliberately generic: does not confirm whether anything inside the
            # layer matches the request.
            raise LayerLockedError("This layer is locked.", details={"layerId": layer_id})
        return layer

    def readable_layers(self) -> list[LayerDescriptor]:
        return [layer for layer in self.descriptor.ordered_layers() if layer.is_readable]

    def locked_layers(self) -> list[LayerDescriptor]:
        return [layer for layer in self.descriptor.ordered_layers() if layer.is_locked]

    def layer_store(self, layer_id: str) -> MarkdownLayerStore:
        layer = self.require_readable_layer(layer_id)
        if layer.visibility != "public":
            raise UnsupportedError("Only public layers use Markdown storage today.")
        if layer_id not in self._layer_stores:
            store = MarkdownLayerStore(layer_id, self._require_store().layer_root(layer_id))
            store.ensure()
            self._layer_stores[layer_id] = store
        return self._layer_stores[layer_id]

    # -- lenses --------------------------------------------------------------

    def _install_default_lenses(self) -> None:
        descriptor = self.descriptor
        all_layers = [layer.id for layer in descriptor.layers]
        descriptor.lenses = [
            KnowledgeLens(
                id="lens_all",
                name="All Knowledge",
                visible_layer_ids=all_layers,
                layer_order=list(all_layers),
                ai_readable_layer_ids=all_layers,
                is_default=True,
            ),
            KnowledgeLens(
                id="lens_recent",
                name="Recently Modified",
                visible_layer_ids=all_layers,
                layer_order=list(all_layers),
                ai_readable_layer_ids=all_layers,
                time_range_days=14,
            ),
            KnowledgeLens(
                id="lens_public",
                name="Public Documentation",
                visible_layer_ids=[
                    layer.id for layer in descriptor.layers if layer.visibility == "public"
                ],
                layer_order=list(all_layers),
                ai_readable_layer_ids=[
                    layer.id for layer in descriptor.layers if layer.visibility == "public"
                ],
            ),
        ]
        self._save()

    def lenses(self) -> list[KnowledgeLens]:
        return list(self.descriptor.lenses)

    def save_lens(self, lens: KnowledgeLens) -> KnowledgeLens:
        descriptor = self.descriptor
        existing = next(
            (index for index, saved in enumerate(descriptor.lenses) if saved.id == lens.id),
            None,
        )
        if existing is None:
            descriptor.lenses.append(lens)
        else:
            descriptor.lenses[existing] = lens
        self._save()
        return lens
