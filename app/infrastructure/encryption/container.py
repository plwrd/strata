"""The encrypted object container.

One object = one file. The layout (docs/security/encryption-format.md):

    offset  size  field
    0       7     magic          b"STRATA1"
    7       1     format_version u8
    8       1     algorithm      u8   (1 = XChaCha20-Poly1305)
    9       1     object_type    u8
    10      1     flags          u8   (bit 0: padded)
    11      16    layer_binding  BLAKE2b-128(layer_id)
    27      16    object_id      raw 16 bytes (hex form is the filename)
    43      24    nonce
    67      4     plaintext_len  u32 big-endian (before padding)
    ---- 71-byte header ends; the whole header is the AAD ----
    71      ..    ciphertext + 16-byte Poly1305 tag

Two properties do the real work:

**The header is the AAD.** Every field above is authenticated, so an attacker
cannot swap two objects (the object id is bound), move an object between layers
(the layer binding is bound), relabel a note as a manifest (the object type is
bound), or downgrade the format (the version is bound). Any tampering fails
authentication, and the object is refused rather than half-read.

**Plaintext is padded into buckets before encryption.** AEAD hides content but not
length, and lengths leak: a 40-byte note is a title, a 4 MB one is a PDF, and the
size of "Acquisition of X" is a fingerprint. `plaintext_len` records the true size
so padding is exactly reversible.

What still leaks, honestly: the object count, the *bucketed* size, the file
mtimes, and the existence of the layer. See THREAT_MODEL.md.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import Final

from app.infrastructure.encryption.primitives import (
    ALG_XCHACHA20_POLY1305,
    NONCE_BYTES,
    DecryptionError,
    decrypt,
    encrypt,
)

MAGIC: Final = b"STRATA1"
FORMAT_VERSION: Final = 1
HEADER_SIZE: Final = 71

FLAG_PADDED: Final = 0b0000_0001

# Object types. Bound into the AAD, so a manifest can never be served as a note.
TYPE_MANIFEST: Final = 1
TYPE_NOTE: Final = 2
TYPE_ATTACHMENT: Final = 3
TYPE_INDEX: Final = 4
TYPE_EMBEDDING: Final = 5
# M9: a CRDT update object (a Yjs binary update, sealed) and a compacted CRDT
# base state. Distinct types so a note can never be served as a CRDT update or
# vice versa, and so the relay's blobs are typed at the AAD.
TYPE_CRDT_UPDATE: Final = 6
TYPE_CRDT_STATE: Final = 7

OBJECT_TYPES: Final = frozenset(
    {
        TYPE_MANIFEST,
        TYPE_NOTE,
        TYPE_ATTACHMENT,
        TYPE_INDEX,
        TYPE_EMBEDDING,
        TYPE_CRDT_UPDATE,
        TYPE_CRDT_STATE,
    }
)

_HEADER_STRUCT: Final = struct.Struct(">7sBBBB16s16s24sI")

# Padding ladder. Small objects round up to the next bucket; past 1 MiB the step is
# 1 MiB, because at that size the content is an attachment and the marginal privacy
# of finer buckets is not worth the disk.
_BUCKETS: Final = (256, 1024, 4096, 16_384, 65_536, 262_144, 1_048_576)
_LARGE_STEP: Final = 1_048_576

MAX_PLAINTEXT = 256 * 1024 * 1024


def layer_binding(layer_id: str) -> bytes:
    """A 16-byte commitment to the layer, for the AAD.

    Hashed rather than stored raw so the file does not carry a readable layer
    identifier, while still binding the object to exactly one layer.
    """
    return hashlib.blake2b(layer_id.encode("utf-8"), digest_size=16).digest()


def padded_length(length: int) -> int:
    for bucket in _BUCKETS:
        if length <= bucket:
            return bucket
    steps = (length + _LARGE_STEP - 1) // _LARGE_STEP
    return steps * _LARGE_STEP


@dataclass(frozen=True)
class ObjectHeader:
    format_version: int
    algorithm: int
    object_type: int
    flags: int
    layer_binding: bytes
    object_id: bytes
    nonce: bytes
    plaintext_len: int

    def pack(self) -> bytes:
        return _HEADER_STRUCT.pack(
            MAGIC,
            self.format_version,
            self.algorithm,
            self.object_type,
            self.flags,
            self.layer_binding,
            self.object_id,
            self.nonce,
            self.plaintext_len,
        )

    @classmethod
    def unpack(cls, raw: bytes) -> ObjectHeader:
        if len(raw) < HEADER_SIZE:
            raise DecryptionError("The object is truncated.")
        (
            magic,
            format_version,
            algorithm,
            object_type,
            flags,
            binding,
            object_id,
            nonce,
            plaintext_len,
        ) = _HEADER_STRUCT.unpack(raw[:HEADER_SIZE])

        if magic != MAGIC:
            raise DecryptionError("Not a Strata object.")
        if format_version > FORMAT_VERSION:
            raise DecryptionError("This object was written by a newer version of Strata.")
        if algorithm != ALG_XCHACHA20_POLY1305:
            raise DecryptionError("Unsupported encryption algorithm.")
        if object_type not in OBJECT_TYPES:
            raise DecryptionError("Unknown object type.")
        if plaintext_len > MAX_PLAINTEXT:
            raise DecryptionError("The object claims an implausible size.")

        return cls(
            format_version=format_version,
            algorithm=algorithm,
            object_type=object_type,
            flags=flags,
            layer_binding=binding,
            object_id=object_id,
            nonce=nonce,
            plaintext_len=plaintext_len,
        )


def seal(
    *,
    key: bytes,
    layer_id: str,
    object_id: bytes,
    object_type: int,
    plaintext: bytes,
    pad: bool = True,
) -> bytes:
    """Encrypt ``plaintext`` into a complete on-disk object."""
    if object_type not in OBJECT_TYPES:
        raise DecryptionError("Unknown object type.")
    if len(object_id) != 16:
        raise DecryptionError("Object ids are 16 bytes.")
    if len(plaintext) > MAX_PLAINTEXT:
        raise DecryptionError("The object is too large.")

    body = plaintext
    flags = 0
    if pad:
        target = padded_length(len(plaintext))
        body = plaintext + b"\x00" * (target - len(plaintext))
        flags |= FLAG_PADDED

    # The nonce goes into the header, and the header is the AAD, so the nonce is
    # authenticated too — an attacker cannot swap it for one that decrypts to
    # something else under a related key.
    from app.infrastructure.encryption.primitives import random_nonce

    nonce = random_nonce()
    header = ObjectHeader(
        format_version=FORMAT_VERSION,
        algorithm=ALG_XCHACHA20_POLY1305,
        object_type=object_type,
        flags=flags,
        layer_binding=layer_binding(layer_id),
        object_id=object_id,
        nonce=nonce,
        plaintext_len=len(plaintext),
    )
    aad = header.pack()
    _nonce, ciphertext = encrypt(key, body, aad, nonce=nonce)
    return aad + ciphertext


def open_sealed(
    *,
    key: bytes,
    layer_id: str,
    object_id: bytes,
    expected_type: int,
    blob: bytes,
) -> bytes:
    """Verify and decrypt an object. Any mismatch is a :class:`DecryptionError`."""
    header = ObjectHeader.unpack(blob)

    # These checks are belt-and-braces: the AAD already binds all three, so a
    # mismatch would fail authentication anyway. Checking first turns a confusing
    # "decryption failed" into a precise internal error during development, and
    # costs nothing.
    if header.layer_binding != layer_binding(layer_id):
        raise DecryptionError("This object does not belong to this layer.")
    if header.object_id != object_id:
        raise DecryptionError("This object is not the one that was requested.")
    if header.object_type != expected_type:
        raise DecryptionError("This object is not of the expected type.")
    if len(header.nonce) != NONCE_BYTES:
        raise DecryptionError()

    aad = blob[:HEADER_SIZE]
    ciphertext = blob[HEADER_SIZE:]
    if not ciphertext:
        raise DecryptionError("The object is truncated.")

    body = decrypt(key, header.nonce, ciphertext, aad)

    if header.flags & FLAG_PADDED:
        if header.plaintext_len > len(body):
            raise DecryptionError("The object's declared length is inconsistent.")
        return body[: header.plaintext_len]
    return body
