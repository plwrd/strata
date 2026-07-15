"""AI domain model: providers, capabilities, requests, and the policy gate.

The two ideas that shape this module:

**Capabilities are declared, not assumed.** Not every provider streams, does
structured output, sees images, or embeds. The UI disables what a provider cannot
do rather than offering it and failing at request time.

**The policy gate is in the domain, not the UI.** Whether a given request may go to
a given provider is decided by :func:`evaluate_policy`, which takes the layers
involved and the provider, and returns a verdict. The UI renders that verdict; it
does not compute it. A button that is merely hidden is not a security control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.layer import LayerDescriptor

ProviderId = Literal[
    "ollama",
    "llamacpp",
    "lmstudio",
    "openai",
    "anthropic",
    "openai-compatible",
    "claude-cli",
]


class Capability(str, Enum):
    TEXT = "text"
    STREAMING = "streaming"
    STRUCTURED_OUTPUT = "structured_output"
    TOOLS = "tools"
    VISION = "vision"
    PDF = "pdf"
    EMBEDDINGS = "embeddings"
    LARGE_CONTEXT = "large_context"


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    display_name: str
    # `is_local` means "the bytes do not leave this machine". The Claude CLI runs
    # locally and is NOT local by this definition, because it sends the request to
    # Anthropic. Getting this wrong would be the most dangerous kind of bug in the
    # product: a user choosing it *because* they think it is private.
    is_local: bool
    requires_api_key: bool = False
    capabilities: list[Capability] = Field(default_factory=list)
    max_context_tokens: int = 8192
    note: str = ""

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str = ""
    context_tokens: int = 0
    is_local: bool = False


class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    reachable: bool
    configured: bool
    detail: str = ""
    models: list[ModelInfo] = Field(default_factory=list)


class TokenEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int
    max_output_tokens: int
    context_tokens: int
    fits: bool


class AIMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class AIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    model: str
    messages: list[AIMessage]
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=2048, ge=1, le=32_000)
    # Set when the caller needs a machine-readable answer (an operation plan). The
    # provider adapter is responsible for asking for it in its own dialect, and the
    # *caller* is responsible for validating what comes back — a provider claiming
    # structured output is not the same as a provider producing valid output.
    json_schema: dict[str, object] | None = None
    stop: list[str] = Field(default_factory=list)


AIEventKind = Literal["start", "delta", "done", "error"]


class AIEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AIEventKind
    text: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    model: str
    texts: list[str] = Field(min_length=1, max_length=512)


class EmbeddingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    vectors: list[list[float]]


# -- the policy gate ---------------------------------------------------------


PolicyVerdict = Literal["allowed", "needs_confirmation", "denied"]


@dataclass(frozen=True)
class PolicyDecision:
    """Whether this request may go to this provider, and why.

    `reason` is written to be shown to a user verbatim. If the answer is no, the
    user is entitled to know exactly which layer said no and what its policy is —
    "AI is disabled" with no further detail is how people end up disabling security
    settings out of frustration.
    """

    verdict: PolicyVerdict
    reason: str
    blocking_layers: list[str] = field(default_factory=list)
    remote: bool = False

    @property
    def allowed(self) -> bool:
        return self.verdict != "denied"


def evaluate_policy(
    layers: list[LayerDescriptor],
    provider: ProviderCapabilities,
    *,
    for_embeddings: bool = False,
) -> PolicyDecision:
    """The single place that decides whether content may reach a provider.

    Rules, in order of severity:

    1. **A locked layer is never available to AI.** Not even to a local model. The
       app does not hold the key, so there is nothing to send — and if it somehow
       did, sending it would be worse.
    2. A layer with AI disabled blocks the request.
    3. A layer restricted to local AI blocks any remote provider, and the Claude CLI
       counts as remote.
    4. A layer that allows remote AI "with confirmation" makes the request need one.
    """
    if not layers:
        return PolicyDecision("allowed", "No layers are involved.", remote=not provider.is_local)

    locked = [layer.display_name for layer in layers if layer.is_locked]
    if locked:
        return PolicyDecision(
            "denied",
            "A locked layer can never be sent to a model. Unlock it first, or remove it "
            "from the selection.",
            blocking_layers=locked,
            remote=not provider.is_local,
        )

    remote = not provider.is_local

    disabled = [
        layer.display_name
        for layer in layers
        if (layer.ai_policy.embeddings if for_embeddings else layer.ai_policy.access) == "disabled"
    ]
    if disabled:
        return PolicyDecision(
            "denied",
            f"{'Embeddings are' if for_embeddings else 'AI is'} disabled for "
            f"{_names(disabled)}.",
            blocking_layers=disabled,
            remote=remote,
        )

    if not remote:
        return PolicyDecision("allowed", "This provider runs on your machine.", remote=False)

    if for_embeddings:
        blocking = [
            layer.display_name for layer in layers if layer.ai_policy.embeddings != "remote-allowed"
        ]
        if blocking:
            return PolicyDecision(
                "denied",
                f"{_names(blocking)} only allow local embeddings.",
                blocking_layers=blocking,
                remote=True,
            )
        return PolicyDecision("allowed", "Remote embeddings are permitted.", remote=True)

    local_only = [layer.display_name for layer in layers if layer.ai_policy.access == "local-only"]
    if local_only:
        return PolicyDecision(
            "denied",
            f"{_names(local_only)} may only be sent to a model running on this machine. "
            + (
                "The Claude CLI runs locally but sends your content to Anthropic, so it "
                "counts as remote."
                if provider.provider_id == "claude-cli"
                else ""
            ),
            blocking_layers=local_only,
            remote=True,
        )

    needs_confirmation = [
        layer.display_name
        for layer in layers
        if layer.ai_policy.access == "remote-with-confirmation"
    ]
    if needs_confirmation:
        return PolicyDecision(
            "needs_confirmation",
            f"{_names(needs_confirmation)} require confirmation before content is sent to "
            f"{provider.display_name}.",
            blocking_layers=needs_confirmation,
            remote=True,
        )

    return PolicyDecision(
        "allowed",
        f"These layers allow requests to {provider.display_name}.",
        remote=True,
    )


def _names(names: list[str]) -> str:
    if len(names) == 1:
        return f"“{names[0]}”"
    quoted = [f"“{name}”" for name in names]
    return ", ".join(quoted[:-1]) + f" and {quoted[-1]}"
