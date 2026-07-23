"""The AI Context Composer, and live model requests.

The request path is: plan → policy → confirm → send → stream → receipt. Each step is
a separate call, and the policy step is not advisory: Python refuses the send if the
layers involved do not permit it, whatever the UI displayed.

Streaming crosses the bridge as a Qt Signal, because a slot returns once and a
stream does not. ``aiEvent`` carries the deltas; the request is cancellable.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Signal, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.ai import AIMessage, Capability, ProviderCapabilities
from app.domain.errors import InvalidRequestError, PermissionDeniedError
from app.domain.export import (
    ContentMode,
    ContextDepth,
    ContextPlan,
    ExportShape,
    ExportTarget,
    PrivacyReceipt,
)
from app.domain.history import AIExecutionRecord
from app.domain.ids import new_execution_id, new_job_id, new_object_id
from app.domain.operations import Operation, OperationPlan
from app.domain.prompts import PromptCategory, SavedPrompt
from app.services.ai_service import CATALOGUE
from app.services.container import Services


class ProviderView(BaseModel):
    """A provider as the UI sees it: what it is, and what it can actually do."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    display_name: str
    is_local: bool
    configured: bool
    requires_api_key: bool
    capabilities: list[str] = Field(default_factory=list)
    max_context_tokens: int
    note: str


class ProviderListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderView] = Field(default_factory=list)
    any_configured: bool = False
    keychain_available: bool = True


class ProviderIdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    reachable: bool
    configured: bool
    detail: str = ""
    models: list[dict[str, Any]] = Field(default_factory=list)


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    # Goes straight to the OS keychain. Never stored here, never echoed, never logged.
    api_key: str = Field(min_length=1, max_length=512)


class CredentialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stored: bool
    keychain_available: bool
    detail: str = ""


class PlanContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(min_length=1, max_length=2000)
    prompt: str = Field(default="", max_length=32_000)
    target: ExportTarget = "generic"
    shape: ExportShape = "single-file"
    depth: ContextDepth = "selected-only"
    content_mode: ContentMode = "full"
    token_budget: int | None = Field(default=None, ge=500, le=2_000_000)


class PlanContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: ContextPlan


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(default_factory=list, max_length=2000)
    provider_id: str = Field(min_length=1, max_length=64)


class PolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str
    reason: str
    blocking_layers: list[str] = Field(default_factory=list)
    is_remote: bool
    private_object_count: int = 0
    object_count: int = 0


class SendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    object_ids: list[str] = Field(default_factory=list, max_length=2000)
    prompt: str = Field(min_length=1, max_length=32_000)
    depth: ContextDepth = "selected-only"
    content_mode: ContentMode = "full"
    max_output_tokens: int = Field(default=2048, ge=1, le=32_000)
    # Set only by the privacy-review dialog, after the user has seen exactly what
    # would leave the machine.
    confirmed_remote: bool = False
    # "Ask the workspace": when nothing is selected, retrieval ranks a small set
    # of notes for context instead of sending nothing (never the whole workspace).
    retrieve: bool = False
    retrieve_limit: int = Field(default=8, ge=1, le=25)
    # Continue a persisted thread. Prior non-redacted turns replay from Python's
    # own store; an empty id starts a new conversation.
    conversation_id: str = Field(default="", max_length=64)


class UsedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    title: str
    is_private: bool = False


class SendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    execution_id: str = ""
    conversation_id: str = ""
    # Exactly which notes the model will see — shown, not implied.
    sources: list[UsedSource] = Field(default_factory=list)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)


class CancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cancelled: bool


class ReceiptsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipts: list[PrivacyReceipt] = Field(default_factory=list)


class HistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=100, ge=1, le=500)


class HistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executions: list[AIExecutionRecord] = Field(default_factory=list)


class ClearHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cleared_files: int = 0


class SaveOutputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str = Field(default="", max_length=64)
    # The content as the user sees it — including any edits they made to it.
    content: str = Field(min_length=1, max_length=512_000)
    title: str = Field(min_length=1, max_length=200)
    target: Literal["note", "report", "append"] = "note"
    layer_id: str = Field(default="", max_length=128)
    note_id: str = Field(default="", max_length=128)


class SaveOutputResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str
    title: str
    plan_id: str


class SavePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: str = Field(default="", max_length=64)
    name: str = Field(min_length=1, max_length=120)
    prompt_text: str = Field(min_length=1, max_length=32_000)
    description: str = Field(default="", max_length=1000)
    category: PromptCategory = "other"
    model_preference: str = Field(default="", max_length=128)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class PromptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: SavedPrompt


class PromptListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompts: list[SavedPrompt] = Field(default_factory=list)


class PromptIdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_id: str = Field(min_length=1, max_length=64)


class PromptDeletedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: bool = True


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_ids: list[str] = Field(default_factory=list, max_length=2000)
    required_tokens: int = Field(default=0, ge=0, le=2_000_000)


class RouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str | None = None
    reason: str


def _view(capabilities: ProviderCapabilities, configured: bool) -> ProviderView:
    return ProviderView(
        provider_id=capabilities.provider_id,
        display_name=capabilities.display_name,
        is_local=capabilities.is_local,
        configured=configured,
        requires_api_key=capabilities.requires_api_key,
        capabilities=[capability.value for capability in capabilities.capabilities],
        max_context_tokens=capabilities.max_context_tokens,
        note=capabilities.note,
    )


class AIComposerBridge(QObject):
    """Context planning, provider management, and live requests.

    ``aiEvent`` streams ``{requestId, kind, text, …}`` to the frontend. It is the
    only push channel here, and it carries model output — never source content, and
    never a credential.
    """

    aiEvent = Signal(str)

    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._cancels: dict[str, asyncio.Event] = {}
        self._loops: dict[str, asyncio.AbstractEventLoop] = {}

    # -- providers -----------------------------------------------------------

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def list_providers(self, _request: EmptyRequest) -> ProviderListResponse:
        ai = self._services.ai
        views = [
            _view(capabilities, ai.is_configured(capabilities.provider_id))
            for capabilities in ai.catalogue()
        ]
        return ProviderListResponse(
            providers=views,
            any_configured=any(view.configured for view in views),
            keychain_available=ai.credentials.is_available(),
        )

    @Slot(str, result=str)
    @bridge_method(ProviderIdRequest)
    def check_health(self, request: ProviderIdRequest) -> HealthResponse:
        health = _run_async(self._services.ai.health(request.provider_id))
        return HealthResponse(
            provider_id=health.provider_id,
            reachable=health.reachable,
            configured=health.configured,
            detail=health.detail,
            models=[model.model_dump() for model in health.models],
        )

    @Slot(str, result=str)
    @bridge_method(CredentialRequest)
    def store_credential(self, request: CredentialRequest) -> CredentialResponse:
        store = self._services.ai.credentials
        if not store.is_available():
            # Fail closed. Never fall back to a file: silently downgrading from
            # "encrypted by the OS" to "plaintext on disk" is how keys get stolen.
            return CredentialResponse(
                stored=False,
                keychain_available=False,
                detail=(
                    "This system has no usable keychain, so Strata will not store the key. "
                    "It refuses to write it to a file instead."
                ),
            )
        stored = store.set(request.provider_id, request.api_key)
        return CredentialResponse(
            stored=stored,
            keychain_available=True,
            detail="Stored in the system keychain." if stored else "The keychain refused it.",
        )

    @Slot(str, result=str)
    @bridge_method(ProviderIdRequest)
    def delete_credential(self, request: ProviderIdRequest) -> CredentialResponse:
        deleted = self._services.ai.credentials.delete(request.provider_id)
        return CredentialResponse(
            stored=False,
            keychain_available=True,
            detail="Removed." if deleted else "There was nothing to remove.",
        )

    # -- planning and policy -------------------------------------------------

    @Slot(str, result=str)
    @bridge_method(PlanContextRequest)
    def plan_context(self, request: PlanContextRequest) -> PlanContextResponse:
        """What *would* be sent. Computing this sends nothing."""
        plan = self._services.exports.plan(
            object_ids=request.object_ids,
            prompt=request.prompt,
            target=request.target,
            shape=request.shape,
            depth=request.depth,
            content_mode=request.content_mode,
            token_budget=request.token_budget,
        )
        return PlanContextResponse(plan=plan)

    @Slot(str, result=str)
    @bridge_method(PolicyRequest)
    def check_policy(self, request: PolicyRequest) -> PolicyResponse:
        """Would this be allowed? The UI asks before it offers a Send button.

        Advisory *to the UI* only. The authoritative check runs again inside
        `send_request`, so a frontend that ignores this one gains nothing.
        """
        plan = (
            self._services.exports.plan(object_ids=request.object_ids)
            if request.object_ids
            else None
        )
        layer_ids = sorted({source.layer_id for source in plan.sources}) if plan else []
        decision = self._services.ai.policy_for(layer_ids, request.provider_id)

        return PolicyResponse(
            verdict=decision.verdict,
            reason=decision.reason,
            blocking_layers=decision.blocking_layers,
            is_remote=decision.remote,
            private_object_count=plan.private_source_count if plan else 0,
            object_count=len(plan.sources) if plan else 0,
        )

    @Slot(str, result=str)
    @bridge_method(RouteRequest)
    def route(self, request: RouteRequest) -> RouteResponse:
        plan = (
            self._services.exports.plan(object_ids=request.object_ids)
            if request.object_ids
            else None
        )
        layer_ids = sorted({source.layer_id for source in plan.sources}) if plan else []
        provider_id, reason = self._services.ai.route(
            layer_ids,
            prefer_local=self._services.settings.settings.prefer_local_ai,
            required_tokens=request.required_tokens,
        )
        return RouteResponse(provider_id=provider_id, reason=reason)

    # -- sending -------------------------------------------------------------

    @Slot(str, result=str)
    @bridge_method(SendRequest)
    def send_request(self, request: SendRequest) -> SendResponse:
        """Start a streaming request. Events arrive on ``aiEvent``.

        The policy gate runs again inside `AIService.run`, before a provider is even
        constructed — so a caller that skipped `check_policy` gains nothing by it.
        """
        capabilities = CATALOGUE.get(request.provider_id)
        if capabilities is None:
            raise InvalidRequestError("Unknown provider.")
        if not capabilities.supports(Capability.STREAMING):
            raise InvalidRequestError(f"{capabilities.display_name} cannot stream.")

        object_ids = list(request.object_ids)
        if not object_ids and request.retrieve:
            # "Ask the workspace": retrieval selects a small ranked context.
            # The ids then flow through the same plan → policy → render path as
            # a manual selection — retrieval widens nothing.
            object_ids = self._services.retrieval.retrieve(
                request.prompt, limit=request.retrieve_limit
            )

        plan = (
            self._services.exports.plan(
                object_ids=object_ids,
                prompt=request.prompt,
                depth=request.depth,
                content_mode=request.content_mode,
                target="claude",
            )
            if object_ids
            else None
        )

        sources = ""
        layer_ids: list[str] = []
        if plan is not None:
            # The same rendering as an export: delimiters inside note content are
            # neutralised, so a note containing "</source>" cannot break out of the
            # untrusted-data section of the request.
            sources = "\n\n".join(
                self._services.exports.render_source_block(source) for source in plan.sources
            )
            layer_ids = sorted({source.layer_id for source in plan.sources})

        # Fail fast, on the calling thread, for the cases the user must see straight
        # away: a denial is not something to discover halfway through a stream.
        decision = self._services.ai.policy_for(layer_ids, request.provider_id)
        if decision.verdict == "denied":
            raise PermissionDeniedError(
                decision.reason, details={"layers": decision.blocking_layers}
            )
        if decision.verdict == "needs_confirmation" and not request.confirmed_remote:
            raise PermissionDeniedError(
                decision.reason,
                details={"layers": decision.blocking_layers, "needsConfirmation": True},
            )

        request_id = new_job_id()
        execution_id = new_execution_id()
        conversation_id = request.conversation_id or f"conv_{new_object_id()[:16]}"
        cancel = asyncio.Event()
        self._cancels[request_id] = cancel

        thread = threading.Thread(
            target=self._pump,
            args=(request_id, execution_id, conversation_id, request, sources, layer_ids, plan),
            kwargs={"cancel": cancel},
            daemon=True,
        )
        thread.start()

        return SendResponse(
            request_id=request_id,
            execution_id=execution_id,
            conversation_id=conversation_id,
            sources=(
                [
                    UsedSource(
                        object_id=source.object_id,
                        title="(private)" if source.is_private else source.title,
                        is_private=source.is_private,
                    )
                    for source in plan.sources
                ]
                if plan
                else []
            ),
        )

    def _pump(
        self,
        request_id: str,
        execution_id: str,
        conversation_id: str,
        request: SendRequest,
        sources: str,
        layer_ids: list[str],
        plan: ContextPlan | None,
        *,
        cancel: asyncio.Event,
    ) -> None:
        """Run the async stream on its own loop and forward events to Qt.

        On a thread, not the Qt event loop: inference can take minutes, and the
        editor must stay responsive. ``Signal.emit`` is thread-safe.
        """
        loop = asyncio.new_event_loop()
        self._loops[request_id] = loop
        asyncio.set_event_loop(loop)

        # Prior turns come from Python's own store; redacted turns are dropped
        # before they can re-enter a model context.
        conversation_messages = [
            message
            for turn in self._services.conversations.replayable_turns(conversation_id)
            for message in (
                AIMessage(role="user", content=turn.prompt),
                AIMessage(role="assistant", content=turn.response_text),
            )
        ]

        chunks: list[str] = []
        completed = False

        async def consume() -> None:
            nonlocal completed
            try:
                async for event in self._services.ai.run(
                    provider_id=request.provider_id,
                    model=request.model,
                    prompt=request.prompt,
                    sources=sources,
                    layer_ids=layer_ids,
                    object_count=len(plan.sources) if plan else 0,
                    private_object_count=plan.private_source_count if plan else 0,
                    confirmed_remote=request.confirmed_remote,
                    max_output_tokens=request.max_output_tokens,
                    cancel=cancel,
                    source_object_ids=(
                        [source.object_id for source in plan.sources] if plan else []
                    ),
                    execution_id=execution_id,
                    conversation_messages=conversation_messages,
                ):
                    if event.kind == "delta":
                        chunks.append(event.text)
                    elif event.kind == "done":
                        completed = True
                    self._emit(request_id, event.model_dump())
            except PermissionDeniedError as exc:
                self._emit(request_id, {"kind": "error", "error": exc.message})
            except Exception:
                self._emit(
                    request_id,
                    {"kind": "error", "error": "The request failed. See the local log."},
                )

        try:
            loop.run_until_complete(consume())
            if completed and not cancel.is_set():
                private_layers = {
                    layer.id
                    for layer in self._services.workspace.descriptor.layers
                    if layer.visibility == "private"
                }
                self._services.conversations.append_turn(
                    conversation_id,
                    execution_id=execution_id,
                    prompt=request.prompt,
                    response_text="".join(chunks),
                    provider=request.provider_id,
                    model=request.model,
                    involves_private=bool(plan and plan.private_source_count)
                    or any(layer_id in private_layers for layer_id in layer_ids),
                )
        finally:
            loop.close()
            self._cancels.pop(request_id, None)
            self._loops.pop(request_id, None)

    def _emit(self, request_id: str, payload: dict[str, Any]) -> None:
        self.aiEvent.emit(json.dumps({"requestId": request_id, **payload}))

    @Slot(str, result=str)
    @bridge_method(CancelRequest)
    def cancel_request(self, request: CancelRequest) -> CancelResponse:
        cancel = self._cancels.get(request.request_id)
        loop = self._loops.get(request.request_id)
        if cancel is None or loop is None:
            return CancelResponse(cancelled=False)
        # The event belongs to the worker's loop, so set it from that loop's thread.
        loop.call_soon_threadsafe(cancel.set)
        return CancelResponse(cancelled=True)

    # -- receipts ------------------------------------------------------------

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def privacy_receipts(self, _request: EmptyRequest) -> ReceiptsResponse:
        """What has left this machine. Records the *fact* of the content, not the
        content: a receipt that quoted the note would itself be a leak."""
        return ReceiptsResponse(receipts=self._services.ai.receipts())

    # -- history -------------------------------------------------------------

    @Slot(str, result=str)
    @bridge_method(HistoryRequest)
    def list_history(self, request: HistoryRequest) -> HistoryResponse:
        """The workspace's persisted AI activity, newest first.

        Records that involved a private layer come back redacted — metadata only —
        because that is all that ever reached disk (docs/ai-memory-design.md §3).
        """
        return HistoryResponse(executions=self._services.ai.executions(request.limit))

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def clear_history(self, _request: EmptyRequest) -> ClearHistoryResponse:
        """Delete the persisted AI history and conversations. User-initiated,
        and only ever history: this cannot touch layer content."""
        cleared = self._services.ai.clear_history() + self._services.conversations.clear()
        return ClearHistoryResponse(cleared_files=cleared)

    # -- save AI output as a permanent asset ---------------------------------

    @Slot(str, result=str)
    @bridge_method(SaveOutputRequest)
    def save_output(self, request: SaveOutputRequest) -> SaveOutputResponse:
        """Turn a response the user just read into a note, report, or append.

        Goes through the operation engine — one-operation plan, reviewed and
        applied — so the save is snapshot-backed, audited, undoable, and stamped
        with provenance. The user's click on content they are looking at is the
        approval; the diff they would review *is* the visible response.
        """
        services = self._services
        execution = (
            services.history.get_execution(request.execution_id) if request.execution_id else None
        )

        derived = ""
        if execution is not None and not execution.redacted and execution.source_object_ids:
            titles = [
                note.metadata.title
                for note in services.notes.get_notes(execution.source_object_ids)
            ]
            if titles:
                derived = "\n\n" + "\n".join(f"derived_from:: [[{title}]]" for title in titles)

        if request.target == "append":
            if not request.note_id:
                raise InvalidRequestError("Choose a note to append to.")
            target_note = services.notes.get_note(request.note_id)
            operation = Operation(
                type="append_note",
                layer_id=target_note.metadata.layer_id,
                note_id=request.note_id,
                content=request.content,
                rationale="Append the AI response the user chose to keep",
            )
        else:
            layer_id = request.layer_id or self._first_public_layer()
            folder = "Reports" if request.target == "report" else "Knowledge"
            properties: dict[str, str] = {"review_status": "ai-inferred"}
            if request.target == "report":
                properties["type"] = "report"
            if execution is not None:
                properties["generated_by"] = execution.id
                if execution.model:
                    properties["model"] = execution.model
            operation = Operation(
                type="create_note",
                layer_id=layer_id,
                folder_path=folder,
                title=self._unique_title(request.title),
                content=request.content + derived + "\n",
                properties=properties,
                rationale="Save the AI response the user chose to keep",
            )

        plan = OperationPlan(
            id=new_job_id(),
            summary=f"Save AI output: {request.title}"[:400],
            operations=[operation],
            provider=execution.provider if execution else "",
            model=execution.model if execution else "",
            prompt=(execution.prompt if execution and not execution.redacted else ""),
        )
        review = services.operations.review(plan, allowed_layer_ids=[operation.layer_id])
        if review.valid_count != 1:
            problem = next((entry.problem for entry in review.entries if entry.problem), "")
            raise InvalidRequestError(problem or "The output could not be saved.")
        applied = services.operations.apply(
            review, approved_indexes=[0], allowed_layer_ids=[operation.layer_id]
        )
        services.watcher.announce("strata")
        return SaveOutputResponse(
            note_id=applied.results[0].note_id or "",
            title=operation.title or request.title,
            plan_id=applied.plan_id,
        )

    def _first_public_layer(self) -> str:
        layer_id = next(
            (
                layer.id
                for layer in self._services.workspace.readable_layers()
                if layer.storage == "markdown"
            ),
            None,
        )
        if layer_id is None:
            raise InvalidRequestError("No writable public layer is available.")
        return layer_id

    def _unique_title(self, title: str) -> str:
        existing = {
            note.metadata.title.strip().lower() for note in self._services.notes.list_notes()
        }
        if title.strip().lower() not in existing:
            return title
        counter = 2
        while f"{title} {counter}".strip().lower() in existing:
            counter += 1
        return f"{title} {counter}"

    # -- prompt library -------------------------------------------------------

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def list_prompts(self, _request: EmptyRequest) -> PromptListResponse:
        return PromptListResponse(prompts=self._services.prompts.list_prompts())

    @Slot(str, result=str)
    @bridge_method(SavePromptRequest)
    def save_prompt(self, request: SavePromptRequest) -> PromptResponse:
        prompt = self._services.prompts.save(
            prompt_id=request.prompt_id,
            name=request.name,
            prompt_text=request.prompt_text,
            description=request.description,
            category=request.category,
            model_preference=request.model_preference,
            temperature=request.temperature,
        )
        return PromptResponse(prompt=prompt)

    @Slot(str, result=str)
    @bridge_method(PromptIdRequest)
    def use_prompt(self, request: PromptIdRequest) -> PromptResponse:
        """Fetch a prompt to run, counting the use."""
        return PromptResponse(prompt=self._services.prompts.record_use(request.prompt_id))

    @Slot(str, result=str)
    @bridge_method(PromptIdRequest)
    def delete_prompt(self, request: PromptIdRequest) -> PromptDeletedResponse:
        self._services.prompts.delete(request.prompt_id)
        return PromptDeletedResponse(deleted=True)


def _run_async(coroutine: Any) -> Any:
    """Run a coroutine to completion from the calling thread.

    Used only for short calls (a health check). Anything long is streamed instead —
    blocking the UI thread on inference would freeze the editor, which is the one
    thing this product's performance rules do not permit.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()
