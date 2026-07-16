"""Sealing Yjs updates for storage and for the relay.

A Yjs update is an opaque binary diff. Before it leaves the process — to disk or
to the relay — it is sealed with XChaCha20-Poly1305 under the layer key (ADR-0005,
ADR-0006).

The sealed object id is **content-derived**: ``blake2b(doc_id || plaintext)``. It
binds the update to its document (a blob from another doc fails to open) and to
its own bytes (tampering fails authentication), and — crucially — it does *not*
depend on the update's position in the relay log. An earlier design derived the
id from the relay sequence number, but the sealer cannot know the sequence the
relay will assign until after publishing; under concurrent publishers the sealed
id and the stored position diverged and every peer silently dropped the update.
Deriving from content removes the race entirely: the reader recovers the id from
the (authenticated) header and re-checks it against the decrypted bytes.
"""

from __future__ import annotations

import hashlib

from app.infrastructure.encryption.container import (
    TYPE_CRDT_STATE,
    TYPE_CRDT_UPDATE,
    ObjectHeader,
    open_sealed,
    seal,
)
from app.infrastructure.encryption.primitives import DecryptionError


def update_object_id(doc_id: str, update: bytes) -> bytes:
    """A 16-byte id binding an update to its document and its exact content."""
    material = doc_id.encode("utf-8") + b"\x00" + update
    return hashlib.blake2b(material, digest_size=16).digest()


def seal_update(
    *,
    key: bytes,
    layer_id: str,
    doc_id: str,
    update: bytes,
    is_state: bool = False,
) -> bytes:
    """Seal a Yjs update (or a compacted base state) into a storable blob."""
    return seal(
        key=key,
        layer_id=layer_id,
        object_id=update_object_id(doc_id, update),
        object_type=TYPE_CRDT_STATE if is_state else TYPE_CRDT_UPDATE,
        plaintext=update,
        pad=True,
    )


def open_update(
    *,
    key: bytes,
    layer_id: str,
    doc_id: str,
    blob: bytes,
    is_state: bool = False,
) -> bytes:
    """Verify and decrypt a sealed update. Any tampering is a DecryptionError."""
    # The object id lives in the (AAD-authenticated) header, so the reader need
    # not know the relay position to open the blob.
    header = ObjectHeader.unpack(blob)
    plaintext = open_sealed(
        key=key,
        layer_id=layer_id,
        object_id=header.object_id,
        expected_type=TYPE_CRDT_STATE if is_state else TYPE_CRDT_UPDATE,
        blob=blob,
    )
    # Bind the id to the content: a blob whose header id does not match its own
    # bytes (or belongs to another document) is a tamper, not an update.
    if header.object_id != update_object_id(doc_id, plaintext):
        raise DecryptionError("The update object id does not bind its content.")
    return plaintext
