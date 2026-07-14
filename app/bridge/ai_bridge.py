"""AI Context Composer.

Milestone 1 delivers the half of the composer that does not need a model: build a
context plan from the current selection, show exactly what would be sent, and
estimate the cost. ``send_request`` deliberately refuses until Milestone 7 rather
than pretending to talk to a provider.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.errors import UnsupportedError
from app.domain.export import ContentMode, ContextDepth, ContextPlan, ExportShape, ExportTarget
from app.services.container import Services


class ProviderCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    display_name: str
    is_local: bool
    configured: bool = False
    streaming: bool = True
    structured_output: bool = True
    embeddings: bool = False
    vision: bool = False
    max_context_tokens: int = 0
    note: str = ""


class ProviderListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderCapability] = Field(default_factory=list)
    any_configured: bool = False


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


class SendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    object_ids: list[str] = Field(min_length=1, max_length=2000)
    prompt: str = Field(min_length=1, max_length=32_000)
    confirmed_remote: bool = False


# The provider catalogue the UI renders. `configured=False` everywhere in
# Milestone 1: the adapters land in Milestone 7, and the UI shows them disabled
# with the reason rather than offering a button that does nothing.
PROVIDER_CATALOGUE: list[ProviderCapability] = [
    ProviderCapability(
        provider_id="ollama",
        display_name="Ollama",
        is_local=True,
        embeddings=True,
        max_context_tokens=32_768,
        note="Local. Arrives in Milestone 7.",
    ),
    ProviderCapability(
        provider_id="llamacpp",
        display_name="llama.cpp server",
        is_local=True,
        embeddings=True,
        max_context_tokens=32_768,
        note="Local. Arrives in Milestone 7.",
    ),
    ProviderCapability(
        provider_id="lmstudio",
        display_name="LM Studio",
        is_local=True,
        embeddings=True,
        max_context_tokens=32_768,
        note="Local. Arrives in Milestone 7.",
    ),
    ProviderCapability(
        provider_id="openai",
        display_name="OpenAI",
        is_local=False,
        embeddings=True,
        vision=True,
        max_context_tokens=400_000,
        note="Remote. Arrives in Milestone 7.",
    ),
    ProviderCapability(
        provider_id="anthropic",
        display_name="Anthropic",
        is_local=False,
        vision=True,
        max_context_tokens=200_000,
        note="Remote. Arrives in Milestone 7.",
    ),
    ProviderCapability(
        provider_id="claude-cli",
        display_name="Claude CLI",
        is_local=False,
        max_context_tokens=200_000,
        note="Runs locally but sends data to Anthropic. Arrives in Milestone 7.",
    ),
]


class AIComposerBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def list_providers(self, _request: EmptyRequest) -> ProviderListResponse:
        return ProviderListResponse(
            providers=PROVIDER_CATALOGUE,
            any_configured=any(provider.configured for provider in PROVIDER_CATALOGUE),
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(PlanContextRequest)
    def plan_context(self, request: PlanContextRequest) -> PlanContextResponse:
        """What *would* be sent. Computing this never sends anything."""
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

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(SendRequest)
    def send_request(self, _request: SendRequest) -> PlanContextResponse:
        raise UnsupportedError(
            "No AI provider is configured yet. Provider adapters arrive in Milestone 7. "
            "Export the context package instead.",
            details={"milestone": 7},
        )
