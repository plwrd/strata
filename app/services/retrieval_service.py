"""Scoped retrieval: choose the notes an AI request should see.

"Ask your workspace" must not mean "send the workspace". Retrieval turns a
question into a small, ranked set of note ids using the existing hybrid search
(lexical + semantic + tags + recency), and the request pipeline then renders
those notes through the same context plan as a manual selection — policy gate,
privacy counts, neutralised source boundaries and all.

Permissions come first by construction: the search service only ever indexes
readable layers, and a locked layer's index is destroyed at lock time. There is
no code path here that could rank what cannot be read.
"""

from __future__ import annotations

from app.infrastructure.logging.logger import get_logger
from app.services.search_service import SearchService

logger = get_logger(__name__)

DEFAULT_LIMIT = 8
MAX_LIMIT = 25


class RetrievalService:
    def __init__(self, search: SearchService) -> None:
        self._search = search

    def retrieve(
        self,
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        layer_ids: list[str] | None = None,
    ) -> list[str]:
        """Ranked note ids for AI context. Empty when nothing qualifies —
        an honest "your notes do not cover this" beats padding the context."""
        capped = max(1, min(limit, MAX_LIMIT))
        results = self._search.search(query, layer_ids=layer_ids, limit=capped)
        note_ids = [result.object_id for result in results]
        logger.info("retrieval.selected", requested=capped, selected=len(note_ids))
        return note_ids
