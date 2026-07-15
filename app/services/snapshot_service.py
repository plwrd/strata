"""Workspace snapshots.

A snapshot is a point-in-time copy of the workspace's content, taken so that a
large or risky change — especially an AI reorganisation — is recoverable. "Never
allow destructive AI reorganization without recoverable history" is a product rule,
and this is how it is kept: the transactional applier takes a snapshot *before* it
touches anything, and undo restores it.

What a snapshot captures: the public layers' Markdown trees and every private
layer's encrypted objects, exactly as they are on disk. Private layers are copied
**as ciphertext** — a snapshot never decrypts anything, so taking one while a layer
is unlocked does not write its plaintext anywhere. A locked layer is copied too (its
opaque objects are just files), and stays exactly as unreadable in the snapshot as
it is live.

Snapshots live under ``.strata/snapshots/<id>/`` and are plain copies. They are not
encrypted as a whole beyond the per-object encryption already in place, which is an
honest limitation: a snapshot of a *public* layer is as readable as the public layer
itself. That is the same trust level as the workspace it copies.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.domain.errors import InvalidRequestError, NotFoundError
from app.domain.ids import new_export_id
from app.infrastructure.logging.logger import get_logger
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)

SNAPSHOTS_DIR = "snapshots"
MANIFEST_NAME = "snapshot.json"
LAYERS_SUBDIR = "layers"


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class SnapshotInfo:
    id: str
    name: str
    created_at: str
    kind: str  # "manual" | "pre-ai" | "pre-restore"
    layer_count: int
    note_count: int


class SnapshotService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self._workspace = workspace

    def _root(self) -> Path:
        root = self._workspace.root / ".strata" / SNAPSHOTS_DIR
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _ignore(self, _directory: str, names: list[str]) -> set[str]:
        # Never copy a layer's own transient temp files into a snapshot.
        return {name for name in names if name.endswith(".tmp")}

    def create(self, name: str, *, kind: str = "manual") -> SnapshotInfo:
        descriptor = self._workspace.descriptor
        snapshot_id = new_export_id()
        target = self._root() / snapshot_id
        (target / LAYERS_SUBDIR).mkdir(parents=True, exist_ok=True)

        note_count = 0
        for layer in descriptor.layers:
            source = self._workspace.root / "layers" / layer.id
            if not source.is_dir():
                continue
            # A plain copy. Private layers are copied as ciphertext — the snapshot
            # never decrypts, so an unlocked layer's plaintext is not written here.
            shutil.copytree(
                source,
                target / LAYERS_SUBDIR / layer.id,
                ignore=self._ignore,
                dirs_exist_ok=True,
            )

        try:
            note_count = len(self._workspace_note_count())
        except Exception:
            note_count = 0

        info = SnapshotInfo(
            id=snapshot_id,
            name=name,
            created_at=_now(),
            kind=kind,
            layer_count=len(descriptor.layers),
            note_count=note_count,
        )
        self._write_manifest(target, info)
        logger.info("snapshot.created", snapshot_id=snapshot_id, kind=kind)
        return info

    def _workspace_note_count(self) -> list[object]:
        from app.services.note_service import NoteService

        return list(NoteService(self._workspace).list_notes())

    def _write_manifest(self, target: Path, info: SnapshotInfo) -> None:
        (target / MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "id": info.id,
                    "name": info.name,
                    "created_at": info.created_at,
                    "kind": info.kind,
                    "layer_count": info.layer_count,
                    "note_count": info.note_count,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def list(self) -> list[SnapshotInfo]:
        snapshots: list[SnapshotInfo] = []
        for directory in self._root().iterdir():
            manifest = directory / MANIFEST_NAME
            if not manifest.is_file():
                continue
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            snapshots.append(
                SnapshotInfo(
                    id=str(raw["id"]),
                    name=str(raw.get("name", "")),
                    created_at=str(raw.get("created_at", "")),
                    kind=str(raw.get("kind", "manual")),
                    layer_count=int(raw.get("layer_count", 0)),
                    note_count=int(raw.get("note_count", 0)),
                )
            )
        return sorted(snapshots, key=lambda info: info.created_at, reverse=True)

    def restore(self, snapshot_id: str, *, take_safety_snapshot: bool = True) -> SnapshotInfo:
        """Restore a snapshot over the live workspace.

        Before overwriting anything, a safety snapshot of the *current* state is
        taken, so a restore is itself undoable — restoring the wrong snapshot must
        not be a one-way door.
        """
        source = self._root() / snapshot_id / LAYERS_SUBDIR
        if not source.is_dir():
            raise NotFoundError("Snapshot not found.")

        if take_safety_snapshot:
            self.create("Before restore", kind="pre-restore")

        # Unlock state does not survive a restore: the keys still belong to the
        # layers, but the manifest/objects underneath them have just been replaced,
        # so any cached decrypted view must be dropped.
        self._workspace.lock_all_layers()

        live_layers = self._workspace.root / "layers"
        for layer_dir in source.iterdir():
            if not layer_dir.is_dir():
                continue
            destination = live_layers / layer_dir.name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(layer_dir, destination, ignore=self._ignore)

        # Indexes and any decrypted caches now describe content that is gone.
        for layer in self._workspace.descriptor.layers:
            self._workspace._private_access.pop(layer.id, None)

        logger.info("snapshot.restored", snapshot_id=snapshot_id)
        return self._info_for(snapshot_id)

    def delete(self, snapshot_id: str) -> None:
        target = self._root() / snapshot_id
        if not target.is_dir():
            raise NotFoundError("Snapshot not found.")
        shutil.rmtree(target, ignore_errors=True)
        logger.info("snapshot.deleted", snapshot_id=snapshot_id)

    def _info_for(self, snapshot_id: str) -> SnapshotInfo:
        for info in self.list():
            if info.id == snapshot_id:
                return info
        raise InvalidRequestError("Snapshot manifest is missing.")

    def snapshot_root(self, snapshot_id: str) -> Path:
        return self._root() / snapshot_id
