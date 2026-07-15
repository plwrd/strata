"""Workspace snapshots: capture, restore, and the privacy of a snapshotted layer."""

from __future__ import annotations

import pytest

from app.services.container import Paths, Services

pytestmark = []


def test_a_snapshot_captures_the_current_notes(workspace: Services) -> None:
    info = workspace.snapshots.create("Checkpoint")

    assert info.name == "Checkpoint"
    assert info.note_count > 0
    assert workspace.snapshots.list()[0].id == info.id


def test_restoring_brings_back_a_deleted_note(workspace: Services) -> None:
    before = {note.metadata.title for note in workspace.notes.list_notes()}
    snapshot = workspace.snapshots.create("Before delete")

    workspace.notes.delete_note(
        next(
            n.metadata.id
            for n in workspace.notes.list_notes()
            if n.metadata.title == "Threat Model"
        )
    )
    assert "Threat Model" not in {n.metadata.title for n in workspace.notes.list_notes()}

    workspace.snapshots.restore(snapshot.id, take_safety_snapshot=False)

    assert {note.metadata.title for note in workspace.notes.list_notes()} == before


def test_restore_takes_a_safety_snapshot_by_default(workspace: Services) -> None:
    first = workspace.snapshots.create("First")
    workspace.notes.create_note(
        layer_id=workspace.workspace.descriptor.layers[0].id, folder_path="", title="Added"
    )

    workspace.snapshots.restore(first.id)

    # Restoring is itself undoable: a pre-restore snapshot now exists.
    kinds = {info.kind for info in workspace.snapshots.list()}
    assert "pre-restore" in kinds


def test_deleting_a_snapshot_removes_it(workspace: Services) -> None:
    info = workspace.snapshots.create("Temporary")

    workspace.snapshots.delete(info.id)

    assert all(snapshot.id != info.id for snapshot in workspace.snapshots.list())


@pytest.mark.security()
def test_a_snapshot_of_a_private_layer_stores_only_ciphertext(
    services: Services, paths: Paths
) -> None:
    """Snapshotting an *unlocked* private layer must not write its plaintext."""
    services.workspace.open_or_create(paths.default_workspace, "Test")
    layer, _recovery = services.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )
    services.notes.create_note(
        layer_id=layer.id, folder_path="", title="Acquisition", content="BLUEJAY 4.2 million"
    )

    info = services.snapshots.create("With a secret")
    snapshot_root = services.snapshots.snapshot_root(info.id)

    blob = b"".join(path.read_bytes() for path in snapshot_root.rglob("*") if path.is_file())
    assert b"BLUEJAY" not in blob
    assert b"Acquisition" not in blob
    assert b"4.2 million" not in blob


@pytest.mark.security()
def test_restoring_locks_private_layers(services: Services, paths: Paths) -> None:
    """After a restore the objects underneath are new, so cached decrypted views
    must be dropped — the layer relocks."""
    services.workspace.open_or_create(paths.default_workspace, "Test")
    layer, _recovery = services.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )
    services.notes.create_note(layer_id=layer.id, folder_path="", title="Secret", content="content")
    snapshot = services.snapshots.create("checkpoint")

    assert services.encryption.is_unlocked(layer.id)

    services.snapshots.restore(snapshot.id, take_safety_snapshot=False)

    assert not services.encryption.is_unlocked(layer.id)
