"""Sealing Yjs updates for storage and for the relay.

A Yjs update is an opaque binary diff. Before it leaves the process — to disk or
to the relay — it is sealed with XChaCha20-Poly1305 under the layer key (ADR-0005,
ADR-0006). The AAD binds ``{fmt, layer_id, doc_id, seq}``: we derive the object
id as ``blake2b(doc_id || seq)`` so the container's header (which authenticates
``object_type`` + ``layer`` + ``object_id``) authenticates the doc and sequence
too. A relay operator, or anyone reading the disk, sees only sizes and timing.
"""

from __future__ import annotations

import hashlib
import struct

from app.infrastructure.encryption.container import (
    TYPE_CRDT_STATE,
    TYPE_CRDT_UPDATE,
    open_sealed,
    seal,
)


def update_object_id(doc_id: str, seq: int) -> bytes:
    """Deterministic 16-byte id binding a doc and a sequence number.

    Deterministic on purpose: it makes the AAD authenticate ``(doc_id, seq)``, and
    it makes re-sealing the same logical update idempotent at the storage layer.
    """
    material = doc_id.encode("utf-8") + b"\x00" + struct.pack(">Q", seq)
    return hashlib.blake2b(material, digest_size=16).digest()


def seal_update(
    *,
    key: bytes,
    layer_id: str,
    doc_id: str,
    seq: int,
    update: bytes,
    is_state: bool = False,
) -> bytes:
    """Seal a Yjs update (or a compacted base state) into a storable blob."""
    return seal(
        key=key,
        layer_id=layer_id,
        object_id=update_object_id(doc_id, seq),
        object_type=TYPE_CRDT_STATE if is_state else TYPE_CRDT_UPDATE,
        plaintext=update,
        pad=True,
    )


def open_update(
    *,
    key: bytes,
    layer_id: str,
    doc_id: str,
    seq: int,
    blob: bytes,
    is_state: bool = False,
) -> bytes:
    """Verify and decrypt a sealed update. Any tampering is a DecryptionError."""
    return open_sealed(
        key=key,
        layer_id=layer_id,
        object_id=update_object_id(doc_id, seq),
        expected_type=TYPE_CRDT_STATE if is_state else TYPE_CRDT_UPDATE,
        blob=blob,
    )
