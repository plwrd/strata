"""Locality is a property of the endpoint, not of the label on it.

The catalogue says Ollama is local ("Runs on this machine. Nothing leaves it.")
and the policy gate reads that flag to decide whether a layer marked *local AI
only* may be used. But the endpoint is a setting, and a setting can point the
same provider at a machine on the LAN or on the internet.

If the gate trusted the label alone, "local AI only" would be enforced against a
word while the plaintext of a private layer went to another host. These tests
pin the rule that closes that: a provider is local while — and only while — it is
pointed at loopback, and a settings file can only ever hold an endpoint an HTTP
client would actually dial.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.ai import is_local_endpoint
from app.services.container import Services
from app.services.settings_service import AppSettings, SettingsService

pytestmark = pytest.mark.security


# --- the helper -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:1234/v1",
        "http://[::1]:8080",
        "https://127.0.0.1",
    ],
)
def test_loopback_endpoints_are_local(url: str) -> None:
    assert is_local_endpoint(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://gpu-box.example:11434",
        "http://192.168.1.50:11434",  # the LAN is not this machine
        "http://10.0.0.5:11434",
        "https://api.openai.com",
        "http://127.0.0.1.attacker.example",  # the loopback is in the label only
        "http://0x7f000001",  # an obfuscated literal is not a recognised host
        "not a url",
    ],
)
def test_everything_else_is_remote(url: str) -> None:
    assert is_local_endpoint(url) is False


# --- the gate ---------------------------------------------------------------


def test_a_relocated_local_provider_is_reported_as_remote(services: Services) -> None:
    services.settings.update({"provider_base_urls": {"ollama": "http://gpu-box.example:11434"}})

    capabilities = services.ai.capabilities_for("ollama")

    assert capabilities.is_local is False
    assert "gpu-box.example" in capabilities.note
    # And the UI catalogue tells the same story, so the badge cannot say "local"
    # while the gate says otherwise.
    listed = {item.provider_id: item for item in services.ai.catalogue()}
    assert listed["ollama"].is_local is False


def test_a_relocated_local_provider_cannot_take_a_local_only_layer(workspace: Services) -> None:
    """The whole point: a private layer defaults to local-only, and it must not
    be sent to a host that merely has a local-sounding provider id."""
    descriptor, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )
    assert workspace.ai.policy_for([descriptor.id], "ollama").verdict == "allowed"

    workspace.settings.update({"provider_base_urls": {"ollama": "https://ollama.example.com"}})

    decision = workspace.ai.policy_for([descriptor.id], "ollama")

    assert decision.verdict == "denied"
    assert decision.remote is True
    assert "Deals" in decision.reason


def test_the_default_endpoint_stays_local(workspace: Services) -> None:
    descriptor, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )

    assert workspace.ai.policy_for([descriptor.id], "ollama").verdict == "allowed"
    assert workspace.ai.capabilities_for("ollama").is_local is True


def test_routing_never_calls_a_relocated_provider_local(workspace: Services) -> None:
    """`route` prefers a local provider. With every local provider pointed off
    this machine there is no local choice left, and the caller must be told so
    rather than handed a remote one under a local description."""
    workspace.settings.update(
        {
            "provider_base_urls": {
                "ollama": "http://gpu-box.example:11434",
                "llamacpp": "http://gpu-box.example:8080",
                "lmstudio": "http://gpu-box.example:1234",
            }
        }
    )

    _provider_id, reason = workspace.ai.route([], required_tokens=0)

    assert "runs on this machine" not in reason
    assert "is remote" in reason


# --- the setting itself -----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com",
        "javascript:fetch('//x')",
        "http://user:password@example.com",
        "http:///no-host",
    ],
)
def test_a_provider_endpoint_must_be_a_plain_http_url(url: str) -> None:
    with pytest.raises(ValidationError):
        AppSettings(provider_base_urls={"ollama": url})


def test_a_relay_url_must_be_a_plain_http_url() -> None:
    with pytest.raises(ValidationError):
        AppSettings(relay_url="file://relay")
    with pytest.raises(ValidationError):
        AppSettings(relay_url="https://user:secret@relay.example")

    assert AppSettings(relay_url="https://relay.example/").relay_url == "https://relay.example/"


def test_a_rejected_endpoint_does_not_land_on_disk(tmp_path: Path) -> None:
    settings = SettingsService(tmp_path / "settings.json")

    with pytest.raises(ValidationError):
        settings.update({"provider_base_urls": {"ollama": "file:///etc/passwd"}})

    assert settings.settings.provider_base_urls == {}
