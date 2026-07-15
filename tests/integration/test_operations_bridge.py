"""The operations and snapshot bridges, driven as the frontend drives them."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.bridge.operations_bridge import OperationsBridge
from app.bridge.snapshot_bridge import SnapshotBridge
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
