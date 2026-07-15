"""The AI policy gate.

This is the control that decides whether a user's notes may reach a model. If it is
wrong, private content goes to a third party. Every rule in it is tested here.

The rule that matters most, and the one easiest to get wrong: **the Claude CLI runs
locally and is not local.** It sends content to Anthropic. A layer marked "local AI
only" must refuse it, and a user who picks it *because* they believe it is private
must be told otherwise.
"""

from __future__ import annotations

import asyncio

import pytest

from app.domain.ai import (
    Capability,
    ProviderCapabilities,
    evaluate_policy,
)
from app.domain.errors import PermissionDeniedError
from app.domain.layer import LayerAIPolicy, LayerDescriptor
from app.infrastructure.ai_providers.anthropic import ANTHROPIC
from app.infrastructure.ai_providers.claude_cli import CLAUDE_CLI
from app.infrastructure.ai_providers.openai_compatible import OLLAMA, OPENAI
from app.services.container import Services

pytestmark = pytest.mark.security


def layer(
    name: str,
    *,
    private: bool = False,
    locked: bool = False,
    access: str = "remote-always",
    embeddings: str = "remote-allowed",
) -> LayerDescriptor:
    return LayerDescriptor(
        id=f"layer_{name}",
        display_name=name,
        visibility="private" if private else "public",
        state="locked" if locked else ("unlocked" if private else "mounted"),
        created_at="",
        updated_at="",
        ai_policy=LayerAIPolicy(access=access, embeddings=embeddings),  # type: ignore[arg-type]
    )


# --- the honest labelling of the Claude CLI ---------------------------------


def test_the_claude_cli_is_not_local() -> None:
    """It runs on your machine. It sends your content to Anthropic."""
    assert CLAUDE_CLI.is_local is False
    assert "anthropic" in CLAUDE_CLI.note.lower()
    assert "not an offline model" in CLAUDE_CLI.note.lower()


def test_a_local_only_layer_refuses_the_claude_cli() -> None:
    decision = evaluate_policy([layer("Research", access="local-only")], CLAUDE_CLI)

    assert decision.verdict == "denied"
    assert decision.remote is True
    # And it explains the trap explicitly, because this is the one people fall into.
    assert "runs locally but sends" in decision.reason


def test_ollama_is_local_and_openai_is_not() -> None:
    assert OLLAMA.is_local is True
    assert OPENAI.is_local is False
    assert ANTHROPIC.is_local is False


# --- locked layers ----------------------------------------------------------


def test_a_locked_layer_is_denied_even_to_a_local_model() -> None:
    decision = evaluate_policy([layer("Deals", private=True, locked=True)], OLLAMA)

    assert decision.verdict == "denied"
    assert decision.blocking_layers == ["Deals"]
    assert "locked" in decision.reason.lower()


def test_a_locked_layer_denies_the_whole_request_not_just_its_own_part() -> None:
    """One locked layer in the selection blocks it. Partial sends are how content
    leaks: the user asked about *these* notes, not a subset chosen for them."""
    decision = evaluate_policy([layer("Public"), layer("Deals", private=True, locked=True)], OLLAMA)

    assert decision.verdict == "denied"


# --- disabled and local-only ------------------------------------------------


def test_ai_disabled_denies_every_provider() -> None:
    for provider in (OLLAMA, OPENAI, ANTHROPIC, CLAUDE_CLI):
        decision = evaluate_policy([layer("Journal", access="disabled")], provider)
        assert decision.verdict == "denied", provider.provider_id


def test_local_only_allows_a_local_provider_and_denies_a_remote_one() -> None:
    layers = [layer("Research", access="local-only")]

    assert evaluate_policy(layers, OLLAMA).verdict == "allowed"
    assert evaluate_policy(layers, OPENAI).verdict == "denied"
    assert evaluate_policy(layers, ANTHROPIC).verdict == "denied"


def test_remote_with_confirmation_needs_confirmation() -> None:
    layers = [layer("Notes", access="remote-with-confirmation")]

    decision = evaluate_policy(layers, OPENAI)

    assert decision.verdict == "needs_confirmation"
    assert decision.remote is True
    # A local provider needs no confirmation: nothing is leaving.
    assert evaluate_policy(layers, OLLAMA).verdict == "allowed"


def test_remote_always_allows_without_confirmation() -> None:
    decision = evaluate_policy([layer("Public", access="remote-always")], OPENAI)

    assert decision.verdict == "allowed"


def test_the_strictest_layer_in_the_selection_wins() -> None:
    """A selection spanning several layers gets the most restrictive answer."""
    layers = [
        layer("Public", access="remote-always"),
        layer("Research", access="local-only"),
    ]

    assert evaluate_policy(layers, OPENAI).verdict == "denied"
    assert evaluate_policy(layers, OLLAMA).verdict == "allowed"


# --- embeddings -------------------------------------------------------------


def test_embeddings_have_their_own_policy() -> None:
    layers = [layer("Research", access="remote-always", embeddings="local-only")]

    # The layer allows remote text generation but not remote embeddings. An
    # embedding is content, so it gets its own decision.
    assert evaluate_policy(layers, OPENAI).verdict == "allowed"
    assert evaluate_policy(layers, OPENAI, for_embeddings=True).verdict == "denied"


def test_disabled_embeddings_deny_even_a_local_provider() -> None:
    layers = [layer("Journal", embeddings="disabled")]

    decision = evaluate_policy(layers, OLLAMA, for_embeddings=True)

    assert decision.verdict == "denied"


# --- through the service ----------------------------------------------------


def test_a_private_layer_defaults_to_local_only(workspace: Services) -> None:
    layer_descriptor, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )

    decision = workspace.ai.policy_for([layer_descriptor.id], "openai")

    assert decision.verdict == "denied"
    assert workspace.ai.policy_for([layer_descriptor.id], "ollama").verdict == "allowed"


def test_the_service_asks_the_key_holder_not_the_descriptor(workspace: Services) -> None:
    """The descriptor's `state` is JSON. The key holder is the truth."""
    layer_descriptor, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )
    workspace.workspace.lock_layer(layer_descriptor.id)

    # Lie to the descriptor, exactly as a corrupted file or a bug would.
    layer_descriptor.state = "unlocked"

    decision = workspace.ai.policy_for([layer_descriptor.id], "ollama")

    assert decision.verdict == "denied"
    assert "locked" in decision.reason.lower()


@pytest.mark.asyncio()
async def test_run_refuses_a_denied_request_before_touching_a_provider(
    workspace: Services,
) -> None:
    layer_descriptor, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )

    async def attempt() -> None:
        async for _event in workspace.ai.run(
            provider_id="openai",
            model="gpt-4",
            prompt="Summarise",
            sources="secret",
            layer_ids=[layer_descriptor.id],
        ):
            pass

    with pytest.raises(PermissionDeniedError):
        await attempt()


def test_run_refuses_synchronously_without_a_network_call(workspace: Services) -> None:
    """No API key is configured, so if the gate leaked we would see a network error
    rather than a permission error. Getting `permission_denied` proves the check
    happened first."""
    layer_descriptor, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )

    async def attempt() -> None:
        async for _event in workspace.ai.run(
            provider_id="anthropic",
            model="claude-opus-4-8",
            prompt="Summarise",
            sources="secret",
            layer_ids=[layer_descriptor.id],
        ):
            pass

    with pytest.raises(PermissionDeniedError) as error:
        asyncio.run(attempt())

    assert "running on this machine" in error.value.message.lower()
    assert "Deals" in error.value.message


# --- prompt-injection framing -----------------------------------------------


def test_every_request_frames_sources_as_untrusted_data() -> None:
    from app.services.ai_service import UNTRUSTED_PREAMBLE

    lowered = UNTRUSTED_PREAMBLE.lower()

    assert "data, not instructions" in lowered
    assert "ignore previous instructions" in lowered  # names the attack explicitly
    assert (
        "not obeyed" in lowered
        or "not to be obeyed" in lowered
        or "reported, not obeyed" in lowered
    )


# --- capabilities -----------------------------------------------------------


def test_a_provider_that_cannot_stream_is_not_offered_streaming() -> None:
    silent = ProviderCapabilities(
        provider_id="silent",
        display_name="Silent",
        is_local=True,
        capabilities=[Capability.TEXT],
    )

    assert silent.supports(Capability.STREAMING) is False
    assert OLLAMA.supports(Capability.STREAMING) is True


def test_only_providers_that_declare_embeddings_offer_them() -> None:
    assert OLLAMA.supports(Capability.EMBEDDINGS) is True
    assert ANTHROPIC.supports(Capability.EMBEDDINGS) is False
