"""Encrypted object storage for private layers.

On disk::

    layers/layer_ab12/
      layer.header                 # wrapped keys only; no content, no names
      objects/
        02/02f8a72be4304c92…       # opaque: 16 random bytes as 32 hex chars
        a9/a937c89dc81f4cc8…

There are no filenames, no extensions, and no folders that mean anything. The real
structure — titles, the folder tree, tags, properties, links, attachment names —
lives *inside* an encrypted manifest object, which is itself just another opaque
file.

Object ids are **random**, never derived from the name (ADR-0004). Deterministic
filename encryption would let anyone with the directory listing confirm a guess:
"does this vault contain a note called `Acquisition of Northwind`?" becomes a
single hash comparison. Random ids make that question unanswerable from the disk.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain.errors import ConflictError, NotFoundError
from app.domain.note import Note, NoteMetadata, extract_links, extract_tags, word_count
from app.infrastructure.encryption.container import (
    TYPE_ATTACHMENT,
    TYPE_MANIFEST,
    TYPE_NOTE,
    open_sealed,
    seal,
)
from app.infrastructure.encryption.primitives import DecryptionError

OBJECTS_DIR = "objects"
MANIFEST_FORMAT_VERSION = 1


def new_raw_object_id() -> bytes:
    return secrets.token_bytes(16)


@dataclass
class ManifestEntry:
    """One knowledge object, as recorded in the encrypted manifest."""

    object_id: str  # hex; the file under objects/<xx>/
    kind: str  # "note" | "folder" | "attachment"
    title: str = ""
    folder_path: str = ""
    parent_id: str | None = None
    filename: str = ""
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    size_bytes: int = 0
    word_count: int = 0
    trashed_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "kind": self.kind,
            "title": self.title,
            "folder_path": self.folder_path,
            "parent_id": self.parent_id,
            "filename": self.filename,
            "tags": self.tags,
            "aliases": self.aliases,
            "properties": self.properties,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "size_bytes": self.size_bytes,
            "word_count": self.word_count,
            "trashed_at": self.trashed_at,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ManifestEntry:
        return cls(
            object_id=str(raw["object_id"]),
            kind=str(raw.get("kind", "note")),
            title=str(raw.get("title", "")),
            folder_path=str(raw.get("folder_path", "")),
            parent_id=raw.get("parent_id"),
            filename=str(raw.get("filename", "")),
            tags=list(raw.get("tags", [])),
            aliases=list(raw.get("aliases", [])),
            properties=dict(raw.get("properties", {})),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
            size_bytes=int(raw.get("size_bytes", 0)),
            word_count=int(raw.get("word_count", 0)),
            trashed_at=raw.get("trashed_at"),
        )


@dataclass
class Manifest:
    format_version: int = MANIFEST_FORMAT_VERSION
    entries: dict[str, ManifestEntry] = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        payload = {
            "format_version": self.format_version,
            "entries": [entry.to_json() for entry in self.entries.values()],
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> Manifest:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # The manifest authenticated but will not parse: that is corruption we
            # cannot paper over, and pretending the layer is empty would look like
            # data loss.
            raise DecryptionError("The layer manifest is corrupt.") from exc
        version = int(payload.get("format_version", 0))
        if version > MANIFEST_FORMAT_VERSION:
            raise DecryptionError("This layer was written by a newer version of Strata.")
        entries = [ManifestEntry.from_json(item) for item in payload.get("entries", [])]
        return cls(format_version=version, entries={entry.object_id: entry for entry in entries})


class EncryptedLayerStore:
    """Reads and writes one private layer. Requires the layer key on every call.

    The key is passed in rather than held: this object cannot be used after a lock
    because the caller has nothing to pass it.
    """

    def __init__(self, layer_id: str, root: Path, *, padding: bool = True) -> None:
        self.layer_id = layer_id
        self.root = root
        self.padding = padding

    # -- object files --------------------------------------------------------

    def ensure(self) -> None:
        (self.root / OBJECTS_DIR).mkdir(parents=True, exist_ok=True)

    def _object_path(self, object_id: str) -> Path:
        return self.root / OBJECTS_DIR / object_id[:2] / object_id

    def _write_object(self, key: bytes, object_id: str, object_type: int, plaintext: bytes) -> None:
        blob = seal(
            key=key,
            layer_id=self.layer_id,
            object_id=bytes.fromhex(object_id),
            object_type=object_type,
            plaintext=plaintext,
            pad=self.padding,
        )
        path = self._object_path(object_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(blob)
        temporary.replace(path)

    def _read_object(self, key: bytes, object_id: str, object_type: int) -> bytes:
        path = self._object_path(object_id)
        if not path.is_file():
            raise NotFoundError("Knowledge object not found.")
        return open_sealed(
            key=key,
            layer_id=self.layer_id,
            object_id=bytes.fromhex(object_id),
            expected_type=object_type,
            blob=path.read_bytes(),
        )

    def _delete_object(self, object_id: str) -> None:
        path = self._object_path(object_id)
        if path.is_file():
            path.unlink()

    def object_ids(self) -> list[str]:
        objects = self.root / OBJECTS_DIR
        if not objects.is_dir():
            return []
        return sorted(
            path.name
            for shard in objects.iterdir()
            if shard.is_dir()
            for path in shard.iterdir()
            if path.is_file() and not path.name.endswith(".tmp")
        )

    # -- manifest ------------------------------------------------------------

    def create_manifest(self, key: bytes) -> str:
        object_id = new_raw_object_id().hex()
        self._write_object(key, object_id, TYPE_MANIFEST, Manifest().to_bytes())
        return object_id

    def read_manifest(self, key: bytes, manifest_id: str) -> Manifest:
        return Manifest.from_bytes(self._read_object(key, manifest_id, TYPE_MANIFEST))

    def write_manifest(self, key: bytes, manifest_id: str, manifest: Manifest) -> None:
        self._write_object(key, manifest_id, TYPE_MANIFEST, manifest.to_bytes())

    # -- notes ---------------------------------------------------------------

    def read_note(self, key: bytes, entry: ManifestEntry) -> Note:
        body = self._read_object(key, entry.object_id, TYPE_NOTE).decode("utf-8", errors="replace")
        return Note(metadata=self._metadata_for(entry, body), content=body)

    def _metadata_for(self, entry: ManifestEntry, body: str) -> NoteMetadata:
        return NoteMetadata(
            id=entry.object_id,
            layer_id=self.layer_id,
            title=entry.title,
            folder_path=entry.folder_path,
            aliases=list(entry.aliases),
            tags=extract_tags(body, list(entry.tags)),
            properties=dict(entry.properties),
            links=extract_links(body),
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            size_bytes=entry.size_bytes,
            word_count=entry.word_count,
        )

    def write_note(
        self,
        key: bytes,
        manifest: Manifest,
        *,
        object_id: str | None,
        title: str,
        folder_path: str,
        content: str,
        properties: dict[str, Any],
        timestamp: str,
    ) -> ManifestEntry:
        object_id = object_id or new_raw_object_id().hex()
        body = content.encode("utf-8")
        self._write_object(key, object_id, TYPE_NOTE, body)

        existing = manifest.entries.get(object_id)
        entry = ManifestEntry(
            object_id=object_id,
            kind="note",
            title=title,
            folder_path=folder_path,
            filename=f"{title}.md",
            tags=[tag for tag in (properties.get("tags") or []) if isinstance(tag, str)],
            aliases=list(existing.aliases) if existing else [],
            properties=dict(properties),
            created_at=existing.created_at if existing else timestamp,
            updated_at=timestamp,
            size_bytes=len(body),
            word_count=word_count(content),
            trashed_at=existing.trashed_at if existing else None,
        )
        manifest.entries[object_id] = entry
        return entry

    def delete_note_object(self, object_id: str) -> None:
        """Remove the ciphertext. Only called when emptying the trash."""
        self._delete_object(object_id)

    # -- folders and attachments ---------------------------------------------

    def add_folder(
        self, manifest: Manifest, *, path: str, name: str, timestamp: str
    ) -> ManifestEntry:
        for entry in manifest.entries.values():
            if entry.kind == "folder" and entry.folder_path == path:
                raise ConflictError("A folder with that name already exists.")
        entry = ManifestEntry(
            object_id=new_raw_object_id().hex(),
            kind="folder",
            title=name,
            folder_path=path,
            created_at=timestamp,
            updated_at=timestamp,
        )
        manifest.entries[entry.object_id] = entry
        return entry

    def write_attachment(
        self,
        key: bytes,
        manifest: Manifest,
        *,
        filename: str,
        data: bytes,
        timestamp: str,
    ) -> ManifestEntry:
        object_id = new_raw_object_id().hex()
        self._write_object(key, object_id, TYPE_ATTACHMENT, data)
        entry = ManifestEntry(
            object_id=object_id,
            kind="attachment",
            title=filename,
            filename=filename,
            created_at=timestamp,
            updated_at=timestamp,
            size_bytes=len(data),
        )
        manifest.entries[object_id] = entry
        return entry

    def read_attachment(self, key: bytes, object_id: str) -> bytes:
        return self._read_object(key, object_id, TYPE_ATTACHMENT)

    # -- rotation ------------------------------------------------------------

    def rotate(self, old_key: bytes, new_key: bytes, manifest_id: str) -> int:
        """Re-encrypt every object under a new key.

        This is what actually revokes someone who kept the old key. It is expensive
        (every object is rewritten), and it is the only honest way to do it.

        Object ids are preserved so the manifest stays valid, and each object is
        written atomically, so an interrupted rotation leaves a layer whose objects
        are a mix of generations — which is why the caller only commits the new
        header once this returns.
        """
        manifest = self.read_manifest(old_key, manifest_id)
        rewritten = 0

        for entry in manifest.entries.values():
            object_type = {
                "note": TYPE_NOTE,
                "attachment": TYPE_ATTACHMENT,
            }.get(entry.kind)
            if object_type is None:
                continue  # folders exist only in the manifest
            plaintext = self._read_object(old_key, entry.object_id, object_type)
            self._write_object(new_key, entry.object_id, object_type, plaintext)
            rewritten += 1

        self._write_object(new_key, manifest_id, TYPE_MANIFEST, manifest.to_bytes())
        return rewritten + 1
