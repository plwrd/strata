"""Scoped retrieval — ranked, bounded, and permission-clean by construction."""

from __future__ import annotations

from app.services.container import Services


def test_retrieval_finds_the_relevant_notes(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id
    workspace.notes.create_note(
        layer_id=layer,
        title="Kestrel migration notes",
        content="The kestrel population migrates through the valley in autumn.",
    )

    note_ids = workspace.retrieval.retrieve("kestrel migration autumn")

    notes = {n.metadata.id: n for n in workspace.notes.list_notes()}
    titles = [notes[nid].metadata.title for nid in note_ids if nid in notes]
    assert "Kestrel migration notes" in titles


def test_retrieval_is_bounded(workspace: Services) -> None:
    assert len(workspace.retrieval.retrieve("the", limit=3)) <= 3
    # An absurd limit is capped, not honoured.
    assert len(workspace.retrieval.retrieve("the", limit=10_000)) <= 25


def test_locked_layers_are_never_retrieved(workspace: Services) -> None:
    private, _recovery = workspace.workspace.create_layer(
        "Vault", visibility="private", password="a sound passphrase"
    )
    workspace.notes.create_note(
        layer_id=private.id,
        title="Secret zanzibar plans",
        content="The zanzibar operation is confidential.",
    )
    # Unlocked: the private note is retrievable (the user can read it).
    assert workspace.retrieval.retrieve("zanzibar operation") != []

    workspace.workspace.lock_layer(private.id)

    assert workspace.retrieval.retrieve("zanzibar operation") == []


def test_an_unanswerable_query_returns_nothing(workspace: Services) -> None:
    assert workspace.retrieval.retrieve("xyzzy plugh frobnicate") == []
