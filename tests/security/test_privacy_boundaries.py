"""Privacy behaviour of locked layers.

Private *encryption* arrives in Milestone 3, but the privacy *rules* — what a
locked layer is allowed to contribute to the graph, to search, and to an export —
are enforced by code that exists now, in the service layer. These tests lock a
layer descriptor and assert the rules hold, so that Milestone 3 inherits a tested
boundary instead of inventing one.

If any of these ever fail, a locked layer is leaking.
"""

from __future__ import annotations

import pytest

from app.domain.errors import LayerLockedError
from app.domain.graph import LOCKED_NODE_LABEL
from app.domain.layer import LayerDescriptor
from app.infrastructure.storage.markdown_store import MarkdownLayerStore, now_iso
from app.services.container import Services

pytestmark = pytest.mark.security

SECRET_TITLE = "Acquisition Of Northwind"
SECRET_BODY = "We will offer 4.2 million for Northwind in Q3. Codename: BLUEJAY."
SECRET_TAG = "codename-bluejay"


@pytest.fixture()
def locked_layer(workspace: Services) -> LayerDescriptor:
    """A layer with real content on disk whose descriptor is then locked.

    This simulates exactly the Milestone 3 state that matters: content exists, the
    app knows the layer exists, and the app does not hold the key.
    """
    layer, _recovery = workspace.workspace.create_layer("Deals")
    store = MarkdownLayerStore(layer.id, workspace.workspace.root / "layers" / layer.id)
    store.ensure()
    store.write_note(
        folder_path="M&A",
        title=SECRET_TITLE,
        content=f"{SECRET_BODY}\n\n#{SECRET_TAG}\n\nsupports:: [[Strata Overview]]\n",
        properties={"type": "decision", "status": "confidential"},
    )

    # Locked: the app knows the layer exists and holds no key for it. In Milestone 3
    # `storage` becomes "encrypted-objects"; the privacy rules under test here are
    # enforced from the descriptor and do not depend on that.
    layer.visibility = "private"
    layer.state = "locked"
    layer.updated_at = now_iso()
    return layer


def test_a_locked_layer_is_not_readable(workspace: Services, locked_layer: LayerDescriptor) -> None:
    with pytest.raises(LayerLockedError):
        workspace.workspace.require_readable_layer(locked_layer.id)

    assert locked_layer.id not in [layer.id for layer in workspace.workspace.readable_layers()]


def test_notes_in_a_locked_layer_are_not_listed(
    workspace: Services, locked_layer: LayerDescriptor
) -> None:
    titles = {note.metadata.title for note in workspace.notes.list_notes()}

    assert SECRET_TITLE not in titles
    assert all(note.metadata.layer_id != locked_layer.id for note in workspace.notes.list_notes())


def test_search_reveals_nothing_about_a_locked_layer(
    workspace: Services, locked_layer: LayerDescriptor
) -> None:
    for query in ("Northwind", "BLUEJAY", "acquisition", "4.2 million", SECRET_TAG):
        results = workspace.search.search(query)
        # Not "no matching results": no results at all, no counts, no snippets.
        assert results == [], f"the locked layer answered a search for {query!r}"


def test_search_snippets_never_contain_locked_content(
    workspace: Services, locked_layer: LayerDescriptor
) -> None:
    for result in workspace.search.search("strata"):
        assert SECRET_BODY not in result.snippet
        assert SECRET_TITLE not in result.title


def test_the_graph_shows_a_locked_layer_only_as_a_redacted_marker(
    workspace: Services, locked_layer: LayerDescriptor
) -> None:
    snapshot = workspace.graph.build()

    locked_nodes = [node for node in snapshot.nodes if node.locked]
    assert len(locked_nodes) == 1

    node = locked_nodes[0]
    assert node.label == LOCKED_NODE_LABEL
    assert node.tags == []
    assert node.folder_path == ""
    assert node.word_count == 0
    # Not even how many objects are behind the lock.
    assert node.degree == 0

    assert locked_layer.id in snapshot.locked_layer_ids


def test_no_locked_title_tag_or_body_appears_anywhere_in_the_graph_payload(
    workspace: Services, locked_layer: LayerDescriptor
) -> None:
    payload = workspace.graph.build().model_dump_json()

    assert SECRET_TITLE not in payload
    assert SECRET_BODY not in payload
    assert SECRET_TAG not in payload
    assert "Northwind" not in payload
    assert "M&A" not in payload


def test_a_locked_note_cannot_be_opened_and_the_error_does_not_confirm_it_exists(
    workspace: Services, locked_layer: LayerDescriptor
) -> None:
    from app.domain.errors import NotFoundError
    from app.infrastructure.storage.markdown_store import note_id_for

    # An attacker who *guesses* the id of a note inside the locked layer must get
    # the same answer as for an id that does not exist at all.
    real_id = note_id_for(locked_layer.id, f"M&A/{SECRET_TITLE}.md")

    with pytest.raises(NotFoundError) as guessed:
        workspace.notes.get_note(real_id)
    with pytest.raises(NotFoundError) as absent:
        workspace.notes.get_note("f" * 32)

    assert str(guessed.value) == str(absent.value)


def test_locked_notes_cannot_be_exported(
    workspace: Services, locked_layer: LayerDescriptor
) -> None:
    from app.infrastructure.storage.markdown_store import note_id_for

    public = next(
        note.metadata.id
        for note in workspace.notes.list_notes()
        if note.metadata.title == "Strata Overview"
    )
    locked = note_id_for(locked_layer.id, f"M&A/{SECRET_TITLE}.md")

    plan = workspace.exports.plan(object_ids=[public, locked], prompt="Summarise")

    assert len(plan.sources) == 1
    assert plan.sources[0].title == "Strata Overview"
    assert plan.excluded_locked_count == 1
    assert any("locked" in warning for warning in plan.warnings)

    document = workspace.exports.render(plan).parts[0].content
    assert SECRET_TITLE not in document
    assert SECRET_BODY not in document


def test_graph_expansion_never_crosses_into_a_locked_layer(
    workspace: Services, locked_layer: LayerDescriptor
) -> None:
    """The locked note links to a public one. Expanding from the public note must
    not drag the locked one in, even at two hops."""
    public = next(
        note.metadata.id
        for note in workspace.notes.list_notes()
        if note.metadata.title == "Strata Overview"
    )

    plan = workspace.exports.plan(object_ids=[public], depth="two-hops")

    assert all(not source.is_private for source in plan.sources)
    document = workspace.exports.render(plan).parts[0].content
    assert "Northwind" not in document


def test_private_content_needs_an_explicit_acknowledgement_to_be_exported(
    workspace: Services,
) -> None:
    """An *unlocked* private layer can be exported — but only after a confirmed
    privacy review. The bridge refuses without the acknowledgement."""
    import json

    from app.bridge.export_bridge import ExportBridge

    layer, _recovery = workspace.workspace.create_layer(
        "Research", visibility="private", password="correct horse battery"
    )
    workspace.notes.create_note(
        layer_id=layer.id, folder_path="", title="Private Finding", content="Sensitive result."
    )

    note_id = next(
        note.metadata.id
        for note in workspace.notes.list_notes()
        if note.metadata.title == "Private Finding"
    )

    request = json.dumps(
        {
            "v": 1,
            "requestId": "req_1",
            "payload": {"object_ids": [note_id], "prompt": "Summarise"},
        }
    )
    refused = json.loads(ExportBridge(workspace).render_export(request))

    assert refused["ok"] is False
    assert refused["error"]["code"] == "permission_denied"
    assert refused["error"]["details"]["privateSourceCount"] == 1
    assert "Sensitive result." not in json.dumps(refused)

    acknowledged = json.dumps(
        {
            "v": 1,
            "requestId": "req_2",
            "payload": {
                "object_ids": [note_id],
                "prompt": "Summarise",
                "acknowledge_private": True,
            },
        }
    )
    allowed = json.loads(ExportBridge(workspace).render_export(acknowledged))

    assert allowed["ok"] is True
    assert allowed["data"]["result"]["private_source_count"] == 1
