"""Note and folder operations across readable layers.

Resolution of wiki links happens here, once, and everything else (graph,
backlinks, export, search) consumes the result. Link targets are matched by
title or alias, case-insensitively, within the set of *readable* layers only —
a locked layer never resolves and never reports a miss differently from an
absent note.

Renames rewrite the links that point at the note. That is the one place where
Strata edits a file the user did not open, so it is deliberate, bounded (only
`[[wiki links]]`, never prose), and reported back as a count.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.domain.errors import ConflictError, InvalidRequestError, NotFoundError
from app.domain.note import FolderNode, Note, NoteLink
from app.infrastructure.storage.markdown_store import (
    MARKDOWN_SUFFIX,
    MarkdownLayerStore,
    note_id_for,
    parse_frontmatter,
    render_frontmatter,
)
from app.infrastructure.storage.paths import safe_filename
from app.services.private_layer_access import PrivateLayerAccess
from app.services.workspace_service import WorkspaceService

TRASH_DIR = "trash"


@dataclass(frozen=True)
class Backlink:
    """An incoming link, with the line it appears on so the user can see context."""

    source_id: str
    source_title: str
    layer_id: str
    relationship: str
    context: str


@dataclass(frozen=True)
class UnlinkedMention:
    source_id: str
    source_title: str
    layer_id: str
    context: str


@dataclass(frozen=True)
class LinkHealth:
    broken: list[tuple[str, str]]  # (source note id, missing target title)
    orphans: list[str]  # note ids with no incoming and no outgoing links


class NoteService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self._workspace = workspace

    # -- reading -------------------------------------------------------------

    def _markdown_layers(self, layer_ids: list[str] | None) -> list[str]:
        return [
            layer.id
            for layer in self._workspace.readable_layers()
            if layer.storage == "markdown" and (layer_ids is None or layer.id in layer_ids)
        ]

    def _private_layers(self, layer_ids: list[str] | None) -> list[str]:
        """Unlocked private layers only — `readable_layers()` already excludes the
        locked ones, and it does so by asking the key holder, not the descriptor."""
        return [
            layer.id
            for layer in self._workspace.readable_layers()
            if layer.storage == "encrypted-objects" and (layer_ids is None or layer.id in layer_ids)
        ]

    def list_notes(self, layer_ids: list[str] | None = None) -> list[Note]:
        notes: list[Note] = []
        for layer_id in self._markdown_layers(layer_ids):
            notes.extend(self._workspace.layer_store(layer_id).list_notes())
        for layer_id in self._private_layers(layer_ids):
            notes.extend(self._workspace.private_access(layer_id).list_notes())
        return notes

    def list_folders(self, layer_ids: list[str] | None = None) -> list[FolderNode]:
        folders: list[FolderNode] = []
        for layer_id in self._markdown_layers(layer_ids):
            folders.extend(self._workspace.layer_store(layer_id).list_folders())
        for layer_id in self._private_layers(layer_ids):
            folders.extend(self._workspace.private_access(layer_id).list_folders())
        return folders

    def _private_owner(self, note_id: str) -> PrivateLayerAccess | None:
        """The unlocked private layer holding this note, if any."""
        for layer_id in self._private_layers(None):
            access = self._workspace.private_access(layer_id)
            if access.has_note(note_id):
                return access
        return None

    def get_note(self, note_id: str) -> Note:
        for note in self.list_notes():
            if note.metadata.id == note_id:
                return note
        # Same error whether the note does not exist or lives in a locked layer.
        raise NotFoundError("Knowledge object not found.")

    def get_notes(self, note_ids: list[str]) -> list[Note]:
        wanted = set(note_ids)
        found = [note for note in self.list_notes() if note.metadata.id in wanted]
        order = {note_id: index for index, note_id in enumerate(note_ids)}
        return sorted(found, key=lambda note: order.get(note.metadata.id, len(order)))

    def _locate(self, note_id: str) -> tuple[MarkdownLayerStore, Note, Path]:
        for layer_id in self._markdown_layers(None):
            store = self._workspace.layer_store(layer_id)
            for path in store.iter_markdown_files():
                note = store.read_note(path)
                if note.metadata.id == note_id:
                    return store, note, path
        raise NotFoundError("Knowledge object not found.")

    # -- writing -------------------------------------------------------------

    def create_note(
        self,
        *,
        layer_id: str,
        folder_path: str = "",
        title: str,
        content: str = "",
        properties: dict[str, object] | None = None,
    ) -> Note:
        layer = self._workspace.require_readable_layer(layer_id)
        if layer.storage == "encrypted-objects":
            return self._workspace.private_access(layer_id).create_note(
                folder_path=folder_path,
                title=title,
                content=content,
                properties=dict(properties or {}),
            )

        store = self._workspace.layer_store(layer_id)
        if store.path_for(folder_path, title).exists():
            raise ConflictError("A note with that name already exists in this folder.")
        return store.write_note(
            folder_path=folder_path,
            title=title,
            content=content,
            properties=dict(properties or {}),
        )

    def update_note(self, note_id: str, content: str) -> Note:
        """Overwrite the body, preserving the frontmatter block."""
        private = self._private_owner(note_id)
        if private is not None:
            return private.update_note(note_id, content)

        store, _note, path = self._locate(note_id)
        frontmatter, _ = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        path.write_text(
            render_frontmatter(frontmatter) + content,
            encoding="utf-8",
            newline="\n",
        )
        return store.read_note(path)

    def update_properties(self, note_id: str, properties: dict[str, object]) -> Note:
        private = self._private_owner(note_id)
        if private is not None:
            return private.update_properties(note_id, dict(properties))

        store, _note, path = self._locate(note_id)
        _old, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        path.write_text(
            render_frontmatter(dict(properties)) + body,
            encoding="utf-8",
            newline="\n",
        )
        return store.read_note(path)

    def rename_note(self, note_id: str, title: str) -> tuple[Note, int]:
        """Rename a note and repoint every wiki link that referenced it.

        Returns the new note and the number of links rewritten. A rename that
        silently broke every inbound link would make linking untrustworthy, which
        is the whole point of the graph.
        """
        private = self._private_owner(note_id)
        if private is not None:
            old_title = private.get_note(note_id).metadata.title
            renamed = private.rename_note(note_id, title)
            # Links are rewritten across every *readable* layer. A locked layer is
            # not rewritten — we cannot read it, and we will not decrypt a layer the
            # user did not unlock just to fix a link.
            rewritten = self._rewrite_links(old_title, renamed.metadata.title)
            return renamed, rewritten

        store, note, path = self._locate(note_id)
        old_title = note.metadata.title
        if title.strip() == old_title:
            return note, 0

        destination = store.path_for(note.metadata.folder_path, title)
        if destination.exists():
            raise ConflictError("A note with that name already exists in this folder.")

        path.replace(destination)
        renamed = store.read_note(destination)

        # The title lives in the filename unless frontmatter overrides it; if it
        # does, the frontmatter is the source of truth and must move too.
        if "title" in note.metadata.properties or note.metadata.properties.get("title"):
            renamed = self.update_properties(
                renamed.metadata.id, {**renamed.metadata.properties, "title": title}
            )

        rewritten = self._rewrite_links(old_title, renamed.metadata.title)
        return renamed, rewritten

    def _rewrite_links(self, old_title: str, new_title: str) -> int:
        """Repoint `[[Old Title]]` (and `[[Old Title|alias]]`, `[[Old#heading]]`).

        Runs across every readable layer, public and unlocked-private alike. A
        *locked* layer is left alone: we cannot read it, and unlocking someone's
        layer without being asked — even to fix a link — is not ours to do. The
        link there stays stale until the layer is next unlocked and the note is
        touched, which is the honest trade.
        """
        pattern = re.compile(
            r"\[\[\s*" + re.escape(old_title) + r"\s*(?=[\]|#^])",
            re.IGNORECASE,
        )
        rewritten = 0

        for layer_id in self._markdown_layers(None):
            store = self._workspace.layer_store(layer_id)
            for path in store.iter_markdown_files():
                text = path.read_text(encoding="utf-8", errors="replace")
                updated, count = pattern.subn(f"[[{new_title}", text)
                if count:
                    path.write_text(updated, encoding="utf-8", newline="\n")
                    rewritten += count

        for layer_id in self._private_layers(None):
            access = self._workspace.private_access(layer_id)
            for note in access.list_notes():
                updated, count = pattern.subn(f"[[{new_title}", note.content)
                if count:
                    access.update_note(note.metadata.id, updated)
                    rewritten += count

        return rewritten

    def move_note(self, note_id: str, folder_path: str) -> Note:
        private = self._private_owner(note_id)
        if private is not None:
            return private.move_note(note_id, folder_path)

        store, note, path = self._locate(note_id)
        destination = store.path_for(folder_path, note.metadata.title)
        if destination == path:
            return note
        if destination.exists():
            raise ConflictError("A note with that name already exists in the destination folder.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.replace(destination)
        return store.read_note(destination)

    def duplicate_note(self, note_id: str) -> Note:
        private = self._private_owner(note_id)
        if private is not None:
            return private.duplicate_note(note_id)

        store, note, path = self._locate(note_id)
        base = f"{note.metadata.title} copy"
        title = base
        counter = 2
        while store.path_for(note.metadata.folder_path, title).exists():
            title = f"{base} {counter}"
            counter += 1
        destination = store.path_for(note.metadata.folder_path, title)
        shutil.copy2(path, destination)
        return store.read_note(destination)

    # -- trash ---------------------------------------------------------------

    def _trash_root(self) -> Path:
        root = self._workspace.root / ".strata" / TRASH_DIR
        root.mkdir(parents=True, exist_ok=True)
        return root

    def delete_note(self, note_id: str) -> str:
        """Move a note to the trash. Deleting is never immediate destruction.

        A *private* note is soft-deleted inside its own encrypted layer. Moving it
        to the workspace's plaintext trash folder would mean that "delete" quietly
        decrypted it — the exact opposite of what the user asked for.
        """
        private = self._private_owner(note_id)
        if private is not None:
            return private.trash_note(note_id)

        _store, note, path = self._locate(note_id)
        trash = self._trash_root()
        # The trash entry records where it came from so restore is exact.
        stamp = f"{note.metadata.layer_id}__{safe_filename(note.metadata.folder_path or 'root')}"
        target = trash / f"{stamp}__{path.name}"
        counter = 2
        while target.exists():
            target = trash / f"{stamp}__{path.stem} {counter}{MARKDOWN_SUFFIX}"
            counter += 1
        path.replace(target)
        return target.name

    def list_trash(self) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for path in sorted(self._trash_root().glob(f"*{MARKDOWN_SUFFIX}")):
            layer_id, _, rest = path.stem.partition("__")
            folder, _, title = rest.partition("__")
            entries.append(
                {
                    "entry": path.name,
                    "layer_id": layer_id,
                    "folder_path": "" if folder == "root" else folder,
                    "title": title,
                }
            )

        # A locked layer contributes nothing here: its trash is inside it, and it
        # is encrypted. The count would otherwise leak how much was deleted.
        for layer_id in self._private_layers(None):
            access = self._workspace.private_access(layer_id)
            for entry in access.list_trash():
                entries.append(
                    {
                        "entry": entry.object_id,
                        "layer_id": layer_id,
                        "folder_path": entry.folder_path,
                        "title": entry.title,
                    }
                )
        return entries

    def restore_from_trash(self, entry: str) -> Note:
        for layer_id in self._private_layers(None):
            access = self._workspace.private_access(layer_id)
            if any(item.object_id == entry for item in access.list_trash()):
                return access.restore_note(entry)

        path = self._trash_root() / Path(entry).name
        if not path.is_file():
            raise NotFoundError("That trash entry no longer exists.")
        layer_id, _, rest = path.stem.partition("__")
        folder, _, title = rest.partition("__")
        folder_path = "" if folder == "root" else folder

        store = self._workspace.layer_store(layer_id)
        destination = store.path_for(folder_path, title)
        if destination.exists():
            raise ConflictError("A note with that name exists again; rename it first.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.replace(destination)
        return store.read_note(destination)

    def empty_trash(self) -> int:
        removed = 0
        for path in self._trash_root().glob("*"):
            if path.is_file():
                path.unlink()
                removed += 1
        for layer_id in self._private_layers(None):
            removed += self._workspace.private_access(layer_id).empty_trash()
        return removed

    # -- folders -------------------------------------------------------------

    def create_folder(self, layer_id: str, folder_path: str, name: str) -> FolderNode:
        layer = self._workspace.require_readable_layer(layer_id)
        if layer.storage == "encrypted-objects":
            return self._workspace.private_access(layer_id).create_folder(folder_path, name)

        store = self._workspace.layer_store(layer_id)
        parts = [p for p in folder_path.split("/") if p] + [safe_filename(name)]
        from app.infrastructure.storage.paths import resolve_within

        target = resolve_within(store.root, *parts)
        if target.exists():
            raise ConflictError("A folder with that name already exists.")
        target.mkdir(parents=True)
        relative = target.relative_to(store.root).as_posix()
        return FolderNode(
            id=note_id_for(layer_id, relative + "/"),
            layer_id=layer_id,
            name=target.name,
            path=relative,
            parent_id=None,
        )

    def _private_folder_owner(self, folder_id: str) -> PrivateLayerAccess | None:
        for layer_id in self._private_layers(None):
            access = self._workspace.private_access(layer_id)
            if any(folder.id == folder_id for folder in access.list_folders()):
                return access
        return None

    def _locate_folder(self, folder_id: str) -> tuple[MarkdownLayerStore, FolderNode, Path]:
        for layer_id in self._markdown_layers(None):
            store = self._workspace.layer_store(layer_id)
            for folder in store.list_folders():
                if folder.id == folder_id:
                    return store, folder, store.root / folder.path
        raise NotFoundError("Folder not found.")

    def rename_folder(self, folder_id: str, name: str) -> FolderNode:
        private = self._private_folder_owner(folder_id)
        if private is not None:
            return private.rename_folder(folder_id, name)

        store, _folder, path = self._locate_folder(folder_id)
        destination = path.parent / safe_filename(name)
        if destination.exists():
            raise ConflictError("A folder with that name already exists.")
        path.replace(destination)
        relative = destination.relative_to(store.root).as_posix()
        return FolderNode(
            id=note_id_for(store.layer_id, relative + "/"),
            layer_id=store.layer_id,
            name=destination.name,
            path=relative,
            parent_id=None,
        )

    def delete_folder(self, folder_id: str) -> int:
        """Trash every note in the folder, then remove it. Nothing is destroyed."""
        private = self._private_folder_owner(folder_id)
        if private is not None:
            return private.delete_folder(folder_id)

        store, _folder, path = self._locate_folder(folder_id)
        moved = 0
        for note_path in sorted(path.rglob(f"*{MARKDOWN_SUFFIX}")):
            note = store.read_note(note_path)
            self.delete_note(note.metadata.id)
            moved += 1
        shutil.rmtree(path, ignore_errors=True)
        return moved

    # -- links ---------------------------------------------------------------

    @staticmethod
    def build_title_index(notes: list[Note]) -> dict[str, str]:
        """Map lowercased title and alias to note id (first writer wins)."""
        index: dict[str, str] = {}
        for note in notes:
            for name in [note.metadata.title, *note.metadata.aliases]:
                key = name.strip().lower()
                if key and key not in index:
                    index[key] = note.metadata.id
        return index

    def resolve_link(self, target_title: str, notes: list[Note] | None = None) -> str | None:
        index = self.build_title_index(notes if notes is not None else self.list_notes())
        return index.get(target_title.strip().lower())

    def backlinks(self, note_id: str) -> list[Backlink]:
        notes = self.list_notes()
        target = next((n for n in notes if n.metadata.id == note_id), None)
        if target is None:
            raise NotFoundError("Knowledge object not found.")

        names = {target.metadata.title.lower(), *(a.lower() for a in target.metadata.aliases)}
        results: list[Backlink] = []
        for note in notes:
            if note.metadata.id == note_id:
                continue
            for link in note.metadata.links:
                if link.target_title.strip().lower() not in names:
                    continue
                results.append(
                    Backlink(
                        source_id=note.metadata.id,
                        source_title=note.metadata.title,
                        layer_id=note.metadata.layer_id,
                        relationship=link.relationship,
                        context=_context_for(note.content, link.target_title),
                    )
                )
        return results

    def unlinked_mentions(self, note_id: str) -> list[UnlinkedMention]:
        """Notes that name this note in prose without linking to it."""
        notes = self.list_notes()
        target = next((n for n in notes if n.metadata.id == note_id), None)
        if target is None:
            raise NotFoundError("Knowledge object not found.")

        title = target.metadata.title
        if len(title) < 3:
            return []  # too short to mention meaningfully; every match would be noise
        pattern = re.compile(rf"(?<!\[\[)\b{re.escape(title)}\b(?!\]\])", re.IGNORECASE)

        results: list[UnlinkedMention] = []
        linked = {b.source_id for b in self.backlinks(note_id)}
        for note in notes:
            if note.metadata.id == note_id or note.metadata.id in linked:
                continue
            if pattern.search(note.content):
                results.append(
                    UnlinkedMention(
                        source_id=note.metadata.id,
                        source_title=note.metadata.title,
                        layer_id=note.metadata.layer_id,
                        context=_context_for(note.content, title),
                    )
                )
        return results

    def link_health(self) -> LinkHealth:
        notes = self.list_notes()
        index = self.build_title_index(notes)
        broken: list[tuple[str, str]] = []
        has_edge: set[str] = set()

        for note in notes:
            for link in note.metadata.links:
                target = index.get(link.target_title.strip().lower())
                if target is None:
                    broken.append((note.metadata.id, link.target_title))
                else:
                    has_edge.add(note.metadata.id)
                    has_edge.add(target)

        orphans = [note.metadata.id for note in notes if note.metadata.id not in has_edge]
        return LinkHealth(broken=broken, orphans=orphans)

    def outgoing_links(self, note_id: str) -> list[NoteLink]:
        return self.get_note(note_id).metadata.links

    # -- attachments ---------------------------------------------------------

    def save_attachment(self, layer_id: str, filename: str, data: bytes) -> str:
        """Store an attachment inside the layer and return its relative path."""
        if len(data) > 64 * 1024 * 1024:
            raise InvalidRequestError("Attachments larger than 64 MB are not supported.")

        layer = self._workspace.require_readable_layer(layer_id)
        if layer.storage == "encrypted-objects":
            # Encrypted, opaque, and named by a random object id: an attachment in a
            # private layer must not put "passport-scan.pdf" on the disk.
            return self._workspace.private_access(layer_id).save_attachment(filename, data)

        store = self._workspace.layer_store(layer_id)
        from app.infrastructure.storage.paths import resolve_within

        name = safe_filename(Path(filename).stem)
        suffix = Path(filename).suffix.lower()[:12]
        target = resolve_within(store.root, "attachments", f"{name}{suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)

        counter = 2
        while target.exists():
            target = resolve_within(store.root, "attachments", f"{name} {counter}{suffix}")
            counter += 1

        target.write_bytes(data)
        return target.relative_to(store.root).as_posix()


def _context_for(content: str, needle: str, radius: int = 60) -> str:
    lowered = content.lower()
    position = lowered.find(needle.lower())
    if position < 0:
        return ""
    start = max(0, position - radius)
    end = min(len(content), position + len(needle) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{content[start:end].strip()}{suffix}".replace("\n", " ")
