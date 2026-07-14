"""Notes, folders, links, properties and attachments."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Signal, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.errors import InvalidRequestError
from app.domain.note import Note, NoteMetadata
from app.domain.schema import (
    BUILTIN_SCHEMAS,
    NoteSchema,
    ValidationIssue,
    schema_by_id,
    schema_for_note,
    validate_properties,
)
from app.services.container import Services

MAX_NOTE_BYTES = 512_000
# Attachments arrive base64-encoded inside the envelope, so the cap keeps the
# whole request under the 1 MiB bridge limit.
MAX_ATTACHMENT_CHUNK = 700_000


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


class NoteIdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1, max_length=128)


class NoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Note
    schema_id: str | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)


class CreateNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    folder_path: str = Field(default="", max_length=1024)
    content: str = Field(default="", max_length=MAX_NOTE_BYTES)
    schema_id: str | None = Field(default=None, max_length=64)


class UpdateNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1, max_length=128)
    content: str = Field(max_length=MAX_NOTE_BYTES)


class UpdatePropertiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1, max_length=128)
    properties: dict[str, Any] = Field(default_factory=dict)


class RenameNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)


class RenameNoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Note
    links_rewritten: int = 0


class MoveNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str = Field(min_length=1, max_length=128)
    folder_path: str = Field(default="", max_length=1024)


class DeleteNoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trash_entry: str


class TrashEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry: str
    layer_id: str
    folder_path: str
    title: str


class TrashListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[TrashEntry] = Field(default_factory=list)


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry: str = Field(min_length=1, max_length=400)


class CreateFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    folder_path: str = Field(default="", max_length=1024)
    name: str = Field(min_length=1, max_length=120)


class FolderIdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_id: str = Field(min_length=1, max_length=128)


class RenameFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)


class FolderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder: TreeFolder


class CountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = 0


class BacklinkModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_title: str
    layer_id: str
    relationship: str
    context: str


class MentionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_title: str
    layer_id: str
    context: str


class LinksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backlinks: list[BacklinkModel] = Field(default_factory=list)
    unlinked_mentions: list[MentionModel] = Field(default_factory=list)
    outgoing: list[dict[str, str]] = Field(default_factory=list)


class LinkHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    broken: list[dict[str, str]] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)


class SchemaListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemas: list[NoteSchema] = Field(default_factory=list)


class AttachmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(max_length=MAX_ATTACHMENT_CHUNK)


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    markdown: str


class NotesBridge(QObject):
    """Notes, folders and links.

    ``changed`` is pushed whenever the workspace changes on disk — because Strata
    wrote to it, or because the user edited a file in another editor. The frontend
    reloads rather than trusting its own cache: the file is the truth.
    """

    changed = Signal(str)

    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._services.watcher.changed.connect(self.changed)

    def _announce(self) -> None:
        self._services.watcher.announce("strata")

    # -- reading -------------------------------------------------------------

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
    @bridge_method(NoteIdRequest)
    def get_note(self, request: NoteIdRequest) -> NoteResponse:
        return self._with_schema(self._services.notes.get_note(request.note_id))

    @staticmethod
    def _with_schema(note: Note) -> NoteResponse:
        schema = schema_for_note(note.metadata.properties)
        issues = validate_properties(schema, note.metadata.properties) if schema else []
        return NoteResponse(note=note, schema_id=schema.id if schema else None, issues=issues)

    # -- writing -------------------------------------------------------------

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(CreateNoteRequest)
    def create_note(self, request: CreateNoteRequest) -> NoteResponse:
        content = request.content
        properties: dict[str, Any] = {}

        if request.schema_id:
            schema = schema_by_id(request.schema_id)
            if schema is None:
                raise InvalidRequestError(
                    "Unknown schema.", details={"schemaId": request.schema_id}
                )
            properties = {
                definition.key: definition.default
                for definition in schema.properties
                if definition.default is not None
            }
            content = content or schema.template

        note = self._services.notes.create_note(
            layer_id=request.layer_id,
            folder_path=request.folder_path,
            title=request.title,
            content=content,
            properties=properties,
        )
        self._announce()
        return self._with_schema(note)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(UpdateNoteRequest)
    def update_note(self, request: UpdateNoteRequest) -> NoteResponse:
        note = self._services.notes.update_note(request.note_id, request.content)
        self._announce()
        return self._with_schema(note)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(UpdatePropertiesRequest)
    def update_properties(self, request: UpdatePropertiesRequest) -> NoteResponse:
        note = self._services.notes.update_properties(request.note_id, request.properties)
        self._announce()
        return self._with_schema(note)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(RenameNoteRequest)
    def rename_note(self, request: RenameNoteRequest) -> RenameNoteResponse:
        note, rewritten = self._services.notes.rename_note(request.note_id, request.title)
        self._announce()
        return RenameNoteResponse(note=note, links_rewritten=rewritten)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(MoveNoteRequest)
    def move_note(self, request: MoveNoteRequest) -> NoteResponse:
        note = self._services.notes.move_note(request.note_id, request.folder_path)
        self._announce()
        return self._with_schema(note)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(NoteIdRequest)
    def duplicate_note(self, request: NoteIdRequest) -> NoteResponse:
        note = self._services.notes.duplicate_note(request.note_id)
        self._announce()
        return self._with_schema(note)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(NoteIdRequest)
    def delete_note(self, request: NoteIdRequest) -> DeleteNoteResponse:
        entry = self._services.notes.delete_note(request.note_id)
        self._announce()
        return DeleteNoteResponse(trash_entry=entry)

    # -- trash ---------------------------------------------------------------

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def list_trash(self, _request: EmptyRequest) -> TrashListResponse:
        return TrashListResponse(
            entries=[TrashEntry(**entry) for entry in self._services.notes.list_trash()]
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(RestoreRequest)
    def restore_note(self, request: RestoreRequest) -> NoteResponse:
        note = self._services.notes.restore_from_trash(request.entry)
        self._announce()
        return self._with_schema(note)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def empty_trash(self, _request: EmptyRequest) -> CountResponse:
        return CountResponse(count=self._services.notes.empty_trash())

    # -- folders -------------------------------------------------------------

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(CreateFolderRequest)
    def create_folder(self, request: CreateFolderRequest) -> FolderResponse:
        folder = self._services.notes.create_folder(
            request.layer_id, request.folder_path, request.name
        )
        self._announce()
        return FolderResponse(folder=TreeFolder(**folder.model_dump()))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(RenameFolderRequest)
    def rename_folder(self, request: RenameFolderRequest) -> FolderResponse:
        folder = self._services.notes.rename_folder(request.folder_id, request.name)
        self._announce()
        return FolderResponse(folder=TreeFolder(**folder.model_dump()))

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(FolderIdRequest)
    def delete_folder(self, request: FolderIdRequest) -> CountResponse:
        trashed = self._services.notes.delete_folder(request.folder_id)
        self._announce()
        return CountResponse(count=trashed)

    # -- links ---------------------------------------------------------------

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(NoteIdRequest)
    def get_links(self, request: NoteIdRequest) -> LinksResponse:
        notes = self._services.notes
        return LinksResponse(
            backlinks=[
                BacklinkModel(**vars(backlink)) for backlink in notes.backlinks(request.note_id)
            ],
            unlinked_mentions=[
                MentionModel(**vars(mention))
                for mention in notes.unlinked_mentions(request.note_id)
            ],
            outgoing=[
                {"target": link.target_title, "relationship": link.relationship}
                for link in notes.outgoing_links(request.note_id)
            ],
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def get_link_health(self, _request: EmptyRequest) -> LinkHealthResponse:
        health = self._services.notes.link_health()
        return LinkHealthResponse(
            broken=[{"source_id": source, "target": target} for source, target in health.broken],
            orphans=health.orphans,
        )

    # -- schemas and attachments ---------------------------------------------

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def list_schemas(self, _request: EmptyRequest) -> SchemaListResponse:
        return SchemaListResponse(schemas=BUILTIN_SCHEMAS)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(AttachmentRequest)
    def save_attachment(self, request: AttachmentRequest) -> AttachmentResponse:
        try:
            data = base64.b64decode(request.data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidRequestError("The attachment payload is not valid base64.") from exc

        path = self._services.notes.save_attachment(request.layer_id, request.filename, data)
        self._announce()

        is_image = path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))
        markdown = f"{'!' if is_image else ''}[{request.filename}]({path})"
        return AttachmentResponse(path=path, markdown=markdown)
