"""Persistent AI memory — receipts, execution records, and the audit log.

The property under test is durability with redaction: everything the AI did is
still known after a restart, and nothing a private layer contained is on disk.
Restarts are simulated the honest way — a second Services container over the
same directories.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from app.domain.ai import AIEvent, ModelInfo, ProviderHealth
from app.domain.export import PrivacyReceipt
from app.domain.history import AIExecutionRecord
from app.domain.ids import new_execution_id, new_export_id
from app.domain.operations import Operation, OperationPlan
from app.infrastructure.ai_providers.base import AIProvider
from app.infrastructure.ai_providers.openai_compatible import OLLAMA
from app.services.ai_history_service import (
    APPLIED_PLANS_FILE,
    EXECUTIONS_FILE,
    RECEIPTS_FILE,
)
from app.services.container import Paths, Services


class StubProvider(AIProvider):
    """A local 'model' that streams a canned reply. No network, no mocks of
    Strata's own behaviour — only the provider boundary is stubbed."""

    capabilities = OLLAMA

    def __init__(self, reply: str = "An answer.", fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail

    @property
    def provider_id(self) -> str:
        return "ollama"

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider_id="ollama", reachable=True, configured=True)

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def stream(self, request, cancel) -> AsyncIterator[AIEvent]:
        yield AIEvent(kind="start", model=request.model)
        if self.fail:
            yield AIEvent(kind="error", error="The provider failed.")
            return
        yield AIEvent(kind="delta", text=self.reply)
        yield AIEvent(kind="done", model=request.model, input_tokens=11, output_tokens=7)


async def _drain(stream: AsyncIterator[AIEvent]) -> list[AIEvent]:
    return [event async for event in stream]


def _run_request(services: Services, layer_ids: list[str], prompt: str) -> list[AIEvent]:
    services.ai.provider = lambda provider_id: StubProvider()  # type: ignore[method-assign]
    return asyncio.run(
        _drain(
            services.ai.run(
                provider_id="ollama",
                model="test-model",
                prompt=prompt,
                sources="",
                layer_ids=layer_ids,
                object_count=2,
            )
        )
    )


def _reopen(paths: Paths) -> Services:
    fresh = Services(paths, environment="test")
    fresh.workspace.open(paths.default_workspace)
    return fresh


def _history_file(services: Services, filename: str) -> str:
    path = services.workspace.root / ".strata" / "ai" / filename
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _public_layer(services: Services) -> str:
    return next(
        layer.id for layer in services.workspace.descriptor.layers if layer.visibility == "public"
    )


# -- executions and receipts through the real request path ---------------------


def test_a_request_leaves_an_execution_record_and_a_receipt(workspace: Services) -> None:
    layer = _public_layer(workspace)

    _run_request(workspace, [layer], "What do my notes say about testing?")

    executions = workspace.ai.executions()
    assert len(executions) == 1
    record = executions[0]
    assert record.kind == "ai-request"
    assert record.prompt == "What do my notes say about testing?"
    assert record.response_text == "An answer."
    assert record.result == "completed"
    assert record.input_tokens == 11
    assert record.output_tokens == 7
    assert record.redacted is False
    assert record.duration_ms >= 0
    assert workspace.ai.receipts()[0].result == "completed"


def test_history_survives_a_restart(workspace: Services, paths: Paths) -> None:
    layer = _public_layer(workspace)
    _run_request(workspace, [layer], "Remember this question.")

    fresh = _reopen(paths)

    assert [record.prompt for record in fresh.ai.executions()] == ["Remember this question."]
    assert len(fresh.ai.receipts()) == 1


def test_a_failed_request_is_recorded_as_failed(workspace: Services) -> None:
    layer = _public_layer(workspace)
    workspace.ai.provider = lambda provider_id: StubProvider(fail=True)  # type: ignore[method-assign]

    asyncio.run(
        _drain(
            workspace.ai.run(
                provider_id="ollama",
                model="test-model",
                prompt="p",
                sources="",
                layer_ids=[layer],
            )
        )
    )

    record = workspace.ai.executions()[0]
    assert record.result == "failed"
    assert record.error_message == "The provider failed."


def test_newest_execution_comes_first_and_limit_applies(workspace: Services) -> None:
    layer = _public_layer(workspace)
    _run_request(workspace, [layer], "first")
    _run_request(workspace, [layer], "second")

    newest_first = workspace.ai.executions()
    assert [record.prompt for record in newest_first] == ["second", "first"]
    assert len(workspace.ai.executions(limit=1)) == 1


# -- redaction ------------------------------------------------------------------


def test_private_layer_requests_are_redacted_on_disk(workspace: Services) -> None:
    private, _recovery = workspace.workspace.create_layer(
        "Secret", visibility="private", password="a sound passphrase"
    )

    _run_request(workspace, [private.id], "SENTINEL-PROMPT-77")

    record = workspace.ai.executions()[0]
    assert record.redacted is True
    assert record.prompt == ""
    assert record.response_text == ""
    raw = _history_file(workspace, EXECUTIONS_FILE)
    assert "SENTINEL-PROMPT-77" not in raw
    assert "An answer." not in raw


def test_an_unknown_layer_id_redacts_conservatively(workspace: Services) -> None:
    record = workspace.history.record_execution(
        AIExecutionRecord(
            id=new_execution_id(),
            layer_ids=["layer_that_does_not_exist"],
            prompt="secret-ish",
            response_text="body",
        )
    )

    assert record.redacted is True
    assert "secret-ish" not in _history_file(workspace, EXECUTIONS_FILE)


# -- storage robustness ---------------------------------------------------------


def test_a_corrupt_line_loses_one_record_not_the_history(workspace: Services) -> None:
    layer = _public_layer(workspace)
    _run_request(workspace, [layer], "kept")
    path = workspace.workspace.root / ".strata" / "ai" / EXECUTIONS_FILE
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{this is not json}\n")
    _run_request(workspace, [layer], "also kept")

    assert [record.prompt for record in workspace.ai.executions()] == ["also kept", "kept"]


def test_without_a_workspace_history_is_silent_and_empty(services: Services) -> None:
    services.history.record_receipt(
        PrivacyReceipt(id=new_export_id(), created_at="2026-01-01T00:00:00+00:00", kind="export")
    )

    assert services.history.list_receipts() == []
    assert services.ai.executions() == []
    assert services.ai.clear_history() == 0


def test_clear_history_deletes_the_files(workspace: Services) -> None:
    layer = _public_layer(workspace)
    _run_request(workspace, [layer], "to be forgotten")

    removed = workspace.ai.clear_history()

    assert removed == 2  # receipts + executions
    assert workspace.ai.executions() == []
    assert workspace.ai.receipts() == []
    assert _history_file(workspace, EXECUTIONS_FILE) == ""
    assert _history_file(workspace, RECEIPTS_FILE) == ""


# -- the applied-plan audit log --------------------------------------------------


def _apply_create_note(services: Services, layer_id: str, title: str):
    plan = OperationPlan(
        id=f"plan_{title.lower().replace(' ', '_')}",
        summary=f"Create {title}",
        operations=[
            Operation(
                type="create_note",
                layer_id=layer_id,
                title=title,
                content=f"# {title}\n\nBody.",
                rationale="test",
            )
        ],
    )
    review = services.operations.review(plan, allowed_layer_ids=[layer_id])
    return services.operations.apply(review, approved_indexes=[0], allowed_layer_ids=[layer_id])


def test_the_audit_log_survives_a_restart(workspace: Services, paths: Paths) -> None:
    layer = _public_layer(workspace)
    applied = _apply_create_note(workspace, layer, "Durable Note")

    fresh = _reopen(paths)

    log = fresh.operations.audit_log()
    assert [entry.plan_id for entry in log] == [applied.plan_id]
    assert log[0].summary == "Create Durable Note"
    assert log[0].redacted is False


def test_undo_works_after_a_restart(workspace: Services, paths: Paths) -> None:
    layer = _public_layer(workspace)
    applied = _apply_create_note(workspace, layer, "Undo Me")
    assert any(n.metadata.title == "Undo Me" for n in workspace.notes.list_notes())

    fresh = _reopen(paths)
    undone = fresh.operations.undo(applied.plan_id)

    assert undone.undone is True
    assert not any(n.metadata.title == "Undo Me" for n in fresh.notes.list_notes())
    # The undone flag is durable too: a second restart still knows.
    again = _reopen(paths)
    assert again.operations.audit_log()[0].undone is True


def test_private_plans_are_redacted_in_the_persisted_audit_log(workspace: Services) -> None:
    private, _recovery = workspace.workspace.create_layer(
        "Vault", visibility="private", password="a sound passphrase"
    )

    _apply_create_note(workspace, private.id, "Hidden Title")

    raw = _history_file(workspace, APPLIED_PLANS_FILE)
    assert raw  # the plan was persisted…
    assert "Hidden Title" not in raw  # …without the private note's title
    stored = json.loads(raw.splitlines()[0])
    assert stored["redacted"] is True
    assert stored["summary"] == ""
    # The session copy stays complete for the UI that just applied it.
    assert workspace.operations.audit_log()[0].summary == "Create Hidden Title"
