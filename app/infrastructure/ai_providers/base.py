"""The provider interface, and the shared HTTP plumbing.

Every provider is an adapter behind this one interface. Nothing above this layer
knows whether it is talking to a local llama.cpp server or to Anthropic — that is
what makes the router, the policy gate and the composer provider-neutral rather
than provider-neutral-in-the-brochure.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.domain.ai import (
    AIEvent,
    AIRequest,
    EmbeddingRequest,
    EmbeddingResult,
    ModelInfo,
    ProviderCapabilities,
    ProviderHealth,
    TokenEstimate,
)
from app.domain.errors import ProviderError, UnsupportedError
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 120.0
CONNECT_TIMEOUT = 5.0

# A rough character-per-token ratio, used only when a provider offers no tokeniser.
# Deliberately pessimistic so a budget is never blown by an optimistic estimate.
CHARS_PER_TOKEN = 3.6


class AIProvider(ABC):
    """What every provider must do.

    Note what is *not* here: no filesystem access, no shell, no arbitrary HTTP. A
    provider adapter talks to its own endpoint and returns text. It cannot be
    persuaded by a prompt to do anything else, because there is nothing else it can
    do.
    """

    capabilities: ProviderCapabilities

    @property
    def provider_id(self) -> str:
        return self.capabilities.provider_id

    @abstractmethod
    async def health_check(self) -> ProviderHealth: ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    async def estimate_tokens(self, request: AIRequest) -> TokenEstimate:
        """A character-ratio estimate by default.

        Providers that expose a real tokeniser override this. The estimate is always
        labelled as an estimate in the UI: showing "1,204 tokens" when the number is
        a guess is worse than showing "~1,200".
        """
        text = "".join(message.content for message in request.messages)
        prompt = int(len(text) / CHARS_PER_TOKEN) + 1
        context = self.capabilities.max_context_tokens
        return TokenEstimate(
            prompt_tokens=prompt,
            max_output_tokens=request.max_output_tokens,
            context_tokens=context,
            fits=prompt + request.max_output_tokens <= context,
        )

    @abstractmethod
    def stream(self, request: AIRequest, cancel: asyncio.Event) -> AsyncIterator[AIEvent]:
        """Stream a completion. Must stop promptly when ``cancel`` is set."""

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        raise UnsupportedError(f"{self.capabilities.display_name} does not provide embeddings.")


def http_client(base_url: str = "", headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers or {},
        timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=CONNECT_TIMEOUT),
        # No redirects: an endpoint that redirects our request somewhere else is
        # sending the user's notes somewhere they did not agree to.
        follow_redirects=False,
    )


def provider_error(exc: Exception, provider: str) -> ProviderError:
    """Turn a transport failure into a safe, useful error.

    The message must be helpful ("is Ollama running?") without echoing back a URL
    or a header that might contain a key.
    """
    if isinstance(exc, httpx.ConnectError):
        return ProviderError(
            f"Could not reach {provider}. Is it running, and is the endpoint correct?"
        )
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(f"{provider} did not respond in time.")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return ProviderError(f"{provider} rejected the credentials.")
        if status == 404:
            return ProviderError(f"{provider} does not have that model.")
        if status == 429:
            return ProviderError(f"{provider} is rate-limiting the request.", retryable=True)
        return ProviderError(f"{provider} returned an error (HTTP {status}).")
    logger.exception("provider.unexpected", provider=provider)
    return ProviderError(f"{provider} failed unexpectedly.")


async def iter_sse(response: httpx.Response, cancel: asyncio.Event) -> AsyncIterator[Any]:
    """Iterate a Server-Sent Events stream, honouring cancellation between chunks.

    Yields decoded JSON objects. The static type is ``Any`` on purpose: each
    provider's event shape is different and self-describing (a ``type`` field, a
    ``choices`` array), and the adapters do their own narrowing. Forcing a single
    typed shape here would just move the casts, not remove them.
    """
    async for line in response.aiter_lines():
        if cancel.is_set():
            return
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue
