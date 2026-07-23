"""Project memory: keep a project's page current — with the diff shown first.

"Refresh project memory" compares a project note with the notes around it
(backlinks, outgoing links, recently updated related material), asks the model
for an updated page, and proposes it as an ``update_note`` operation — which is
*destructive* by classification, so the review UI shows the full before/after
and never pre-approves it. Nothing about a project page ever changes silently.
"""

from __future__ import annotations

import asyncio
import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.errors import ProviderError
from app.domain.ids import new_execution_id, new_job_id
from app.domain.operations import Operation, OperationPlan
from app.infrastructure.logging.logger import get_logger
from app.services.ai_service import AIService
from app.services.context_export_service import ContextExportService
from app.services.note_service import NoteService

logger = get_logger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
MAX_RELATED = 15

PROJECT_MEMORY_INSTRUCTIONS = """You maintain a project's memory page.

You are given the CURRENT project page and the related notes around it. Write
the UPDATED page as JSON:

{
  "content": "The full updated Markdown body of the project page.",
  "changes": ["one line per meaningful change you made, e.g. 'Added risk: …'"]
}

Rules:
- Keep the page's structure: Goal, Current status, Key people, Decisions,
  Risks, Open questions, Recent changes, Next actions (add missing sections).
- Preserve everything still true; update what the related notes supersede.
- Every NEW claim must come from a related note. Do not invent progress.
- List every meaningful change in "changes" — an unexplained edit is a bug."""


class ProjectMemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(default="", max_length=512_000)
    changes: list[str] = Field(default_factory=list)


class MemoryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: OperationPlan
    changes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MemoryService:
    def __init__(
        self,
        ai: AIService,
        notes: NoteService,
        exports: ContextExportService,
    ) -> None:
        self._ai = ai
        self._notes = notes
        self._exports = exports

    def related_note_ids(self, note_id: str) -> list[str]:
        """The project's neighbourhood: backlinks and outgoing links, by recency."""
        note = self._notes.get_note(note_id)
        all_notes = self._notes.list_notes()
        title_index = self._notes.build_title_index(all_notes)
        related: set[str] = set()
        for backlink in self._notes.backlinks(note_id):
            related.add(backlink.source_id)
        for link in note.metadata.links:
            target = title_index.get(link.target_title.strip().lower())
            if target:
                related.add(target)
        related.discard(note_id)
        by_id = {candidate.metadata.id: candidate for candidate in all_notes}
        ordered = sorted(
            (by_id[rid] for rid in related if rid in by_id),
            key=lambda candidate: candidate.metadata.updated_at,
            reverse=True,
        )
        return [candidate.metadata.id for candidate in ordered[:MAX_RELATED]]

    async def refresh_project(
        self,
        *,
        note_id: str,
        provider_id: str,
        model: str,
        confirmed_remote: bool = False,
    ) -> MemoryProposal:
        project = self._notes.get_note(note_id)
        related_ids = self.related_note_ids(note_id)
        if not related_ids:
            raise ProviderError(
                "Nothing links to or from this note yet — there is no new material to refresh from."
            )

        plan = self._exports.plan(object_ids=[note_id, *related_ids], target="claude")
        blocks = "\n\n".join(self._exports.render_source_block(source) for source in plan.sources)
        context = (
            f"The FIRST source below is the current project page; the rest are the "
            f"related notes.\n\n{blocks}\n\n{PROJECT_MEMORY_INSTRUCTIONS}"
        )
        layer_ids = sorted({source.layer_id for source in plan.sources})
        execution_id = new_execution_id()

        full = ""
        async for event in self._ai.run(
            provider_id=provider_id,
            model=model,
            prompt=f"Refresh the project memory page “{project.metadata.title}” as JSON only.",
            sources=context,
            layer_ids=layer_ids,
            object_count=len(plan.sources),
            private_object_count=plan.private_source_count,
            confirmed_remote=confirmed_remote,
            max_output_tokens=8000,
            kind="processing",
            source_object_ids=[source.object_id for source in plan.sources],
            execution_id=execution_id,
        ):
            if event.kind == "delta":
                full += event.text
            elif event.kind == "error":
                raise ProviderError(event.error or "The model failed to refresh the page.")

        update = self._parse(full)
        warnings: list[str] = []
        operations: list[Operation] = []
        if not update.content.strip():
            warnings.append("The model returned no updated page; nothing is proposed.")
        elif update.content.strip() == project.content.strip():
            warnings.append("The page is already current; nothing changed.")
        else:
            operations.append(
                Operation(
                    type="update_note",
                    layer_id=project.metadata.layer_id,
                    note_id=note_id,
                    content=update.content,
                    rationale=("; ".join(update.changes[:5])[:400] or "Refresh from related notes"),
                )
            )

        return MemoryProposal(
            plan=OperationPlan(
                id=new_job_id(),
                summary=f"Refresh project memory: {project.metadata.title}"[:400],
                operations=operations,
                provider=provider_id,
                model=model,
                prompt="Refresh project memory",
            ),
            changes=update.changes,
            warnings=warnings,
        )

    def refresh_project_sync(self, **kwargs: object) -> MemoryProposal:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.refresh_project(**kwargs))  # type: ignore[arg-type]
        finally:
            loop.close()

    @staticmethod
    def _parse(text: str) -> ProjectMemoryUpdate:
        match = _JSON_BLOCK.search(text)
        if not match:
            return ProjectMemoryUpdate()
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return ProjectMemoryUpdate()
        if not isinstance(payload, dict):
            return ProjectMemoryUpdate()
        try:
            return ProjectMemoryUpdate.model_validate(payload)
        except ValidationError:
            content = payload.get("content")
            return ProjectMemoryUpdate(content=content if isinstance(content, str) else "")
