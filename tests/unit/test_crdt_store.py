"""The sealed update log persists, reloads, and compacts."""

from __future__ import annotations

from pathlib import Path

from app.domain.collaboration import TreeNode
from app.infrastructure.crdt.document import LayerDocument
from app.infrastructure.crdt.relay import DirectoryRelay, LocalRelay
from app.infrastructure.crdt.store import CRDTStore
from app.infrastructure.encryption.primitives import random_key


def _note(i: int) -> TreeNode:
    return TreeNode(node_id=f"n{i}", name=f"note-{i}", is_note=True)


def test_append_and_reload_reconstructs_the_document(tmp_path: Path) -> None:
    key = random_key()
    store = CRDTStore(tmp_path, layer_id="L", doc_id="doc")

    source = LayerDocument("doc")
    prev = None
    for i in range(5):
        source.upsert_node(_note(i), body=f"body-{i}")
        store.append(key, source.encode_update(prev))
        prev = source.state_vector()

    # Reloading from disk yields the same content.
    reloaded = store.load_document(key)
    assert {n.node_id for n in reloaded.nodes()} == {f"n{i}" for i in range(5)}
    assert reloaded.body("n3") == "body-3"


def test_only_ciphertext_touches_disk(tmp_path: Path) -> None:
    key = random_key()
    store = CRDTStore(tmp_path, layer_id="L", doc_id="doc")
    doc = LayerDocument("doc")
    doc.upsert_node(TreeNode(node_id="n1", name="Findable", is_note=True), body="NEEDLE")
    store.append(key, doc.encode_update())

    for path in tmp_path.rglob("*.blob"):
        raw = path.read_bytes()
        assert b"NEEDLE" not in raw
        assert b"Findable" not in raw


def test_compaction_reclaims_updates_and_preserves_state(tmp_path: Path) -> None:
    key = random_key()
    store = CRDTStore(tmp_path, layer_id="L", doc_id="doc")

    source = LayerDocument("doc")
    prev = None
    for i in range(20):
        source.upsert_node(_note(i))
        store.append(key, source.encode_update(prev))
        prev = source.state_vector()

    assert len(list(tmp_path.glob("u*.blob"))) == 20
    reclaimed = store.compact(key)
    assert reclaimed == 20
    assert len(list(tmp_path.glob("u*.blob"))) == 0
    assert len(list(tmp_path.glob("base-*.blob"))) == 1

    # Content survives compaction.
    reloaded = store.load_document(key)
    assert {n.node_id for n in reloaded.nodes()} == {f"n{i}" for i in range(20)}

    # New updates after a base state still apply on reload.
    source.upsert_node(_note(99))
    store.append(key, source.encode_update(prev))
    again = store.load_document(key)
    assert any(n.node_id == "n99" for n in again.nodes())


def test_local_relay_forwards_in_order() -> None:
    relay = LocalRelay()
    assert relay.head("chan") == 0
    relay.publish("chan", b"a")
    relay.publish("chan", b"b")
    assert relay.head("chan") == 2

    # A peer at cursor 0 gets everything; at cursor 1 only the tail.
    assert [b for _, b in relay.fetch("chan", 0)] == [b"a", b"b"]
    assert [b for _, b in relay.fetch("chan", 1)] == [b"b"]

    relay.announce("chan", "peer-1", b"awareness")
    assert relay.presence("chan") == {"peer-1": b"awareness"}


def test_directory_relay_round_trips(tmp_path: Path) -> None:
    relay = DirectoryRelay(tmp_path)
    relay.publish("chan", b"one")
    relay.publish("chan", b"two")
    assert [b for _, b in relay.fetch("chan", 0)] == [b"one", b"two"]
    assert relay.fetch("chan", 2) == []

    relay.announce("chan", "peer-1", b"aw")
    assert relay.presence("chan")["peer-1"] == b"aw"


def test_two_peers_converge_through_a_relay(tmp_path: Path) -> None:
    """End-to-end: two documents sync only via sealed blobs on a relay."""
    key = random_key()
    relay = DirectoryRelay(tmp_path)

    a = LayerDocument("doc")
    b = LayerDocument("doc")
    from app.infrastructure.crdt.updates import open_update, seal_update

    def push(doc: LayerDocument, since: bytes | None) -> None:
        blob = seal_update(key=key, layer_id="L", doc_id="doc", update=doc.encode_update(since))
        relay.publish("chan", blob)

    a.upsert_node(_note(1), body="from A")
    push(a, None)

    for _seq, blob in relay.fetch("chan", 0):
        update = open_update(key=key, layer_id="L", doc_id="doc", blob=blob)
        b.apply_update(update)

    assert b.body("n1") == "from A"
