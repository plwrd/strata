#!/usr/bin/env python3
"""Generate byte-stable encryption / CRDT format KATs for the C# port.

Run from repo root:

    python tools/format_fixtures/generate.py

Writes under ``tests/fixtures/format/``. Vectors use fixed keys, nonces, and
salts so regeneration is deterministic. Source of truth is the shipping Python
modules under ``app/infrastructure/encryption`` and ``app/infrastructure/crdt``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.infrastructure.crdt.updates import seal_update, update_object_id  # noqa: E402
from app.infrastructure.encryption.container import (  # noqa: E402
    FLAG_PADDED,
    FORMAT_VERSION,
    HEADER_SIZE,
    MAGIC,
    TYPE_CRDT_UPDATE,
    TYPE_NOTE,
    ObjectHeader,
    layer_binding,
    padded_length,
    seal,
)
from app.infrastructure.encryption.primitives import (  # noqa: E402
    ALG_XCHACHA20_POLY1305,
    KdfParams,
    decrypt,
    derive_key,
    encrypt,
)
from app.infrastructure.encryption.layer_header import (  # noqa: E402
    _AAD_PASSWORD,
    _AAD_RECOVERY,
    LayerHeader,
    WrappedKey,
    normalise_recovery_key,
)
from app.infrastructure.encryption.rotation_journal import AAD as ROTATION_AAD  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "format"

# Fixed material — never use secrets.token_* here.
KEY = bytes(range(32))
NONCE = bytes(range(24))
SALT = bytes(range(16, 32))
LAYER_ID = "layer-kat-0001"
OBJECT_ID = bytes.fromhex("0123456789abcdef0123456789abcdef")
PASSWORD = "correct-horse-battery-staple"
# Normalised recovery secret (no grouping dashes).
RECOVERY_NORMALISED = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789ABCDEFGHJKLMNPQRST"
DOC_ID = "interop-doc"

# Same base64 vector as frontend/src/tests/crdtInterop.test.ts
PYCRDT_UPDATE_B64 = (
    "ARCzvbTqlKbCBgAnAQR0cmVlBm5vdGUtMQEoALO9tOqUpsIGAAZwYXJlbnQBfigAs7206pSmwgYABG5h"
    "bWUBdwVIZWxsbygAs7206pSmwgYABW9yZGVyAXcCYTAoALO9tOqUpsIGAAdpc19ub3RlAXgoALO9tOqU"
    "psIGAAdkZWxldGVkAXknAQZib2RpZXMGbm90ZS0xAgQAs7206pSmwgYGEWhlbGxvIGZyb20gcHljcmR0"
    "JwEEbWV0YQZub3RlLTEBJwCzvbTqlKbCBhgEdGFncwAnAQR0cmVlCGZvbGRlci0xASgAs7206pSmwgYa"
    "BnBhcmVudAF+KACzvbTqlKbCBhoEbmFtZQF3CFJlc2VhcmNoKACzvbTqlKbCBhoFb3JkZXIBdwJhMCgA"
    "s7206pSmwgYaB2lzX25vdGUBeSgAs7206pSmwgYaB2RlbGV0ZWQBeQA="
)


def _write_json(name: str, payload: dict) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


def _write_bytes(name: str, data: bytes) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"wrote {path.relative_to(ROOT)} ({len(data)} bytes)")


def generate_primitives() -> None:
    plaintext = b"strata-kat-plaintext-v1"
    aad = b"strata:kat:aad:v1"
    nonce, ciphertext = encrypt(KEY, plaintext, aad, nonce=NONCE)
    assert nonce == NONCE
    roundtrip = decrypt(KEY, nonce, ciphertext, aad)
    assert roundtrip == plaintext
    _write_json(
        "primitives.json",
        {
            "description": "XChaCha20-Poly1305-IETF encrypt with fixed key/nonce/AAD",
            "key_hex": KEY.hex(),
            "nonce_hex": NONCE.hex(),
            "aad_hex": aad.hex(),
            "plaintext_hex": plaintext.hex(),
            "ciphertext_with_tag_hex": ciphertext.hex(),
        },
    )


def _seal_with_nonce(
    *,
    key: bytes,
    layer_id: str,
    object_id: bytes,
    object_type: int,
    plaintext: bytes,
    nonce: bytes,
    pad: bool = True,
) -> bytes:
    """Like container.seal but with a fixed nonce for KAT determinism."""
    body = plaintext
    flags = 0
    if pad:
        target = padded_length(len(plaintext))
        body = plaintext + b"\x00" * (target - len(plaintext))
        flags |= FLAG_PADDED
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
    _, ciphertext = encrypt(key, body, aad, nonce=nonce)
    return aad + ciphertext


def generate_container() -> None:
    plaintext = b"# hello from strata kat\n"
    blob = _seal_with_nonce(
        key=KEY,
        layer_id=LAYER_ID,
        object_id=OBJECT_ID,
        object_type=TYPE_NOTE,
        plaintext=plaintext,
        nonce=NONCE,
        pad=True,
    )
    assert blob[:7] == MAGIC
    assert len(blob) >= HEADER_SIZE + 16
    header = ObjectHeader.unpack(blob)
    assert header.plaintext_len == len(plaintext)
    assert header.layer_binding == hashlib.blake2b(LAYER_ID.encode(), digest_size=16).digest()
    # big-endian plaintext_len at offset 67
    be_len = struct.unpack(">I", blob[67:71])[0]
    assert be_len == len(plaintext)

    _write_bytes("container_note.bin", blob)
    _write_json(
        "container_note.json",
        {
            "description": "Sealed TYPE_NOTE object with padding; header is AAD",
            "key_hex": KEY.hex(),
            "layer_id": LAYER_ID,
            "object_id_hex": OBJECT_ID.hex(),
            "object_type": TYPE_NOTE,
            "plaintext_hex": plaintext.hex(),
            "nonce_hex": NONCE.hex(),
            "layer_binding_hex": header.layer_binding.hex(),
            "padded_length": padded_length(len(plaintext)),
            "blob_file": "container_note.bin",
            "header_size": HEADER_SIZE,
            "negatives": {
                "wrong_layer_id": "layer-other",
                "wrong_object_id_hex": "ff" * 16,
                "truncated_len": HEADER_SIZE - 1,
            },
        },
    )


def generate_layer_header() -> None:
    params = KdfParams(
        version=1,
        time_cost=1,  # fast for CI; production uses 3/262144/4 — format identical
        memory_kib=8,
        parallelism=1,
        salt=SALT,
    )
    kek = derive_key(PASSWORD, params)
    nonce, ciphertext = encrypt(kek, KEY, _AAD_PASSWORD, nonce=NONCE)
    password_wrap = WrappedKey(kdf=params, nonce=nonce, ciphertext=ciphertext)

    recovery_secret = normalise_recovery_key(RECOVERY_NORMALISED)
    kek_rk = derive_key(recovery_secret, params)
    # Distinct nonce for recovery envelope so both can coexist.
    recovery_nonce = bytes((b + 1) % 256 for b in NONCE)
    r_nonce, r_ct = encrypt(kek_rk, KEY, _AAD_RECOVERY, nonce=recovery_nonce)
    recovery_wrap = WrappedKey(kdf=params, nonce=r_nonce, ciphertext=r_ct)

    header = {
        "format_version": 1,
        "layer_id": LAYER_ID,
        "created_at": "2020-01-01T00:00:00+00:00",
        "updated_at": "2020-01-01T00:00:00+00:00",
        "key_generation": 1,
        "manifest_object_id": OBJECT_ID.hex(),
        "padding_enabled": True,
        "password": password_wrap.to_json(),
        "recovery": recovery_wrap.to_json(),
    }
    _write_json("layer_header.json", {
        "description": "layer.header wrap KATs (reduced Argon2 costs for CI)",
        "password": PASSWORD,
        "recovery_normalised": RECOVERY_NORMALISED,
        "layer_key_hex": KEY.hex(),
        "aad_password_hex": _AAD_PASSWORD.hex(),
        "aad_recovery_hex": _AAD_RECOVERY.hex(),
        "note": "Production KDF is t=3 m=262144 p=4; this fixture uses t=1 m=8 p=1 with fixed salt.",
        "header": header,
    })


def generate_rotation_journal() -> None:
    old_key = KEY
    new_key = bytes((b ^ 0xFF) for b in KEY)
    nonce, wrapped = encrypt(old_key, new_key, ROTATION_AAD, nonce=NONCE)
    payload = {
        "v": 1,
        "layer_id": LAYER_ID,
        "manifest_object_id": OBJECT_ID.hex(),
        "padding_enabled": True,
        "done_object_ids": [],
        "nonce": nonce.hex(),
        "wrapped_new_key": wrapped.hex(),
    }
    _write_json(
        "rotation_journal.json",
        {
            "description": "rotation.journal wrap under AAD strata-rotation-journal-v1",
            "old_key_hex": old_key.hex(),
            "new_key_hex": new_key.hex(),
            "aad_hex": ROTATION_AAD.hex(),
            "journal": payload,
        },
    )


def generate_crdt_seal() -> None:
    update = base64.b64decode(PYCRDT_UPDATE_B64)
    oid = update_object_id(DOC_ID, update)

    # seal_update uses random nonce — build deterministically like container.
    blob = _seal_with_nonce(
        key=KEY,
        layer_id=LAYER_ID,
        object_id=oid,
        object_type=TYPE_CRDT_UPDATE,
        plaintext=update,
        nonce=NONCE,
        pad=True,
    )
    # Cross-check against production sealer shape (ignore nonce).
    live = seal_update(key=KEY, layer_id=LAYER_ID, doc_id=DOC_ID, update=update)
    live_hdr = ObjectHeader.unpack(live)
    kat_hdr = ObjectHeader.unpack(blob)
    assert live_hdr.object_id == kat_hdr.object_id == oid
    assert live_hdr.object_type == kat_hdr.object_type == TYPE_CRDT_UPDATE
    assert live_hdr.layer_binding == kat_hdr.layer_binding

    _write_bytes("crdt_update.bin", blob)
    _write_json(
        "crdt_update.json",
        {
            "description": "Sealed TYPE_CRDT_UPDATE from committed pycrdt/Yjs interop vector",
            "key_hex": KEY.hex(),
            "layer_id": LAYER_ID,
            "doc_id": DOC_ID,
            "object_type": TYPE_CRDT_UPDATE,
            "yjs_update_b64": PYCRDT_UPDATE_B64,
            "object_id_hex": oid.hex(),
            "nonce_hex": NONCE.hex(),
            "blob_file": "crdt_update.bin",
            "negatives": {
                "wrong_doc_id": "other-doc",
                "wrong_layer_id": "layer-other",
            },
        },
    )


def generate_store_layer() -> None:
    """A whole private layer, written by the shipping Python store.

    The other vectors pin one blob each; this pins the *shape on disk* — the
    ``objects/<xx>/<id>`` sharding, the encrypted manifest, and the header that
    unwraps them. A port that opens this directory can open a real workspace.
    """
    import shutil

    from app.infrastructure.encryption import primitives as primitives_mod
    from app.infrastructure.storage import encrypted_store as store_mod

    root = OUT / "store_layer"
    if root.exists():
        shutil.rmtree(root)

    # Deterministic but *distinct* nonces and object ids: a fixture that reused a
    # nonce would be both non-reproducible in spirit and a bad example.
    counter = {"nonce": 0, "oid": 0}

    def fixed_nonce() -> bytes:
        counter["nonce"] += 1
        return counter["nonce"].to_bytes(24, "big")

    def fixed_object_id() -> bytes:
        counter["oid"] += 1
        return bytes([0xA0 + counter["oid"]]) + counter["oid"].to_bytes(15, "big")

    original_nonce = primitives_mod.random_nonce
    original_oid = store_mod.new_raw_object_id
    primitives_mod.random_nonce = fixed_nonce
    store_mod.new_raw_object_id = fixed_object_id
    try:
        store = store_mod.EncryptedLayerStore(LAYER_ID, root, padding=True)
        store.ensure()

        manifest_id = store.create_manifest(KEY)
        manifest = store.read_manifest(KEY, manifest_id)

        timestamp = "2020-01-01T00:00:00+00:00"
        note_body = (
            "# Northwind\n\n"
            "Tagged #research and #deals/q1.\n\n"
            "Links to [[Second Note]] and supports:: [[Evidence]].\n"
        )
        first = store.write_note(
            KEY,
            manifest,
            object_id=None,
            title="Northwind",
            folder_path="",
            content=note_body,
            properties={"tags": ["research"], "status": "open"},
            timestamp=timestamp,
        )
        second = store.write_note(
            KEY,
            manifest,
            object_id=None,
            title="Second Note",
            folder_path="Deals",
            content="Plain body, no links.\n",
            properties={},
            timestamp=timestamp,
        )
        folder = store.add_folder(manifest, path="Deals", name="Deals", timestamp=timestamp)
        attachment_data = b"\x00\x01\x02attachment bytes\xff"
        attachment = store.write_attachment(
            KEY, manifest, filename="chart.png", data=attachment_data, timestamp=timestamp
        )
        store.write_manifest(KEY, manifest_id, manifest)

        params = KdfParams(version=1, time_cost=1, memory_kib=8, parallelism=1, salt=SALT)
        kek = derive_key(PASSWORD, params)
        nonce, ciphertext = encrypt(kek, KEY, _AAD_PASSWORD, nonce=NONCE)
        header = LayerHeader(
            layer_id=LAYER_ID,
            created_at=timestamp,
            updated_at=timestamp,
            manifest_object_id=manifest_id,
            padding_enabled=True,
            password_envelope=WrappedKey(kdf=params, nonce=nonce, ciphertext=ciphertext),
        )
        header.save(root)
    finally:
        primitives_mod.random_nonce = original_nonce
        store_mod.new_raw_object_id = original_oid

    # Prove the fixture opens with nothing but the password.
    reloaded = LayerHeader.load(root)
    layer_key = reloaded.unlock_with_password(PASSWORD)
    assert layer_key == KEY
    reopened = store_mod.EncryptedLayerStore(LAYER_ID, root).read_manifest(
        layer_key, reloaded.manifest_object_id
    )
    assert reopened.entries[first.object_id].title == "Northwind"

    _write_json(
        "store_layer.json",
        {
            "description": "A private layer written by the Python store; open it end to end",
            "directory": "store_layer",
            "password": PASSWORD,
            "layer_id": LAYER_ID,
            "layer_key_hex": KEY.hex(),
            "manifest_object_id": manifest_id,
            "objects_dir": store_mod.OBJECTS_DIR,
            "object_ids": store.object_ids(),
            "note": {
                "object_id": first.object_id,
                "title": first.title,
                "content": note_body,
                "expected_tags": ["research", "deals/q1"],
                "expected_link_targets": ["Second Note", "Evidence"],
                "expected_typed_relationship": {"Evidence": "supports"},
                "word_count": first.word_count,
                "size_bytes": first.size_bytes,
            },
            "second_note": {
                "object_id": second.object_id,
                "title": second.title,
                "folder_path": second.folder_path,
                "content": "Plain body, no links.\n",
            },
            "folder": {"object_id": folder.object_id, "path": folder.folder_path},
            "attachment": {
                "object_id": attachment.object_id,
                "filename": attachment.filename,
                "data_hex": attachment_data.hex(),
            },
        },
    )
    print(f"wrote {(root).relative_to(ROOT)}/ ({len(store.object_ids())} objects)")


def generate_public_layer() -> None:
    """A public Markdown layer plus a ``workspace.json``, written by Python.

    Public layers have no ciphertext to pin, but they have three things a port can
    get wrong silently: the derived note id, frontmatter parsing, and the
    descriptor schema. All three are pinned here.
    """
    import shutil

    from app.domain.layer import LayerAIPolicy, LayerDescriptor
    from app.domain.views import ViewConfig, ViewFilter, ViewSort
    from app.domain.workspace import KnowledgeLens, WorkspaceDescriptor
    from app.infrastructure.storage.markdown_store import MarkdownLayerStore, note_id_for
    from app.infrastructure.storage.workspace_store import WorkspaceStore

    root = OUT / "public_workspace"
    if root.exists():
        shutil.rmtree(root)

    timestamp = "2020-01-01T00:00:00+00:00"
    public_id = "layer_public01"
    private_id = "layer_private1"

    store = WorkspaceStore(root)
    descriptor = WorkspaceDescriptor(
        id="ws-kat-0001",
        name="KAT Workspace",
        created_at=timestamp,
        updated_at=timestamp,
        layer_order=[private_id, public_id],
        layers=[
            LayerDescriptor(
                id=public_id,
                display_name="Public",
                created_at=timestamp,
                updated_at=timestamp,
            ),
            LayerDescriptor(
                id=private_id,
                display_name="Private",
                visibility="private",
                state="locked",
                storage="encrypted-objects",
                sharing_mode="shared-password",
                color="layer-private",
                created_at=timestamp,
                updated_at=timestamp,
                ai_policy=LayerAIPolicy(
                    access="remote-with-confirmation",
                    embeddings="disabled",
                    may_apply_approved_edits=True,
                ),
            ),
        ],
        lenses=[
            KnowledgeLens(
                id="lens-1",
                name="Deals only",
                visible_layer_ids=[public_id],
                tag_filters=["deals"],
                property_filters={"status": "open"},
                graph_camera={"x": 1.5, "y": -2.0, "zoom": 0.75},
                time_range_days=30,
                is_default=True,
            )
        ],
        saved_views=[
            ViewConfig(
                id="view-1",
                name="Open items",
                type="kanban",
                layer_ids=[public_id],
                filters=[ViewFilter(field="status", operator="not_equals", value="done")],
                sort=[ViewSort(field="updated", direction="desc")],
                group_by="status",
                date_field="updated",
            )
        ],
    )
    store.initialise(descriptor)

    layer_root = store.layer_root(public_id)
    markdown = MarkdownLayerStore(public_id, layer_root)
    markdown.ensure()
    markdown.write_note(
        folder_path="",
        title="Northwind",
        content=(
            "Body with #research and a [[Second Note]] link.\n\n"
            "supports:: [[Evidence]]\n"
        ),
        properties={"status": "open", "priority": 2, "draft": False, "tags": ["deals"]},
    )
    markdown.write_note(
        folder_path="Deals",
        title="Second Note",
        content="Nested note, no frontmatter of interest.\n",
        properties={},
    )

    notes = markdown.list_notes()
    assert len(notes) == 2, notes
    first = next(note for note in notes if note.metadata.title == "Northwind")
    second = next(note for note in notes if note.metadata.title == "Second Note")

    _write_json(
        "public_workspace.json",
        {
            "description": "A public Markdown layer + workspace.json written by Python",
            "directory": "public_workspace",
            "workspace_file": WorkspaceStore(root).descriptor_path.name,
            "workspace_id": descriptor.id,
            "public_layer_id": public_id,
            "private_layer_id": private_id,
            "layer_order": list(descriptor.layer_order),
            "ordered_layer_ids": [layer.id for layer in descriptor.ordered_layers()],
            "notes": [
                {
                    "id": first.metadata.id,
                    "relative_path": "Northwind.md",
                    "title": first.metadata.title,
                    "folder_path": first.metadata.folder_path,
                    "content": first.content,
                    "tags": list(first.metadata.tags),
                    "properties": dict(first.metadata.properties),
                    "link_targets": [link.target_title for link in first.metadata.links],
                    "word_count": first.metadata.word_count,
                },
                {
                    "id": second.metadata.id,
                    "relative_path": "Deals/Second Note.md",
                    "title": second.metadata.title,
                    "folder_path": second.metadata.folder_path,
                    "content": second.content,
                    "tags": list(second.metadata.tags),
                    "properties": dict(second.metadata.properties),
                    "link_targets": [],
                    "word_count": second.metadata.word_count,
                },
            ],
            "folders": [
                {"id": folder.id, "path": folder.path, "name": folder.name}
                for folder in markdown.list_folders()
            ],
            "note_id_recipe": {
                "layer_id": public_id,
                "relative_path": "Northwind.md",
                "expected": note_id_for(public_id, "Northwind.md"),
            },
        },
    )
    print(f"wrote {root.relative_to(ROOT)}/ ({len(notes)} notes)")


def generate_manifest() -> None:
    """Index of all vectors for the C# discovery test."""
    files = sorted(p.name for p in OUT.iterdir() if p.is_file() and p.name != "manifest.json")
    _write_json(
        "manifest.json",
        {
            "format_fixture_version": 1,
            "files": files,
            "required": [
                "primitives.json",
                "container_note.json",
                "container_note.bin",
                "layer_header.json",
                "rotation_journal.json",
                "crdt_update.json",
                "crdt_update.bin",
                "store_layer.json",
                "public_workspace.json",
            ],
            # Vectors that are whole directories, not single files.
            "directories": ["store_layer", "public_workspace"],
        },
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    generate_primitives()
    generate_container()
    generate_layer_header()
    generate_rotation_journal()
    generate_crdt_seal()
    generate_store_layer()
    generate_public_layer()
    generate_manifest()
    print("done")


if __name__ == "__main__":
    main()
