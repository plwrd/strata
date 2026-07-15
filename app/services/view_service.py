"""Query notes for a structured view.

Filters, sorts and groups over the readable notes. A locked layer contributes
nothing — the candidate set is ``NoteService.list_notes()``, which is readable-only,
so a view can never surface a locked note's title or property by construction rather
than by a filter that could be forgotten.

Everything is computed from the live notes on each call. That is the whole point of
"Markdown stays the source of truth": the view is a lens, not a store.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.domain.note import Note
from app.domain.views import (
    ViewConfig,
    ViewFilter,
    ViewGroup,
    ViewResult,
    ViewRow,
)
from app.services.note_service import NoteService
from app.services.workspace_service import WorkspaceService

SNIPPET_LENGTH = 140
UNGROUPED = "\x00ungrouped"


class ViewService:
    def __init__(self, workspace: WorkspaceService, notes: NoteService) -> None:
        self._workspace = workspace
        self._notes = notes

    def run(self, config: ViewConfig) -> ViewResult:
        layer_ids = config.layer_ids or None
        notes = self._notes.list_notes(layer_ids)

        layer_names = {layer.id: layer.display_name for layer in self._workspace.descriptor.layers}
        private_layers = {
            layer.id for layer in self._workspace.descriptor.layers if layer.visibility == "private"
        }

        rows: list[ViewRow] = []
        for note in notes:
            if config.folder_scope and not note.metadata.folder_path.startswith(
                config.folder_scope
            ):
                continue
            if not all(self._matches(note, f) for f in config.filters):
                continue
            rows.append(self._row(note, layer_names, private_layers))

        rows = self._sort(rows, config)
        groups = self._group(rows, config)

        available = sorted(
            {key for row in rows for key in row.properties}
            | {"title", "tags", "folder", "created", "updated"}
        )

        return ViewResult(
            config=config,
            rows=rows,
            groups=groups,
            total=len(rows),
            available_properties=available,
            locked_layers_excluded=len(self._workspace.locked_layers()),
        )

    # -- rows ----------------------------------------------------------------

    def _row(self, note: Note, layer_names: dict[str, str], private_layers: set[str]) -> ViewRow:
        meta = note.metadata
        return ViewRow(
            object_id=meta.id,
            layer_id=meta.layer_id,
            layer_name=layer_names.get(meta.layer_id, "Unknown"),
            is_private=meta.layer_id in private_layers,
            title=meta.title,
            folder_path=meta.folder_path,
            tags=list(meta.tags),
            properties={
                key: _stringify(value)
                for key, value in meta.properties.items()
                if key not in ("tags", "title")
            },
            created_at=meta.created_at,
            updated_at=meta.updated_at,
            snippet=note.content.strip()[:SNIPPET_LENGTH].replace("\n", " "),
        )

    def _field(self, row: ViewRow, field: str) -> str:
        if field == "title":
            return row.title
        if field == "tags":
            return ", ".join(row.tags)
        if field == "folder":
            return row.folder_path
        if field == "layer":
            return row.layer_name
        if field == "created":
            return row.created_at
        if field == "updated":
            return row.updated_at
        return row.properties.get(field, "")

    # -- filtering -----------------------------------------------------------

    def _matches(self, note: Note, view_filter: ViewFilter) -> bool:
        value = self._note_field(note, view_filter.field)
        target = view_filter.value
        op = view_filter.operator

        if op == "is_empty":
            return not value
        if op == "is_not_empty":
            return bool(value)
        if op == "equals":
            return value.lower() == target.lower()
        if op == "not_equals":
            return value.lower() != target.lower()
        if op == "contains":
            return target.lower() in value.lower()
        if op == "not_contains":
            return target.lower() not in value.lower()
        if op == "in":
            options = [item.strip().lower() for item in target.split(",")]
            return value.lower() in options
        if op in ("greater_than", "less_than"):
            return self._numeric_compare(value, target, op)
        if op in ("before", "after"):
            return self._date_compare(value, target, op)
        return False

    def _note_field(self, note: Note, field: str) -> str:
        meta = note.metadata
        if field == "title":
            return meta.title
        if field == "tags":
            return ", ".join(meta.tags)
        if field == "folder":
            return meta.folder_path
        if field == "layer":
            return meta.layer_id
        if field == "created":
            return meta.created_at
        if field == "updated":
            return meta.updated_at
        return _stringify(meta.properties.get(field, ""))

    @staticmethod
    def _numeric_compare(value: str, target: str, op: str) -> bool:
        try:
            left, right = float(value), float(target)
        except ValueError:
            return False
        return left > right if op == "greater_than" else left < right

    @staticmethod
    def _date_compare(value: str, target: str, op: str) -> bool:
        left = _parse_date(value)
        right = _parse_date(target)
        if left is None or right is None:
            return False
        return left < right if op == "before" else left > right

    # -- sorting and grouping ------------------------------------------------

    def _sort(self, rows: list[ViewRow], config: ViewConfig) -> list[ViewRow]:
        for sort in reversed(config.sort):
            rows = sorted(
                rows,
                key=lambda row: _sort_key(self._field(row, sort.field)),
                reverse=(sort.direction == "desc"),
            )
        if not config.sort:
            rows = sorted(rows, key=lambda row: row.title.lower())
        return rows

    def _group(self, rows: list[ViewRow], config: ViewConfig) -> list[ViewGroup]:
        if not config.group_by:
            return []

        buckets: dict[str, list[ViewRow]] = {}
        order: list[str] = []
        for row in rows:
            key = self._field(row, config.group_by) or UNGROUPED
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(row)

        return [
            ViewGroup(
                key="" if key == UNGROUPED else key,
                label="No value" if key == UNGROUPED else key,
                rows=buckets[key],
            )
            for key in order
        ]


def _stringify(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value) if value is not None else ""


def _sort_key(value: str) -> tuple[int, float, str]:
    """Numbers sort numerically, everything else lexically, empties last."""
    if not value:
        return (2, 0.0, "")
    try:
        return (0, float(value), "")
    except ValueError:
        return (1, 0.0, value.lower())


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value[:10]), datetime.min.time())
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
