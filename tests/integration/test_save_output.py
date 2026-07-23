"""Saving an AI output as a permanent asset — through the operation engine.

The save is one bridge call, but underneath it is a reviewed, applied,
snapshot-backed plan: audited, undoable, and stamped with provenance. These
tests drive the bridge slot exactly as the frontend would.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.bridge.ai_bridge import AIComposerBridge
from app.domain.history import AIExecutionRecord
from app.domain.ids import new_execution_id
from app.services.container import Services

pytestmark = pytest.mark.usefixtures("workspace")


def call(bridge: Any, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = json.dumps({"v": 1, "requestId": "req_test", "payload": payload or {}})
    response: dict[str, Any] = json.loads(getattr(bridge, method)(request))
    return response


def data(response: dict[str, Any]) -> dict[str, Any]:
    assert response["ok"] is True, response.get("error")
    payload: dict[str, Any] = response["data"]
    return payload


def _recorded_execution(workspace: Services) -> AIExecutionRecord:
    """A persisted execution, as a real request would have left behind."""
    source = workspace.notes.list_notes()[0]
    return workspace.history.record_execution(
        AIExecutionRecord(
            id=new_execution_id(),
            provider="ollama",
            model="llama3",
            layer_ids=[source.metadata.layer_id],
            prompt="What did I decide?",
            response_text="You decided X.",
            source_object_ids=[source.metadata.id],
            source_count=1,
        )
    )


def test_save_as_report_lands_in_reports_with_provenance(workspace: Services) -> None:
    execution = _recorded_execution(workspace)
    bridge = AIComposerBridge(workspace)

    saved = data(
        call(
            bridge,
            "save_output",
            {
                "execution_id": execution.id,
                "content": "# Findings\n\nYou decided X.",
                "title": "Decision findings",
                "target": "report",
            },
        )
    )

    note = workspace.notes.get_note(saved["note_id"])
    assert note.metadata.folder_path == "Reports"
    assert note.metadata.properties["type"] == "report"
    assert note.metadata.properties["review_status"] == "ai-inferred"
    assert note.metadata.properties["generated_by"] == execution.id
    source_title = workspace.notes.list_notes()[0].metadata.title
    assert f"derived_from:: [[{source_title}]]" in note.content or any(
        link.relationship == "derived_from" for link in note.metadata.links
    )
    # The save is in the audit log and undoable like any other AI change.
    log = workspace.operations.audit_log()
    assert log[0].plan_id == saved["plan_id"]
    workspace.operations.undo(saved["plan_id"])
    assert all(n.metadata.id != saved["note_id"] for n in workspace.notes.list_notes())


def test_save_twice_never_overwrites_the_first(workspace: Services) -> None:
    execution = _recorded_execution(workspace)
    bridge = AIComposerBridge(workspace)
    payload = {
        "execution_id": execution.id,
        "content": "Body.",
        "title": "Same title",
        "target": "note",
    }

    first = data(call(bridge, "save_output", payload))
    second = data(call(bridge, "save_output", payload))

    assert first["note_id"] != second["note_id"]
    assert second["title"] == "Same title 2"


def test_append_adds_to_the_chosen_note(workspace: Services) -> None:
    target = workspace.notes.list_notes()[0]
    bridge = AIComposerBridge(workspace)

    data(
        call(
            bridge,
            "save_output",
            {
                "content": "An appended AI answer.",
                "title": "ignored for append",
                "target": "append",
                "note_id": target.metadata.id,
            },
        )
    )

    assert "An appended AI answer." in workspace.notes.get_note(target.metadata.id).content


def test_append_without_a_note_is_refused(workspace: Services) -> None:
    response = call(
        AIComposerBridge(workspace),
        "save_output",
        {"content": "x", "title": "t", "target": "append"},
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"
