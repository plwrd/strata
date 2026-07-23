"""The transactional AI change engine over the WebChannel.

The flow the frontend drives: generate a plan (a job, because it calls a model),
review it (validation + diff), then apply the approved subset in a transaction, and
undo if wanted. The bridge never applies anything the review did not mark valid, and
the *service* re-validates at apply time — a frontend that lies about what was
approved still cannot apply an invalid operation.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Signal, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.errors import InvalidRequestError
from app.domain.ids import new_job_id
from app.domain.operations import AppliedPlan, OperationPlan, PlanReview
from app.services.ai_generation_service import MAX_GENERATED_NOTES
from app.services.container import Services
from app.services.synthesis_service import SynthesisKind


class GeneratePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=8000)
    object_ids: list[str] = Field(default_factory=list, max_length=2000)
    layer_ids: list[str] = Field(min_length=1, max_length=200)
    confirmed_remote: bool = False
    # "plan" reorganises; "notes" writes new Markdown notes from the selection.
    mode: Literal["plan", "notes"] = "plan"
    # How many notes to generate in "notes" mode. 0 lets the model decide.
    note_count: int = Field(default=0, ge=0, le=MAX_GENERATED_NOTES)


class GenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str


class ProcessNotesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    note_ids: list[str] = Field(min_length=1, max_length=25)
    confirmed_remote: bool = False


class ProcessNotesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str


class SynthesizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    note_ids: list[str] = Field(min_length=2, max_length=25)
    kind: SynthesisKind = "summary"
    confirmed_remote: bool = False


class ReviewPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: OperationPlan
    allowed_layer_ids: list[str] = Field(min_length=1, max_length=200)


class ReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review: PlanReview


class ApplyPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: OperationPlan
    approved_indexes: list[int] = Field(default_factory=list, max_length=500)
    allowed_layer_ids: list[str] = Field(min_length=1, max_length=200)


class ApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applied: AppliedPlan


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=128)


class AuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[AppliedPlan] = Field(default_factory=list)


class OperationsBridge(QObject):
    """Plan generation streams a completion signal; review/apply/undo are synchronous.

    ``planEvent`` fires ``{requestId, kind, plan?, error?}`` when a generation job
    finishes, because generating a plan calls a model and must not block the UI.
    """

    planEvent = Signal(str)

    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)
    @bridge_method(GeneratePlanRequest)
    def generate_plan(self, request: GeneratePlanRequest) -> GenerateResponse:
        context, layer_ids = self._build_context(request)

        request_id = new_job_id()
        thread = threading.Thread(
            target=self._generate,
            args=(request_id, request, context, layer_ids),
            daemon=True,
        )
        thread.start()
        return GenerateResponse(request_id=request_id)

    def _build_context(self, request: GeneratePlanRequest) -> tuple[str, list[str]]:
        """The model's context, and the layer ids the policy gate must cover.

        Built from the selected objects exactly as an export would be, so the model
        sees what the user chose. Plan mode shares only titles; notes mode shares
        the selected notes' full content (inside the same neutralised source
        boundaries as an export), so the gate set widens to include the source
        layers — content must never leave through a layer the gate did not see.
        """
        if not request.object_ids:
            return "", list(request.layer_ids)

        plan = self._services.exports.plan(object_ids=request.object_ids, target="claude")
        listing = "Existing notes you may reference:\n\n" + "\n".join(
            f"- id={source.object_id} layer={source.layer_id} title={source.title!r}"
            for source in plan.sources
        )
        if request.mode != "notes":
            return listing, list(request.layer_ids)

        blocks = "\n\n".join(
            self._services.exports.render_source_block(source) for source in plan.sources
        )
        context = listing + "\n\nSource notes to generate from:\n\n" + blocks
        layer_ids = sorted(set(request.layer_ids) | {source.layer_id for source in plan.sources})
        return context, layer_ids

    def _generate(
        self,
        request_id: str,
        request: GeneratePlanRequest,
        context: str,
        layer_ids: list[str],
    ) -> None:
        try:
            plan = self._services.ai_generation.generate_plan_sync(
                provider_id=request.provider_id,
                model=request.model,
                prompt=request.prompt,
                context=context,
                layer_ids=layer_ids,
                confirmed_remote=request.confirmed_remote,
                mode=request.mode,
                note_count=request.note_count,
            )
            self._emit(request_id, {"kind": "plan", "plan": plan.model_dump()})
        except Exception as exc:
            from app.domain.errors import StrataError

            message = exc.message if isinstance(exc, StrataError) else "Plan generation failed."
            self._emit(request_id, {"kind": "error", "error": message})

    def _emit(self, request_id: str, payload: dict[str, Any]) -> None:
        self.planEvent.emit(json.dumps({"requestId": request_id, **payload}))

    # -- process into knowledge ----------------------------------------------

    @Slot(str, result=str)
    @bridge_method(ProcessNotesRequest)
    def process_notes(self, request: ProcessNotesRequest) -> ProcessNotesResponse:
        """Extract structured knowledge from the selected notes, as a plan.

        Runs as a background job (it calls a model), reports progress on the
        jobs channel, and delivers the finished plan on ``planEvent`` under the
        job's id — from there it enters the normal review → apply flow.
        """
        notes = self._services.notes.get_notes(request.note_ids)
        if not notes:
            raise InvalidRequestError("None of the selected notes are readable.")
        private_layers = {
            layer.id
            for layer in self._services.workspace.descriptor.layers
            if layer.visibility == "private"
        }
        involves_private = any(note.metadata.layer_id in private_layers for note in notes)

        def work(handle: Any) -> dict[str, Any]:
            handle.progress(0.1, f"Processing {len(notes)} note(s)")
            try:
                proposal = self._services.knowledge.process_sync(
                    note_ids=request.note_ids,
                    provider_id=request.provider_id,
                    model=request.model,
                    confirmed_remote=request.confirmed_remote,
                )
            except Exception as exc:
                from app.domain.errors import StrataError

                message = exc.message if isinstance(exc, StrataError) else "Processing failed."
                self._emit(handle.id, {"kind": "error", "error": message})
                raise
            handle.progress(0.9, "Proposal ready for review")
            self._emit(
                handle.id,
                {
                    "kind": "plan",
                    "plan": proposal.plan.model_dump(),
                    "warnings": proposal.warnings,
                },
            )
            return {"operations": len(proposal.plan.operations)}

        record = self._services.jobs.submit(
            job_type="ai_request",
            title=f"Process {len(notes)} note(s) into knowledge",
            work=work,
            privacy="private" if involves_private else "public",
        )
        return ProcessNotesResponse(request_id=record.id)

    # -- multi-source synthesis ----------------------------------------------

    @Slot(str, result=str)
    @bridge_method(SynthesizeRequest)
    def synthesize_notes(self, request: SynthesizeRequest) -> ProcessNotesResponse:
        """Combine the selected notes into one cited document — as a plan.

        Source notes are never modified. The synthesis arrives on ``planEvent``
        under the job's id and enters the normal review → apply flow; citations
        of sources that were never sent are stripped and reported.
        """
        notes = self._services.notes.get_notes(request.note_ids)
        if len(notes) < 2:
            raise InvalidRequestError("Select at least two readable notes to synthesise.")
        private_layers = {
            layer.id
            for layer in self._services.workspace.descriptor.layers
            if layer.visibility == "private"
        }
        involves_private = any(note.metadata.layer_id in private_layers for note in notes)

        def work(handle: Any) -> dict[str, Any]:
            handle.progress(0.1, f"Synthesising {len(notes)} note(s)")
            try:
                proposal = self._services.synthesis.synthesize_sync(
                    note_ids=request.note_ids,
                    kind=request.kind,
                    provider_id=request.provider_id,
                    model=request.model,
                    confirmed_remote=request.confirmed_remote,
                )
            except Exception as exc:
                from app.domain.errors import StrataError

                message = exc.message if isinstance(exc, StrataError) else "Synthesis failed."
                self._emit(handle.id, {"kind": "error", "error": message})
                raise
            handle.progress(0.9, "Synthesis ready for review")
            self._emit(
                handle.id,
                {
                    "kind": "plan",
                    "plan": proposal.plan.model_dump(),
                    "warnings": proposal.warnings,
                },
            )
            return {"operations": len(proposal.plan.operations)}

        record = self._services.jobs.submit(
            job_type="ai_request",
            title=f"Synthesise {len(notes)} note(s) ({request.kind})",
            work=work,
            privacy="private" if involves_private else "public",
        )
        return ProcessNotesResponse(request_id=record.id)

    @Slot(str, result=str)
    @bridge_method(ReviewPlanRequest)
    def review_plan(self, request: ReviewPlanRequest) -> ReviewResponse:
        review = self._services.operations.review(
            request.plan, allowed_layer_ids=request.allowed_layer_ids
        )
        return ReviewResponse(review=review)

    @Slot(str, result=str)
    @bridge_method(ApplyPlanRequest)
    def apply_plan(self, request: ApplyPlanRequest) -> ApplyResponse:
        if not request.approved_indexes:
            raise InvalidRequestError("Approve at least one operation.")
        review = self._services.operations.review(
            request.plan, allowed_layer_ids=request.allowed_layer_ids
        )
        applied = self._services.operations.apply(
            review,
            approved_indexes=request.approved_indexes,
            allowed_layer_ids=request.allowed_layer_ids,
        )
        return ApplyResponse(applied=applied)

    @Slot(str, result=str)
    @bridge_method(UndoRequest)
    def undo_plan(self, request: UndoRequest) -> ApplyResponse:
        return ApplyResponse(applied=self._services.operations.undo(request.plan_id))

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def audit_log(self, _request: EmptyRequest) -> AuditResponse:
        return AuditResponse(entries=self._services.operations.audit_log())
