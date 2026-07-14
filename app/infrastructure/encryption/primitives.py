"""Cryptographic primitives.

Thin, deliberately boring wrappers over libsodium (PyNaCl) and Argon2. Nothing in
Strata calls a cipher directly — everything goes through here, so there is exactly
one place to audit and exactly one place a mistake can live.

Choices, and why (ADR-0005):

* **XChaCha20-Poly1305** for authenticated encryption. Its 192-bit nonce is large
  enough that random nonces are safe: the birthday bound is ~2^96 messages per
  key, so we never need a counter, and therefore never need the counter state that
  a crash could roll back. AES-GCM's 96-bit nonce would put us at meaningful
  collision risk after ~2^32 objects under one key, which a large workspace can
  reach.
* **Argon2id** for the password KDF. Memory-hard, side-channel resistant, and the
  current recommendation. Parameters are versioned and stored, so they can be
  raised later without breaking old layers.
* Keys are **random**, never derived from a password. The password unlocks the key;
  it never *is* the key. That is what makes a password change cheap (rewrap) and a
  key rotation possible at all.
"""

from __future__ import annotations

import ctypes
import secrets
from dataclasses import dataclass
from typing import Any, Final

from argon2.low_level import Type, hash_secret_raw
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
)
from nacl.exceptions import CryptoError

from app.domain.errors import StrataError

KEY_BYTES: Final = 32
NONCE_BYTES: Final = 24
TAG_BYTES: Final = 16
SALT_BYTES: Final = 16

ALG_XCHACHA20_POLY1305: Final = 1

# Argon2id parameters, version 1. Roughly 0.5-1s on a 2020-era laptop.
# `kdf_version` is stored with every layer so these can be raised for new layers
# (and old layers rewrapped) without a format break.
KDF_VERSION: Final = 1
ARGON2_TIME_COST: Final = 3
ARGON2_MEMORY_KIB: Final = 262_144  # 256 MiB
ARGON2_PARALLELISM: Final = 4


class DecryptionError(StrataError):
    """Authentication failed.

    Deliberately says nothing about *why*. A wrong password, a corrupted object and
    a forged object are the same event to a caller — distinguishing them for the
    user would distinguish them for an attacker too.
    """

    def __init__(self, message: str = "The data could not be decrypted.") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class KdfParams:
    """Everything needed to re-derive a key-encryption key from a password."""

    version: int = KDF_VERSION
    time_cost: int = ARGON2_TIME_COST
    memory_kib: int = ARGON2_MEMORY_KIB
    parallelism: int = ARGON2_PARALLELISM
    salt: bytes = b""

    @classmethod
    def new(cls) -> KdfParams:
        return cls(salt=secrets.token_bytes(SALT_BYTES))

    def to_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "algorithm": "argon2id",
            "time_cost": self.time_cost,
            "memory_kib": self.memory_kib,
            "parallelism": self.parallelism,
            "salt": self.salt.hex(),
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> KdfParams:
        algorithm = str(raw.get("algorithm", "argon2id"))
        if algorithm != "argon2id":
            raise DecryptionError("Unsupported key-derivation algorithm.")
        try:
            return cls(
                version=int(raw["version"]),
                time_cost=int(raw["time_cost"]),
                memory_kib=int(raw["memory_kib"]),
                parallelism=int(raw["parallelism"]),
                salt=bytes.fromhex(str(raw["salt"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            # A header we cannot parse is a header we must not guess at: deriving a
            # key from defaulted parameters would silently produce the wrong key.
            raise DecryptionError("The layer header is not valid.") from exc


def random_key() -> bytes:
    """A fresh 256-bit key from the OS CSPRNG."""
    return secrets.token_bytes(KEY_BYTES)


def random_nonce() -> bytes:
    """A fresh 192-bit nonce.

    Random, not counter-based: with 24 bytes the collision probability is
    negligible, and a counter would need persistent state that a crash or a restore
    from backup could rewind — reusing a nonce is the one failure this construction
    cannot survive.
    """
    return secrets.token_bytes(NONCE_BYTES)


def derive_key(password: str, params: KdfParams) -> bytes:
    """Derive a key-encryption key from a password with Argon2id."""
    if not params.salt:
        raise DecryptionError("Missing key-derivation salt.")
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=params.salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_kib,
        parallelism=params.parallelism,
        hash_len=KEY_BYTES,
        type=Type.ID,
    )


def encrypt(
    key: bytes, plaintext: bytes, aad: bytes, nonce: bytes | None = None
) -> tuple[bytes, bytes]:
    """Encrypt, binding ``aad``. Returns ``(nonce, ciphertext_with_tag)``."""
    if len(key) != KEY_BYTES:
        raise DecryptionError("Invalid key length.")
    nonce = nonce or random_nonce()
    if len(nonce) != NONCE_BYTES:
        raise DecryptionError("Invalid nonce length.")
    ciphertext = crypto_aead_xchacha20poly1305_ietf_encrypt(plaintext, aad, nonce, key)
    return nonce, ciphertext


def decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    """Decrypt and verify. Raises :class:`DecryptionError` on any failure."""
    if len(key) != KEY_BYTES or len(nonce) != NONCE_BYTES:
        raise DecryptionError()
    try:
        return crypto_aead_xchacha20poly1305_ietf_decrypt(ciphertext, aad, nonce, key)
    except (CryptoError, ValueError, TypeError) as exc:
        # Never leak which check failed, and never let the library's message out.
        raise DecryptionError() from exc


def zeroize(buffer: bytearray) -> None:
    """Overwrite a mutable buffer in place.

    Honest limits: this cannot reach copies CPython already made (``bytes`` are
    immutable and may have been duplicated by the interpreter, and the page may
    have been swapped to disk). It shrinks the window in which a key sits in
    process memory; it does not close it. THREAT_MODEL.md says so plainly.
    """
    if not buffer:
        return
    length = len(buffer)
    ctypes.memset((ctypes.c_char * length).from_buffer(buffer), 0, length)


def constant_time_equals(left: bytes, right: bytes) -> bool:
    return secrets.compare_digest(left, right)
