"""Per-note version history for Markdown layers.

One JSONL file per note under ``.strata/versions/<layer_id>/<note_id>.jsonl``,
appended before every mutation. Public-layer note ids are derived from the path
(ADR-0004), so a rename or move changes the id — :meth:`relocate` moves the
history file with it, keeping the trail attached to the note.

Private layers are excluded by design: a version file is plaintext on disk,
which a private layer's content must never be. ``supports()`` is the single
place that rule lives.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from app.domain.errors import NotFoundError
from app.domain.note import Note
from app.domain.versions import NoteVersion, NoteVersionSummary
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.storage.markdown_store import now_iso
from app.infrastructure.storage.paths import replace_atomic, resolve_within
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)

VERSIONS_DIR = "versions"
# Oldest versions beyond the cap are dropped on write. Fifty mutations of one
# note is a deep trail; unbounded history is a disk leak.
MAX_VERSIONS = 50


class VersionService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self._workspace = workspace

    # -- scope ---------------------------------------------------------------

    def supports(self, layer_id: str) -> bool:
        """Versions exist only where plaintext on disk is already the deal."""
        if not self._workspace.is_open:
            return False
        layer = self._workspace.descriptor.layer(layer_id)
        return layer is not None and layer.storage == "markdown"

    def _path(self, layer_id: str, note_id: str) -> Path | None:
        if not self.supports(layer_id):
            return None
        root = self._workspace.root / ".strata" / VERSIONS_DIR
        return resolve_within(root, layer_id, f"{note_id}.jsonl")

    # -- recording -----------------------------------------------------------

    def record(self, note: Note, *, origin: str, change: str) -> None:
        """Capture the note's current state before a mutation replaces it.

        Never raises: losing one version must not fail the write it precedes.
        """
        path = self._path(note.metadata.layer_id, note.metadata.id)
        if path is None:
            return
        version = NoteVersion(
            created_at=now_iso(),
            origin=origin,
            change=change,
            title=note.metadata.title,
            content=note.content,
            properties=dict(note.metadata.properties),
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(version.model_dump_json() + "\n")
            self._enforce_cap(path)
        except OSError:
            logger.warning("versions.record_failed", layer_id=note.metadata.layer_id)

    def _enforce_cap(self, path: Path) -> None:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) <= MAX_VERSIONS:
            return
        temporary = path.with_suffix(".jsonl.tmp")
        temporary.write_text(
            "\n".join(lines[-MAX_VERSIONS:]) + "\n", encoding="utf-8", newline="\n"
        )
        replace_atomic(temporary, path)

    # -- reading -------------------------------------------------------------

    def _load(self, layer_id: str, note_id: str) -> list[NoteVersion]:
        path = self._path(layer_id, note_id)
        if path is None or not path.is_file():
            return []
        versions: list[NoteVersion] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                versions.append(NoteVersion.model_validate_json(line))
            except ValidationError:
                continue  # one corrupt line loses one version, never the history
        for index, version in enumerate(versions):
            version.index = index
        return versions

    def list_versions(self, layer_id: str, note_id: str) -> list[NoteVersionSummary]:
        """Newest first. Empty for private layers — that is a rule, not a gap."""
        return [
            NoteVersionSummary(
                index=version.index,
                created_at=version.created_at,
                origin=version.origin,
                change=version.change,
                title=version.title,
                size_chars=len(version.content),
            )
            for version in reversed(self._load(layer_id, note_id))
        ]

    def get_version(self, layer_id: str, note_id: str, index: int) -> NoteVersion:
        versions = self._load(layer_id, note_id)
        if index < 0 or index >= len(versions):
            raise NotFoundError("That version no longer exists.")
        return versions[index]

    # -- lifecycle -----------------------------------------------------------

    def relocate(self, layer_id: str, old_note_id: str, new_note_id: str) -> None:
        """Keep history attached across rename/move (path-derived ids change)."""
        if old_note_id == new_note_id:
            return
        old_path = self._path(layer_id, old_note_id)
        new_path = self._path(layer_id, new_note_id)
        if old_path is None or new_path is None or not old_path.is_file():
            return
        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            replace_atomic(old_path, new_path)
        except OSError:
            logger.warning("versions.relocate_failed", layer_id=layer_id)
