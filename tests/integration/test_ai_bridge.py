"""The AI bridge: provider listing, policy checks, credentials, and refusals.

The live streaming path is covered by the provider adapter tests (mocked HTTP) and
the policy tests. Here we drive the bridge slots the frontend calls, and prove the
refusals happen at the boundary — a denied request never reaches a provider.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.bridge.ai_bridge import AIComposerBridge
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


def public_note_ids(workspace: Services, count: int = 2) -> list[str]:
    return [note.metadata.id for note in workspace.notes.list_notes()[:count]]


def test_all_seven_providers_are_listed_with_capabilities(workspace: Services) -> None:
    response = data(call(AIComposerBridge(workspace), "list_providers"))

    ids = {provider["provider_id"] for provider in response["providers"]}
    assert ids == {
        "ollama",
        "llamacpp",
        "lmstudio",
        "openai",
        "anthropic",
        "openai-compatible",
        "claude-cli",
    }
    # Each carries what it can actually do, so the UI can disable the rest.
    ollama = next(p for p in response["providers"] if p["provider_id"] == "ollama")
    assert "streaming" in ollama["capabilities"]
    assert ollama["is_local"] is True


def test_the_claude_cli_is_listed_as_remote(workspace: Services) -> None:
    response = data(call(AIComposerBridge(workspace), "list_providers"))

    claude = next(p for p in response["providers"] if p["provider_id"] == "claude-cli")
    assert claude["is_local"] is False
    assert "anthropic" in claude["note"].lower()


def test_local_providers_are_configured_and_remote_ones_are_not(workspace: Services) -> None:
    response = data(call(AIComposerBridge(workspace), "list_providers"))
    configured = {p["provider_id"]: p["configured"] for p in response["providers"]}

    # Local providers need no key, so they count as configured. Remote ones need a
    # key in the keychain, which the test environment does not have.
    assert configured["ollama"] is True
    assert configured["openai"] is False
    assert configured["anthropic"] is False


def test_policy_check_allows_a_public_selection_to_a_local_model(workspace: Services) -> None:
    response = data(
        call(
            AIComposerBridge(workspace),
            "check_policy",
            {"object_ids": public_note_ids(workspace), "provider_id": "ollama"},
        )
    )

    assert response["verdict"] == "allowed"
    assert response["is_remote"] is False


def test_even_a_public_selection_defaults_to_local_only(workspace: Services) -> None:
    """Secure by default: remote AI is opt-in, even for public content.

    Nothing is sent to a third party until the user changes the layer's policy —
    the default is that your notes stay on your machine.
    """
    response = data(
        call(
            AIComposerBridge(workspace),
            "check_policy",
            {"object_ids": public_note_ids(workspace), "provider_id": "openai"},
        )
    )

    assert response["verdict"] == "denied"
    assert response["is_remote"] is True


def test_a_layer_opted_into_confirmation_needs_confirmation(workspace: Services) -> None:
    from app.domain.layer import LayerAIPolicy

    layer = workspace.workspace.descriptor.layers[0]
    layer.ai_policy = LayerAIPolicy(access="remote-with-confirmation")

    response = data(
        call(
            AIComposerBridge(workspace),
            "check_policy",
            {"object_ids": public_note_ids(workspace), "provider_id": "openai"},
        )
    )

    assert response["verdict"] == "needs_confirmation"
    assert response["is_remote"] is True


def test_policy_check_denies_a_private_selection_to_a_remote_model(workspace: Services) -> None:
    layer, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )
    note = workspace.notes.create_note(
        layer_id=layer.id, folder_path="", title="Secret", content="BLUEJAY"
    )

    response = data(
        call(
            AIComposerBridge(workspace),
            "check_policy",
            {"object_ids": [note.metadata.id], "provider_id": "openai"},
        )
    )

    assert response["verdict"] == "denied"
    assert "Deals" in response["blocking_layers"]
    assert response["private_object_count"] == 1


def test_sending_to_an_unconfigured_provider_is_refused(workspace: Services) -> None:
    response = call(
        AIComposerBridge(workspace),
        "send_request",
        {
            "provider_id": "openai",
            "model": "gpt-4",
            "object_ids": public_note_ids(workspace),
            "prompt": "Summarise",
            "confirmed_remote": True,
        },
    )

    # Public layers need confirmation (given), but the key is missing — either way
    # this must not start a request.
    assert response["ok"] is False
    assert response["error"]["code"] == "permission_denied"


def test_sending_private_content_to_a_remote_model_is_refused_at_the_bridge(
    workspace: Services,
) -> None:
    layer, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )
    note = workspace.notes.create_note(
        layer_id=layer.id, folder_path="", title="Secret", content="BLUEJAY"
    )

    response = call(
        AIComposerBridge(workspace),
        "send_request",
        {
            "provider_id": "openai",
            "model": "gpt-4",
            "object_ids": [note.metadata.id],
            "prompt": "Summarise",
            "confirmed_remote": True,  # even *with* confirmation, local-only wins
        },
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "permission_denied"
    # The refusal names the layer and does not echo the secret.
    assert "BLUEJAY" not in json.dumps(response)


def test_storing_a_credential_reports_whether_the_keychain_took_it(workspace: Services) -> None:
    response = data(
        call(
            AIComposerBridge(workspace),
            "store_credential",
            {"provider_id": "openai", "api_key": "sk-test-not-a-real-key"},
        )
    )

    # In a headless test environment the keychain may be unavailable; either way the
    # response is honest about what happened, and never echoes the key.
    assert isinstance(response["stored"], bool)
    assert "sk-test-not-a-real-key" not in json.dumps(response)


@pytest.mark.security()
def test_a_credential_is_never_echoed_back(workspace: Services) -> None:
    raw = AIComposerBridge(workspace).store_credential(
        json.dumps(
            {
                "v": 1,
                "requestId": "req_test",
                "payload": {"provider_id": "openai", "api_key": "sk-super-secret-value"},
            }
        )
    )

    assert "sk-super-secret-value" not in raw


def test_routing_prefers_a_local_provider(workspace: Services) -> None:
    response = data(
        call(
            AIComposerBridge(workspace),
            "route",
            {"object_ids": public_note_ids(workspace)},
        )
    )

    # Ollama is configured (local, no key) and permitted, so it wins over remote.
    assert response["provider_id"] == "ollama"
    assert "machine" in response["reason"]


def test_receipts_start_empty(workspace: Services) -> None:
    response = data(call(AIComposerBridge(workspace), "privacy_receipts"))
    assert response["receipts"] == []
