"""A working handle on an unlocked private layer.

Bundles the store, the key, the header and the manifest so that callers do not
juggle four objects — and, more importantly, so that *obtaining* one of these is
the single moment where the lock is checked. If you hold a
:class:`PrivateLayerAccess`, the layer was unlocked when you got it.

Every mutation writes the object and then the manifest. The manifest is written
last, so a crash between the two leaves an orphaned ciphertext blob that nothing
references — wasted bytes, but never a manifest pointing at an object that does
not exist. Losing the reverse would look like data loss.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain.errors import ConflictError, NotFoundError
from app.domain.note import FolderNode, Note
from app.infrastructure.encryption.layer_header import LayerHeader
from app.infrastructure.storage.encrypted_store import (
    EncryptedLayerStore,
    Manifest,
    ManifestEntry,
)
from app.infrastructure.storage.paths import safe_filename


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class PrivateLayerAccess:
    def __init__(
        self,
        *,
        layer_id: str,
        root: Path,
        key: bytes,
        header: LayerHeader,
    ) -> None:
        self.layer_id = layer_id
        self.root = root
        self._key = key
        self._header = header
        self._store = EncryptedLayerStore(layer_id, root, padding=header.padding_enabled)
        self._manifest: Manifest | None = None

    @property
    def manifest(self) -> Manifest:
        if self._manifest is None:
            self._manifest = self._store.read_manifest(self._key, self._header.manifest_object_id)
        return self._manifest

    def _commit(self) -> None:
        self._store.write_manifest(self._key, self._header.manifest_object_id, self.manifest)

    def _live(self, kind: str | None = None) -> list[ManifestEntry]:
        return [
            entry
            for entry in self.manifest.entries.values()
            if entry.trashed_at is None and (kind is None or entry.kind == kind)
        ]

    # -- reading -------------------------------------------------------------

    def list_notes(self) -> list[Note]:
        return [self._store.read_note(self._key, entry) for entry in self._live("note")]

    def list_folders(self) -> list[FolderNode]:
        return [
            FolderNode(
                id=entry.object_id,
                layer_id=self.layer_id,
                name=entry.title,
                path=entry.folder_path,
                parent_id=entry.parent_id,
            )
            for entry in self._live("folder")
        ]

    def get_note(self, note_id: str) -> Note:
        entry = self.manifest.entries.get(note_id)
        if entry is None or entry.kind != "note" or entry.trashed_at is not None:
            raise NotFoundError("Knowledge object not found.")
        return self._store.read_note(self._key, entry)

    def has_note(self, note_id: str) -> bool:
        entry = self.manifest.entries.get(note_id)
        return entry is not None and entry.kind == "note" and entry.trashed_at is None

    def _title_taken(self, folder_path: str, title: str, *, ignoring: str | None = None) -> bool:
        return any(
            entry.object_id != ignoring
            and entry.folder_path == folder_path
            and entry.title.lower() == title.lower()
            for entry in self._live("note")
        )

    # -- writing -------------------------------------------------------------

    def create_note(
        self,
        *,
        folder_path: str,
        title: str,
        content: str,
        properties: dict[str, Any] | None = None,
    ) -> Note:
        title = safe_filename(title)
        if self._title_taken(folder_path, title):
            raise ConflictError("A note with that name already exists in this folder.")

        entry = self._store.write_note(
            self._key,
            self.manifest,
            object_id=None,
            title=title,
            folder_path=folder_path,
            content=content,
            properties=dict(properties or {}),
            timestamp=_now(),
        )
        self._commit()
        return self._store.read_note(self._key, entry)

    def update_note(self, note_id: str, content: str) -> Note:
        entry = self._require(note_id, "note")
        updated = self._store.write_note(
            self._key,
            self.manifest,
            object_id=entry.object_id,
            title=entry.title,
            folder_path=entry.folder_path,
            content=content,
            properties=entry.properties,
            timestamp=_now(),
        )
        self._commit()
        return self._store.read_note(self._key, updated)

    def update_properties(self, note_id: str, properties: dict[str, Any]) -> Note:
        entry = self._require(note_id, "note")
        note = self._store.read_note(self._key, entry)
        updated = self._store.write_note(
            self._key,
            self.manifest,
            object_id=entry.object_id,
            title=entry.title,
            folder_path=entry.folder_path,
            content=note.content,
            properties=dict(properties),
            timestamp=_now(),
        )
        self._commit()
        return self._store.read_note(self._key, updated)

    def rename_note(self, note_id: str, title: str) -> Note:
        entry = self._require(note_id, "note")
        title = safe_filename(title)
        if self._title_taken(entry.folder_path, title, ignoring=note_id):
            raise ConflictError("A note with that name already exists in this folder.")

        # The object id does not change on rename — it is random and has nothing to
        # do with the name. So links by id survive, and the ciphertext is untouched.
        entry.title = title
        entry.filename = f"{title}.md"
        entry.updated_at = _now()
        self._commit()
        return self._store.read_note(self._key, entry)

    def move_note(self, note_id: str, folder_path: str) -> Note:
        entry = self._require(note_id, "note")
        if self._title_taken(folder_path, entry.title, ignoring=note_id):
            raise ConflictError("A note with that name already exists in the destination folder.")
        entry.folder_path = folder_path
        entry.updated_at = _now()
        self._commit()
        return self._store.read_note(self._key, entry)

    def duplicate_note(self, note_id: str) -> Note:
        entry = self._require(note_id, "note")
        note = self._store.read_note(self._key, entry)

        base = f"{entry.title} copy"
        title = base
        counter = 2
        while self._title_taken(entry.folder_path, title):
            title = f"{base} {counter}"
            counter += 1

        return self.create_note(
            folder_path=entry.folder_path,
            title=title,
            content=note.content,
            properties=entry.properties,
        )

    # -- trash ---------------------------------------------------------------

    def trash_note(self, note_id: str) -> str:
        """Soft-delete. The ciphertext stays encrypted in the layer, not in a
        plaintext trash folder — deleting a private note must not decrypt it."""
        entry = self._require(note_id, "note")
        entry.trashed_at = _now()
        self._commit()
        return entry.object_id

    def list_trash(self) -> list[ManifestEntry]:
        return [
            entry
            for entry in self.manifest.entries.values()
            if entry.trashed_at is not None and entry.kind == "note"
        ]

    def restore_note(self, note_id: str) -> Note:
        entry = self.manifest.entries.get(note_id)
        if entry is None or entry.trashed_at is None:
            raise NotFoundError("That trash entry no longer exists.")
        if self._title_taken(entry.folder_path, entry.title):
            raise ConflictError("A note with that name exists again; rename it first.")
        entry.trashed_at = None
        entry.updated_at = _now()
        self._commit()
        return self._store.read_note(self._key, entry)

    def empty_trash(self) -> int:
        removed = 0
        for entry in list(self.manifest.entries.values()):
            if entry.trashed_at is None:
                continue
            self._store.delete_note_object(entry.object_id)
            del self.manifest.entries[entry.object_id]
            removed += 1
        if removed:
            self._commit()
        return removed

    # -- folders and attachments ---------------------------------------------

    def create_folder(self, folder_path: str, name: str) -> FolderNode:
        name = safe_filename(name)
        path = f"{folder_path}/{name}" if folder_path else name
        entry = self._store.add_folder(self.manifest, path=path, name=name, timestamp=_now())
        self._commit()
        return FolderNode(
            id=entry.object_id, layer_id=self.layer_id, name=name, path=path, parent_id=None
        )

    def rename_folder(self, folder_id: str, name: str) -> FolderNode:
        entry = self._require(folder_id, "folder")
        name = safe_filename(name)
        old_path = entry.folder_path
        parent = old_path.rsplit("/", 1)[0] if "/" in old_path else ""
        new_path = f"{parent}/{name}" if parent else name

        entry.title = name
        entry.folder_path = new_path
        entry.updated_at = _now()

        # Every note and subfolder beneath it moves too.
        for other in self.manifest.entries.values():
            if other.object_id == folder_id:
                continue
            if other.folder_path == old_path:
                other.folder_path = new_path
            elif other.folder_path.startswith(f"{old_path}/"):
                other.folder_path = new_path + other.folder_path[len(old_path) :]

        self._commit()
        return FolderNode(
            id=folder_id, layer_id=self.layer_id, name=name, path=new_path, parent_id=None
        )

    def delete_folder(self, folder_id: str) -> int:
        entry = self._require(folder_id, "folder")
        path = entry.folder_path
        trashed = 0
        timestamp = _now()

        for other in self.manifest.entries.values():
            if other.kind != "note" or other.trashed_at is not None:
                continue
            if other.folder_path == path or other.folder_path.startswith(f"{path}/"):
                other.trashed_at = timestamp
                trashed += 1

        del self.manifest.entries[folder_id]
        self._commit()
        return trashed

    def save_attachment(self, filename: str, data: bytes) -> str:
        entry = self._store.write_attachment(
            self._key, self.manifest, filename=filename, data=data, timestamp=_now()
        )
        self._commit()
        # The "path" of a private attachment is its opaque object id: there is no
        # folder on disk to point at, and inventing a readable one would leak.
        return f"strata-object://{entry.object_id}"

    def read_attachment(self, object_id: str) -> bytes:
        return self._store.read_attachment(self._key, object_id)

    # -- helpers -------------------------------------------------------------

    def _require(self, object_id: str, kind: str) -> ManifestEntry:
        entry = self.manifest.entries.get(object_id)
        if entry is None or entry.kind != kind:
            raise NotFoundError("Knowledge object not found.")
        return entry
