"""Markdown-file storage for public layers.

Markdown on disk is the source of truth. This store parses files into domain
objects and writes them back; it never becomes the authority. If a user edits a
file in another editor, the next scan simply reflects it.

Frontmatter is *untrusted input*: it is parsed with ``yaml.safe_load`` (never
``load``), any non-mapping document is discarded, and unknown keys are kept as
opaque properties rather than being interpreted.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.domain.note import (
    FolderNode,
    Note,
    NoteMetadata,
    extract_links,
    extract_tags,
    word_count,
)
from app.infrastructure.storage.paths import resolve_within, safe_filename

FRONTMATTER_FENCE = "---"
MARKDOWN_SUFFIX = ".md"


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown document into (frontmatter, body).

    Malformed YAML is not an error: the document is treated as having no
    frontmatter, which is what every other Markdown tool does and avoids making
    a corrupt file unreadable.
    """
    if not text.startswith(FRONTMATTER_FENCE):
        return {}, text
    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONTMATTER_FENCE:
            raw = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            try:
                loaded = yaml.safe_load(raw)
            except yaml.YAMLError:
                return {}, text
            if not isinstance(loaded, dict):
                return {}, text
            return {str(key): value for key, value in loaded.items()}, body.lstrip("\n")
    return {}, text


def render_frontmatter(properties: dict[str, Any]) -> str:
    if not properties:
        return ""
    dumped = yaml.safe_dump(properties, sort_keys=True, allow_unicode=True).strip()
    return f"{FRONTMATTER_FENCE}\n{dumped}\n{FRONTMATTER_FENCE}\n\n"


def note_id_for(layer_id: str, relative_path: str) -> str:
    """A stable, opaque id for a public note.

    Derived from the layer id and the path so that ids survive a restart without
    a sidecar database. Public layers have no filename privacy requirement (that
    is what private layers are for), so a derived id is acceptable here and is
    explicitly *not* used for private layers — see ADR-0004.
    """
    digest = hashlib.blake2b(f"{layer_id}\x00{relative_path}".encode(), digest_size=16).hexdigest()
    return digest


class MarkdownLayerStore:
    """Reads and writes the Markdown tree of one public layer."""

    def __init__(self, layer_id: str, root: Path) -> None:
        self.layer_id = layer_id
        self.root = root

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def iter_markdown_files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(
            path
            for path in self.root.rglob(f"*{MARKDOWN_SUFFIX}")
            if path.is_file() and not any(part.startswith(".") for part in path.parts)
        )

    def read_note(self, path: Path) -> Note:
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = parse_frontmatter(text)
        relative = self._relative(path)
        stat = path.stat()

        raw_tags = frontmatter.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        if not isinstance(raw_tags, list):
            raw_tags = []

        raw_aliases = frontmatter.get("aliases", [])
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        if not isinstance(raw_aliases, list):
            raw_aliases = []

        properties = {
            key: value
            for key, value in frontmatter.items()
            if key not in {"tags", "aliases", "title"}
        }
        title = str(frontmatter.get("title") or path.stem)
        folder_path = str(Path(relative).parent.as_posix())
        if folder_path == ".":
            folder_path = ""

        metadata = NoteMetadata(
            id=note_id_for(self.layer_id, relative),
            layer_id=self.layer_id,
            title=title,
            folder_path=folder_path,
            aliases=[str(alias) for alias in raw_aliases],
            tags=extract_tags(body, [str(tag) for tag in raw_tags]),
            properties=properties,
            links=extract_links(body),
            created_at=_iso(stat.st_ctime),
            updated_at=_iso(stat.st_mtime),
            size_bytes=stat.st_size,
            word_count=word_count(body),
        )
        return Note(metadata=metadata, content=body)

    def list_notes(self) -> list[Note]:
        return [self.read_note(path) for path in self.iter_markdown_files()]

    def list_folders(self) -> list[FolderNode]:
        if not self.root.exists():
            return []
        folders: list[FolderNode] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_dir() or any(part.startswith(".") for part in path.parts):
                continue
            relative = self._relative(path)
            parent = str(Path(relative).parent.as_posix())
            folders.append(
                FolderNode(
                    id=note_id_for(self.layer_id, relative + "/"),
                    layer_id=self.layer_id,
                    name=path.name,
                    path=relative,
                    parent_id=(
                        None if parent in ("", ".") else note_id_for(self.layer_id, parent + "/")
                    ),
                )
            )
        return folders

    def path_for(self, folder_path: str, title: str) -> Path:
        filename = f"{safe_filename(title)}{MARKDOWN_SUFFIX}"
        parts = [*(p for p in folder_path.split("/") if p and p != "."), filename]
        return resolve_within(self.root, *parts)

    def write_note(
        self,
        *,
        folder_path: str,
        title: str,
        content: str,
        properties: dict[str, Any] | None = None,
    ) -> Note:
        path = self.path_for(folder_path, title)
        path.parent.mkdir(parents=True, exist_ok=True)
        document = render_frontmatter(properties or {}) + content
        path.write_text(document, encoding="utf-8", newline="\n")
        return self.read_note(path)
