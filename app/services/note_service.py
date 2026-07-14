"""Note reading and writing across readable layers.

Resolution of wiki links happens here, once, and everything else (graph,
backlinks, export, search) consumes the result. Link targets are matched by
title or alias, case-insensitively, within the set of *readable* layers only —
a locked layer never resolves and never reports a miss differently from an
absent note.
"""

from __future__ import annotations

from app.domain.errors import NotFoundError
from app.domain.note import FolderNode, Note
from app.services.workspace_service import WorkspaceService


class NoteService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self._workspace = workspace

    def list_notes(self, layer_ids: list[str] | None = None) -> list[Note]:
        notes: list[Note] = []
        for layer in self._workspace.readable_layers():
            if layer_ids is not None and layer.id not in layer_ids:
                continue
            if layer.storage != "markdown":
                continue  # Milestone 3 storage; not readable through this store
            notes.extend(self._workspace.layer_store(layer.id).list_notes())
        return notes

    def list_folders(self, layer_ids: list[str] | None = None) -> list[FolderNode]:
        folders: list[FolderNode] = []
        for layer in self._workspace.readable_layers():
            if layer_ids is not None and layer.id not in layer_ids:
                continue
            if layer.storage != "markdown":
                continue  # Milestone 3 storage; not readable through this store
            folders.extend(self._workspace.layer_store(layer.id).list_folders())
        return folders

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

    def create_note(
        self,
        *,
        layer_id: str,
        folder_path: str,
        title: str,
        content: str = "",
    ) -> Note:
        store = self._workspace.layer_store(layer_id)
        return store.write_note(folder_path=folder_path, title=title, content=content)

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
