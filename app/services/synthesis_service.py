"""Multi-source synthesis: several notes in, one cited knowledge page out.

The model reads the selected notes inside neutralised source boundaries and
answers with one JSON object matching :class:`SynthesisResult`. The result is
validated, its citations are checked against the *actual* context — a cited
source id that was never sent is stripped and reported, not printed — and the
whole thing becomes a single ``create_note`` operation plan into ``Reports/``
with full provenance. Source notes are never modified or replaced.

The output structure forces the honesty the product requires: agreements,
disagreements and contradictions are separate sections, and statements the
model marks as its own inference are listed apart from what the sources say.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.errors import ProviderError
from app.domain.ids import new_execution_id, new_job_id
from app.domain.operations import Operation, OperationPlan
from app.domain.schema import REPORTS_FOLDER
from app.infrastructure.logging.logger import get_logger
from app.services.ai_service import AIService
from app.services.context_export_service import ContextExportService
from app.services.note_service import NoteService

logger = get_logger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_CITATION = re.compile(r"\[?(STRATA-SOURCE-\d{3})\]?")

SynthesisKind = Literal[
    "concept",
    "summary",
    "comparison",
    "research-brief",
    "project-plan",
    "faq",
    "timeline",
]

MAX_SOURCES = 25

KIND_DIRECTIVES: dict[str, str] = {
    "concept": "Write a reusable concept page: definition, why it matters, and boundaries.",
    "summary": "Write a faithful summary that preserves the sources' own emphasis.",
    "comparison": "Compare the sources: criteria, positions, and what the differences turn on.",
    "research-brief": "Write a research brief: question, findings, evidence quality, next steps.",
    "project-plan": "Write a project plan: goal, milestones, risks, and open decisions.",
    "faq": "Write an FAQ: the questions this material answers, each answered from the sources.",
    "timeline": "Write a timeline: events in order, each anchored to its source.",
}

SYNTHESIS_INSTRUCTIONS = """You synthesise a user's notes into one structured document.

Return ONLY one JSON object of this exact shape, with no prose around it:

{
  "title": "a specific, descriptive title",
  "main_idea": "the through-line, in two or three sentences",
  "sections": [
    {"heading": "Section heading", "body": "Markdown. Cite sources inline as [STRATA-SOURCE-001]."}
  ],
  "agreements": ["where the sources agree [STRATA-SOURCE-001][STRATA-SOURCE-002]"],
  "disagreements": ["where they diverge, and what it turns on"],
  "contradictions": ["direct contradictions between sources"],
  "examples": ["important concrete examples from the sources"],
  "missing_information": ["what the sources do not cover"],
  "open_questions": ["what remains unresolved"],
  "inferences": ["statements that are YOUR inference, not in any source"]
}

Rules:
- Cite ONLY source ids that appear in the context. Never invent a citation.
- A claim from a source carries its citation. A claim without a citation must
  appear under "inferences", not in a section.
- Empty lists are fine. Do not pad."""


class SynthesisSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=32_000)


class SynthesisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200)
    main_idea: str = Field(default="", max_length=4000)
    sections: list[SynthesisSection] = Field(default_factory=list)
    agreements: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)


class SynthesisProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: OperationPlan
    warnings: list[str] = Field(default_factory=list)


class SynthesisService:
    def __init__(
        self,
        ai: AIService,
        notes: NoteService,
        exports: ContextExportService,
    ) -> None:
        self._ai = ai
        self._notes = notes
        self._exports = exports

    async def synthesize(
        self,
        *,
        note_ids: list[str],
        kind: SynthesisKind,
        provider_id: str,
        model: str,
        confirmed_remote: bool = False,
    ) -> SynthesisProposal:
        if len(note_ids) < 2:
            raise ProviderError("Select at least two notes to synthesise.")
        if len(note_ids) > MAX_SOURCES:
            raise ProviderError(f"Synthesise at most {MAX_SOURCES} notes at a time.")

        plan = self._exports.plan(object_ids=note_ids, target="claude")
        if len(plan.sources) < 2:
            raise ProviderError("Fewer than two of the selected notes are readable.")

        blocks = "\n\n".join(self._exports.render_source_block(source) for source in plan.sources)
        directive = KIND_DIRECTIVES[kind]
        context = f"{blocks}\n\n{SYNTHESIS_INSTRUCTIONS}\n\nDocument kind: {directive}"
        layer_ids = sorted({source.layer_id for source in plan.sources})
        execution_id = new_execution_id()

        full = ""
        async for event in self._ai.run(
            provider_id=provider_id,
            model=model,
            prompt=f"Synthesise the sources into a {kind.replace('-', ' ')} as JSON only.",
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
                raise ProviderError(event.error or "The model failed to synthesise.")

        result = self._parse(full)
        valid_ids = {source.source_id for source in plan.sources}
        result, stripped = _validate_citations(result, valid_ids)

        titles = {
            source.source_id: source.title for source in plan.sources if not source.is_private
        }
        body = _render(result, kind, titles)
        source_notes = self._notes.get_notes(note_ids)
        derived = "\n".join(f"derived_from:: [[{note.metadata.title}]]" for note in source_notes)

        warnings: list[str] = []
        if stripped:
            warnings.append(
                f"{stripped} citation(s) referenced sources that were never sent and "
                "were stripped — the model tried to invent them."
            )
        if not result.sections and not result.main_idea:
            warnings.append("The model returned no usable synthesis.")

        operation_plan = OperationPlan(
            id=new_job_id(),
            summary=f"Synthesis ({kind}): {result.title or 'untitled'}"[:400],
            operations=(
                []
                if not result.sections and not result.main_idea
                else [
                    Operation(
                        type="create_note",
                        layer_id=source_notes[0].metadata.layer_id,
                        folder_path=REPORTS_FOLDER,
                        title=(result.title or f"Synthesis of {len(plan.sources)} notes")[:200],
                        content=f"{body}\n\n{derived}\n",
                        properties={
                            "type": "report",
                            "report_kind": ("concept-synthesis" if kind == "concept" else kind),
                            "review_status": "ai-inferred",
                            "generated_by": execution_id,
                        },
                        rationale=f"Synthesis of {len(plan.sources)} selected notes",
                    )
                ]
            ),
            provider=provider_id,
            model=model,
            prompt=f"Synthesise ({kind})",
        )
        logger.info("synthesis.proposed", kind=kind, sources=len(plan.sources))
        return SynthesisProposal(plan=operation_plan, warnings=warnings)

    def synthesize_sync(self, **kwargs: object) -> SynthesisProposal:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.synthesize(**kwargs))  # type: ignore[arg-type]
        finally:
            loop.close()

    @staticmethod
    def _parse(text: str) -> SynthesisResult:
        match = _JSON_BLOCK.search(text)
        if not match:
            return SynthesisResult()
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return SynthesisResult()
        if not isinstance(payload, dict):
            return SynthesisResult()
        try:
            return SynthesisResult.model_validate(payload)
        except ValidationError:
            salvaged = SynthesisResult()
            for field in SynthesisResult.model_fields:
                if field not in payload:
                    continue
                try:
                    partial = SynthesisResult.model_validate({field: payload[field]})
                except ValidationError:
                    continue
                setattr(salvaged, field, getattr(partial, field))
            return salvaged


def _validate_citations(
    result: SynthesisResult, valid_ids: set[str]
) -> tuple[SynthesisResult, int]:
    """Strip citations of sources that were never sent. Count what was stripped."""
    stripped = 0

    def clean(text: str) -> str:
        nonlocal stripped

        def replace(match: re.Match[str]) -> str:
            nonlocal stripped
            if match.group(1) in valid_ids:
                return match.group(0)
            stripped += 1
            return ""

        return _CITATION.sub(replace, text)

    return (
        result.model_copy(
            update={
                "main_idea": clean(result.main_idea),
                "sections": [
                    section.model_copy(update={"body": clean(section.body)})
                    for section in result.sections
                ],
                "agreements": [clean(item) for item in result.agreements],
                "disagreements": [clean(item) for item in result.disagreements],
                "contradictions": [clean(item) for item in result.contradictions],
                "examples": [clean(item) for item in result.examples],
            }
        ),
        stripped,
    )


def _render(result: SynthesisResult, kind: str, titles: dict[str, str]) -> str:
    """The synthesis as Markdown, with a source key so citations stay readable."""

    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items if item.strip())

    parts: list[str] = [f"# {result.title or 'Synthesis'}", ""]
    if result.main_idea:
        parts += ["## Main idea", "", result.main_idea, ""]
    for section in result.sections:
        parts += [f"## {section.heading}", "", section.body, ""]
    for heading, items in (
        ("Agreements between sources", result.agreements),
        ("Disagreements", result.disagreements),
        ("Contradictions", result.contradictions),
        ("Important examples", result.examples),
        ("Missing information", result.missing_information),
        ("Open questions", result.open_questions),
    ):
        if items:
            parts += [f"## {heading}", "", bullets(items), ""]
    if result.inferences:
        parts += [
            "## AI inferences",
            "",
            "_These statements are the model's inference, not sourced claims._",
            "",
            bullets(result.inferences),
            "",
        ]
    if titles:
        parts += [
            "## Sources",
            "",
            "\n".join(f"- {source_id}: [[{title}]]" for source_id, title in titles.items()),
            "",
        ]
    return "\n".join(parts).strip()
