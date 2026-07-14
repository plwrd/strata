"""Per-layer search indexes.

Two implementations, one interface, chosen by the layer's storage (ADR-0007):

* **Public layers** get a persistent SQLite FTS5 index on disk. It is a derived
  artefact — delete it and it rebuilds — so it may live next to the Markdown.

* **Private layers** get an **in-memory** index, built on unlock and dropped on
  lock. This is the ADR's "ephemeral" option, chosen because a persistent
  encrypted inverted index leaks structure even when every posting list is
  encrypted: term frequencies, document frequencies and update patterns are all
  visible to someone watching the file. Rebuilding costs a fraction of a second
  for a few thousand notes and leaks nothing at rest.

The private index is never written to disk. Not to a temp file, not to SQLite's
journal, not to a swap-backed mmap — it is a Python dict, and it disappears with
the process.
"""

from __future__ import annotations

import math
import re
import sqlite3
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.domain.note import Note

_WORD = re.compile(r"[\w'-]+", re.UNICODE)

# BM25 constants. k1 controls term-frequency saturation, b controls length
# normalisation; these are the standard defaults and are good enough that tuning
# them without a relevance corpus would be superstition.
BM25_K1 = 1.5
BM25_B = 0.75


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in _WORD.finditer(text)]


@dataclass
class IndexedDocument:
    object_id: str
    layer_id: str
    title: str
    path: str
    tags: list[str]
    properties: dict[str, str]
    body: str
    updated_at: str
    length: int = 0
    terms: dict[str, int] = field(default_factory=dict)


class SearchIndex(ABC):
    """What a layer index must be able to do."""

    layer_id: str

    @abstractmethod
    def rebuild(self, notes: list[Note]) -> int: ...

    @abstractmethod
    def search(self, terms: list[str], limit: int) -> list[tuple[str, float]]:
        """Return (object_id, lexical score) pairs, best first."""

    @abstractmethod
    def document(self, object_id: str) -> IndexedDocument | None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def size(self) -> int: ...


def _document_for(note: Note) -> IndexedDocument:
    meta = note.metadata
    body_terms = tokenize(note.content)
    title_terms = tokenize(meta.title)
    tag_terms = [tag.lower() for tag in meta.tags]
    property_terms = [
        term
        for value in meta.properties.values()
        if isinstance(value, str)
        for term in tokenize(value)
    ]

    counts: dict[str, int] = defaultdict(int)
    # Title and tag terms are counted more than once: a match in the title is a
    # stronger signal than the same word buried in the body, and folding that into
    # the term frequency keeps the ranker a plain BM25 rather than a pile of ad-hoc
    # bonuses.
    for term in title_terms:
        counts[term] += 4
    for term in tag_terms:
        counts[term] += 3
    for term in property_terms:
        counts[term] += 2
    for term in body_terms:
        counts[term] += 1

    return IndexedDocument(
        object_id=meta.id,
        layer_id=meta.layer_id,
        title=meta.title,
        path=meta.display_path,
        tags=list(meta.tags),
        properties={
            k: str(v) for k, v in meta.properties.items() if isinstance(v, str | int | float | bool)
        },
        body=note.content,
        updated_at=meta.updated_at,
        length=len(body_terms) + len(title_terms),
        terms=dict(counts),
    )


class MemoryIndex(SearchIndex):
    """An in-memory BM25 index. Used for private layers, and as a fallback.

    Everything here dies with the process. There is no persistence path, on
    purpose: see the module docstring.
    """

    def __init__(self, layer_id: str) -> None:
        self.layer_id = layer_id
        self._documents: dict[str, IndexedDocument] = {}
        self._postings: dict[str, dict[str, int]] = defaultdict(dict)
        self._average_length = 0.0

    def rebuild(self, notes: list[Note]) -> int:
        self._documents = {}
        self._postings = defaultdict(dict)

        for note in notes:
            document = _document_for(note)
            self._documents[document.object_id] = document
            for term, count in document.terms.items():
                self._postings[term][document.object_id] = count

        lengths = [document.length for document in self._documents.values()]
        self._average_length = (sum(lengths) / len(lengths)) if lengths else 0.0
        return len(self._documents)

    def search(self, terms: list[str], limit: int) -> list[tuple[str, float]]:
        if not terms or not self._documents:
            return []

        total = len(self._documents)
        scores: dict[str, float] = defaultdict(float)

        for term in terms:
            postings = self._postings.get(term)
            if not postings:
                continue
            # Standard BM25 IDF, with the +1 that keeps it positive for a term that
            # appears in every document.
            idf = math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
            for object_id, frequency in postings.items():
                document = self._documents[object_id]
                norm = (
                    1
                    - BM25_B
                    + BM25_B
                    * (document.length / self._average_length if self._average_length else 1)
                )
                scores[object_id] += idf * (
                    frequency * (BM25_K1 + 1) / (frequency + BM25_K1 * norm)
                )

        ranked = sorted(scores.items(), key=lambda item: -item[1])
        return ranked[:limit]

    def document(self, object_id: str) -> IndexedDocument | None:
        return self._documents.get(object_id)

    def close(self) -> None:
        self._documents = {}
        self._postings = defaultdict(dict)
        self._average_length = 0.0

    def size(self) -> int:
        return len(self._documents)


class SqliteIndex(SearchIndex):
    """A persistent FTS5 index for a public layer.

    Only ever used for public layers: writing one of these for a private layer is
    exactly the leak the ADR rejects, so the constructor takes a path and the
    caller (`SearchService`) is the one that decides — and never passes a private
    layer's path.
    """

    def __init__(self, layer_id: str, path: Path) -> None:
        self.layer_id = layer_id
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._create()

    def _create(self) -> None:
        self._connection.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(
                object_id UNINDEXED,
                title,
                tags,
                properties,
                body,
                path UNINDEXED,
                updated_at UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        self._connection.commit()

    def rebuild(self, notes: list[Note]) -> int:
        with self._connection:
            self._connection.execute("DELETE FROM documents")
            self._connection.executemany(
                """
                INSERT INTO documents
                    (object_id, title, tags, properties, body, path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        note.metadata.id,
                        note.metadata.title,
                        " ".join(note.metadata.tags),
                        " ".join(
                            str(value)
                            for value in note.metadata.properties.values()
                            if isinstance(value, str | int | float | bool)
                        ),
                        note.content,
                        note.metadata.display_path,
                        note.metadata.updated_at,
                    )
                    for note in notes
                ],
            )
        return len(notes)

    def search(self, terms: list[str], limit: int) -> list[tuple[str, float]]:
        if not terms:
            return []
        # Each term is quoted, so a query like `foo OR bar*` is data, not syntax:
        # a user searching for `"` must not be able to inject FTS5 operators.
        query = " OR ".join(f'"{term}"' for term in terms if term)
        if not query:
            return []

        try:
            rows = self._connection.execute(
                """
                SELECT object_id,
                       bm25(documents, 0.0, 4.0, 3.0, 2.0, 1.0) AS score
                FROM documents
                WHERE documents MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # A malformed FTS query is a user typo, not a crash.
            return []

        # SQLite's bm25() is negative-is-better; flip it so every index agrees that
        # bigger means more relevant.
        return [(row["object_id"], -float(row["score"])) for row in rows]

    def document(self, object_id: str) -> IndexedDocument | None:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE object_id = ?", (object_id,)
        ).fetchone()
        if row is None:
            return None
        return IndexedDocument(
            object_id=row["object_id"],
            layer_id=self.layer_id,
            title=row["title"],
            path=row["path"],
            tags=row["tags"].split() if row["tags"] else [],
            properties={},
            body=row["body"],
            updated_at=row["updated_at"],
        )

    def close(self) -> None:
        self._connection.close()

    def size(self) -> int:
        row = self._connection.execute("SELECT count(*) AS n FROM documents").fetchone()
        return int(row["n"]) if row else 0
