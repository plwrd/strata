"""The bridge objects end to end, against a real workspace on disk.

These are the Milestone 1 "Python bridge tests" from the Definition of Done: they
drive the same slots the frontend calls, with the same JSON.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.bridge.ai_bridge import AIComposerBridge
from app.bridge.export_bridge import ExportBridge
from app.bridge.graph_bridge import GraphBridge
from app.bridge.layer_bridge import LayerBridge
from app.bridge.notes_bridge import NotesBridge
from app.bridge.search_bridge import SearchBridge
from app.bridge.settings_bridge import SettingsBridge
from app.bridge.workspace_bridge import WorkspaceBridge
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


def test_health_reports_a_live_host(workspace: Services) -> None:
    response = data(call(WorkspaceBridge(workspace), "health"))

    assert response["ok"] is True
    assert response["app"] == "strata"
    assert response["protocol_version"] == 1
    assert response["workspace_open"] is True
    assert response["python_version"].startswith("3.")


def test_get_state_returns_layers_and_lenses(workspace: Services) -> None:
    response = data(call(WorkspaceBridge(workspace), "get_state"))

    assert response["is_open"] is True
    assert len(response["workspace"]["layers"]) == 1
    assert response["workspace"]["layers"][0]["visibility"] == "public"
    assert {lens["name"] for lens in response["lenses"]} >= {"All Knowledge"}


def test_tree_lists_the_seeded_notes(workspace: Services) -> None:
    response = data(call(NotesBridge(workspace), "get_tree"))

    titles = {note["title"] for note in response["notes"]}
    assert "Encryption Architecture" in titles
    assert "Threat Model" in titles
    assert {folder["name"] for folder in response["folders"]} >= {"Security", "Architecture"}
    assert response["locked_layer_ids"] == []


def test_get_note_returns_real_markdown_from_disk(workspace: Services) -> None:
    notes = data(call(NotesBridge(workspace), "get_tree"))["notes"]
    target = next(note for note in notes if note["title"] == "Encryption Architecture")

    response = data(call(NotesBridge(workspace), "get_note", {"note_id": target["id"]}))

    assert "Argon2id" in response["note"]["content"]
    assert response["note"]["metadata"]["title"] == "Encryption Architecture"
    assert "encryption" in response["note"]["metadata"]["tags"]


def test_missing_note_is_not_found(workspace: Services) -> None:
    response = call(NotesBridge(workspace), "get_note", {"note_id": "0" * 32})

    assert response["ok"] is False
    assert response["error"]["code"] == "not_found"


def test_graph_has_nodes_edges_and_typed_relationships(workspace: Services) -> None:
    graph = data(call(GraphBridge(workspace), "load_graph"))["graph"]

    assert graph["total_nodes"] > 8
    assert graph["total_edges"] > 8
    relationships = {edge["relationship"] for edge in graph["edges"]}
    # The seeded notes use typed links (`supports::`, `contradicts::`).
    assert "supports" in relationships
    assert "contradicts" in relationships
    assert graph["locked_layer_ids"] == []


def test_graph_edges_reference_existing_nodes(workspace: Services) -> None:
    graph = data(call(GraphBridge(workspace), "load_graph"))["graph"]
    ids = {node["id"] for node in graph["nodes"]}

    for edge in graph["edges"]:
        assert edge["source"] in ids
        assert edge["target"] in ids


def test_search_explains_why_a_result_matched(workspace: Services) -> None:
    response = data(call(SearchBridge(workspace), "search", {"query": "encryption"}))

    assert response["total"] >= 1
    top = response["results"][0]
    assert top["reasons"], "a result with no explanation is not a result"
    assert response["locked_layers_excluded"] == 0


def test_search_with_no_matches_is_empty_not_an_error(workspace: Services) -> None:
    response = data(call(SearchBridge(workspace), "search", {"query": "zzzznotathing"}))
    assert response["results"] == []


def test_creating_a_private_layer_is_refused_honestly(workspace: Services) -> None:
    response = call(
        LayerBridge(workspace),
        "create_layer",
        {"display_name": "Research", "visibility": "private"},
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "unsupported"
    assert response["error"]["details"]["milestone"] == 3


def test_unlock_is_refused_rather_than_faked(workspace: Services) -> None:
    response = call(
        LayerBridge(workspace),
        "unlock_layer",
        {"layer_id": "layer_x", "password": "hunter2"},
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "unsupported"


@pytest.mark.security()
def test_a_password_never_appears_in_a_response(workspace: Services) -> None:
    raw = LayerBridge(workspace).unlock_layer(
        json.dumps(
            {
                "v": 1,
                "requestId": "req_test",
                "payload": {"layer_id": "layer_x", "password": "correct horse battery staple"},
            }
        )
    )

    assert "correct horse" not in raw
    assert "battery" not in raw


def test_public_layer_can_be_created_and_renamed(workspace: Services) -> None:
    created = data(call(LayerBridge(workspace), "create_layer", {"display_name": "Scratch"}))
    layer_id = created["layer"]["id"]

    renamed = data(
        call(
            LayerBridge(workspace),
            "rename_layer",
            {"layer_id": layer_id, "display_name": "Scratchpad"},
        )
    )

    assert renamed["layer"]["display_name"] == "Scratchpad"
    assert len(data(call(LayerBridge(workspace), "list_layers"))["layers"]) == 2


def test_providers_are_listed_but_none_are_configured_yet(workspace: Services) -> None:
    response = data(call(AIComposerBridge(workspace), "list_providers"))

    assert response["any_configured"] is False
    ids = {provider["provider_id"] for provider in response["providers"]}
    assert {"ollama", "openai", "anthropic", "claude-cli"} <= ids

    # The Claude CLI runs locally but sends data to Anthropic: it must never be
    # labelled local, or a user will reach for it expecting privacy.
    claude_cli = next(p for p in response["providers"] if p["provider_id"] == "claude-cli")
    assert claude_cli["is_local"] is False


def test_sending_to_a_provider_is_refused_in_milestone_1(workspace: Services) -> None:
    response = call(
        AIComposerBridge(workspace),
        "send_request",
        {
            "provider_id": "openai",
            "model": "gpt-4",
            "object_ids": ["a" * 32],
            "prompt": "hello",
        },
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "unsupported"
    assert response["error"]["details"]["milestone"] == 7


def test_plan_context_summarises_what_would_be_sent(workspace: Services) -> None:
    notes = data(call(NotesBridge(workspace), "get_tree"))["notes"]
    ids = [note["id"] for note in notes[:3]]

    plan = data(
        call(
            AIComposerBridge(workspace),
            "plan_context",
            {"object_ids": ids, "prompt": "Summarise these", "target": "chatgpt"},
        )
    )["plan"]

    assert len(plan["sources"]) == 3
    assert plan["estimated_tokens"] > 0
    assert plan["private_source_count"] == 0
    assert [source["source_id"] for source in plan["sources"]] == [
        "STRATA-SOURCE-001",
        "STRATA-SOURCE-002",
        "STRATA-SOURCE-003",
    ]


def test_plan_context_requires_a_selection(workspace: Services) -> None:
    response = call(AIComposerBridge(workspace), "plan_context", {"object_ids": []})

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


def test_render_export_produces_markdown(workspace: Services) -> None:
    notes = data(call(NotesBridge(workspace), "get_tree"))["notes"]
    ids = [note["id"] for note in notes[:2]]

    result = data(
        call(
            ExportBridge(workspace),
            "render_export",
            {"object_ids": ids, "prompt": "Analyse this", "target": "generic"},
        )
    )["result"]

    assert len(result["parts"]) == 1
    content = result["parts"][0]["content"]
    assert "# User Prompt" in content
    assert "Analyse this" in content
    assert "STRATA-SOURCE-001" in content


def test_settings_round_trip(workspace: Services) -> None:
    updated = data(
        call(
            SettingsBridge(workspace),
            "update_settings",
            {"values": {"motion": "reduced", "graph_quality": "low-gpu"}},
        )
    )["settings"]

    assert updated["motion"] == "reduced"
    assert updated["graph_quality"] == "low-gpu"

    reloaded = data(call(SettingsBridge(workspace), "get_settings"))["settings"]
    assert reloaded["motion"] == "reduced"


def test_unknown_settings_keys_are_ignored(workspace: Services) -> None:
    settings = data(
        call(
            SettingsBridge(workspace),
            "update_settings",
            {"values": {"motion": "reduced", "is_admin": True}},
        )
    )["settings"]

    assert "is_admin" not in settings
