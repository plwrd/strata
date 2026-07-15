"""Provider adapters: streaming, cancellation, capability gating, and error safety.

The HTTP layer is mocked, so these run offline and deterministically — but the
adapter under test is the real one, including its SSE parsing and its error mapping.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.domain.ai import AIMessage, AIRequest, EmbeddingRequest
from app.domain.errors import ProviderError
from app.infrastructure.ai_providers.anthropic import ANTHROPIC, AnthropicProvider
from app.infrastructure.ai_providers.openai_compatible import (
    OLLAMA,
    OPENAI,
    OpenAICompatibleProvider,
)

BASE = "http://127.0.0.1:11434"

pytestmark = pytest.mark.asyncio


def request_for(provider: str, model: str = "llama3") -> AIRequest:
    return AIRequest(
        provider_id=provider,
        model=model,
        messages=[AIMessage(role="user", content="Say hello")],
    )


def sse(*chunks: str) -> str:
    return "".join(f"data: {chunk}\n\n" for chunk in chunks) + "data: [DONE]\n\n"


# --- OpenAI-compatible ------------------------------------------------------


@respx.mock
async def test_streaming_yields_start_deltas_and_done() -> None:
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text=sse(
                '{"choices":[{"delta":{"content":"Hel"}}]}',
                '{"choices":[{"delta":{"content":"lo"}}]}',
            ),
        )
    )
    provider = OpenAICompatibleProvider(OLLAMA, BASE)

    events = [event async for event in provider.stream(request_for("ollama"), asyncio.Event())]

    assert [event.kind for event in events] == ["start", "delta", "delta", "done"]
    assert "".join(event.text for event in events) == "Hello"


@respx.mock
async def test_cancellation_stops_the_stream() -> None:
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text=sse(*[f'{{"choices":[{{"delta":{{"content":"{i}"}}}}]}}' for i in range(50)]),
        )
    )
    provider = OpenAICompatibleProvider(OLLAMA, BASE)
    cancel = asyncio.Event()

    collected = []
    async for event in provider.stream(request_for("ollama"), cancel):
        collected.append(event)
        if len(collected) >= 3:
            cancel.set()

    # It stopped early, and it did not emit a `done` — a cancelled request did not
    # complete, and saying it did would be a lie the receipt would repeat.
    assert len(collected) < 50
    assert all(event.kind != "done" for event in collected)


@respx.mock
async def test_a_provider_error_becomes_an_error_event_not_an_exception() -> None:
    respx.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(500))
    provider = OpenAICompatibleProvider(OLLAMA, BASE)

    events = [event async for event in provider.stream(request_for("ollama"), asyncio.Event())]

    assert events[-1].kind == "error"
    assert "Ollama" in events[-1].error


@respx.mock
async def test_a_connection_failure_says_something_useful() -> None:
    respx.post(f"{BASE}/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    provider = OpenAICompatibleProvider(OLLAMA, BASE)

    events = [event async for event in provider.stream(request_for("ollama"), asyncio.Event())]

    assert events[-1].kind == "error"
    assert "Is it running" in events[-1].error


@respx.mock
async def test_an_api_key_never_appears_in_an_error() -> None:
    """A key in an error message ends up in a log, a bug report, and a screenshot."""
    respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=httpx.Response(401))
    provider = OpenAICompatibleProvider(
        OPENAI, "https://api.openai.com", api_key="sk-super-secret-key"
    )

    events = [
        event async for event in provider.stream(request_for("openai", "gpt-4"), asyncio.Event())
    ]

    assert events[-1].kind == "error"
    assert "sk-super-secret" not in events[-1].error
    assert "rejected the credentials" in events[-1].error


@respx.mock
async def test_health_check_reports_models() -> None:
    respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "llama3"}, {"id": "phi3"}]})
    )
    provider = OpenAICompatibleProvider(OLLAMA, BASE)

    health = await provider.health_check()

    assert health.reachable is True
    assert {model.id for model in health.models} == {"llama3", "phi3"}
    assert all(model.is_local for model in health.models)


@respx.mock
async def test_health_check_reports_unreachable_without_raising() -> None:
    respx.get(f"{BASE}/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    provider = OpenAICompatibleProvider(OLLAMA, BASE)

    health = await provider.health_check()

    assert health.reachable is False
    assert health.configured is True  # no key needed
    assert "Could not reach" in health.detail


async def test_a_provider_that_needs_a_key_and_has_none_is_unconfigured() -> None:
    provider = OpenAICompatibleProvider(OPENAI, "https://api.openai.com", api_key=None)

    health = await provider.health_check()

    assert health.configured is False
    assert health.reachable is False
    assert "No API key" in health.detail


@respx.mock
async def test_embeddings_round_trip() -> None:
    respx.post(f"{BASE}/v1/embeddings").mock(
        return_value=httpx.Response(
            200, json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}
        )
    )
    provider = OpenAICompatibleProvider(OLLAMA, BASE)

    result = await provider.create_embeddings(
        EmbeddingRequest(provider_id="ollama", model="nomic", texts=["a", "b"])
    )

    assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]


async def test_a_provider_without_embeddings_refuses_them() -> None:
    from app.domain.errors import UnsupportedError

    provider = AnthropicProvider("key")

    with pytest.raises(UnsupportedError):
        await provider.create_embeddings(
            EmbeddingRequest(provider_id="anthropic", model="x", texts=["a"])
        )


# --- Anthropic --------------------------------------------------------------


@respx.mock
async def test_anthropic_streaming_parses_its_own_event_shape() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            text=sse(
                '{"type":"message_start","message":{"usage":{"input_tokens":12}}}',
                '{"type":"content_block_delta","delta":{"text":"Hel"}}',
                '{"type":"content_block_delta","delta":{"text":"lo"}}',
                '{"type":"message_delta","usage":{"output_tokens":2}}',
            ),
        )
    )
    provider = AnthropicProvider("sk-ant-test")

    events = [
        event
        async for event in provider.stream(
            request_for("anthropic", "claude-opus-4-8"), asyncio.Event()
        )
    ]

    assert "".join(event.text for event in events) == "Hello"
    done = events[-1]
    assert done.kind == "done"
    assert done.input_tokens == 12
    assert done.output_tokens == 2


@respx.mock
async def test_anthropic_takes_the_system_prompt_as_a_top_level_field() -> None:
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=sse())
    )
    provider = AnthropicProvider("sk-ant-test")
    request = AIRequest(
        provider_id="anthropic",
        model="claude-opus-4-8",
        messages=[
            AIMessage(role="system", content="You are careful."),
            AIMessage(role="user", content="Hi"),
        ],
    )

    async for _event in provider.stream(request, asyncio.Event()):
        pass

    import json

    body = json.loads(route.calls.last.request.read())
    # The system prompt is a top-level field, not a message.
    assert body["system"] == "You are careful."
    assert all(message["role"] != "system" for message in body["messages"])


async def test_anthropic_is_unconfigured_without_a_key() -> None:
    health = await AnthropicProvider(None).health_check()

    assert health.configured is False
    assert "No API key" in health.detail


def test_anthropic_declares_no_embeddings() -> None:
    from app.domain.ai import Capability

    assert ANTHROPIC.supports(Capability.EMBEDDINGS) is False


# --- token estimation -------------------------------------------------------


async def test_token_estimate_flags_a_context_overflow() -> None:
    provider = OpenAICompatibleProvider(OLLAMA, BASE)
    huge = AIRequest(
        provider_id="ollama",
        model="llama3",
        messages=[AIMessage(role="user", content="x" * 200_000)],
        max_output_tokens=1000,
    )

    estimate = await provider.estimate_tokens(huge)

    assert estimate.fits is False
    assert estimate.prompt_tokens > OLLAMA.max_context_tokens


async def test_token_estimate_fits_for_a_small_request() -> None:
    provider = OpenAICompatibleProvider(OLLAMA, BASE)

    estimate = await provider.estimate_tokens(request_for("ollama"))

    assert estimate.fits is True
    assert estimate.prompt_tokens > 0


# --- the error mapper -------------------------------------------------------


def test_rate_limiting_is_marked_retryable() -> None:
    from app.infrastructure.ai_providers.base import provider_error

    response = httpx.Response(429, request=httpx.Request("POST", "http://x"))
    error = provider_error(
        httpx.HTTPStatusError("429", request=response.request, response=response), "OpenAI"
    )

    assert isinstance(error, ProviderError)
    assert error.retryable is True
