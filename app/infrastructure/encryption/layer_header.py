"""``layer.header`` — the only unencrypted file in a private layer.

It holds the *wrapped* layer data key, never the key itself:

    password ──Argon2id──▶ KEK_pw ──AEAD-wrap──▶ [LDK]
    recovery key ─────────▶ KEK_rk ──AEAD-wrap──▶ [LDK]   (optional, same LDK)

Consequences of wrapping rather than deriving:

* **Changing the password is cheap.** Re-derive a KEK, rewrap the same LDK. No
  object is touched, so a password change on a 10 GB layer is instant.
* **Rotating the key is possible at all.** A new LDK means every object must be
  re-encrypted — expensive, but it is the only thing that actually revokes a
  collaborator who kept a copy of the old key.
* **A recovery key is not a backdoor.** It wraps the same LDK independently, so it
  grants exactly what the password grants, and nothing more. Losing both makes the
  layer unrecoverable — there is no third door, and we say so.

The header deliberately contains no title, no object count, and no hint about the
contents. What it does reveal, unavoidably: that this is a Strata private layer,
when it was created and last touched, and the KDF parameters.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from app.infrastructure.encryption.primitives import (
    KEY_BYTES,
    DecryptionError,
    KdfParams,
    decrypt,
    derive_key,
    encrypt,
    random_key,
)

HEADER_FILENAME: Final = "layer.header"
HEADER_FORMAT_VERSION: Final = 1

# AAD for the wrap. Binds the envelope to its purpose, so a password envelope can
# never be presented as a recovery envelope (or vice versa) to confuse the KDF.
_AAD_PASSWORD: Final = b"strata:layer-key:password:v1"
_AAD_RECOVERY: Final = b"strata:layer-key:recovery:v1"

RECOVERY_KEY_BYTES: Final = 32
_RECOVERY_ALPHABET: Final = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1


def generate_recovery_key() -> str:
    """A 256-bit recovery key, grouped for transcription.

    Crockford-ish alphabet: no I, O, 0 or 1, because this is a string a human will
    write on paper and type back in a year, probably in a hurry.
    """
    raw = secrets.token_bytes(RECOVERY_KEY_BYTES)
    value = int.from_bytes(raw, "big")
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 32)
        digits.append(_RECOVERY_ALPHABET[remainder])
    text = "".join(reversed(digits)).rjust(52, _RECOVERY_ALPHABET[0])
    return "-".join(text[i : i + 4] for i in range(0, len(text), 4))


def normalise_recovery_key(value: str) -> str:
    return value.replace("-", "").replace(" ", "").strip().upper()


@dataclass
class WrappedKey:
    """One envelope around the layer data key."""

    kdf: KdfParams
    nonce: bytes
    ciphertext: bytes

    def to_json(self) -> dict[str, Any]:
        return {
            "kdf": self.kdf.to_json(),
            "nonce": self.nonce.hex(),
            "ciphertext": self.ciphertext.hex(),
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> WrappedKey:
        return cls(
            kdf=KdfParams.from_json(raw["kdf"]),
            nonce=bytes.fromhex(raw["nonce"]),
            ciphertext=bytes.fromhex(raw["ciphertext"]),
        )


def wrap_key(secret: str, layer_key: bytes, *, aad: bytes) -> WrappedKey:
    params = KdfParams.new()
    kek = derive_key(secret, params)
    nonce, ciphertext = encrypt(kek, layer_key, aad)
    return WrappedKey(kdf=params, nonce=nonce, ciphertext=ciphertext)


def unwrap_key(secret: str, wrapped: WrappedKey, *, aad: bytes) -> bytes:
    kek = derive_key(secret, wrapped.kdf)
    key = decrypt(kek, wrapped.nonce, wrapped.ciphertext, aad)
    if len(key) != KEY_BYTES:
        raise DecryptionError()
    return key


@dataclass
class LayerHeader:
    format_version: int = HEADER_FORMAT_VERSION
    layer_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    key_generation: int = 1
    manifest_object_id: str = ""
    password_envelope: WrappedKey | None = None
    recovery_envelope: WrappedKey | None = None
    padding_enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    # -- key operations ------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        layer_id: str,
        password: str,
        created_at: str,
        recovery_key: str | None = None,
    ) -> tuple[LayerHeader, bytes]:
        """Create a header around a *fresh random* layer key.

        The password never becomes the key. It wraps it.
        """
        layer_key = random_key()
        header = cls(
            layer_id=layer_id,
            created_at=created_at,
            updated_at=created_at,
            password_envelope=wrap_key(password, layer_key, aad=_AAD_PASSWORD),
        )
        if recovery_key:
            header.recovery_envelope = wrap_key(
                normalise_recovery_key(recovery_key), layer_key, aad=_AAD_RECOVERY
            )
        return header, layer_key

    def unlock_with_password(self, password: str) -> bytes:
        if self.password_envelope is None:
            raise DecryptionError()
        return unwrap_key(password, self.password_envelope, aad=_AAD_PASSWORD)

    def unlock_with_recovery_key(self, recovery_key: str) -> bytes:
        if self.recovery_envelope is None:
            raise DecryptionError()
        return unwrap_key(
            normalise_recovery_key(recovery_key), self.recovery_envelope, aad=_AAD_RECOVERY
        )

    def change_password(self, old_password: str, new_password: str) -> None:
        """Rewrap the *same* layer key. No object is re-encrypted.

        This does not revoke anyone who already has the layer key — it only changes
        what unlocks it on this device. Revocation is key *rotation*, and it is a
        different, expensive operation.
        """
        layer_key = self.unlock_with_password(old_password)
        self.password_envelope = wrap_key(new_password, layer_key, aad=_AAD_PASSWORD)

    def set_recovery_key(self, password: str, recovery_key: str) -> None:
        layer_key = self.unlock_with_password(password)
        self.recovery_envelope = wrap_key(
            normalise_recovery_key(recovery_key), layer_key, aad=_AAD_RECOVERY
        )

    def rewrap_for_rotation(self, password: str, new_layer_key: bytes) -> None:
        """Point the envelopes at a new layer key. The caller re-encrypts objects."""
        self.password_envelope = wrap_key(password, new_layer_key, aad=_AAD_PASSWORD)
        # A recovery key that still opened the *old* key would be a hole straight
        # through the rotation, so it is dropped and must be re-issued.
        self.recovery_envelope = None
        self.key_generation += 1

    # -- persistence ---------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "layer_id": self.layer_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "key_generation": self.key_generation,
            "manifest_object_id": self.manifest_object_id,
            "padding_enabled": self.padding_enabled,
            "password": self.password_envelope.to_json() if self.password_envelope else None,
            "recovery": self.recovery_envelope.to_json() if self.recovery_envelope else None,
            **self.extra,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> LayerHeader:
        version = int(raw.get("format_version", 0))
        if version > HEADER_FORMAT_VERSION:
            raise DecryptionError("This layer was created by a newer version of Strata.")
        return cls(
            format_version=version,
            layer_id=str(raw.get("layer_id", "")),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
            key_generation=int(raw.get("key_generation", 1)),
            manifest_object_id=str(raw.get("manifest_object_id", "")),
            padding_enabled=bool(raw.get("padding_enabled", True)),
            password_envelope=(
                WrappedKey.from_json(raw["password"]) if raw.get("password") else None
            ),
            recovery_envelope=(
                WrappedKey.from_json(raw["recovery"]) if raw.get("recovery") else None
            ),
        )

    def save(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        path = root / HEADER_FILENAME
        temporary = path.with_suffix(".header.tmp")
        temporary.write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")
        # Atomic: losing the header to a half-written file would lose the layer.
        temporary.replace(path)

    @classmethod
    def load(cls, root: Path) -> LayerHeader:
        path = root / HEADER_FILENAME
        if not path.is_file():
            raise DecryptionError("This layer has no header.")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise DecryptionError("The layer header could not be read.") from exc
        if not isinstance(raw, dict):
            raise DecryptionError("The layer header is not valid.")
        return cls.from_json(raw)
