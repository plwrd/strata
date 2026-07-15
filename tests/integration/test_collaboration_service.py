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
