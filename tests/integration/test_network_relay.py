"""The HTTP relay forwards ciphertext and lets two peers converge over it.

The relay server is exercised in-process through httpx's WSGI transport (no real
socket), and the HttpRelay client talks to it exactly as it would over the wire.
The security assertion is the one that matters: only ciphertext crosses the
relay, and the server can produce nothing else.
"""

from __future__ import annotations

import httpx
import pytest

from app.domain.collaboration import PresencePeer, TreeNode
from app.infrastructure.crdt.document import LayerDocument
from app.infrastructure.crdt.relay import HttpRelay
from app.infrastructure.crdt.relay_server import _Store, make_relay_app
from app.infrastructure.crdt.updates import open_update, seal_update
from app.infrastructure.encryption.primitives import random_key
from app.services.collaboration_service import CollaborationService


@pytest.fixture()
def relay() -> HttpRelay:
    store = _Store()
    app = make_relay_app(store)
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://relay")
    return HttpRelay("http://relay", client=client)


def test_publish_fetch_and_head(relay: HttpRelay) -> None:
    assert relay.head("chan") == 0
    assert relay.publish("chan", b"one") == 1
    assert relay.publish("chan", b"two") == 2
    assert relay.head("chan") == 2
    assert [b for _, b in relay.fetch("chan", 0)] == [b"one", b"two"]
    assert [b for _, b in relay.fetch("chan", 1)] == [b"two"]
    assert relay.fetch("chan", 2) == []


def test_presence_round_trips(relay: HttpRelay) -> None:
    relay.announce("chan", "peer-1", b"awareness-blob")
    assert relay.presence("chan") == {"peer-1": b"awareness-blob"}


def test_an_invalid_channel_is_refused(relay: HttpRelay) -> None:
    with pytest.raises(ValueError, match="channel"):
        relay.publish("../etc/passwd", b"x")


def test_two_peers_converge_over_the_http_relay(relay: HttpRelay) -> None:
    key = random_key()
    a = LayerDocument("doc")
    b = LayerDocument("doc")

    a.upsert_node(TreeNode(node_id="n1", name="Shared", is_note=True), body="hello from A")
    blob = seal_update(key=key, layer_id="L", doc_id="doc", seq=1, update=a.encode_update())
    relay.publish("chan", blob)

    for seq, sealed in relay.fetch("chan", 0):
        b.apply_update(open_update(key=key, layer_id="L", doc_id="doc", seq=seq, blob=sealed))

    assert b.body("n1") == "hello from A"


def test_only_ciphertext_crosses_the_relay(relay: HttpRelay) -> None:
    key = random_key()
    doc = LayerDocument("doc")
    doc.upsert_node(TreeNode(node_id="n1", name="Secret", is_note=True), body="NEEDLE-XYZ")
    relay.publish(
        "chan",
        seal_update(key=key, layer_id="L", doc_id="doc", seq=1, update=doc.encode_update()),
    )
    # Whatever the relay hands back is opaque — no plaintext, no title.
    for _seq, blob in relay.fetch("chan", 0):
        assert b"NEEDLE-XYZ" not in blob
        assert b"Secret" not in blob


def test_collaboration_service_syncs_through_the_http_relay(tmp_path, relay: HttpRelay) -> None:
    key = random_key()
    seed = (
        [TreeNode(node_id="n1", name="Note", is_note=True)],
        {"n1": "initial"},
    )

    def peer(sub: str) -> CollaborationService:
        return CollaborationService(
            key_for=lambda _l: key,
            doc_root_for=lambda _l: tmp_path / sub,
            ensure_readable=lambda _l: None,
            seed_content=lambda _l: seed if sub == "alice" else ([], {}),
            relay=relay,
        )

    alice = peer("alice")
    bob = peer("bob")

    state = alice.share_layer("L")
    bob.join_layer("L", state.doc_id or "", role="editor")
    assert bob._active["L"].document.body("n1") == "initial"

    alice.set_body("L", "n1", "edited over the network")
    bob.sync("L")
    assert bob._active["L"].document.body("n1") == "edited over the network"

    # presence also traverses the relay
    alice.announce("L", PresencePeer(peer_id="p-alice", display_name="Alice"))
    assert any(p.display_name == "Alice" for p in bob.presence("L"))
