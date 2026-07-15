"""Anthropic Messages API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx

from app.domain.ai import (
    AIEvent,
    AIRequest,
    Capability,
    ModelInfo,
    ProviderCapabilities,
    ProviderHealth,
)
from app.infrastructure.ai_providers.base import AIProvider, http_client, iter_sse, provider_error

API_VERSION = "2023-06-01"
BASE_URL = "https://api.anthropic.com"

ANTHROPIC = ProviderCapabilities(
    provider_id="anthropic",
    display_name="Anthropic",
    is_local=False,
    requires_api_key=True,
    capabilities=[
        Capability.TEXT,
        Capability.STREAMING,
        Capability.STRUCTURED_OUTPUT,
        Capability.TOOLS,
        Capability.VISION,
        Capability.PDF,
        Capability.LARGE_CONTEXT,
    ],
    max_context_tokens=200_000,
    note="Remote. Your selected content is sent to Anthropic.",
)

# Kept here rather than fetched, because the Messages API has no models endpoint in
# every deployment. The list is a default, and a user may type any model id.
KNOWN_MODELS = [
    ModelInfo(id="claude-opus-4-8", display_name="Claude Opus 4.8", context_tokens=200_000),
    ModelInfo(id="claude-sonnet-5", display_name="Claude Sonnet 5", context_tokens=200_000),
    ModelInfo(id="claude-haiku-4-5", display_name="Claude Haiku 4.5", context_tokens=200_000),
]


class AnthropicProvider(AIProvider):
    capabilities = ANTHROPIC

    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "anthropic-version": API_VERSION,
            **({"x-api-key": self._api_key} if self._api_key else {}),
        }

    async def health_check(self) -> ProviderHealth:
        if not self._api_key:
            return ProviderHealth(
                provider_id=self.provider_id,
                reachable=False,
                configured=False,
                detail="No API key is configured.",
            )

        # A one-token request is the cheapest way to prove the key works. There is no
        # free "ping" endpoint, and pretending the provider is reachable because a
        # key *exists* would be a lie the user only discovers mid-request.
        async with http_client(self._base_url, self._headers()) as client:
            try:
                response = await client.post(
                    "/v1/messages",
                    json={
                        "model": KNOWN_MODELS[-1].id,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                return ProviderHealth(
                    provider_id=self.provider_id,
                    reachable=False,
                    configured=True,
                    detail=str(provider_error(exc, self.capabilities.display_name)),
                )

        return ProviderHealth(
            provider_id=self.provider_id,
            reachable=True,
            configured=True,
            detail="The API key works.",
            models=KNOWN_MODELS,
        )

    async def list_models(self) -> list[ModelInfo]:
        return list(KNOWN_MODELS)

    async def stream(self, request: AIRequest, cancel: asyncio.Event) -> AsyncIterator[AIEvent]:
        # Anthropic takes the system prompt as a top-level field, not as a message.
        system = "\n\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]

        body: dict[str, object] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": True,
        }
        if system:
            body["system"] = system
        if request.stop:
            body["stop_sequences"] = request.stop

        yield AIEvent(kind="start", model=request.model)

        input_tokens = 0
        output_tokens = 0

        try:
            async with http_client(self._base_url, self._headers()) as client:
                async with client.stream("POST", "/v1/messages", json=body) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        response.raise_for_status()

                    async for chunk in iter_sse(response, cancel):
                        kind = chunk.get("type")

                        if kind == "message_start":
                            usage = (chunk.get("message") or {}).get("usage") or {}
                            input_tokens = int(usage.get("input_tokens", 0))

                        elif kind == "content_block_delta":
                            delta = chunk.get("delta") or {}
                            text = delta.get("text") or ""
                            if text:
                                yield AIEvent(kind="delta", text=text)

                        elif kind == "message_delta":
                            usage = chunk.get("usage") or {}
                            output_tokens = int(usage.get("output_tokens", output_tokens))

                        elif kind == "error":
                            error = chunk.get("error") or {}
                            yield AIEvent(
                                kind="error",
                                error=str(error.get("message", "The request failed.")),
                            )
                            return

            if cancel.is_set():
                return

            yield AIEvent(
                kind="done",
                model=request.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        except httpx.HTTPError as exc:
            error_obj = provider_error(exc, self.capabilities.display_name)
            yield AIEvent(kind="error", error=error_obj.message)
