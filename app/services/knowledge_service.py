"""Process raw captures into structured knowledge — as a reviewable plan.

The model reads the selected notes (rendered inside the same neutralised source
boundaries as an export) and answers with one JSON object matching
:class:`app.domain.knowledge.KnowledgeExtraction`. The answer is validated,
cross-checked against the real workspace (unknown note ids are discarded,
already-existing titles are skipped), and turned into an
:class:`OperationPlan` — concept pages, entity pages, decision records, tasks,
tags, and `processing_status: processed` stamps. Nothing applies here; the plan
goes through the standard review → approve → transactional apply flow.

Every page the plan creates carries provenance: `review_status: ai-inferred`,
`generated_by: <execution id>`, and a `derived_from::` link back to its source.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

from pydantic import ValidationError

from app.domain.errors import ProviderError
from app.domain.ids import new_execution_id, new_job_id
from app.domain.knowledge import KnowledgeExtraction, KnowledgeProposal
from app.domain.note import Note
from app.domain.operations import Operation, OperationPlan
from app.domain.schema import KNOWLEDGE_FOLDER
from app.infrastructure.logging.logger import get_logger
from app.services.ai_service import AIService
from app.services.context_export_service import ContextExportService
from app.services.note_service import NoteService

logger = get_logger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

MAX_SOURCES = 25
MAX_CONCEPTS = 12
MAX_ENTITIES = 8
MAX_DECISIONS = 8
MAX_TASKS = 12
MAX_TAGS_PER_SOURCE = 5
MAX_RELATED = 5

PROCESS_INSTRUCTIONS = """You extract structured, reusable knowledge from a user's raw notes.

Return ONLY one JSON object of this exact shape, with no prose around it:

{
  "summary": "two or three sentences describing what the material covers",
  "concepts": [
    {"name": "Persistent AI memory", "description": "one reusable definition", "confidence": 0.9}
  ],
  "entities": [
    {"name": "Ada Lovelace", "kind": "person", "description": "who or what this is"}
  ],
  "decisions": [
    {"decision": "what was decided", "rationale": "why", "owner": "", "date": ""}
  ],
  "action_items": [
    {"action": "what must be done", "owner": "", "deadline": ""}
  ],
  "open_questions": ["what the material leaves unresolved"],
  "suggested_tags": ["lowercase-topic-tags"],
  "related_note_ids": ["<an existing note id from the context>"],
  "claims_to_verify": ["statements that need checking before being trusted"]
}

Rules:
- "kind" is one of: person, organization, project, tool, other.
- Concepts are reusable ideas worth their own page — not section headings.
- Use ONLY note ids that appear in the context. Never invent an id.
- Confidence is your honest estimate between 0 and 1.
- Empty lists are fine. Do not pad."""


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class KnowledgeService:
    def __init__(
        self,
        ai: AIService,
        notes: NoteService,
        exports: ContextExportService,
    ) -> None:
        self._ai = ai
        self._notes = notes
        self._exports = exports

    # -- the action ----------------------------------------------------------

    async def process(
        self,
        *,
        note_ids: list[str],
        provider_id: str,
        model: str,
        confirmed_remote: bool = False,
    ) -> KnowledgeProposal:
        """Extract knowledge from the selected notes and propose a plan."""
        if not note_ids:
            raise ProviderError("Select at least one note to process.")
        if len(note_ids) > MAX_SOURCES:
            raise ProviderError(f"Process at most {MAX_SOURCES} notes at a time.")

        plan = self._exports.plan(object_ids=note_ids, target="claude")
        if not plan.sources:
            raise ProviderError("None of the selected notes are readable.")

        listing = "Existing notes you may reference by id:\n\n" + "\n".join(
            f"- id={note.metadata.id} title={note.metadata.title!r}"
            for note in self._notes.list_notes()
        )
        blocks = "\n\n".join(self._exports.render_source_block(source) for source in plan.sources)
        context = f"{listing}\n\nMaterial to process:\n\n{blocks}\n\n{PROCESS_INSTRUCTIONS}"
        layer_ids = sorted({source.layer_id for source in plan.sources})
        execution_id = new_execution_id()

        full = ""
        async for event in self._ai.run(
            provider_id=provider_id,
            model=model,
            prompt="Process the material into structured knowledge as JSON only.",
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
                raise ProviderError(event.error or "The model failed to process the notes.")

        extraction = self._parse(full)
        sources = [
            note for note in self._notes.get_notes(note_ids) if note.metadata.id in set(note_ids)
        ]
        return self._propose(extraction, sources, execution_id, provider_id, model)

    def process_sync(self, **kwargs: object) -> KnowledgeProposal:
        """Blocking wrapper for the bridge's worker thread."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.process(**kwargs))  # type: ignore[arg-type]
        finally:
            loop.close()

    # -- parsing -------------------------------------------------------------

    def _parse(self, text: str) -> KnowledgeExtraction:
        """Validated or empty — a garbage answer proposes nothing, silently
        inventing knowledge is not an option."""
        match = _JSON_BLOCK.search(text)
        if not match:
            return KnowledgeExtraction(summary="The model did not return an extraction.")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return KnowledgeExtraction(summary="The model's extraction was not valid JSON.")
        if not isinstance(payload, dict):
            return KnowledgeExtraction(summary="The model's extraction was not an object.")
        try:
            return KnowledgeExtraction.model_validate(payload)
        except ValidationError:
            # Salvage the shape field by field rather than dropping everything.
            salvaged = KnowledgeExtraction()
            for field in KnowledgeExtraction.model_fields:
                if field not in payload:
                    continue
                try:
                    partial = KnowledgeExtraction.model_validate({field: payload[field]})
                except ValidationError:
                    continue
                setattr(salvaged, field, getattr(partial, field))
            if not salvaged.summary:
                salvaged.summary = "Parts of the extraction did not fit the schema."
            return salvaged

    # -- proposal building ---------------------------------------------------

    def _propose(
        self,
        extraction: KnowledgeExtraction,
        sources: list[Note],
        execution_id: str,
        provider: str,
        model: str,
    ) -> KnowledgeProposal:
        all_notes = self._notes.list_notes()
        existing_titles = {note.metadata.title.strip().lower() for note in all_notes}
        known_ids = {note.metadata.id for note in all_notes}
        warnings: list[str] = []
        operations: list[Operation] = []

        derived = "\n".join(f"derived_from:: [[{note.metadata.title}]]" for note in sources)
        source_layer = sources[0].metadata.layer_id

        def provenance(confidence: float | None = None) -> dict[str, str]:
            fields = {
                "review_status": "ai-inferred",
                "generated_by": execution_id,
            }
            if confidence is not None:
                fields["confidence"] = str(round(confidence * 100))
            return fields

        for concept in extraction.concepts[:MAX_CONCEPTS]:
            if concept.name.strip().lower() in existing_titles:
                warnings.append(f"Concept “{concept.name}” already exists — skipped.")
                continue
            operations.append(
                Operation(
                    type="create_note",
                    layer_id=source_layer,
                    folder_path=KNOWLEDGE_FOLDER,
                    title=concept.name[:200],
                    content=(
                        f"# {concept.name}\n\n{concept.description}\n\n## Sources\n\n{derived}\n"
                    ),
                    properties={"type": "concept", **provenance(concept.confidence)},
                    rationale=f"Concept extracted (confidence {concept.confidence:.2f})",
                )
            )

        entity_schema = {"person": "person", "organization": "organization", "project": "project"}
        for entity in extraction.entities[:MAX_ENTITIES]:
            if entity.name.strip().lower() in existing_titles:
                continue
            operations.append(
                Operation(
                    type="create_note",
                    layer_id=source_layer,
                    folder_path=KNOWLEDGE_FOLDER,
                    title=entity.name[:200],
                    content=f"# {entity.name}\n\n{entity.description}\n\n{derived}\n",
                    properties={
                        "type": entity_schema.get(entity.kind, "concept"),
                        **provenance(),
                    },
                    rationale=f"{entity.kind.title()} mentioned in the material",
                )
            )

        for decision in extraction.decisions[:MAX_DECISIONS]:
            title = f"Decision: {decision.decision[:80]}"
            if title.strip().lower() in existing_titles:
                continue
            body = (
                f"# {title}\n\n## Decision\n\n{decision.decision}\n\n"
                f"## Rationale\n\n{decision.rationale or '_Not stated in the source._'}\n\n"
                f"{derived}\n"
            )
            operations.append(
                Operation(
                    type="create_note",
                    layer_id=source_layer,
                    folder_path=KNOWLEDGE_FOLDER,
                    title=title[:200],
                    content=body,
                    properties={
                        "type": "decision",
                        "status": "proposed",
                        **({"date": decision.date} if decision.date else {}),
                        **({"deciders": decision.owner} if decision.owner else {}),
                        **provenance(),
                    },
                    rationale="Possible decision detected — confirm or reject it",
                )
            )

        for item in extraction.action_items[:MAX_TASKS]:
            operations.append(
                Operation(
                    type="create_task",
                    layer_id=source_layer,
                    folder_path=KNOWLEDGE_FOLDER,
                    title=item.action[:200],
                    content=f"# {item.action}\n\n{derived}\n",
                    properties={
                        "status": "not started",
                        **({"assignee": item.owner} if item.owner else {}),
                        **({"due": item.deadline} if item.deadline else {}),
                        **provenance(),
                    },
                    rationale="Action item extracted from the material",
                )
            )

        clean_tags = [
            tag.strip().lstrip("#").lower()
            for tag in extraction.suggested_tags
            if tag.strip().lstrip("#")
        ]
        for note in sources:
            for tag in clean_tags[:MAX_TAGS_PER_SOURCE]:
                if tag not in note.metadata.tags:
                    operations.append(
                        Operation(
                            type="add_tag",
                            layer_id=note.metadata.layer_id,
                            note_id=note.metadata.id,
                            tag=tag[:120],
                            rationale="Suggested topic tag",
                        )
                    )

        valid_related = [nid for nid in extraction.related_note_ids if nid in known_ids]
        dropped = len(extraction.related_note_ids) - len(valid_related)
        if dropped:
            warnings.append(f"{dropped} related-note id(s) did not exist and were discarded.")
        for note in sources:
            for related_id in valid_related[:MAX_RELATED]:
                if related_id == note.metadata.id:
                    continue
                operations.append(
                    Operation(
                        type="add_relationship",
                        layer_id=note.metadata.layer_id,
                        note_id=note.metadata.id,
                        target_note_id=related_id,
                        relationship="relates_to",
                        rationale="The material relates to this existing note",
                    )
                )

        # Stamp the sources as processed only when the extraction actually
        # produced something — a failed extraction must not mark work as done.
        if operations:
            for note in sources:
                operations.append(
                    Operation(
                        type="set_property",
                        layer_id=note.metadata.layer_id,
                        note_id=note.metadata.id,
                        property_key="processing_status",
                        property_value="processed",
                        rationale="Mark the capture as processed",
                    )
                )

        summary = extraction.summary or "Process the selected notes into knowledge."
        if extraction.open_questions:
            warnings.append("Open questions: " + " · ".join(extraction.open_questions[:5]))
        if extraction.claims_to_verify:
            warnings.append("Needs verification: " + " · ".join(extraction.claims_to_verify[:5]))

        plan = OperationPlan(
            id=new_job_id(),
            summary=summary[:400],
            operations=operations,
            created_at=_now(),
            provider=provider,
            model=model,
            prompt="Process into knowledge",
        )
        logger.info(
            "knowledge.proposed",
            operations=len(operations),
            concepts=len(extraction.concepts),
            sources=len(sources),
        )
        return KnowledgeProposal(
            source_note_ids=[note.metadata.id for note in sources],
            extractions=[extraction],
            plan=plan,
            warnings=warnings,
        )
