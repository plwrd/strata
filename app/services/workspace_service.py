"""Workspace and layer lifecycle.

Holds the *one* open workspace and the per-layer stores. Every other service asks
this one for a readable layer, which is where the lock check lives — so a lock
check cannot be forgotten by a caller.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.domain.errors import (
    InvalidRequestError,
    LayerLockedError,
    NotFoundError,
    UnsupportedError,
)
from app.domain.ids import new_layer_id, new_workspace_id
from app.domain.layer import (
    LayerAIPolicy,
    LayerDescriptor,
    LayerState,
    LayerStorage,
    LayerVisibility,
)
from app.domain.views import ViewConfig
from app.domain.workspace import KnowledgeLens, WorkspaceDescriptor
from app.infrastructure.encryption.layer_header import LayerHeader
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.storage.markdown_store import MarkdownLayerStore, now_iso
from app.infrastructure.storage.workspace_store import WorkspaceStore
from app.services.demo_content import seed_demo_workspace
from app.services.encryption_service import EncryptionService
from app.services.private_layer_access import PrivateLayerAccess

logger = get_logger(__name__)


class WorkspaceService:
    def __init__(
        self,
        *,
        on_open: Callable[[Path], None] | None = None,
        on_close: Callable[[], None] | None = None,
        encryption: EncryptionService | None = None,
    ) -> None:
        self._store: WorkspaceStore | None = None
        self._descriptor: WorkspaceDescriptor | None = None
        self._layer_stores: dict[str, MarkdownLayerStore] = {}
        self._private_access: dict[str, PrivateLayerAccess] = {}
        # Injected rather than imported: the workspace does not know a file watcher
        # exists, it just says when it opened and closed.
        self._on_open = on_open
        self._on_close = on_close
        self._encryption = encryption

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

    def layer_root(self, layer_id: str) -> Path:
        """The on-disk root for a layer's objects. Used by collaboration (M9)."""
        return self._require_store().layer_root(layer_id)

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

        layer, _recovery = self.create_layer("Knowledge", visibility="public")
        if seed_demo:
            seed_demo_workspace(self.layer_store(layer.id))
        self._install_default_lenses()
        logger.info("workspace.created", workspace_id=descriptor.id)
        if self._on_open:
            self._on_open(root)
        return self.descriptor

    def open(self, root: Path) -> WorkspaceDescriptor:
        store = WorkspaceStore(root)
        descriptor = store.load()

        # A freshly-opened workspace holds no keys, so every private layer is
        # locked — regardless of what the file says. `state` is JSON on disk, and
        # trusting it would let anyone who edits workspace.json flip a layer to
        # "unlocked" and have the UI believe it.
        for layer in descriptor.layers:
            if layer.visibility == "private":
                layer.state = "locked"

        self._store = store
        self._descriptor = descriptor
        self._layer_stores = {}
        self._private_access = {}
        logger.info("workspace.opened", workspace_id=descriptor.id, layers=len(descriptor.layers))
        if self._on_open:
            self._on_open(root)
        return descriptor

    def open_or_create(self, root: Path, name: str) -> WorkspaceDescriptor:
        """Open the workspace at ``root``, creating a seeded one if absent."""
        if WorkspaceStore(root).exists():
            return self.open(root)
        return self.create(root, name, seed_demo=True)

    def close(self) -> None:
        # Closing the workspace locks every private layer. Leaving a key in memory
        # after the user closed the thing it belongs to would be indefensible.
        if self._encryption is not None:
            self._encryption.lock_all()
        if self._on_close:
            self._on_close()
        self._store = None
        self._descriptor = None
        self._layer_stores = {}
        self._private_access = {}

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
        password: str | None = None,
        with_recovery_key: bool = True,
        padding: bool = True,
    ) -> tuple[LayerDescriptor, str | None]:
        """Create a layer. Returns the descriptor and, for a private layer, the
        recovery key — which is shown once and never stored."""
        descriptor = self.descriptor
        timestamp = now_iso()
        store = self._require_store()
        layer_id = new_layer_id()
        recovery_key: str | None = None

        if visibility == "private":
            if self._encryption is None:
                raise UnsupportedError("Encryption is not available in this build.")
            if not password:
                raise InvalidRequestError("A private layer needs a password.")
            _header, recovery_key = self._encryption.create_layer(
                layer_id=layer_id,
                root=store.layer_root(layer_id),
                password=password,
                with_recovery_key=with_recovery_key,
                padding=padding,
            )
            storage: LayerStorage = "encrypted-objects"
            # A private layer is created *unlocked*: the user just proved they hold
            # the password by choosing it.
            state: LayerState = "unlocked"
        else:
            MarkdownLayerStore(layer_id, store.layer_root(layer_id)).ensure()
            storage = "markdown"
            state = "mounted"

        layer = LayerDescriptor(
            id=layer_id,
            display_name=display_name,
            visibility=visibility,
            state=state,
            storage=storage,
            created_at=timestamp,
            updated_at=timestamp,
            color="layer-private" if visibility == "private" else "layer-public",
            ai_policy=ai_policy
            or (
                # A private layer defaults to local-only AI. Opting in to a remote
                # model with private content has to be a decision, not a default.
                LayerAIPolicy(access="local-only", embeddings="local-only")
                if visibility == "private"
                else LayerAIPolicy()
            ),
        )
        descriptor.layers.append(layer)
        descriptor.layer_order.append(layer.id)
        self._save()
        logger.info("layer.created", layer_id=layer.id, visibility=visibility)
        return layer, recovery_key

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

    def _holds_key(self, layer_id: str) -> bool:
        return self._encryption is not None and self._encryption.is_unlocked(layer_id)

    def require_readable_layer(self, layer_id: str) -> LayerDescriptor:
        """The single place a lock check happens. Callers cannot skip it.

        For a private layer the truth is the *key holder*, not the descriptor's
        `state` field — that is JSON on disk, and a bug (or someone with a text
        editor) could set it to "unlocked". Without the key nothing decrypts
        anyway; checking here just makes the failure early and legible.
        """
        layer = self.require_layer(layer_id)
        if layer.visibility == "private":
            if not self._holds_key(layer_id):
                # Deliberately generic: does not confirm whether anything inside the
                # layer matches the request.
                raise LayerLockedError("This layer is locked.", details={"layerId": layer_id})
            return layer
        if layer.is_locked:
            raise LayerLockedError("This layer is locked.", details={"layerId": layer_id})
        return layer

    def _is_readable(self, layer: LayerDescriptor) -> bool:
        if layer.visibility == "private":
            return self._holds_key(layer.id)
        return layer.is_readable

    def readable_layers(self) -> list[LayerDescriptor]:
        return [layer for layer in self.descriptor.ordered_layers() if self._is_readable(layer)]

    def locked_layers(self) -> list[LayerDescriptor]:
        return [layer for layer in self.descriptor.ordered_layers() if not self._is_readable(layer)]

    def layer_store(self, layer_id: str) -> MarkdownLayerStore:
        """The Markdown store for a readable layer with Markdown storage."""
        layer = self.require_readable_layer(layer_id)
        if layer.storage != "markdown":
            raise InvalidRequestError("This layer does not use Markdown storage.")
        if layer_id not in self._layer_stores:
            store = MarkdownLayerStore(layer_id, self._require_store().layer_root(layer_id))
            store.ensure()
            self._layer_stores[layer_id] = store
        return self._layer_stores[layer_id]

    # -- private layers ------------------------------------------------------

    def _require_encryption(self) -> EncryptionService:
        if self._encryption is None:
            raise UnsupportedError("Encryption is not available in this build.")
        return self._encryption

    def unlock_layer(self, layer_id: str, password: str) -> LayerDescriptor:
        layer = self._require_private(layer_id)
        self._require_encryption().unlock(
            layer_id, self._require_store().layer_root(layer_id), password
        )
        return self._mark_unlocked(layer)

    def unlock_layer_with_recovery_key(self, layer_id: str, recovery_key: str) -> LayerDescriptor:
        layer = self._require_private(layer_id)
        self._require_encryption().unlock_with_recovery_key(
            layer_id, self._require_store().layer_root(layer_id), recovery_key
        )
        return self._mark_unlocked(layer)

    def _mark_unlocked(self, layer: LayerDescriptor) -> LayerDescriptor:
        layer.state = "unlocked"
        self._private_access.pop(layer.id, None)
        self._save()
        return layer

    def lock_layer(self, layer_id: str) -> LayerDescriptor:
        layer = self._require_private(layer_id)
        self._require_encryption().lock(layer_id)
        # Drop the cached handle: it holds a decrypted manifest, which is every
        # title, tag and folder name in the layer.
        self._private_access.pop(layer_id, None)
        layer.state = "locked"
        self._save()
        return layer

    def lock_all_layers(self) -> int:
        count = 0
        for layer in list(self.descriptor.layers):
            if layer.visibility == "private" and self._holds_key(layer.id):
                self.lock_layer(layer.id)
                count += 1
        return count

    def change_layer_password(self, layer_id: str, old_password: str, new_password: str) -> None:
        self._require_private(layer_id)
        self._require_encryption().change_password(
            layer_id, self._require_store().layer_root(layer_id), old_password, new_password
        )

    def reissue_recovery_key(self, layer_id: str, password: str) -> str:
        self._require_private(layer_id)
        return self._require_encryption().reissue_recovery_key(
            layer_id, self._require_store().layer_root(layer_id), password
        )

    def rotate_layer_key(self, layer_id: str, password: str) -> int:
        self._require_private(layer_id)
        rewritten = self._require_encryption().rotate_key(
            layer_id, self._require_store().layer_root(layer_id), password
        )
        self._private_access.pop(layer_id, None)
        return rewritten

    def _require_private(self, layer_id: str) -> LayerDescriptor:
        layer = self.require_layer(layer_id)
        if layer.visibility != "private":
            raise InvalidRequestError("This operation applies only to private layers.")
        return layer

    def private_access(self, layer_id: str) -> PrivateLayerAccess:
        """A working handle on an unlocked private layer.

        Obtaining one *is* the lock check: `require_readable_layer` runs first, and
        the key comes from the key holder, which is empty while locked.
        """
        layer = self.require_readable_layer(layer_id)
        if layer.storage != "encrypted-objects":
            raise InvalidRequestError("This layer is not encrypted.")

        cached = self._private_access.get(layer_id)
        if cached is not None:
            return cached

        encryption = self._require_encryption()
        root = self._require_store().layer_root(layer_id)
        access = PrivateLayerAccess(
            layer_id=layer_id,
            root=root,
            key=encryption.keys.key_for(layer_id),
            header=LayerHeader.load(root),
        )
        self._private_access[layer_id] = access
        return access

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

    # -- saved views ---------------------------------------------------------

    def saved_views(self) -> list[ViewConfig]:
        return list(self.descriptor.saved_views)

    def save_view(self, view: ViewConfig) -> ViewConfig:
        descriptor = self.descriptor
        existing = next(
            (index for index, saved in enumerate(descriptor.saved_views) if saved.id == view.id),
            None,
        )
        if existing is None:
            descriptor.saved_views.append(view)
        else:
            descriptor.saved_views[existing] = view
        self._save()
        return view

    def delete_view(self, view_id: str) -> None:
        descriptor = self.descriptor
        descriptor.saved_views = [v for v in descriptor.saved_views if v.id != view_id]
        self._save()
