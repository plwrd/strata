"""Crash-resumed key rotation."""

from __future__ import annotations

from pathlib import Path

from app.infrastructure.encryption.container import TYPE_NOTE
from app.infrastructure.encryption.layer_header import LayerHeader
from app.infrastructure.encryption.primitives import random_key
from app.infrastructure.encryption.rotation_journal import RotationJournal
from app.infrastructure.storage.encrypted_store import EncryptedLayerStore
from app.services.encryption_service import EncryptionService


def test_an_interrupted_rotation_resumes_from_the_journal(tmp_path: Path) -> None:
    service = EncryptionService()
    root = tmp_path / "layer_a"
    header, _recovery = service.create_layer(layer_id="layer_a", root=root, password="password one")
    old_key = service.keys.key_for("layer_a")

    store = EncryptedLayerStore("layer_a", root)
    manifest = store.read_manifest(old_key, header.manifest_object_id)
    store.write_note(
        old_key,
        manifest,
        object_id=None,
        title="Secret",
        folder_path="",
        content="BLUEJAY",
        properties={},
        timestamp="2026-07-14T00:00:00+00:00",
    )
    store.write_manifest(old_key, header.manifest_object_id, manifest)

    note_entry = next(entry for entry in manifest.entries.values() if entry.kind == "note")
    new_key = random_key()
    plaintext = store._read_object(old_key, note_entry.object_id, TYPE_NOTE)
    store._write_object(new_key, note_entry.object_id, TYPE_NOTE, plaintext)

    journal = RotationJournal(root)
    journal.save(
        layer_id="layer_a",
        manifest_object_id=header.manifest_object_id,
        padding_enabled=header.padding_enabled,
        done_object_ids=[note_entry.object_id],
        old_key=old_key,
        new_key=new_key,
    )

    # Header still wraps the old key — this is the crash window.
    assert LayerHeader.load(root).key_generation == header.key_generation
    assert journal.exists()

    rewritten = service.rotate_key("layer_a", root, "password one")
    assert rewritten >= 2
    assert not journal.exists()

    key_after = service.keys.key_for("layer_a")
    assert key_after == new_key
    rotated = store.read_manifest(key_after, LayerHeader.load(root).manifest_object_id)
    note = next(entry for entry in rotated.entries.values() if entry.kind == "note")
    assert store.read_note(key_after, note).content == "BLUEJAY"
