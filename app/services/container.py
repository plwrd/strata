"""Service container.

Explicit construction, no global singletons, no service locator: bridges receive
the container, services receive the collaborators they actually use. This is what
makes the whole graph constructible inside a test with a temporary directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.context_export_service import ContextExportService
from app.services.encryption_service import EncryptionService
from app.services.graph_service import GraphService
from app.services.job_service import JobService
from app.services.note_service import NoteService
from app.services.search_service import SearchService
from app.services.settings_service import SettingsService
from app.services.watch_service import WatchService
from app.services.workspace_service import WorkspaceService

APP_VERSION = "0.1.0"


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
        self.graph = GraphService(self.workspace, self.notes)
        self.search = SearchService(self.notes)
        self.exports = ContextExportService(self.workspace, self.notes, self.graph)
        self.jobs = JobService()

        # Anything that caches decrypted private content must be torn down when a
        # layer locks. Registering here — at the one place that knows every
        # component — means a new cache cannot quietly forget to.
        self.encryption.on_lock(self._forget_private_state)

    def _forget_private_state(self, layer_id: str) -> None:
        """Drop everything derived from a layer that has just locked."""
        self.search.forget_layer(layer_id)

    @property
    def is_development(self) -> bool:
        return self.environment == "development"
