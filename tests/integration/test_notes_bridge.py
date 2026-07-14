"""The Milestone 2 NotesBridge surface, driven exactly as the frontend drives it."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from app.bridge.notes_bridge import NotesBridge
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


def note_id(bridge: NotesBridge, title: str) -> str:
    notes = data(call(bridge, "get_tree"))["notes"]
    return next(note["id"] for note in notes if note["title"] == title)


def test_get_note_reports_its_schema_and_validation_issues(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    target = note_id(bridge, "Marketing Claims")

    response = data(call(bridge, "get_note", {"note_id": target}))

    assert response["schema_id"] == "decision"
    # The seeded note says status: rejected, which is a valid decision status.
    assert response["issues"] == []


def test_a_note_that_violates_its_schema_is_reported_not_rejected(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    target = note_id(bridge, "Marketing Claims")

    updated = data(
        call(
            bridge,
            "update_properties",
            {"note_id": target, "properties": {"type": "decision", "status": "vibes"}},
        )
    )

    assert updated["issues"][0]["key"] == "status"
    # The file kept the value the user wrote.
    assert updated["note"]["metadata"]["properties"]["status"] == "vibes"


def test_creating_a_note_from_a_schema_applies_its_template_and_defaults(
    workspace: Services,
) -> None:
    bridge = NotesBridge(workspace)
    layer = workspace.workspace.descriptor.layers[0].id

    response = data(
        call(
            bridge,
            "create_note",
            {"layer_id": layer, "title": "Sprint Planning", "schema_id": "meeting"},
        )
    )

    assert response["schema_id"] == "meeting"
    assert "## Agenda" in response["note"]["content"]
    assert response["note"]["metadata"]["properties"]["type"] == "meeting"


def test_creating_from_an_unknown_schema_is_rejected(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    layer = workspace.workspace.descriptor.layers[0].id

    response = call(
        bridge, "create_note", {"layer_id": layer, "title": "X", "schema_id": "nonsense"}
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


def test_update_note_round_trips_through_the_bridge(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    target = note_id(bridge, "Threat Model")

    data(call(bridge, "update_note", {"note_id": target, "content": "New body.\n"}))
    reread = data(call(bridge, "get_note", {"note_id": target}))

    assert reread["note"]["content"].strip() == "New body."


def test_rename_reports_how_many_links_it_rewrote(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    target = note_id(bridge, "Threat Model")

    response = data(call(bridge, "rename_note", {"note_id": target, "title": "Adversary Model"}))

    assert response["note"]["metadata"]["title"] == "Adversary Model"
    assert response["links_rewritten"] >= 2


def test_delete_and_restore_through_the_bridge(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    target = note_id(bridge, "Marketing Claims")

    deleted = data(call(bridge, "delete_note", {"note_id": target}))
    trash = data(call(bridge, "list_trash"))

    assert len(trash["entries"]) == 1
    assert trash["entries"][0]["title"] == "Marketing Claims"

    restored = data(call(bridge, "restore_note", {"entry": deleted["trash_entry"]}))

    assert restored["note"]["metadata"]["title"] == "Marketing Claims"
    assert data(call(bridge, "list_trash"))["entries"] == []


def test_folder_lifecycle_through_the_bridge(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    layer = workspace.workspace.descriptor.layers[0].id

    created = data(
        call(bridge, "create_folder", {"layer_id": layer, "folder_path": "", "name": "Research"})
    )
    folder_id = created["folder"]["id"]

    renamed = data(call(bridge, "rename_folder", {"folder_id": folder_id, "name": "Field Notes"}))
    assert renamed["folder"]["path"] == "Field Notes"

    removed = data(call(bridge, "delete_folder", {"folder_id": renamed["folder"]["id"]}))
    assert removed["count"] == 0  # the folder was empty


def test_links_expose_backlinks_mentions_and_outgoing(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    target = note_id(bridge, "Threat Model")

    links = data(call(bridge, "get_links", {"note_id": target}))

    sources = {backlink["source_title"] for backlink in links["backlinks"]}
    assert "Encryption Architecture" in sources
    assert any(backlink["relationship"] == "depends_on" for backlink in links["backlinks"])
    assert isinstance(links["unlinked_mentions"], list)


def test_link_health_lists_broken_links(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    layer = workspace.workspace.descriptor.layers[0].id
    data(
        call(
            bridge,
            "create_note",
            {"layer_id": layer, "title": "Dangling", "content": "[[Nowhere At All]]"},
        )
    )

    health = data(call(bridge, "get_link_health"))

    assert "Nowhere At All" in {entry["target"] for entry in health["broken"]}


def test_schemas_are_listed(workspace: Services) -> None:
    schemas = data(call(NotesBridge(workspace), "list_schemas"))["schemas"]

    ids = {schema["id"] for schema in schemas}
    assert {"meeting", "project", "task", "decision", "security-threat"} <= ids


def test_attachments_round_trip_and_return_markdown(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    layer = workspace.workspace.descriptor.layers[0].id
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()

    response = data(
        call(
            bridge,
            "save_attachment",
            {"layer_id": layer, "filename": "diagram.png", "data_base64": payload},
        )
    )

    assert response["path"] == "attachments/diagram.png"
    assert response["markdown"] == "![diagram.png](attachments/diagram.png)"


@pytest.mark.security()
def test_a_malformed_attachment_payload_is_rejected(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    layer = workspace.workspace.descriptor.layers[0].id

    response = call(
        bridge,
        "save_attachment",
        {"layer_id": layer, "filename": "x.png", "data_base64": "not base64!!!"},
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


@pytest.mark.security()
def test_an_attachment_cannot_escape_the_layer(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    layer = workspace.workspace.descriptor.layers[0].id
    payload = base64.b64encode(b"x").decode()

    response = data(
        call(
            bridge,
            "save_attachment",
            {
                "layer_id": layer,
                "filename": "../../../../Windows/System32/evil.exe",
                "data_base64": payload,
            },
        )
    )

    assert response["path"].startswith("attachments/")
    assert ".." not in response["path"]
    assert (workspace.workspace.root / "layers" / layer / response["path"]).is_file()


def test_note_bodies_larger_than_the_cap_are_rejected(workspace: Services) -> None:
    bridge = NotesBridge(workspace)
    target = note_id(bridge, "Threat Model")

    response = call(bridge, "update_note", {"note_id": target, "content": "x" * 600_000})

    assert response["ok"] is False
    assert response["error"]["code"] in ("invalid_request", "payload_too_large")
