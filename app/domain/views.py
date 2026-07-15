"""Database-style views over Markdown notes.

The product rule that shapes this whole feature: **Markdown files remain the source
of truth.** A view is a *query*, not a table. Filtering, sorting and grouping run
over the notes' frontmatter every time; nothing is copied into a database, and a
note edited in another editor shows up in the next query. There is no schema to
migrate and no second copy to fall out of sync.

The query logic lives here and in the view service (tested in Python) rather than
in the frontend, because a filter that silently drops a row or a sort that reorders
one wrongly is a bug the user cannot see — the note is still on disk, just missing
from the view.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ViewType = Literal["table", "list", "cards", "kanban", "calendar", "timeline", "gallery"]

FilterOperator = Literal[
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "is_empty",
    "is_not_empty",
    "greater_than",
    "less_than",
    "before",
    "after",
    "in",
]

SortDirection = Literal["asc", "desc"]


class ViewFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A property key, or one of the pseudo-fields: title, tags, folder, layer,
    # created, updated. These are computed, not stored, so a note that never wrote
    # a `title:` frontmatter still filters on its filename-derived title.
    field: str = Field(min_length=1, max_length=120)
    operator: FilterOperator = "equals"
    value: str = Field(default="", max_length=2000)


class ViewSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=120)
    direction: SortDirection = "asc"


class ViewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: ViewType = "table"
    layer_ids: list[str] = Field(default_factory=list)  # empty = all readable
    folder_scope: str = ""
    filters: list[ViewFilter] = Field(default_factory=list)
    sort: list[ViewSort] = Field(default_factory=list)
    group_by: str = ""  # a field to group by (kanban columns, list sections)
    visible_properties: list[str] = Field(default_factory=list)  # empty = a default set
    # For calendar/timeline: which property holds the date.
    date_field: str = "updated"


class ViewRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    layer_id: str
    layer_name: str
    is_private: bool
    title: str
    folder_path: str
    tags: list[str] = Field(default_factory=list)
    properties: dict[str, str] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    snippet: str = ""


class ViewGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str  # the group value ("in progress", "2026-07", …); "" is the ungrouped bucket
    label: str
    rows: list[ViewRow] = Field(default_factory=list)


class ViewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: ViewConfig
    rows: list[ViewRow] = Field(default_factory=list)
    groups: list[ViewGroup] = Field(default_factory=list)
    total: int = 0
    # Property keys present across the result, so the UI can offer columns.
    available_properties: list[str] = Field(default_factory=list)
    locked_layers_excluded: int = 0
