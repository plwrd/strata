"""Notes, folders and the navigation tree."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.note import Note, NoteMetadata
from app.services.container import Services


class TreeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_ids: list[str] | None = Field(default=None, max_length=200)


class TreeFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    layer_id: str
    name: str
    path: str
    parent_id: str | None = None


class TreeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folders: list[TreeFolder] = Field(default_factory=list)
    notes: list[NoteMetadata] = Field(default_factory=list)
    locked_layer_ids: list[str] = Field(default_factory=list)


class GetNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1, max_length=128)


class NoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Note


class CreateNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    folder_path: str = Field(default="", max_length=1024)
    content: str = Field(default="", max_length=512_000)


class NotesBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(TreeRequest)
    def get_tree(self, request: TreeRequest) -> TreeResponse:
        notes = self._services.notes.list_notes(request.layer_ids)
        folders = self._services.notes.list_folders(request.layer_ids)
        return TreeResponse(
            folders=[TreeFolder(**folder.model_dump()) for folder in folders],
            notes=[note.metadata for note in notes],
            locked_layer_ids=[layer.id for layer in self._services.workspace.locked_layers()],
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(GetNoteRequest)
    def get_note(self, request: GetNoteRequest) -> NoteResponse:
        return NoteResponse(note=self._services.notes.get_note(request.note_id))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(CreateNoteRequest)
    def create_note(self, request: CreateNoteRequest) -> NoteResponse:
        note = self._services.notes.create_note(
            layer_id=request.layer_id,
            folder_path=request.folder_path,
            title=request.title,
            content=request.content,
        )
        return NoteResponse(note=note)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def list_notes(self, _request: EmptyRequest) -> TreeResponse:
        notes = self._services.notes.list_notes()
        return TreeResponse(notes=[note.metadata for note in notes])
