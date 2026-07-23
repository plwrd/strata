"""Discover connections: what should be linked, merged, or looked at again.

Deliberately model-free. Every suggestion here is *computed* — semantic
similarity from the vector index, unlinked title mentions from the link
scanner — so each one comes with a score and an inspectable reason, and none
of them can hallucinate a relationship that has no basis in the workspace.

Nothing is ever applied from here. A suggestion carries the operation that
*would* connect the notes; accepting it goes through the standard operation
review/apply flow, and merging is never automatic (a "duplicate" suggestion
proposes a relationship, and the human does the merging).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.infrastructure.logging.logger import get_logger
from app.services.note_service import NoteService
from app.services.search_service import SearchService

logger = get_logger(__name__)

# Below this, similarity is a weak signal; above DUPLICATE_THRESHOLD, the notes
# say close to the same thing and should probably become one.
SIMILAR_THRESHOLD = 0.45
DUPLICATE_THRESHOLD = 0.9
MAX_SUGGESTIONS = 12
MAX_DUPLICATES = 20

ConnectionKind = Literal["similar", "duplicate", "mention"]


class ConnectionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_a: str
    note_a_title: str
    note_b: str
    note_b_title: str
    layer_id: str
    kind: ConnectionKind = "similar"
    score: float = 0.0
    explanation: str = ""
    excerpt: str = ""
    # What accepting would do — the UI builds a one-operation plan from this
    # and pushes it through the normal review/apply flow.
    suggested_relationship: str = "relates_to"


class ConnectionService:
    def __init__(self, notes: NoteService, search: SearchService) -> None:
        self._notes = notes
        self._search = search

    def suggest_for_note(self, note_id: str, limit: int = 8) -> list[ConnectionSuggestion]:
        """Connections the open note might be missing, best first."""
        note = self._notes.get_note(note_id)
        already_linked = {backlink.source_id for backlink in self._notes.backlinks(note_id)}
        title_index = self._notes.build_title_index(self._notes.list_notes())
        for link in note.metadata.links:
            target = title_index.get(link.target_title.strip().lower())
            if target:
                already_linked.add(target)

        suggestions: list[ConnectionSuggestion] = []

        for result in self._search.similar_to(note_id, limit=limit + len(already_linked)):
            if result.object_id in already_linked or result.score < SIMILAR_THRESHOLD:
                continue
            duplicate = result.score >= DUPLICATE_THRESHOLD
            suggestions.append(
                ConnectionSuggestion(
                    note_a=note_id,
                    note_a_title=note.metadata.title,
                    note_b=result.object_id,
                    note_b_title=result.title,
                    layer_id=note.metadata.layer_id,
                    kind="duplicate" if duplicate else "similar",
                    score=result.score,
                    explanation=(
                        f"Content is {round(result.score * 100)}% similar — "
                        + (
                            "these may be the same note twice. Review before merging "
                            "anything; nothing merges automatically."
                            if duplicate
                            else "they discuss related material without being linked."
                        )
                    ),
                    excerpt=result.snippet,
                    suggested_relationship="supersedes" if duplicate else "relates_to",
                )
            )

        for mention in self._notes.unlinked_mentions(note_id):
            if mention.source_id in already_linked:
                continue
            suggestions.append(
                ConnectionSuggestion(
                    note_a=mention.source_id,
                    note_a_title=mention.source_title,
                    note_b=note_id,
                    note_b_title=note.metadata.title,
                    layer_id=mention.layer_id,
                    kind="mention",
                    score=0.3,
                    explanation=(
                        f"“{mention.source_title}” names this note in prose without linking to it."
                    ),
                    excerpt=mention.context,
                    suggested_relationship="references",
                )
            )

        deduped: dict[tuple[str, str], ConnectionSuggestion] = {}
        for suggestion in suggestions:
            key = tuple(sorted((suggestion.note_a, suggestion.note_b)))
            if key not in deduped or deduped[key].score < suggestion.score:
                deduped[key] = suggestion  # type: ignore[index]
        ranked = sorted(deduped.values(), key=lambda item: -item.score)[:limit]
        logger.info("connections.suggested", count=len(ranked))
        return ranked

    def workspace_duplicates(self) -> list[ConnectionSuggestion]:
        """Probable duplicates across the whole workspace (readable layers only)."""
        notes = {note.metadata.id: note for note in self._notes.list_notes()}
        pairs = self._search.similar_pairs(list(notes), threshold=DUPLICATE_THRESHOLD)
        suggestions = [
            ConnectionSuggestion(
                note_a=first,
                note_a_title=notes[first].metadata.title,
                note_b=second,
                note_b_title=notes[second].metadata.title,
                layer_id=notes[first].metadata.layer_id,
                kind="duplicate",
                score=round(score, 4),
                explanation=f"Content is {round(score * 100)}% similar.",
                suggested_relationship="supersedes",
            )
            for first, second, score in pairs
            if first in notes and second in notes
        ]
        return sorted(suggestions, key=lambda item: -item.score)[:MAX_DUPLICATES]
