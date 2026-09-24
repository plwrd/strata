"""Presence goes to the relay sealed, like everything else.

ADR-0006 makes one promise about the relay: it forwards ciphertext and learns
nothing. Awareness blobs used to be the exception — plain JSON carrying the
peer's display name, the note they had open and their cursor offset. That is a
small payload and a large leak: it is exactly the "who is working on what, and
when" that a hosted relay should never be able to watch, and it was also
unauthenticated, so the relay could invent collaborators the UI would then show.

These tests hold both halves: unreadable to the relay, and unforgeable by it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.collaboration import PresencePeer, TreeNode
from app.infrastructure.crdt.relay import LocalRelay
from app.infrastructure.crdt.updates import open_presence, seal_presence
from app.infrastructure.encryption.primitives import DecryptionError, random_key
from app.services.collaboration_service import CollaborationService

pytestmark = pytest.mark.security

LAYER = "layer-1"


def _peer(tmp: Path, key: bytes, relay: LocalRelay) -> CollaborationService:
    seed = ([TreeNode(node_id="n1", name="Note", is_note=True)], {"n1": "text"})
    return CollaborationService(
        key_for=lambda _l: key,
        doc_root_for=lambda _l: tmp,
        ensure_readable=lambda _l: None,
        seed_content=lambda _l: seed,
        relay=relay,
    )


# --- the sealed envelope ----------------------------------------------------


def test_a_sealed_presence_blob_hides_who_is_where() -> None:
    key = random_key()
    payload = PresencePeer(
        peer_id="p1", display_name="Dr Ana Ruiz", active_note_id="note-merger-terms"
    ).model_dump_json()

    blob = seal_presence(key=key, layer_id="L", doc_id="d1", peer_id="p1", payload=payload.encode())

    assert b"Ana Ruiz" not in blob
    assert b"note-merger-terms" not in blob
    assert open_presence(key=key, layer_id="L", doc_id="d1", peer_id="p1", blob=blob) == (
        payload.encode()
    )


def test_a_presence_blob_cannot_be_moved_to_another_peer_slot() -> None:
    """The relay indexes presence by peer. Without the peer id in the AAD it
    could serve one peer's blob in another's slot."""
    key = random_key()
    blob = seal_presence(key=key, layer_id="L", doc_id="d1", peer_id="p1", payload=b"{}")

    with pytest.raises(DecryptionError):
        open_presence(key=key, layer_id="L", doc_id="d1", peer_id="p2", blob=blob)


def test_a_presence_blob_cannot_be_moved_to_another_layer_or_document() -> None:
    key = random_key()
    blob = seal_presence(key=key, layer_id="L", doc_id="d1", peer_id="p1", payload=b"{}")

    with pytest.raises(DecryptionError):
        open_presence(key=key, layer_id="OTHER", doc_id="d1", peer_id="p1", blob=blob)
    with pytest.raises(DecryptionError):
        open_presence(key=key, layer_id="L", doc_id="d2", peer_id="p1", blob=blob)


def test_a_presence_blob_cannot_be_opened_as_a_crdt_update() -> None:
    """Types are bound into the AAD, so the two can never be confused."""
    from app.infrastructure.crdt.updates import open_update

    key = random_key()
    blob = seal_presence(key=key, layer_id="L", doc_id="d1", peer_id="p1", payload=b"{}")

    with pytest.raises(DecryptionError):
        open_update(key=key, layer_id="L", doc_id="d1", blob=blob)


# --- through the service ----------------------------------------------------


def test_nothing_readable_reaches_the_relay(tmp_path: Path) -> None:
    key = random_key()
    relay = LocalRelay()
    alice = _peer(tmp_path / "alice", key, relay)
    alice.share_layer(LAYER, role="owner")
    channel = alice._active[LAYER].channel

    alice.announce(
        LAYER,
        PresencePeer(peer_id="p1", display_name="Dr Ana Ruiz", active_note_id="note-merger-terms"),
    )

    (stored,) = relay.presence(channel).values()
    assert b"Ana Ruiz" not in stored
    assert b"note-merger-terms" not in stored
    assert b"display_name" not in stored

    # And the peer still round-trips for someone holding the key.
    (peer,) = alice.presence(LAYER)
    assert peer.display_name == "Dr Ana Ruiz"


def test_a_forged_peer_from_the_relay_is_not_shown(tmp_path: Path) -> None:
    key = random_key()
    relay = LocalRelay()
    alice = _peer(tmp_path / "alice", key, relay)
    alice.share_layer(LAYER, role="owner")
    channel = alice._active[LAYER].channel

    # The relay (or anyone who can write to it) invents a collaborator.
    relay.announce(channel, "ghost", b'{"peer_id":"ghost","display_name":"IT Support"}')

    assert alice.presence(LAYER) == []


def test_a_peer_sealed_under_another_key_is_not_shown(tmp_path: Path) -> None:
    """A second layer's key is still the wrong key here."""
    key = random_key()
    relay = LocalRelay()
    alice = _peer(tmp_path / "alice", key, relay)
    alice.share_layer(LAYER, role="owner")
    active = alice._active[LAYER]

    relay.announce(
        active.channel,
        "ghost",
        seal_presence(
            key=random_key(),
            layer_id=LAYER,
            doc_id=active.doc_id,
            peer_id="ghost",
            payload=b'{"peer_id":"ghost","display_name":"IT Support"}',
        ),
    )

    assert alice.presence(LAYER) == []


def test_two_peers_still_see_each_other(tmp_path: Path) -> None:
    key = random_key()
    relay = LocalRelay()
    alice = _peer(tmp_path / "alice", key, relay)
    bob = _peer(tmp_path / "bob", key, relay)

    state = alice.share_layer(LAYER, role="owner")
    bob.join_layer(LAYER, state.doc_id or "", role="editor")

    alice.announce(LAYER, PresencePeer(peer_id="p-alice", display_name="Alice"))
    bob.announce(LAYER, PresencePeer(peer_id="p-bob", display_name="Bob"))

    assert {peer.display_name for peer in bob.presence(LAYER)} == {"Alice", "Bob"}


def test_a_locked_layer_shows_no_peers_rather_than_failing(tmp_path: Path) -> None:
    """`status` is polled by the UI and does not re-check readability itself."""
    from app.domain.errors import LayerLockedError

    key = random_key()
    relay = LocalRelay()
    alice = _peer(tmp_path / "alice", key, relay)
    alice.share_layer(LAYER, role="owner")
    alice.announce(LAYER, PresencePeer(peer_id="p1", display_name="Alice"))

    # The layer locks: the key holder no longer has anything to give.
    def locked(_layer_id: str) -> bytes:
        raise LayerLockedError("This layer is locked.")

    alice._key_for = locked  # type: ignore[assignment]

    assert alice.status(LAYER).peers == []
