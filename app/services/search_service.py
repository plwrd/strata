"""Lexical search over readable layers.

Milestone 1 ships the lexical half of the hybrid ranker described in ADR-0007:
scored by title, tag, property and body matches with recency as a tiebreak. The
semantic half (embeddings) and the persistent per-layer FTS5 index arrive in
Milestone 4; the ``SearchResult.reasons`` field already carries the
"why this matched" explanation the final ranker will populate.

Privacy: the candidate set is ``NoteService.list_notes()``, which only ever
returns notes from readable layers. A locked layer therefore contributes no
results, no counts, no snippets and no facets — by construction rather than by a
filter that could be forgotten.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.services.note_service import NoteService

_WORD = re.compile(r"[\w'-]+", re.UNICODE)

TITLE_WEIGHT = 8.0
TAG_WEIGHT = 5.0
PROPERTY_WEIGHT = 3.0
BODY_WEIGHT = 1.0
RECENCY_WEIGHT = 2.0

SNIPPET_RADIUS = 90


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


class SearchService:
    def __init__(self, notes: NoteService) -> None:
        self._notes = notes
        # Milestone 4 adds a persistent per-layer index. Until then the candidate
        # set is read live from readable layers, so there is nothing cached that
        # could survive a lock — but the hook exists now, and is called, so that the
        # index cannot be added later without someone confronting this method.
        self._cache: dict[str, object] = {}

    def forget_layer(self, layer_id: str) -> None:
        """Drop everything derived from a layer that just locked."""
        self._cache.pop(layer_id, None)

    def search(
        self,
        query: str,
        *,
        layer_ids: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
    ) -> list[SearchResult]:
        terms = [term.lower() for term in _WORD.findall(query)]
        wanted_tags = {tag.lower() for tag in (tags or [])}
        results: list[SearchResult] = []
        now = datetime.now(tz=timezone.utc)

        for note in self._notes.list_notes(layer_ids):
            meta = note.metadata
            if wanted_tags and not wanted_tags.issubset({t.lower() for t in meta.tags}):
                continue

            score = 0.0
            reasons: list[str] = []

            if not terms:
                # A tag-only or layer-only query: everything that passed the
                # filters matches, ranked by recency.
                score = 1.0
                if wanted_tags:
                    reasons.append(f"Tagged {', '.join(sorted(wanted_tags))}")
            else:
                title_words = {word.lower() for word in _WORD.findall(meta.title)}
                tag_words = {tag.lower() for tag in meta.tags}
                property_words = {
                    word.lower()
                    for value in meta.properties.values()
                    if isinstance(value, str)
                    for word in _WORD.findall(value)
                }
                # Word-level, not substring: counting substrings would match the
                # query "2" inside "256-bit", which is both bad ranking and — when
                # a locked layer is present — an alarming-looking false positive.
                body_words = [word.lower() for word in _WORD.findall(note.content)]
                body_counts: dict[str, int] = {}
                for word in body_words:
                    body_counts[word] = body_counts.get(word, 0) + 1

                matched_title = [term for term in terms if term in title_words]
                matched_tags = [term for term in terms if term in tag_words]
                matched_props = [term for term in terms if term in property_words]
                body_hits = sum(body_counts.get(term, 0) for term in terms)

                score += TITLE_WEIGHT * len(matched_title)
                score += TAG_WEIGHT * len(matched_tags)
                score += PROPERTY_WEIGHT * len(matched_props)
                score += BODY_WEIGHT * min(body_hits, 10)

                if matched_title:
                    reasons.append(f"Title contains {_quote(matched_title)}")
                if matched_tags:
                    reasons.append(f"Tagged {_quote(matched_tags)}")
                if matched_props:
                    reasons.append(f"Property matches {_quote(matched_props)}")
                if body_hits:
                    plural = "s" if body_hits != 1 else ""
                    reasons.append(f"Body mentions the query {body_hits} time{plural}")

                if score <= 0:
                    continue

            age_days = _age_days(meta.updated_at, now)
            if age_days is not None and age_days <= 14:
                score += RECENCY_WEIGHT * (1 - age_days / 14)
                reasons.append(f"Updated {int(age_days)} day(s) ago")

            results.append(
                SearchResult(
                    object_id=meta.id,
                    layer_id=meta.layer_id,
                    title=meta.title,
                    path=meta.display_path,
                    snippet=_snippet(note.content, terms),
                    score=round(score, 3),
                    tags=list(meta.tags),
                    reasons=reasons,
                )
            )

        results.sort(key=lambda result: (-result.score, result.title.lower()))
        return results[:limit]


def _quote(terms: list[str]) -> str:
    return ", ".join(f"“{term}”" for term in dict.fromkeys(terms))


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
        return content[:SNIPPET_RADIUS].strip()
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
