"""OpenAI-compatible providers: Ollama, llama.cpp, LM Studio, vLLM, OpenAI itself.

One adapter, several configurations. The wire format is the same
(`/v1/chat/completions` with SSE); what differs is the base URL, whether an API key
is needed, and — critically — whether the bytes leave the machine.

`is_local` is not cosmetic. It is what the policy gate reads to decide whether a
layer marked "local AI only" may be used at all, so a mislabelled provider is a
privacy failure, not a display bug.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx

from app.domain.ai import (
    AIEvent,
    AIRequest,
    Capability,
    EmbeddingRequest,
    EmbeddingResult,
    ModelInfo,
    ProviderCapabilities,
    ProviderHealth,
)
from app.domain.errors import ProviderError
from app.infrastructure.ai_providers.base import AIProvider, http_client, iter_sse, provider_error
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


class OpenAICompatibleProvider(AIProvider):
    def __init__(
        self,
        capabilities: ProviderCapabilities,
        base_url: str,
        api_key: str | None = None,
        embedding_model: str = "",
    ) -> None:
        self.capabilities = capabilities
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._embedding_model = embedding_model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def health_check(self) -> ProviderHealth:
        configured = bool(self._api_key) or not self.capabilities.requires_api_key
        if not configured:
            return ProviderHealth(
                provider_id=self.provider_id,
                reachable=False,
                configured=False,
                detail="No API key is configured.",
            )
        try:
            models = await self.list_models()
        except ProviderError as error:
            # list_models already mapped the transport failure to a safe message.
            # Re-wrapping it would turn "Could not reach Ollama" into the generic
            # "failed unexpectedly", losing the one useful thing in it.
            return ProviderHealth(
                provider_id=self.provider_id,
                reachable=False,
                configured=configured,
                detail=error.message,
            )
        except Exception as exc:
            return ProviderHealth(
                provider_id=self.provider_id,
                reachable=False,
                configured=configured,
                detail=str(provider_error(exc, self.capabilities.display_name)),
            )
        return ProviderHealth(
            provider_id=self.provider_id,
            reachable=True,
            configured=configured,
            detail=f"{len(models)} model(s) available.",
            models=models,
        )

    async def list_models(self) -> list[ModelInfo]:
        async with http_client(self._base_url, self._headers()) as client:
            try:
                response = await client.get("/v1/models")
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise provider_error(exc, self.capabilities.display_name) from exc

            payload = response.json()

        models = payload.get("data", payload.get("models", []))
        result: list[ModelInfo] = []
        for entry in models:
            identifier = entry.get("id") or entry.get("name") or ""
            if not identifier:
                continue
            result.append(
                ModelInfo(
                    id=identifier,
                    display_name=identifier,
                    context_tokens=int(
                        entry.get("context_length", self.capabilities.max_context_tokens)
                    ),
                    is_local=self.capabilities.is_local,
                )
            )
        return result

    async def stream(self, request: AIRequest, cancel: asyncio.Event) -> AsyncIterator[AIEvent]:
        body: dict[str, object] = {
            "model": request.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "stream": True,
        }
        if request.stop:
            body["stop"] = request.stop
        if request.json_schema and self.capabilities.supports(Capability.STRUCTURED_OUTPUT):
            body["response_format"] = {"type": "json_object"}

        yield AIEvent(kind="start", model=request.model)

        output_tokens = 0
        try:
            async with http_client(self._base_url, self._headers()) as client:
                async with client.stream("POST", "/v1/chat/completions", json=body) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        response.raise_for_status()

                    async for chunk in iter_sse(response, cancel):
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content") or ""
                        if text:
                            output_tokens += 1
                            yield AIEvent(kind="delta", text=text)

            if cancel.is_set():
                # Cancellation is a normal outcome, not an error. The caller stops
                # reading and the HTTP connection closes.
                return

            yield AIEvent(kind="done", model=request.model, output_tokens=output_tokens)

        except httpx.HTTPError as exc:
            error = provider_error(exc, self.capabilities.display_name)
            yield AIEvent(kind="error", error=error.message)

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        model = request.model or self._embedding_model
        async with http_client(self._base_url, self._headers()) as client:
            try:
                response = await client.post(
                    "/v1/embeddings",
                    json={"model": model, "input": request.texts},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise provider_error(exc, self.capabilities.display_name) from exc

            payload = response.json()

        vectors = [entry["embedding"] for entry in payload.get("data", [])]
        return EmbeddingResult(model=model, vectors=vectors)


# -- the catalogue -----------------------------------------------------------

OLLAMA = ProviderCapabilities(
    provider_id="ollama",
    display_name="Ollama",
    is_local=True,
    requires_api_key=False,
    capabilities=[
        Capability.TEXT,
        Capability.STREAMING,
        Capability.STRUCTURED_OUTPUT,
        Capability.EMBEDDINGS,
    ],
    max_context_tokens=32_768,
    note="Runs on this machine. Nothing leaves it.",
)

LLAMACPP = ProviderCapabilities(
    provider_id="llamacpp",
    display_name="llama.cpp server",
    is_local=True,
    requires_api_key=False,
    capabilities=[Capability.TEXT, Capability.STREAMING, Capability.EMBEDDINGS],
    max_context_tokens=32_768,
    note="Runs on this machine. Nothing leaves it.",
)

LMSTUDIO = ProviderCapabilities(
    provider_id="lmstudio",
    display_name="LM Studio",
    is_local=True,
    requires_api_key=False,
    capabilities=[
        Capability.TEXT,
        Capability.STREAMING,
        Capability.STRUCTURED_OUTPUT,
        Capability.EMBEDDINGS,
    ],
    max_context_tokens=32_768,
    note="Runs on this machine. Nothing leaves it.",
)

OPENAI = ProviderCapabilities(
    provider_id="openai",
    display_name="OpenAI",
    is_local=False,
    requires_api_key=True,
    capabilities=[
        Capability.TEXT,
        Capability.STREAMING,
        Capability.STRUCTURED_OUTPUT,
        Capability.TOOLS,
        Capability.VISION,
        Capability.EMBEDDINGS,
        Capability.LARGE_CONTEXT,
    ],
    max_context_tokens=400_000,
    note="Remote. Your selected content is sent to OpenAI.",
)

OPENAI_COMPATIBLE = ProviderCapabilities(
    provider_id="openai-compatible",
    display_name="OpenAI-compatible endpoint",
    is_local=False,
    requires_api_key=False,
    capabilities=[Capability.TEXT, Capability.STREAMING],
    max_context_tokens=32_768,
    note="Remote unless you point it at your own machine. Strata cannot tell — it "
    "treats a custom endpoint as remote, which is the safe assumption.",
)

DEFAULT_BASE_URLS: dict[str, str] = {
    "ollama": "http://127.0.0.1:11434",
    "llamacpp": "http://127.0.0.1:8080",
    "lmstudio": "http://127.0.0.1:1234",
    "openai": "https://api.openai.com",
}
