"""Note and folder domain models.

Markdown files are the source of truth. These models are the *parsed* view of a
note; the file on disk always wins. Nothing here is a database record.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Typed relationships. `relates_to` is the untyped default.
RELATIONSHIP_TYPES: tuple[str, ...] = (
    "references",
    "supports",
    "contradicts",
    "expands",
    "depends_on",
    "created_by",
    "assigned_to",
    "relates_to",
    "supersedes",
    "blocks",
    "evidence_for",
    "derived_from",
)

_WIKI_LINK = re.compile(r"\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|([^\]]+))?\]\]")
_TAG = re.compile(r"(?:^|\s)#([A-Za-z0-9][\w/-]*)")
# `[[target]]` preceded by a relationship marker, e.g. `supports:: [[Note]]`
_TYPED_LINK = re.compile(r"([a-z_]+)::\s*\[\[([^\]|#^]+)")


class NoteLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_title: str
    alias: str | None = None
    relationship: str = "references"


class NoteMetadata(BaseModel):
    """Everything about a note except its body."""

    model_config = ConfigDict(extra="forbid")

    id: str
    layer_id: str
    parent_id: str | None = None
    title: str
    folder_path: str = ""
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    links: list[NoteLink] = Field(default_factory=list)
    created_at: str
    updated_at: str
    size_bytes: int = 0
    word_count: int = 0

    @property
    def display_path(self) -> str:
        return f"{self.folder_path}/{self.title}.md" if self.folder_path else f"{self.title}.md"


class Note(BaseModel):
    """A note plus its Markdown body."""

    model_config = ConfigDict(extra="forbid")

    metadata: NoteMetadata
    content: str = ""


class FolderNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    layer_id: str
    name: str
    path: str
    parent_id: str | None = None


def extract_links(content: str) -> list[NoteLink]:
    """Extract wiki links, including typed ones (``supports:: [[Target]]``).

    Untrusted input: this only ever *reads* the note body. It never resolves a
    path, never follows an instruction, and never executes anything.
    """
    typed: dict[str, str] = {}
    for relationship, target in _TYPED_LINK.findall(content):
        if relationship in RELATIONSHIP_TYPES:
            typed[target.strip()] = relationship

    links: list[NoteLink] = []
    seen: set[tuple[str, str]] = set()
    for target, alias in _WIKI_LINK.findall(content):
        target = target.strip()
        if not target:
            continue
        relationship = typed.get(target, "references")
        key = (target, relationship)
        if key in seen:
            continue
        seen.add(key)
        links.append(
            NoteLink(
                target_title=target,
                alias=(alias.strip() or None) if alias else None,
                relationship=relationship,
            )
        )
    return links


def extract_tags(content: str, frontmatter_tags: list[str] | None = None) -> list[str]:
    """Union of ``#inline`` tags and frontmatter tags, order-stable and unique."""
    tags: list[str] = []
    for tag in list(frontmatter_tags or []) + _TAG.findall(content):
        tag = str(tag).lstrip("#").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def word_count(content: str) -> int:
    return len(content.split())
