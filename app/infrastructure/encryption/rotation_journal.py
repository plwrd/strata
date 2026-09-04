"""Crash-safe key-rotation journal.

Rotation re-encrypts every object under a new layer key, then rewraps the
header. If the process dies in the middle, some objects are under the new key
and the header still unwraps the old one. The journal stores the new key
wrapped with the old key (AEAD, same primitive as objects) plus the object ids
already rewritten, so the next unlock or rotate can finish the job.

The journal is ciphertext. It is useless without the old layer key, which still
comes from the password. It is deleted after the new header is committed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.infrastructure.encryption.primitives import decrypt, encrypt
from app.infrastructure.storage.paths import replace_atomic

JOURNAL_NAME = "rotation.journal"
AAD = b"strata-rotation-journal-v1"


@dataclass(frozen=True)
class RotationJournalState:
    layer_id: str
    manifest_object_id: str
    padding_enabled: bool
    done_object_ids: tuple[str, ...]
    new_key: bytes


class RotationJournal:
    def __init__(self, root: Path) -> None:
        self.path = root / JOURNAL_NAME

    def exists(self) -> bool:
        return self.path.is_file()

    def save(
        self,
        *,
        layer_id: str,
        manifest_object_id: str,
        padding_enabled: bool,
        done_object_ids: list[str],
        old_key: bytes,
        new_key: bytes,
    ) -> None:
        nonce, wrapped = encrypt(old_key, new_key, AAD)
        payload = {
            "v": 1,
            "layer_id": layer_id,
            "manifest_object_id": manifest_object_id,
            "padding_enabled": padding_enabled,
            "done_object_ids": done_object_ids,
            "nonce": nonce.hex(),
            "wrapped_new_key": wrapped.hex(),
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            replace_atomic(temporary, self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def load(self, old_key: bytes) -> RotationJournalState:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        new_key = decrypt(
            old_key,
            bytes.fromhex(str(raw["nonce"])),
            bytes.fromhex(str(raw["wrapped_new_key"])),
            AAD,
        )
        done = raw.get("done_object_ids", [])
        if not isinstance(done, list):
            done = []
        return RotationJournalState(
            layer_id=str(raw["layer_id"]),
            manifest_object_id=str(raw["manifest_object_id"]),
            padding_enabled=bool(raw.get("padding_enabled", True)),
            done_object_ids=tuple(str(item) for item in done),
            new_key=new_key,
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
        self.path.with_name(self.path.name + ".tmp").unlink(missing_ok=True)
