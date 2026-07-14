"""Hybrid search.

Five signals, combined, each one explainable:

* **lexical** — BM25 over a per-layer index (FTS5 for public, in-memory for private)
* **semantic** — cosine similarity against the layer's embeddings
* **graph proximity** — how close a note is to whatever the user is looking at
* **property and tag matches** — a query term that *is* a tag is a strong signal
* **recency** — a note touched this week beats an identical one from 2019

The scores are normalised per signal before they are mixed, because BM25 and cosine
live on different scales and adding them raw would silently make one of them
decorative.

Every result carries the reasons it matched, and each reason is generated from the
signal that actually fired — not from a plausible-sounding template. "Explain why
each result matched" is a product requirement, and an explanation that is not
derived from the score is a lie.

Privacy: indexes are per layer, and `forget_layer` drops one completely. A locked
layer has no index, contributes no candidates, no counts, no snippets, and no
facets. That is enforced by the candidate set being built from readable layers
only, not by filtering results afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.logging.logger import get_logger
from app.infrastructure.search.index import MemoryIndex, SearchIndex, SqliteIndex, tokenize
from app.infrastructure.vector.embeddings import (
    DEFAULT_DIMENSIONS,
    Embedder,
    HashingEmbedder,
    VectorStore,
)
from app.services.note_service import NoteService
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)

_WORD = re.compile(r"[\w'-]+", re.UNICODE)

SNIPPET_RADIUS = 90
RECENT_DAYS = 14

# Signal weights. Deliberately visible constants rather than a learned model: with
# no relevance corpus, a tuned model would be an overfit to whatever notes we
# happened to test with.
WEIGHT_LEXICAL = 1.0
WEIGHT_SEMANTIC = 0.6
WEIGHT_GRAPH = 0.35
WEIGHT_TAG = 0.5
WEIGHT_PROPERTY = 0.3
WEIGHT_RECENCY = 0.25

# Cosine similarity below this is noise, not meaning. Any two English documents
# share enough common words to score above zero, so a low floor makes every note a
# weak semantic match and the result list becomes "everything, sorted by nothing".
SEMANTIC_MIN_SIMILARITY = 0.35

# Semantic matching needs enough query text to mean anything. With the local
# hashing embedder a one-word query is a single hash bucket, and any note with a
# term that collides into it scores highly — noise dressed as insight. A one-word
# query is a lexical query, and we treat it as one.
#
# This floor comes down when a real embedding model is configured (Milestone 7):
# the *pipeline* is the same, but a transformer's vector for a single word is
# meaningful in a way a hash bucket is not.
SEMANTIC_MIN_TERMS = 2

# Signals that make a note a *result*. Recency and graph proximity re-rank results;
# they do not create them. Without this distinction, searching for a word that
# appears in one note returns the whole workspace, because everything is recent.
QUALIFYING_SIGNALS = frozenset({"lexical", "semantic", "tag", "property"})


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    layer_id: str
    title: str
    path: str
    snippet: str = ""
    score: float = 0.0
    tags: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    # The per-signal contributions, so the UI can show *why* rather than just claim.
    signals: dict[str, float] = Field(default_factory=dict)


@dataclass
class LayerIndexes:
    lexical: SearchIndex
    vectors: VectorStore


class SearchService:
    def __init__(
        self,
        notes: NoteService,
        workspace: WorkspaceService | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self._notes = notes
        self._workspace = workspace
        self._embedder = embedder or HashingEmbedder(DEFAULT_DIMENSIONS)
        self._indexes: dict[str, LayerIndexes] = {}
        self._dirty: set[str] = set()

    # -- index lifecycle -----------------------------------------------------

    def _index_path(self, layer_id: str) -> Path | None:
        """Where a *public* layer's index lives. `None` means "in memory only".

        A private layer never gets a path. This is the single place that decision is
        made, and it is made by asking the layer's storage — not by trusting a
        caller to pass the right flag.
        """
        if self._workspace is None:
            return None
        layer = self._workspace.require_layer(layer_id)
        if layer.visibility == "private":
            return None
        return self._workspace.root / ".strata" / "index" / f"{layer_id}.sqlite"

    def _index_for(self, layer_id: str) -> LayerIndexes:
        existing = self._indexes.get(layer_id)
        if existing is not None and layer_id not in self._dirty:
            return existing

        notes = self._notes.list_notes([layer_id])

        if existing is None:
            path = self._index_path(layer_id)
            lexical: SearchIndex = (
                SqliteIndex(layer_id, path) if path is not None else MemoryIndex(layer_id)
            )
            vectors = VectorStore(layer_id, self._embedder.dimensions)
            existing = LayerIndexes(lexical=lexical, vectors=vectors)
            self._indexes[layer_id] = existing

        existing.lexical.rebuild(notes)

        if notes:
            texts = [f"{note.metadata.title}\n{note.content}" for note in notes]
            matrix = self._embedder.embed(texts)
            existing.vectors.replace([note.metadata.id for note in notes], matrix)
        else:
            existing.vectors.replace([], np.zeros((0, self._embedder.dimensions), dtype=np.float32))

        self._dirty.discard(layer_id)
        logger.info(
            "search.index_built",
            layer_id=layer_id,
            documents=existing.lexical.size(),
            persistent=self._index_path(layer_id) is not None,
        )
        return existing

    def invalidate(self, layer_id: str | None = None) -> None:
        """Mark an index stale. Called whenever the workspace changes on disk."""
        if layer_id is None:
            self._dirty |= set(self._indexes)
        else:
            self._dirty.add(layer_id)

    def forget_layer(self, layer_id: str) -> None:
        """Drop everything derived from a layer that just locked.

        The index and the embeddings *are* the layer's content in another shape.
        Leaving them in memory after a lock would mean the layer is only locked as
        far as the filesystem is concerned.
        """
        indexes = self._indexes.pop(layer_id, None)
        if indexes is None:
            return
        indexes.lexical.close()
        indexes.vectors.close()
        self._dirty.discard(layer_id)
        logger.info("search.index_dropped", layer_id=layer_id)

    def close(self) -> None:
        for layer_id in list(self._indexes):
            self.forget_layer(layer_id)

    def index_sizes(self) -> dict[str, int]:
        return {layer_id: indexes.lexical.size() for layer_id, indexes in self._indexes.items()}

    # -- searching -----------------------------------------------------------

    def _readable_layers(self, layer_ids: list[str] | None) -> list[str]:
        if self._workspace is None:
            # Without a workspace (unit tests of the ranker), fall back to whatever
            # NoteService can see — which is already readable-layers-only.
            return sorted({note.metadata.layer_id for note in self._notes.list_notes(layer_ids)})
        return [
            layer.id
            for layer in self._workspace.readable_layers()
            if layer_ids is None or layer.id in layer_ids
        ]

    def search(
        self,
        query: str,
        *,
        layer_ids: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        semantic: bool = True,
        near_object_id: str | None = None,
    ) -> list[SearchResult]:
        terms = tokenize(query)
        wanted_tags = {tag.lower() for tag in (tags or [])}

        graph_scores = self._graph_proximity(near_object_id) if near_object_id else {}
        now = datetime.now(tz=timezone.utc)

        results: dict[str, SearchResult] = {}
        signals: dict[str, dict[str, float]] = {}

        for layer_id in self._readable_layers(layer_ids):
            indexes = self._index_for(layer_id)

            lexical = dict(indexes.lexical.search(terms, limit * 4)) if terms else {}
            semantic_scores: dict[str, float] = {}
            if semantic and len(terms) >= self._semantic_min_terms():
                query_vector = self._embedder.embed([query])[0]
                semantic_scores = dict(indexes.vectors.search(query_vector, limit * 4))

            candidates = set(lexical) | set(semantic_scores)
            if not terms:
                # A tag-only or layer-only query: everything readable is a candidate.
                candidates = {note.metadata.id for note in self._notes.list_notes([layer_id])}

            lexical_max = max(lexical.values(), default=0.0) or 1.0

            for object_id in candidates:
                document = indexes.lexical.document(object_id)
                if document is None:
                    continue

                document_tags = {tag.lower() for tag in document.tags}
                if wanted_tags and not wanted_tags.issubset(document_tags):
                    continue

                contributions: dict[str, float] = {}

                if object_id in lexical:
                    contributions["lexical"] = WEIGHT_LEXICAL * lexical[object_id] / lexical_max

                if (
                    object_id in semantic_scores
                    and semantic_scores[object_id] >= SEMANTIC_MIN_SIMILARITY
                ):
                    contributions["semantic"] = WEIGHT_SEMANTIC * semantic_scores[object_id]

                matched_tags = [term for term in terms if term in document_tags]
                if matched_tags or wanted_tags:
                    contributions["tag"] = WEIGHT_TAG * (len(matched_tags) or 1)

                matched_properties = [
                    key
                    for key, value in document.properties.items()
                    if any(term in tokenize(str(value)) for term in terms)
                ]
                if matched_properties:
                    contributions["property"] = WEIGHT_PROPERTY * len(matched_properties)

                # A note qualifies as a result only if something about it actually
                # matched. Recency and graph proximity are applied *after* this, so
                # they re-rank results rather than inventing them.
                if terms and not (QUALIFYING_SIGNALS & contributions.keys()):
                    continue

                age = _age_days(document.updated_at, now)
                if age is not None and age <= RECENT_DAYS:
                    contributions["recency"] = WEIGHT_RECENCY * (1 - age / RECENT_DAYS)

                if object_id in graph_scores:
                    contributions["graph"] = WEIGHT_GRAPH * graph_scores[object_id]

                if not contributions:
                    continue

                total = sum(contributions.values())
                results[object_id] = SearchResult(
                    object_id=object_id,
                    layer_id=layer_id,
                    title=document.title,
                    path=document.path,
                    snippet=_snippet(document.body, terms),
                    score=round(total, 4),
                    tags=list(document.tags),
                    signals={key: round(value, 4) for key, value in contributions.items()},
                    reasons=_explain(
                        contributions,
                        terms=terms,
                        matched_tags=matched_tags,
                        matched_properties=matched_properties,
                        age=age,
                    ),
                )
                signals[object_id] = contributions

        ranked = sorted(results.values(), key=lambda result: (-result.score, result.title.lower()))
        return ranked[:limit]

    def similar_to(self, object_id: str, limit: int = 10) -> list[SearchResult]:
        """Notes semantically near this one. Never crosses into a locked layer."""
        results: list[SearchResult] = []
        for layer_id in self._readable_layers(None):
            indexes = self._index_for(layer_id)
            vector = indexes.vectors.vector_for(object_id)
            if vector is None:
                continue
            for other_id, score in indexes.vectors.search(vector, limit + 1):
                if other_id == object_id:
                    continue
                document = indexes.lexical.document(other_id)
                if document is None:
                    continue
                results.append(
                    SearchResult(
                        object_id=other_id,
                        layer_id=layer_id,
                        title=document.title,
                        path=document.path,
                        snippet=_snippet(document.body, []),
                        score=round(score, 4),
                        tags=list(document.tags),
                        signals={"semantic": round(score, 4)},
                        reasons=[f"Semantically similar to “{document.title}”"],
                    )
                )
        return sorted(results, key=lambda result: -result.score)[:limit]

    def clusters(self, layer_ids: list[str] | None = None, count: int = 6) -> dict[str, int]:
        """Semantic clusters, for the graph. Deterministic across runs."""
        assignments: dict[str, int] = {}
        offset = 0
        for layer_id in self._readable_layers(layer_ids):
            indexes = self._index_for(layer_id)
            layer_clusters = indexes.vectors.cluster(count)
            for object_id, cluster in layer_clusters.items():
                assignments[object_id] = cluster + offset
            offset += count
        return assignments

    def _semantic_min_terms(self) -> int:
        """How much query text this embedder needs before its similarity is real.

        A property of the *embedder*, not of where it runs: a hashed bag-of-words
        needs a couple of words to be meaningful, a trained model does not — and a
        trained model can be local.
        """
        return getattr(self._embedder, "min_query_terms", 1)

    def _graph_proximity(self, object_id: str) -> dict[str, float]:
        """1.0 for the note itself, 0.5 for a neighbour, 0.25 two hops out."""
        from app.services.graph_service import GraphService

        if self._workspace is None:
            return {}
        graph = GraphService(self._workspace, self._notes)
        snapshot = graph.build(include_tags=False, include_folders=False)

        adjacency: dict[str, set[str]] = {}
        for edge in snapshot.edges:
            adjacency.setdefault(edge.source, set()).add(edge.target)
            adjacency.setdefault(edge.target, set()).add(edge.source)

        scores = {object_id: 1.0}
        first = adjacency.get(object_id, set())
        for neighbour in first:
            scores[neighbour] = 0.5
        for neighbour in first:
            for second in adjacency.get(neighbour, set()):
                scores.setdefault(second, 0.25)
        return scores


def _explain(
    contributions: dict[str, float],
    *,
    terms: list[str],
    matched_tags: list[str],
    matched_properties: list[str],
    age: float | None,
) -> list[str]:
    """Turn the signals that actually fired into sentences.

    Generated from `contributions`, so a reason cannot appear for a signal that
    contributed nothing — which is the difference between an explanation and a
    decoration.
    """
    reasons: list[str] = []

    if "lexical" in contributions:
        reasons.append(f"Contains {_quote(terms)}")
    if "semantic" in contributions:
        reasons.append("Semantically similar to the query")
    if "tag" in contributions and matched_tags:
        reasons.append(f"Tagged {_quote(matched_tags)}")
    if "property" in contributions:
        reasons.append(f"Property matches: {', '.join(matched_properties)}")
    if "graph" in contributions:
        reasons.append("Linked to what you are looking at")
    if "recency" in contributions and age is not None:
        reasons.append(f"Updated {int(age)} day(s) ago")

    return reasons


def _quote(terms: list[str]) -> str:
    unique = list(dict.fromkeys(terms))[:4]
    return ", ".join(f"“{term}”" for term in unique)


def _age_days(updated_at: str, now: datetime) -> float | None:
    try:
        moment = datetime.fromisoformat(updated_at)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (now - moment).total_seconds() / 86400)


def _snippet(content: str, terms: list[str]) -> str:
    if not terms:
        return content[:SNIPPET_RADIUS].strip().replace("\n", " ")
    lowered = content.lower()
    for term in terms:
        position = lowered.find(term)
        if position >= 0:
            start = max(0, position - SNIPPET_RADIUS // 2)
            end = min(len(content), position + SNIPPET_RADIUS)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(content) else ""
            return f"{prefix}{content[start:end].strip()}{suffix}".replace("\n", " ")
    return content[:SNIPPET_RADIUS].strip().replace("\n", " ")
