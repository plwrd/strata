"""Security: persisted AI memory must never contain private-layer content.

The rule under test is the extension of "no decrypted private content ever
touches disk" to the AI history files (docs/ai-memory-design.md §3). These tests
push sentinel private material through the real request and apply paths, then
read the raw bytes under `.strata/ai/` looking for it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.domain.ai import AIEvent, ModelInfo, ProviderHealth
from app.domain.operations import Operation, OperationPlan
from app.infrastructure.ai_providers.base import AIProvider
from app.infrastructure.ai_providers.openai_compatible import OLLAMA
from app.services.container import Services

pytestmark = pytest.mark.security

PROMPT_SENTINEL = "SENTINEL-PRIVATE-PROMPT-4471"
SOURCE_SENTINEL = "SENTINEL-PRIVATE-SOURCE-9082"
REPLY_SENTINEL = "SENTINEL-MODEL-REPLY-3319"
TITLE_SENTINEL = "Sentinel Secret Meeting"


class EchoProvider(AIProvider):
    capabilities = OLLAMA

    @property
    def provider_id(self) -> str:
        return "ollama"

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider_id="ollama", reachable=True, configured=True)

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def stream(self, request, cancel) -> AsyncIterator[AIEvent]:
        yield AIEvent(kind="delta", text=REPLY_SENTINEL)
        yield AIEvent(kind="done", model=request.model, input_tokens=1, output_tokens=1)


def _ai_dir_bytes(services: Services) -> str:
    root = services.workspace.root / ".strata" / "ai"
    if not root.is_dir():
        return ""
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(root.iterdir())
        if path.is_file()
    )


def test_private_request_content_never_reaches_the_history_files(
    workspace: Services,
) -> None:
    private, _recovery = workspace.workspace.create_layer(
        "Vault", visibility="private", password="a sound passphrase"
    )
    workspace.ai.provider = lambda provider_id: EchoProvider()  # type: ignore[method-assign]

    async def drain() -> None:
        async for _event in workspace.ai.run(
            provider_id="ollama",
            model="m",
            prompt=PROMPT_SENTINEL,
            sources=SOURCE_SENTINEL,
            layer_ids=[private.id],
            private_object_count=1,
            object_count=1,
        ):
            pass

    asyncio.run(drain())

    raw = _ai_dir_bytes(workspace)
    assert raw, "the execution should still be recorded — as metadata"
    assert PROMPT_SENTINEL not in raw
    assert SOURCE_SENTINEL not in raw
    assert REPLY_SENTINEL not in raw
    # The record exists and says so honestly.
    record = workspace.ai.executions()[0]
    assert record.redacted is True
    assert record.private_source_count == 1


def test_private_plan_titles_never_reach_the_audit_file(workspace: Services) -> None:
    private, _recovery = workspace.workspace.create_layer(
        "Vault", visibility="private", password="a sound passphrase"
    )
    plan = OperationPlan(
        id="plan_secret",
        summary=f"Create {TITLE_SENTINEL}",
        operations=[
            Operation(
                type="create_note",
                layer_id=private.id,
                title=TITLE_SENTINEL,
                content=f"# {TITLE_SENTINEL}\n\n{SOURCE_SENTINEL}",
                rationale="secret",
            )
        ],
        prompt=PROMPT_SENTINEL,
    )
    review = workspace.operations.review(plan, allowed_layer_ids=[private.id])
    workspace.operations.apply(review, approved_indexes=[0], allowed_layer_ids=[private.id])

    raw = _ai_dir_bytes(workspace)
    assert TITLE_SENTINEL not in raw
    assert SOURCE_SENTINEL not in raw
    assert PROMPT_SENTINEL not in raw


def test_receipts_for_private_requests_stay_metadata_only(workspace: Services) -> None:
    private, _recovery = workspace.workspace.create_layer(
        "Vault", visibility="private", password="a sound passphrase"
    )
    workspace.ai.provider = lambda provider_id: EchoProvider()  # type: ignore[method-assign]

    async def drain() -> None:
        async for _event in workspace.ai.run(
            provider_id="ollama",
            model="m",
            prompt=PROMPT_SENTINEL,
            sources=SOURCE_SENTINEL,
            layer_ids=[private.id],
            private_object_count=3,
        ):
            pass

    asyncio.run(drain())

    receipt = workspace.ai.receipts()[0]
    assert receipt.private_object_count == 3
    dumped = receipt.model_dump_json()
    assert PROMPT_SENTINEL not in dumped
    assert SOURCE_SENTINEL not in dumped
