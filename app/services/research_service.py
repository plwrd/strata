"""Research filing: read the page, find where it belongs, propose the changes.

The action behind the "Analyse & file" button. Given one or more captures (a
scraped page, usually) and the layers the user put in scope, it:

1. **retrieves candidate nodes** from those layers with the existing hybrid
   search — the model does not get to browse the workspace, it gets a shortlist;
2. **asks the model where the material attaches** — which nodes it extends,
   what subnodes belong underneath them, what context each node is missing;
3. **validates the answer against that shortlist** — an id the model was never
   offered is dropped, not resolved, and a node outside the selected layers can
   never be a target;
4. **returns a plan**, never a write. Approval and application stay with the
   user and the operation engine.

Appending to a node the user wrote is the one genuinely new kind of change here
(processing only ever created pages), so every appended block is fenced with a
visible provenance line naming the source and the execution — a reader must be
able to tell, months later, which paragraph was theirs and which one an AI
attached to it.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

from pydantic import ValidationError

from app.domain.errors import InvalidRequestError, ProviderError
from app.domain.ids import new_execution_id, new_job_id
from app.domain.note import Note
from app.domain.operations import Operation, OperationPlan
from app.domain.research import (
    CandidateNode,
    ContextAddition,
    ResearchAnalysis,
    ResearchProposal,
)
from app.domain.schema import KNOWLEDGE_FOLDER
from app.infrastructure.logging.logger import get_logger
from app.services.ai_service import AIService
from app.services.context_export_service import ContextExportService
from app.services.note_service import NoteService
from app.services.retrieval_service import RetrievalService
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

MAX_SOURCES = 10
MAX_CANDIDATES = 15
MAX_MATCHES = 6
MAX_SUBNODES = 10
MAX_CONTEXT_ADDITIONS = 8
MAX_TAGS = 6
# The slice of a capture used to retrieve candidates. The whole page goes to the
# model; the *query* only needs enough to rank.
QUERY_CHARS = 600

RESEARCH_INSTRUCTIONS = """You are filing new research material into a knowledge graph
that already exists.

Your job is placement, not summarising for its own sake: decide which existing
nodes this material belongs to, what belongs *underneath* them, and what each one
is now missing.

Return ONLY one JSON object of this exact shape, with no prose around it:

{
  "summary": "two or three sentences on what this material is and why it matters here",
  "key_points": ["the specific things worth remembering"],
  "node_matches": [
    {"note_id": "<an id from the candidate list>", "relevance": 0.8,
     "reason": "why this material belongs to that node"}
  ],
  "subnodes": [
    {"title": "A specific idea worth its own page", "parent_note_id": "<candidate id or \\"\\">",
     "kind": "concept", "content": "markdown body, starting at heading level 2"}
  ],
  "context_additions": [
    {"note_id": "<a candidate id>", "heading": "What this adds",
     "content": "a short section this node does not already contain"}
  ],
  "tags": ["lowercase-topic-tags"],
  "claims_to_verify": ["statements that need checking before being trusted"],
  "open_questions": ["what the material leaves unresolved"]
}

Rules:
- Use ONLY note ids from the candidate list. Never invent one. If nothing fits,
  return no matches and put the material in subnodes with an empty parent.
- "kind" is one of: concept, entity, source, question, task, other.
- A context addition must add something the node does not already say. Do not
  restate the node back to itself, and do not propose one just to fill the list.
- Write the material's claims as the material's claims. If the page asserts
  something contested, say so rather than adopting it.
- Empty lists are fine. Do not pad."""


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class ResearchService:
    def __init__(
        self,
        ai: AIService,
        notes: NoteService,
        exports: ContextExportService,
        retrieval: RetrievalService,
        workspace: WorkspaceService,
    ) -> None:
        self._ai = ai
        self._notes = notes
        self._exports = exports
        self._retrieval = retrieval
        self._workspace = workspace

    # -- the action ----------------------------------------------------------

    async def analyse(
        self,
        *,
        note_ids: list[str],
        layer_ids: list[str],
        provider_id: str,
        model: str,
        target_layer_id: str = "",
        confirmed_remote: bool = False,
    ) -> ResearchProposal:
        if not note_ids:
            raise InvalidRequestError("There is nothing to file yet.")
        if len(note_ids) > MAX_SOURCES:
            raise InvalidRequestError(f"File at most {MAX_SOURCES} captures at a time.")

        scope = self._scope(layer_ids)
        sources = self._notes.get_notes(note_ids)
        if not sources:
            raise InvalidRequestError("None of the selected captures are readable.")

        target_layer = self._target_layer(target_layer_id, scope, sources)
        candidates = self._candidates(sources, scope)

        plan = self._exports.plan(
            object_ids=[note.metadata.id for note in sources], target="claude"
        )
        if not plan.sources:
            raise InvalidRequestError("None of the selected captures are readable.")
        blocks = "\n\n".join(self._exports.render_source_block(source) for source in plan.sources)
        context = (
            f"{self._render_candidates(candidates)}\n\n"
            f"Research material to file:\n\n{blocks}\n\n{RESEARCH_INSTRUCTIONS}"
        )
        execution_id = new_execution_id()

        full = ""
        async for event in self._ai.run(
            provider_id=provider_id,
            model=model,
            prompt="Decide where this material belongs and answer as JSON only.",
            sources=context,
            layer_ids=sorted({source.layer_id for source in plan.sources} | set(scope)),
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
                raise ProviderError(event.error or "The model failed to analyse the material.")

        analysis = self._parse(full)
        return self._propose(
            analysis=analysis,
            sources=sources,
            candidates=candidates,
            target_layer_id=target_layer,
            execution_id=execution_id,
            provider=provider_id,
            model=model,
        )

    def analyse_sync(self, **kwargs: object) -> ResearchProposal:
        """Blocking wrapper for the bridge's worker thread."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.analyse(**kwargs))  # type: ignore[arg-type]
        finally:
            loop.close()

    # -- scope and candidates ------------------------------------------------

    def _scope(self, layer_ids: list[str]) -> list[str]:
        """The layers the user ticked, minus anything they cannot read.

        This list is the outer boundary of the whole operation: retrieval reads
        nothing outside it, and no proposed operation may target a layer that is
        not in it."""
        readable = {layer.id for layer in self._workspace.readable_layers()}
        chosen = [layer_id for layer_id in layer_ids if layer_id in readable]
        if not chosen:
            raise InvalidRequestError("Select at least one layer that is open and readable.")
        return chosen

    def _target_layer(self, requested: str, scope: list[str], sources: list[Note]) -> str:
        """Where nodes with no parent land."""
        if requested:
            if requested not in scope:
                raise InvalidRequestError("The target layer is not one of the selected layers.")
            self._workspace.require_readable_layer(requested)
            return requested
        source_layer = sources[0].metadata.layer_id
        return source_layer if source_layer in scope else scope[0]

    def _candidates(self, sources: list[Note], scope: list[str]) -> list[CandidateNode]:
        """Shortlist the nodes this material might attach to.

        Ranked by the same hybrid search the rest of the app uses, so the
        shortlist is explainable and permission-filtered by construction. The
        captures themselves are excluded — material does not attach to itself."""
        source_ids = {note.metadata.id for note in sources}
        ranked: dict[str, float] = {}
        for note in sources:
            query = f"{note.metadata.title} {note.content[:QUERY_CHARS]}"
            for position, note_id in enumerate(
                self._retrieval.retrieve(query, limit=MAX_CANDIDATES, layer_ids=scope)
            ):
                if note_id in source_ids:
                    continue
                # Rank by best position across sources, not by sum: a node that
                # is the top hit for one page beats one that is middling for all.
                ranked[note_id] = max(ranked.get(note_id, 0.0), 1.0 / (position + 1))

        shortlist = sorted(ranked.items(), key=lambda item: item[1], reverse=True)[:MAX_CANDIDATES]
        notes = {
            note.metadata.id: note for note in self._notes.get_notes([nid for nid, _ in shortlist])
        }
        candidates: list[CandidateNode] = []
        for note_id, score in shortlist:
            found = notes.get(note_id)
            if found is None or found.metadata.layer_id not in scope:
                continue
            candidates.append(
                CandidateNode(
                    note_id=note_id,
                    title=found.metadata.title,
                    layer_id=found.metadata.layer_id,
                    folder_path=found.metadata.folder_path,
                    node_type=str(found.metadata.properties.get("type", "")),
                    tags=list(found.metadata.tags)[:8],
                    score=round(score, 3),
                )
            )
        return candidates

    @staticmethod
    def _render_candidates(candidates: list[CandidateNode]) -> str:
        if not candidates:
            return (
                "Candidate nodes: none. The selected layers hold nothing related, "
                "so propose subnodes with an empty parent_note_id."
            )
        lines = [
            f"- id={candidate.note_id} title={candidate.title!r} "
            f"type={candidate.node_type or 'note'} tags={','.join(candidate.tags) or '-'}"
            for candidate in candidates
        ]
        return "Candidate nodes this material may attach to:\n\n" + "\n".join(lines)

    # -- parsing -------------------------------------------------------------

    def _parse(self, text: str) -> ResearchAnalysis:
        """Validated or empty. A garbage answer files nothing."""
        match = _JSON_BLOCK.search(text)
        if not match:
            return ResearchAnalysis(summary="The model did not return an analysis.")
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return ResearchAnalysis(summary="The model's analysis was not valid JSON.")
        if not isinstance(payload, dict):
            return ResearchAnalysis(summary="The model's analysis was not an object.")
        try:
            return ResearchAnalysis.model_validate(payload)
        except ValidationError:
            # Salvage field by field rather than discarding a mostly-good answer.
            salvaged = ResearchAnalysis()
            for field in ResearchAnalysis.model_fields:
                if field not in payload:
                    continue
                try:
                    partial = ResearchAnalysis.model_validate({field: payload[field]})
                except ValidationError:
                    continue
                setattr(salvaged, field, getattr(partial, field))
            if not salvaged.summary:
                salvaged.summary = "Parts of the analysis did not fit the schema."
            return salvaged

    # -- proposal building ---------------------------------------------------

    def _propose(
        self,
        *,
        analysis: ResearchAnalysis,
        sources: list[Note],
        candidates: list[CandidateNode],
        target_layer_id: str,
        execution_id: str,
        provider: str,
        model: str,
    ) -> ResearchProposal:
        by_id = {candidate.note_id: candidate for candidate in candidates}
        warnings: list[str] = []
        operations: list[Operation] = []

        derived = "\n".join(f"derived_from:: [[{note.metadata.title}]]" for note in sources)
        source_urls = [
            str(note.metadata.properties.get("source_url", ""))
            for note in sources
            if note.metadata.properties.get("source_url")
        ]
        provenance = {
            "review_status": "ai-inferred",
            "generated_by": execution_id,
        }

        def offered(note_id: str, kind: str) -> CandidateNode | None:
            candidate = by_id.get(note_id)
            if candidate is None:
                warnings.append(f"A {kind} named a node that was not offered — dropped.")
            return candidate

        # 1. Relationships from each capture to the nodes it belongs to.
        matches = [
            candidate
            for candidate in (
                offered(match.note_id, "match") for match in analysis.node_matches[:MAX_MATCHES]
            )
            if candidate is not None
        ]
        for note in sources:
            for candidate in matches:
                operations.append(
                    Operation(
                        type="add_relationship",
                        layer_id=note.metadata.layer_id,
                        note_id=note.metadata.id,
                        target_note_id=candidate.note_id,
                        relationship="relates_to",
                        rationale=f"Research material belongs to “{candidate.title}”",
                    )
                )

        # 2. Subnodes. A parent that was offered decides the layer and folder;
        #    an orphan lands in the target layer's knowledge folder rather than
        #    being thrown away.
        for subnode in analysis.subnodes[:MAX_SUBNODES]:
            parent = by_id.get(subnode.parent_note_id) if subnode.parent_note_id else None
            if subnode.parent_note_id and parent is None:
                warnings.append(
                    f"“{subnode.title}” named a parent that was not offered — filed unparented."
                )
            layer_id = parent.layer_id if parent else target_layer_id
            folder = parent.folder_path if parent else KNOWLEDGE_FOLDER
            parent_link = f"parent:: [[{parent.title}]]\n" if parent else ""
            operations.append(
                Operation(
                    type="create_note",
                    layer_id=layer_id,
                    folder_path=folder,
                    title=subnode.title[:200],
                    content=(
                        f"# {subnode.title}\n\n{subnode.content.strip()}\n\n"
                        f"## Sources\n\n{parent_link}{derived}"
                        + (f"\nsource_url:: {source_urls[0]}\n" if source_urls else "\n")
                    ),
                    properties={"type": subnode.kind, **provenance},
                    rationale=(
                        f"New subnode under “{parent.title}”"
                        if parent
                        else "New node — no existing node fitted"
                    ),
                )
            )
            if parent:
                operations.append(
                    Operation(
                        type="add_relationship",
                        layer_id=parent.layer_id,
                        note_id=parent.note_id,
                        target_title=subnode.title[:200],
                        relationship="has_subnode",
                        rationale=f"Link “{parent.title}” down to its new subnode",
                    )
                )

        # 3. Context appended to nodes that were already about this. This is the
        #    only place research touches something the user wrote, so the block
        #    says so in the note itself, not only in the audit log.
        for addition in analysis.context_additions[:MAX_CONTEXT_ADDITIONS]:
            target = offered(addition.note_id, "context addition")
            if target is None:
                continue
            operations.append(
                Operation(
                    type="append_note",
                    layer_id=target.layer_id,
                    note_id=target.note_id,
                    content=self._context_block(addition, sources, execution_id, source_urls),
                    rationale=f"Add missing context to “{target.title}”",
                )
            )

        # 4. Tags and the processed stamp on the captures themselves.
        clean_tags = [
            tag.strip().lstrip("#").lower()[:120]
            for tag in analysis.tags
            if tag.strip().lstrip("#")
        ][:MAX_TAGS]
        for note in sources:
            for tag in clean_tags:
                if tag not in note.metadata.tags:
                    operations.append(
                        Operation(
                            type="add_tag",
                            layer_id=note.metadata.layer_id,
                            note_id=note.metadata.id,
                            tag=tag,
                            rationale="Topic tag from the research analysis",
                        )
                    )

        if operations:
            for note in sources:
                operations.append(
                    Operation(
                        type="set_property",
                        layer_id=note.metadata.layer_id,
                        note_id=note.metadata.id,
                        property_key="processing_status",
                        property_value="processed",
                        rationale="Mark the research capture as filed",
                    )
                )

        if not candidates:
            warnings.append(
                "No existing node in the selected layers was related, so this material "
                "starts its own."
            )
        if analysis.claims_to_verify:
            warnings.append("Needs verification: " + " · ".join(analysis.claims_to_verify[:5]))
        if analysis.open_questions:
            warnings.append("Open questions: " + " · ".join(analysis.open_questions[:5]))

        plan = OperationPlan(
            id=new_job_id(),
            summary=(analysis.summary or "File the research material into the graph.")[:400],
            operations=operations,
            created_at=_now(),
            provider=provider,
            model=model,
            prompt="Analyse and file research",
        )
        logger.info(
            "research.proposed",
            operations=len(operations),
            candidates=len(candidates),
            matches=len(matches),
            subnodes=len(analysis.subnodes[:MAX_SUBNODES]),
        )
        return ResearchProposal(
            source_note_ids=[note.metadata.id for note in sources],
            analysis=analysis,
            candidates=candidates,
            plan=plan,
            warnings=warnings,
        )

    @staticmethod
    def _context_block(
        addition: ContextAddition,
        sources: list[Note],
        execution_id: str,
        source_urls: list[str],
    ) -> str:
        heading = addition.heading.strip() or "Added context"
        origin = ", ".join(f"[[{note.metadata.title}]]" for note in sources[:3])
        citation = f" — {source_urls[0]}" if source_urls else ""
        return (
            f"## {heading}\n\n"
            f"{addition.content.strip()}\n\n"
            f"_Added by Strata research from {origin}{citation} · "
            f"ai-inferred, unverified · {execution_id}_"
        )
