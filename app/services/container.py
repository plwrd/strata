"""Service container.

Explicit construction, no global singletons, no service locator: bridges receive
the container, services receive the collaborators they actually use. This is what
makes the whole graph constructible inside a test with a temporary directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.domain.collaboration import TreeNode
from app.infrastructure.crdt.relay import DirectoryRelay, HttpRelay, Relay
from app.services.ai_generation_service import AIGenerationService
from app.services.ai_service import AIService
from app.services.collaboration_service import CollaborationService
from app.services.context_export_service import ContextExportService
from app.services.encryption_service import EncryptionService
from app.services.graph_service import GraphService
from app.services.job_service import JobService
from app.services.note_service import NoteService
from app.services.operation_service import OperationService
from app.services.search_service import SearchService
from app.services.settings_service import SettingsService
from app.services.snapshot_service import SnapshotService
from app.services.view_service import ViewService
from app.services.watch_service import WatchService
from app.services.workspace_service import WorkspaceService

APP_VERSION = "1.3.1"


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    data_dir: Path
    log_dir: Path
    default_workspace: Path

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"


class Services:
    """Everything the bridges are allowed to reach."""

    def __init__(self, paths: Paths, *, environment: str = "production") -> None:
        self.paths = paths
        self.environment = environment
        self.app_version = APP_VERSION

        self.settings = SettingsService(paths.settings_file)
        self.watcher = WatchService()
        self.encryption = EncryptionService()
        self.workspace = WorkspaceService(
            on_open=self.watcher.start,
            on_close=self.watcher.stop,
            encryption=self.encryption,
        )
        self.notes = NoteService(self.workspace)
        self.search = SearchService(self.notes, self.workspace)
        # The graph asks the search service for semantic similarity, but only through
        # a plain function — the two stay decoupled (ADR-0010).
        self.graph = GraphService(self.workspace, self.notes, self.search.similar_pairs)
        self.exports = ContextExportService(self.workspace, self.notes, self.graph)
        self.ai = AIService(self.workspace, self.settings)
        self.snapshots = SnapshotService(self.workspace)
        self.operations = OperationService(self.workspace, self.notes, self.snapshots)
        self.ai_generation = AIGenerationService(self.ai)
        self.views = ViewService(self.workspace, self.notes)
        self.jobs = JobService()
        self.collaboration = self._build_collaboration()

        # Any change on disk invalidates the indexes. Rebuilding is lazy, so this is
        # a flag, not a rebuild — the cost lands on the next search, off the write.
        self.watcher.changed.connect(lambda _origin: self.search.invalidate())

        # Anything that caches decrypted private content must be torn down when a
        # layer locks. Registering here — at the one place that knows every
        # component — means a new cache cannot quietly forget to.
        self.encryption.on_lock(self._forget_private_state)

    def _forget_private_state(self, layer_id: str) -> None:
        """Drop everything derived from a layer that has just locked."""
        self.search.forget_layer(layer_id)
        self.collaboration.forget_layer(layer_id)

    def _build_collaboration(self) -> CollaborationService:
        """Wire the collaboration service to the workspace and key holder.

        The relay is a persistent shared-directory relay under the data dir: it
        forwards only sealed blobs, so two Strata instances that see the same
        directory (a synced folder, a LAN mount) converge. A hosted network relay
        would implement the same interface; it is future work, not more trust.
        """

        def key_for(layer_id: str) -> bytes:
            return self.encryption.keys.key_for(layer_id)

        def doc_root_for(layer_id: str) -> Path:
            return self.workspace.layer_root(layer_id) / "crdt"

        def ensure_readable(layer_id: str) -> None:
            self.workspace.require_readable_layer(layer_id)

        def seed_content(layer_id: str) -> tuple[list[TreeNode], dict[str, str]]:
            nodes: list[TreeNode] = [
                TreeNode(
                    node_id=folder.id,
                    name=folder.name,
                    parent=folder.parent_id,
                    is_note=False,
                )
                for folder in self.notes.list_folders([layer_id])
            ]
            bodies: dict[str, str] = {}
            for note in self.notes.list_notes([layer_id]):
                nodes.append(
                    TreeNode(
                        node_id=note.metadata.id,
                        name=note.metadata.title,
                        parent=note.metadata.parent_id,
                        is_note=True,
                    )
                )
                bodies[note.metadata.id] = note.content
            return nodes, bodies

        # A configured relay URL syncs collaboration over the network; otherwise
        # a shared-directory relay handles the local/synced-folder case. Either
        # way the relay only ever sees ciphertext.
        relay_url = self.settings.settings.relay_url.strip()
        relay: Relay = (
            HttpRelay(relay_url) if relay_url else DirectoryRelay(self.paths.data_dir / "relay")
        )
        return CollaborationService(
            key_for=key_for,
            doc_root_for=doc_root_for,
            ensure_readable=ensure_readable,
            seed_content=seed_content,
            relay=relay,
        )

    @property
    def is_development(self) -> bool:
        return self.environment == "development"
