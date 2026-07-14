"""Index and embedding privacy.

An index is the layer's content in another shape, and an embedding is a lossy but
very informative encoding of the text it came from. Both must obey exactly the same
rules as the notes themselves:

* a private layer's index is **never written to disk** — not to SQLite, not to a
  journal, not to a temp file;
* both are dropped when the layer locks;
* a locked layer contributes no results, no counts, no snippets, and no clusters.

These tests exist because "the notes are encrypted" is worth nothing if the search
index next to them is plaintext.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.container import Paths, Services

pytestmark = pytest.mark.security

PASSWORD = "correct horse battery staple"
SECRET_TITLE = "Acquisition Of Northwind"
SECRET_BODY = "We will offer 4.2 million for Northwind in Q3. Codename BLUEJAY."
MARKERS = ["Northwind", "BLUEJAY", "Acquisition", "4.2 million"]


@pytest.fixture()
def private_workspace(services: Services) -> tuple[Services, str, str]:
    services.workspace.open_or_create(services.paths.default_workspace, "Test")
    layer, _recovery = services.workspace.create_layer(
        "Deals", visibility="private", password=PASSWORD
    )
    note = services.notes.create_note(
        layer_id=layer.id,
        folder_path="",
        title=SECRET_TITLE,
        content=f"{SECRET_BODY}\n\n#bluejay\n",
    )
    return services, layer.id, note.metadata.id


def every_byte_in(root: Path) -> bytes:
    return b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())


def test_searching_a_private_layer_writes_no_index_to_disk(
    private_workspace: tuple[Services, str, str],
) -> None:
    services, layer_id, _note_id = private_workspace

    # Force the index to be built by actually searching it.
    results = services.search.search("Northwind")
    assert [result.title for result in results] == [SECRET_TITLE]

    index_dir = services.workspace.root / ".strata" / "index"
    if index_dir.exists():
        # The public layer may have written its own index. The private one must not
        # have — and no file anywhere may contain the secret.
        assert not (index_dir / f"{layer_id}.sqlite").exists()

    # The strongest form: no byte anywhere under the workspace, outside the
    # encrypted objects, contains the secret.
    everything = every_byte_in(services.workspace.root)
    for marker in MARKERS:
        assert marker.encode() not in everything


def test_the_private_index_disappears_when_the_layer_locks(
    private_workspace: tuple[Services, str, str],
) -> None:
    services, layer_id, _note_id = private_workspace

    assert services.search.search("Northwind")
    assert layer_id in services.search.index_sizes()

    services.workspace.lock_layer(layer_id)

    # The index is gone entirely — not emptied, not filtered: absent.
    assert layer_id not in services.search.index_sizes()
    assert services.search.search("Northwind") == []
    assert services.search.search("BLUEJAY") == []


def test_a_locked_layer_contributes_no_clusters(
    private_workspace: tuple[Services, str, str],
) -> None:
    services, layer_id, note_id = private_workspace

    unlocked_clusters = services.search.clusters(count=2)
    assert note_id in unlocked_clusters

    services.workspace.lock_layer(layer_id)

    locked_clusters = services.search.clusters(count=2)
    assert note_id not in locked_clusters


def test_similar_to_cannot_reach_into_a_locked_layer(
    private_workspace: tuple[Services, str, str],
) -> None:
    services, layer_id, note_id = private_workspace
    public_note = next(
        note.metadata.id
        for note in services.notes.list_notes()
        if note.metadata.title == "Encryption Architecture"
    )

    services.workspace.lock_layer(layer_id)

    similar = services.search.similar_to(public_note, limit=20)

    assert all(result.layer_id != layer_id for result in similar)
    assert all(result.title != SECRET_TITLE for result in similar)
    assert services.search.similar_to(note_id, limit=5) == []


def test_index_status_omits_a_locked_layer_rather_than_reporting_zero(
    private_workspace: tuple[Services, str, str],
) -> None:
    """ "0 documents" and "no such layer" must be indistinguishable from outside."""
    services, layer_id, _note_id = private_workspace
    services.search.search("Northwind")

    services.workspace.lock_layer(layer_id)

    sizes = services.search.index_sizes()
    assert layer_id not in sizes
    assert 0 not in {sizes.get(layer_id, -1)}


def test_relocking_and_unlocking_rebuilds_the_index(
    private_workspace: tuple[Services, str, str],
) -> None:
    services, layer_id, _note_id = private_workspace
    services.search.search("Northwind")

    services.workspace.lock_layer(layer_id)
    assert services.search.search("Northwind") == []

    services.workspace.unlock_layer(layer_id, PASSWORD)

    results = services.search.search("Northwind")
    assert [result.title for result in results] == [SECRET_TITLE]


def test_closing_the_workspace_drops_every_private_index(
    private_workspace: tuple[Services, str, str], paths: Paths
) -> None:
    services, layer_id, _note_id = private_workspace
    services.search.search("Northwind")

    services.workspace.close()

    assert layer_id not in services.search.index_sizes()

    # And a fresh process cannot search it either, until it is unlocked.
    reopened = Services(paths, environment="test")
    reopened.workspace.open(paths.default_workspace)
    assert reopened.search.search("Northwind") == []
