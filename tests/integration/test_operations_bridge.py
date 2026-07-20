"""The operations and snapshot bridges, driven as the frontend drives them."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from app.bridge.operations_bridge import OperationsBridge
from app.bridge.snapshot_bridge import SnapshotBridge
from app.domain.operations import OperationPlan
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


def layer_id(workspace: Services) -> str:
    return workspace.workspace.descriptor.layers[0].id


def sample_plan(workspace: Services) -> dict[str, Any]:
    return {
        "id": "plan_test",
        "summary": "Add a research folder",
        "operations": [
            {
                "type": "create_folder",
                "layer_id": layer_id(workspace),
                "folder_path": "Research",
                "rationale": "group research notes",
            },
            {
                "type": "create_note",
                "layer_id": layer_id(workspace),
                "folder_path": "Research",
                "title": "Open Questions",
                "content": "# Open Questions\n",
                "rationale": "capture questions",
            },
        ],
    }


def test_review_returns_a_diff(workspace: Services) -> None:
    bridge = OperationsBridge(workspace)

    response = data(
        call(
            bridge,
            "review_plan",
            {"plan": sample_plan(workspace), "allowed_layer_ids": [layer_id(workspace)]},
        )
    )["review"]

    assert response["valid_count"] == 2
    assert response["invalid_count"] == 0
    assert response["entries"][1]["title"] == "Open Questions"


@pytest.mark.security()
def test_review_flags_an_out_of_scope_operation(workspace: Services) -> None:
    bridge = OperationsBridge(workspace)
    plan = sample_plan(workspace)
    plan["operations"][0]["layer_id"] = "layer_not_in_scope"

    response = data(
        call(
            bridge,
            "review_plan",
            {"plan": plan, "allowed_layer_ids": [layer_id(workspace)]},
        )
    )["review"]

    assert response["invalid_count"] == 1
    assert not response["entries"][0]["valid"]


def test_apply_creates_content_and_undo_removes_it(workspace: Services) -> None:
    bridge = OperationsBridge(workspace)
    plan = sample_plan(workspace)

    applied = data(
        call(
            bridge,
            "apply_plan",
            {
                "plan": plan,
                "approved_indexes": [0, 1],
                "allowed_layer_ids": [layer_id(workspace)],
            },
        )
    )["applied"]

    assert applied["snapshot_id"]
    titles = {note.metadata.title for note in workspace.notes.list_notes()}
    assert "Open Questions" in titles

    data(call(bridge, "undo_plan", {"plan_id": plan["id"]}))

    titles_after = {note.metadata.title for note in workspace.notes.list_notes()}
    assert "Open Questions" not in titles_after


def test_apply_with_no_approval_is_rejected(workspace: Services) -> None:
    response = call(
        OperationsBridge(workspace),
        "apply_plan",
        {
            "plan": sample_plan(workspace),
            "approved_indexes": [],
            "allowed_layer_ids": [layer_id(workspace)],
        },
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


def test_the_audit_log_records_applied_plans(workspace: Services) -> None:
    bridge = OperationsBridge(workspace)
    call(
        bridge,
        "apply_plan",
        {
            "plan": sample_plan(workspace),
            "approved_indexes": [0, 1],
            "allowed_layer_ids": [layer_id(workspace)],
        },
    )

    log = data(call(bridge, "audit_log"))["entries"]

    assert len(log) == 1
    assert log[0]["summary"] == "Add a research folder"


# --- plan generation ----------------------------------------------------------


class RecordingGeneration:
    """Stands in for AIGenerationService: records the call, returns an empty plan."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.done = threading.Event()

    def generate_plan_sync(self, **kwargs: Any) -> OperationPlan:
        self.calls.append(kwargs)
        self.done.set()
        return OperationPlan(id="plan_stub", summary="stub", operations=[])


def generate(bridge: OperationsBridge, workspace: Services, **overrides: Any) -> None:
    payload: dict[str, Any] = {
        "provider_id": "ollama",
        "model": "llama3",
        "prompt": "Split this into standalone notes",
        "object_ids": [],
        "layer_ids": [layer_id(workspace)],
        **overrides,
    }
    assert data(call(bridge, "generate_plan", payload))["request_id"]


def test_notes_mode_shares_the_source_note_content(workspace: Services) -> None:
    note = workspace.notes.create_note(
        layer_id=layer_id(workspace),
        folder_path="",
        title="Seed Note",
        content="# Seed Note\n\nAlpha beta gamma.",
    )
    recorder = RecordingGeneration()
    workspace.ai_generation = recorder  # type: ignore[assignment]

    generate(
        OperationsBridge(workspace),
        workspace,
        object_ids=[note.metadata.id],
        mode="notes",
        note_count=3,
    )

    assert recorder.done.wait(5), "generation thread never ran"
    kwargs = recorder.calls[0]
    assert kwargs["mode"] == "notes"
    assert kwargs["note_count"] == 3
    # The selected note's body is in the context, inside a source boundary.
    assert "Alpha beta gamma." in kwargs["context"]
    assert "<source id=" in kwargs["context"]
    # The policy gate covers the layer the content came from.
    assert layer_id(workspace) in kwargs["layer_ids"]


def test_plan_mode_still_shares_titles_only(workspace: Services) -> None:
    note = workspace.notes.create_note(
        layer_id=layer_id(workspace),
        folder_path="",
        title="Seed Note",
        content="# Seed Note\n\nAlpha beta gamma.",
    )
    recorder = RecordingGeneration()
    workspace.ai_generation = recorder  # type: ignore[assignment]

    generate(OperationsBridge(workspace), workspace, object_ids=[note.metadata.id])

    assert recorder.done.wait(5)
    kwargs = recorder.calls[0]
    assert kwargs["mode"] == "plan"
    assert "Seed Note" in kwargs["context"]
    assert "Alpha beta gamma." not in kwargs["context"]


def test_a_note_count_beyond_the_cap_is_rejected(workspace: Services) -> None:
    response = call(
        OperationsBridge(workspace),
        "generate_plan",
        {
            "provider_id": "ollama",
            "model": "llama3",
            "prompt": "Generate",
            "object_ids": [],
            "layer_ids": [layer_id(workspace)],
            "mode": "notes",
            "note_count": 999,
        },
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


# --- snapshots --------------------------------------------------------------


def test_snapshot_create_list_and_restore(workspace: Services) -> None:
    bridge = SnapshotBridge(workspace)

    created = data(call(bridge, "create_snapshot", {"name": "Checkpoint"}))["snapshot"]
    assert created["name"] == "Checkpoint"

    listed = data(call(bridge, "list_snapshots"))["snapshots"]
    assert any(snapshot["id"] == created["id"] for snapshot in listed)

    restored = data(call(bridge, "restore_snapshot", {"snapshot_id": created["id"]}))["snapshot"]
    assert restored["id"] == created["id"]


def test_deleting_a_snapshot(workspace: Services) -> None:
    bridge = SnapshotBridge(workspace)
    created = data(call(bridge, "create_snapshot", {"name": "Temp"}))["snapshot"]

    assert data(call(bridge, "delete_snapshot", {"snapshot_id": created["id"]}))["deleted"] is True
