"""Two peers share a layer through a relay, converge, and survive conflicts.

Both peers run in-process with the *same* layer key (as they would after an
invite shares the LDK) and the *same* relay, but separate on-disk document roots
— standing in for two devices. No plaintext crosses the relay.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.collaboration import PresencePeer, TreeNode
from app.domain.errors import PermissionDeniedError
from app.infrastructure.crdt.relay import LocalRelay
from app.infrastructure.encryption.primitives import random_key
from app.services.collaboration_service import CollaborationService

LAYER = "layer-1"


def _make_peer(
    tmp: Path, key: bytes, relay: LocalRelay, seed: tuple[list[TreeNode], dict[str, str]]
) -> CollaborationService:
    return CollaborationService(
        key_for=lambda _l: key,
        doc_root_for=lambda _l: tmp,
        ensure_readable=lambda _l: None,
        seed_content=lambda _l: seed,
        relay=relay,
    )


def test_share_join_and_converge(tmp_path: Path) -> None:
    key = random_key()
    relay = LocalRelay()
    seed = (
        [
            TreeNode(node_id="root", name="Root", is_note=False),
            TreeNode(node_id="n1", name="Shared note", parent="root", is_note=True),
        ],
        {"n1": "initial text"},
    )

    alice = _make_peer(tmp_path / "alice", key, relay, seed)
    bob = _make_peer(tmp_path / "bob", key, relay, ([], {}))

    state = alice.share_layer(LAYER, role="owner")
    assert state.enabled and state.mode == "shared"

    # Bob joins the same document and catches up from the relay.
    bob.join_layer(LAYER, state.doc_id or "", role="editor")
    bob_active = bob._active[LAYER]
    assert bob_active.document.body("n1") == "initial text"

    # Alice edits; Bob syncs; they converge.
    alice.set_body(LAYER, "n1", "edited by Alice")
    bob.sync(LAYER)
    assert bob._active[LAYER].document.body("n1") == "edited by Alice"


def test_a_viewer_cannot_edit(tmp_path: Path) -> None:
    key = random_key()
    relay = LocalRelay()
    seed = ([TreeNode(node_id="n1", name="N", is_note=True)], {"n1": "x"})
    alice = _make_peer(tmp_path / "a", key, relay, seed)
    viewer = _make_peer(tmp_path / "v", key, relay, ([], {}))

    state = alice.share_layer(LAYER)
    viewer.join_layer(LAYER, state.doc_id or "", role="viewer")

    with pytest.raises(PermissionDeniedError):
        viewer.set_body(LAYER, "n1", "sneaky edit")


def test_move_vs_delete_is_rescued_across_peers(tmp_path: Path) -> None:
    key = random_key()
    relay = LocalRelay()
    seed = (
        [
            TreeNode(node_id="F", name="Research", is_note=False),
            TreeNode(node_id="N", name="Interview", parent=None, is_note=True),
        ],
        {"N": "notes"},
    )
    alice = _make_peer(tmp_path / "a", key, relay, seed)
    bob = _make_peer(tmp_path / "b", key, relay, ([], {}))

    state = alice.share_layer(LAYER)
    bob.join_layer(LAYER, state.doc_id or "", role="editor")

    # Alice moves N into F; Bob deletes F; both sync.
    alice._active[LAYER].document.move_node("N", "F")
    alice.set_body(LAYER, "N", "notes")  # produces an update carrying the move
    bob._active[LAYER].document.mark_deleted("F")
    bob.apply_local_update(LAYER, bob._active[LAYER].document.encode_update())

    conflicts = alice.sync(LAYER)
    bob.sync(LAYER)

    # A conflict was surfaced, and N was rescued (not lost) on Alice's side.
    assert any(c.kind == "move_vs_delete" for c in alice.conflicts(LAYER)) or any(
        c.kind == "move_vs_delete" for c in conflicts
    )
    node = alice._active[LAYER].document.node("N")
    assert node is not None
    assert alice._active[LAYER].document.body("N") == "notes"  # data intact


def test_presence_round_trips(tmp_path: Path) -> None:
    key = random_key()
    relay = LocalRelay()
    seed = ([TreeNode(node_id="n1", name="N", is_note=True)], {"n1": "x"})
    alice = _make_peer(tmp_path / "a", key, relay, seed)
    alice.share_layer(LAYER)

    alice.announce(LAYER, PresencePeer(peer_id="p1", display_name="Alice", active_note_id="n1"))
    peers = alice.presence(LAYER)
    assert [p.display_name for p in peers] == ["Alice"]


def test_locking_forgets_the_document(tmp_path: Path) -> None:
    key = random_key()
    relay = LocalRelay()
    seed = ([TreeNode(node_id="n1", name="N", is_note=True)], {"n1": "x"})
    alice = _make_peer(tmp_path / "a", key, relay, seed)
    alice.share_layer(LAYER)

    alice.forget_layer(LAYER)
    assert alice.status(LAYER).enabled is False


def test_compaction_keeps_content(tmp_path: Path) -> None:
    key = random_key()
    relay = LocalRelay()
    seed = ([TreeNode(node_id="n1", name="N", is_note=True)], {"n1": "x"})
    alice = _make_peer(tmp_path / "a", key, relay, seed)
    alice.share_layer(LAYER)
    for i in range(10):
        alice.set_body(LAYER, "n1", f"revision {i}")

    reclaimed = alice.compact(LAYER)
    assert reclaimed >= 1
    assert alice._active[LAYER].document.body("n1") == "revision 9"


def test_a_local_edit_before_sync_does_not_drop_earlier_peer_updates(
    tmp_path: Path,
) -> None:
    """Regression: publishing locally must not skip unfetched remote updates."""
    key = random_key()
    relay = LocalRelay()
    seed = ([TreeNode(node_id="n1", name="N", is_note=True)], {"n1": "base"})
    alice = _make_peer(tmp_path / "a", key, relay, seed)
    bob = _make_peer(tmp_path / "b", key, relay, ([], {}))

    state = alice.share_layer(LAYER)
    bob.join_layer(LAYER, state.doc_id or "", role="editor")

    # Bob publishes an edit that Alice has NOT fetched yet.
    bob.set_body(LAYER, "n1", "from Bob")
    # Alice makes her own local edit (this publishes, and must not jump her cursor
    # past Bob's still-unfetched update).
    alice.set_body(LAYER, "n1", "base and Alice")
    # Alice syncs: she must still receive Bob's earlier update and converge.
    alice.sync(LAYER)
    bob.sync(LAYER)

    assert alice._active[LAYER].document.body("n1") == bob._active[LAYER].document.body("n1")


def test_confirmed_delete_stays_deleted(tmp_path: Path) -> None:
    """Regression: 'confirm delete' must not resurrect the note on next reconcile."""
    key = random_key()
    relay = LocalRelay()
    seed = (
        [
            TreeNode(node_id="F", name="Research", is_note=False),
            TreeNode(node_id="N", name="Interview", parent=None, is_note=True),
        ],
        {"N": "notes"},
    )
    alice = _make_peer(tmp_path / "a", key, relay, seed)
    bob = _make_peer(tmp_path / "b", key, relay, ([], {}))
    state = alice.share_layer(LAYER)
    bob.join_layer(LAYER, state.doc_id or "", role="editor")

    # Manufacture an edit-vs-delete conflict: Alice edits while Bob deletes.
    alice.set_body(LAYER, "N", "edited")
    bob.delete_node(LAYER, "N")  # the deleter acknowledges; it is not self-rescued
    bob.sync(LAYER)
    alice.sync(LAYER)

    edit_delete = [c for c in alice.conflicts(LAYER) if c.kind == "edit_vs_delete"]
    assert edit_delete, "expected an edit-vs-delete conflict to rescue Alice's edit"

    # Confirm the delete, then keep working — it must stay deleted.
    alice.resolve_conflict(LAYER, edit_delete[0].conflict_id, "confirm_delete")
    alice.sync(LAYER)
    alice.set_body(LAYER, "n-other", "trigger a reconcile")

    resurrected = [
        c for c in alice.conflicts(LAYER) if c.kind == "edit_vs_delete" and "N" in c.node_ids
    ]
    assert not resurrected, "confirmed delete resurrected as a new conflict"


def test_deleting_a_note_with_content_actually_deletes_it(tmp_path: Path) -> None:
    """Regression: a plain delete must not be un-done by the deleter's rescue."""
    key = random_key()
    relay = LocalRelay()
    seed = ([TreeNode(node_id="N", name="N", is_note=True)], {"N": "has content"})
    alice = _make_peer(tmp_path / "a", key, relay, seed)
    alice.share_layer(LAYER)

    alice.delete_node(LAYER, "N")
    alice.sync(LAYER)

    node = alice._active[LAYER].document.node("N")
    assert node is not None and node.deleted, "note was resurrected by its own reconcile"
    assert not any(c.kind == "edit_vs_delete" for c in alice.conflicts(LAYER))
