"""Weekly synthesis and knowledge health — the workspace looking at itself.

The weekly review is a *manual* action first: gather what changed in the
window, ask the model the review questions, and save the answer as a
`weekly-review` note with `derived_from::` links to everything it read.
Scheduling would be a local, opt-in affair and is deliberately not built until
someone asks — a surprise AI run is a surprise bill and a surprise send.

Knowledge health is model-free arithmetic over the workspace: unprocessed
captures, stale notes, orphans, broken links, duplicates, unreviewed AI pages,
decisions due for review, unused prompts — each with the action that would fix
it, because a dashboard that only counts problems is a guilt trip, not a tool.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.errors import ProviderError
from app.domain.ids import new_execution_id, new_job_id
from app.domain.note import Note
from app.domain.operations import Operation, OperationPlan
from app.domain.schema import REPORTS_FOLDER
from app.infrastructure.logging.logger import get_logger
from app.services.ai_service import AIService
from app.services.connection_service import ConnectionService, ConnectionSuggestion
from app.services.context_export_service import ContextExportService
from app.services.note_service import NoteService
from app.services.prompt_library_service import PromptLibraryService

logger = get_logger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

MAX_REVIEW_SOURCES = 25
STALE_DAYS = 90
UNUSED_PROMPT_DAYS = 30

WEEKLY_INSTRUCTIONS = """You write a weekly review of a personal knowledge workspace.

You are given the notes that were created or changed in the review window.
Return ONLY one JSON object:

{
  "learned": ["what the user learned, grounded in the notes"],
  "decided": ["decisions that were made"],
  "completed": ["work that was finished"],
  "unresolved": ["what remains open"],
  "themes": ["ideas that appeared repeatedly"],
  "next": ["what should plausibly happen next"],
  "promote": ["raw captures worth promoting into permanent knowledge, by title"]
}

Rules:
- Ground every statement in the notes you were given. Do not invent activity.
- Empty lists are fine — a quiet week is a quiet week."""


class WeeklyReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learned: list[str] = Field(default_factory=list)
    decided: list[str] = Field(default_factory=list)
    completed: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    next: list[str] = Field(default_factory=list)
    promote: list[str] = Field(default_factory=list)


class WeeklyProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: OperationPlan
    warnings: list[str] = Field(default_factory=list)


class HealthItem(BaseModel):
    """One category of the health report, with the action that fixes it."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    count: int = 0
    note_ids: list[str] = Field(default_factory=list)
    note_titles: list[str] = Field(default_factory=list)
    recommendation: str = ""


class HealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[HealthItem] = Field(default_factory=list)
    duplicates: list[ConnectionSuggestion] = Field(default_factory=list)
    total_notes: int = 0
    locked_layers: int = 0


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class ReviewService:
    def __init__(
        self,
        ai: AIService,
        notes: NoteService,
        exports: ContextExportService,
        connections: ConnectionService,
        prompts: PromptLibraryService,
    ) -> None:
        self._ai = ai
        self._notes = notes
        self._exports = exports
        self._connections = connections
        self._prompts = prompts

    # -- weekly review -------------------------------------------------------

    def changed_note_ids(self, days: int = 7) -> list[str]:
        cutoff = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
        changed = [note for note in self._notes.list_notes() if note.metadata.updated_at >= cutoff]
        changed.sort(key=lambda note: note.metadata.updated_at, reverse=True)
        return [note.metadata.id for note in changed[:MAX_REVIEW_SOURCES]]

    async def generate_weekly(
        self,
        *,
        provider_id: str,
        model: str,
        days: int = 7,
        confirmed_remote: bool = False,
    ) -> WeeklyProposal:
        note_ids = self.changed_note_ids(days)
        if not note_ids:
            raise ProviderError("Nothing changed in the window — there is nothing to review.")

        plan = self._exports.plan(object_ids=note_ids, target="claude")
        blocks = "\n\n".join(self._exports.render_source_block(source) for source in plan.sources)
        context = f"{blocks}\n\n{WEEKLY_INSTRUCTIONS}"
        layer_ids = sorted({source.layer_id for source in plan.sources})
        execution_id = new_execution_id()

        full = ""
        async for event in self._ai.run(
            provider_id=provider_id,
            model=model,
            prompt=f"Write the weekly review for the last {days} day(s) as JSON only.",
            sources=context,
            layer_ids=layer_ids,
            object_count=len(plan.sources),
            private_object_count=plan.private_source_count,
            confirmed_remote=confirmed_remote,
            max_output_tokens=6000,
            kind="processing",
            source_object_ids=[source.object_id for source in plan.sources],
            execution_id=execution_id,
        ):
            if event.kind == "delta":
                full += event.text
            elif event.kind == "error":
                raise ProviderError(event.error or "The model failed to write the review.")

        result = self._parse(full)
        source_notes = self._notes.get_notes(note_ids)
        derived = "\n".join(f"derived_from:: [[{note.metadata.title}]]" for note in source_notes)
        week_start = (_now() - timedelta(days=days)).date().isoformat()
        title = f"Weekly review {week_start}"

        empty = not any(getattr(result, field) for field in WeeklyReviewResult.model_fields)
        warnings = ["The model returned an empty review."] if empty else []

        sections: list[str] = [f"# {title}", ""]
        for heading, items in (
            ("Learned", result.learned),
            ("Decided", result.decided),
            ("Completed", result.completed),
            ("Unresolved", result.unresolved),
            ("Emerging themes", result.themes),
            ("Next", result.next),
            ("Promote from the Inbox", result.promote),
        ):
            if items:
                sections += [
                    f"## {heading}",
                    "",
                    "\n".join(f"- {item}" for item in items),
                    "",
                ]

        return WeeklyProposal(
            plan=OperationPlan(
                id=new_job_id(),
                summary=f"Weekly review of {len(plan.sources)} changed note(s)",
                operations=(
                    []
                    if empty
                    else [
                        Operation(
                            type="create_note",
                            layer_id=source_notes[0].metadata.layer_id,
                            folder_path=REPORTS_FOLDER,
                            title=title[:200],
                            content="\n".join(sections) + f"\n{derived}\n",
                            properties={
                                "type": "weekly-review",
                                "week_start": week_start,
                                "review_status": "ai-inferred",
                                "generated_by": execution_id,
                            },
                            rationale=f"Review of the last {days} day(s)",
                        )
                    ]
                ),
                provider=provider_id,
                model=model,
                prompt="Generate weekly review",
            ),
            warnings=warnings,
        )

    def generate_weekly_sync(self, **kwargs: object) -> WeeklyProposal:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.generate_weekly(**kwargs))  # type: ignore[arg-type]
        finally:
            loop.close()

    @staticmethod
    def _parse(text: str) -> WeeklyReviewResult:
        match = _JSON_BLOCK.search(text)
        if not match:
            return WeeklyReviewResult()
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return WeeklyReviewResult()
        if not isinstance(payload, dict):
            return WeeklyReviewResult()
        try:
            return WeeklyReviewResult.model_validate(payload)
        except ValidationError:
            salvaged = WeeklyReviewResult()
            for field in WeeklyReviewResult.model_fields:
                if field not in payload:
                    continue
                try:
                    partial = WeeklyReviewResult.model_validate({field: payload[field]})
                except ValidationError:
                    continue
                setattr(salvaged, field, getattr(partial, field))
            return salvaged

    # -- knowledge health ----------------------------------------------------

    def health(self, *, locked_layers: int = 0) -> HealthReport:
        """Model-free health arithmetic. Locked layers contribute nothing —
        their count is reported so the picture is honestly incomplete."""
        notes = self._notes.list_notes()
        link_health = self._notes.link_health()
        stale_cutoff = (_now() - timedelta(days=STALE_DAYS)).isoformat(timespec="seconds")

        def item(key: str, label: str, matching: list[Note], recommendation: str) -> HealthItem:
            return HealthItem(
                key=key,
                label=label,
                count=len(matching),
                note_ids=[note.metadata.id for note in matching[:20]],
                note_titles=[note.metadata.title for note in matching[:20]],
                recommendation=recommendation if matching else "",
            )

        unprocessed = [
            note for note in notes if note.metadata.properties.get("processing_status") == "raw"
        ]
        stale = [
            note
            for note in notes
            if note.metadata.updated_at < stale_cutoff
            and note.metadata.properties.get("processing_status") != "archived"
        ]
        unreviewed = [
            note for note in notes if note.metadata.properties.get("review_status") == "ai-inferred"
        ]
        by_id = {note.metadata.id: note for note in notes}
        orphans = [by_id[nid] for nid in link_health.orphans if nid in by_id]
        broken_sources = sorted({source for source, _target in link_health.broken})
        broken = [by_id[nid] for nid in broken_sources if nid in by_id]
        sourceless = [
            note
            for note in notes
            if note.metadata.properties.get("type") in ("report", "concept")
            and not any(link.relationship == "derived_from" for link in note.metadata.links)
        ]
        today = _now().date().isoformat()
        decisions_due = [
            note
            for note in notes
            if note.metadata.properties.get("type") == "decision"
            and str(note.metadata.properties.get("review_date") or "9999") <= today
        ]

        unused_cutoff = (_now() - timedelta(days=UNUSED_PROMPT_DAYS)).isoformat(timespec="seconds")
        unused_prompts = [
            prompt
            for prompt in self._prompts.list_prompts()
            if prompt.usage_count == 0 and prompt.created_at < unused_cutoff
        ]

        items = [
            item(
                "unprocessed",
                "Unprocessed captures",
                unprocessed,
                "Select them and run “Process into knowledge” in the Changes tab.",
            ),
            item(
                "stale",
                f"Notes untouched for {STALE_DAYS}+ days",
                stale,
                "Skim, archive (set processing_status: archived), or refresh them.",
            ),
            item(
                "unreviewed",
                "AI pages awaiting review",
                unreviewed,
                "Read each page; set review_status to “reviewed” once verified.",
            ),
            item(
                "orphans",
                "Orphan notes (no links in or out)",
                orphans,
                "Open one and run “Discover” in the Links tab to find its neighbours.",
            ),
            item(
                "broken",
                "Notes with broken links",
                broken,
                "Fix or remove the links; renames rewrite links automatically.",
            ),
            item(
                "sourceless",
                "Reports and concepts without sources",
                sourceless,
                "Add derived_from:: links so their claims stay traceable.",
            ),
            item(
                "decisions_due",
                "Decisions due for review",
                decisions_due,
                "Revisit each decision; confirm, supersede, or reject it.",
            ),
            HealthItem(
                key="unused_prompts",
                label=f"Saved prompts unused for {UNUSED_PROMPT_DAYS}+ days",
                count=len(unused_prompts),
                note_titles=[prompt.name for prompt in unused_prompts[:20]],
                recommendation=(
                    "Delete them, or give them a first run from the prompt library."
                    if unused_prompts
                    else ""
                ),
            ),
        ]
        return HealthReport(
            items=items,
            duplicates=self._connections.workspace_duplicates(),
            total_notes=len(notes),
            locked_layers=locked_layers,
        )
