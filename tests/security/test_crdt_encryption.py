"""Sealed CRDT updates leak nothing and refuse tampering.

The relay is untrusted (ADR-0006): it forwards these blobs. So a sealed update
must be indistinguishable from noise, must fail authentication under any
tampering, and must refuse to decrypt if it is transplanted to another layer,
another document, or another sequence position.
"""

from __future__ import annotations

import pytest

from app.domain.collaboration import TreeNode
from app.infrastructure.crdt.document import LayerDocument
from app.infrastructure.crdt.updates import open_update, seal_update, update_object_id
from app.infrastructure.encryption.primitives import DecryptionError, random_key

pytestmark = pytest.mark.security


def _update() -> bytes:
    doc = LayerDocument("doc-1")
    doc.upsert_node(TreeNode(node_id="n1", name="Secret note", is_note=True), body="TOPSECRET")
    return doc.encode_update()


def test_a_sealed_update_reveals_no_plaintext() -> None:
    key = random_key()
    plaintext = _update()
    blob = seal_update(key=key, layer_id="L", doc_id="doc-1", seq=1, update=plaintext)

    assert b"TOPSECRET" not in blob
    assert b"Secret note" not in blob
    # round-trips exactly
    assert open_update(key=key, layer_id="L", doc_id="doc-1", seq=1, blob=blob) == plaintext


def test_wrong_key_fails() -> None:
    blob = seal_update(key=random_key(), layer_id="L", doc_id="doc-1", seq=1, update=_update())
    with pytest.raises(DecryptionError):
        open_update(key=random_key(), layer_id="L", doc_id="doc-1", seq=1, blob=blob)


def test_transplant_to_another_layer_fails() -> None:
    key = random_key()
    blob = seal_update(key=key, layer_id="L", doc_id="doc-1", seq=1, update=_update())
    with pytest.raises(DecryptionError):
        open_update(key=key, layer_id="OTHER", doc_id="doc-1", seq=1, blob=blob)


def test_transplant_to_another_document_fails() -> None:
    key = random_key()
    blob = seal_update(key=key, layer_id="L", doc_id="doc-1", seq=1, update=_update())
    with pytest.raises(DecryptionError):
        open_update(key=key, layer_id="L", doc_id="doc-2", seq=1, blob=blob)


def test_replaying_at_a_different_sequence_fails() -> None:
    key = random_key()
    blob = seal_update(key=key, layer_id="L", doc_id="doc-1", seq=1, update=_update())
    with pytest.raises(DecryptionError):
        open_update(key=key, layer_id="L", doc_id="doc-1", seq=2, blob=blob)


def test_flipping_one_byte_fails_authentication() -> None:
    key = random_key()
    blob = bytearray(seal_update(key=key, layer_id="L", doc_id="doc-1", seq=1, update=_update()))
    blob[-1] ^= 0x01
    with pytest.raises(DecryptionError):
        open_update(key=key, layer_id="L", doc_id="doc-1", seq=1, blob=bytes(blob))


def test_the_object_id_binds_doc_and_seq() -> None:
    # Different (doc, seq) pairs must never collide into the same AAD binding.
    ids = {
        update_object_id("doc-1", 1),
        update_object_id("doc-1", 2),
        update_object_id("doc-2", 1),
    }
    assert len(ids) == 3
    assert all(len(i) == 16 for i in ids)
