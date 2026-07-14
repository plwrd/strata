"""The required Milestone 3 workflows, end to end, against real ciphertext.

These follow the acceptance workflows in the product brief §47 exactly:

* create a private layer, add content, "close the application", inspect the disk;
* confirm no plaintext names, no plaintext content, no readable structure;
* reopen, confirm the layer is locked and reveals nothing;
* unlock, confirm the content is there;
* lock, confirm every surface (editor, search, graph, export) forgets it again.

The plaintext scanner from `scripts/scan_plaintext.py` is pointed at the real layer
directory here — the same tool CI runs — so "the disk reveals nothing" is checked
by the thing that was written to check it, not by an assertion that agrees with the
implementation by construction.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.scan_plaintext import scan_layer

from app.domain.errors import LayerLockedError, NotFoundError
from app.domain.graph import LOCKED_NODE_LABEL
from app.infrastructure.encryption.primitives import DecryptionError
from app.services.container import Paths, Services

pytestmark = pytest.mark.security

PASSWORD = "correct horse battery staple"
SECRET_TITLE = "Acquisition Of Northwind"
SECRET_BODY = "We will offer 4.2 million for Northwind in Q3. Codename BLUEJAY."
SECRET_TAG = "bluejay"
SECRET_FOLDER = "Mergers"
SECRET_ATTACHMENT = b"%PDF-1.7 term sheet: Northwind, 4.2m"

MARKERS = ["Northwind", "BLUEJAY", "Acquisition", "Mergers", "bluejay", "4.2 million"]


def build_private_workspace(services: Services) -> tuple[str, str]:
    """Create a private layer with real content. Returns (layer id, note id)."""
    services.workspace.open_or_create(services.paths.default_workspace, "Test")
    layer, recovery = services.workspace.create_layer(
        "Deals", visibility="private", password=PASSWORD
    )
    assert recovery is not None

    services.notes.create_folder(layer.id, "", SECRET_FOLDER)
    note = services.notes.create_note(
        layer_id=layer.id,
        folder_path=SECRET_FOLDER,
        title=SECRET_TITLE,
        content=f"{SECRET_BODY}\n\n#{SECRET_TAG}\n\nsupports:: [[Strata Overview]]\n",
        properties={"type": "decision", "status": "confidential"},
    )
    services.notes.save_attachment(layer.id, "term-sheet.pdf", SECRET_ATTACHMENT)
    return layer.id, note.metadata.id


def layer_root(services: Services, layer_id: str) -> Path:
    return services.paths.default_workspace / "layers" / layer_id


def all_bytes_under(root: Path) -> bytes:
    return b"".join(path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file())


def all_names_under(root: Path) -> str:
    return "\n".join(str(path.relative_to(root)) for path in sorted(root.rglob("*")))


def notes_from(services: Services, layer_id: str) -> list[str]:
    """Titles the given layer contributes. A locked layer must contribute none."""
    return [
        note.metadata.title
        for note in services.notes.list_notes()
        if note.metadata.layer_id == layer_id
    ]


# --- the disk ---------------------------------------------------------------


def test_the_disk_reveals_no_names(services: Services) -> None:
    layer_id, _note_id = build_private_workspace(services)
    root = layer_root(services, layer_id)

    names = all_names_under(root)

    for marker in MARKERS:
        assert marker not in names
    assert "term-sheet" not in names
    assert ".md" not in names
    assert ".pdf" not in names
    # Only the header and opaque object shards exist.
    assert {path.name for path in root.iterdir()} == {"layer.header", "objects"}


def test_the_disk_reveals_no_content(services: Services) -> None:
    layer_id, _note_id = build_private_workspace(services)

    blob = all_bytes_under(layer_root(services, layer_id))

    for marker in MARKERS:
        assert marker.encode() not in blob
    assert SECRET_BODY.encode() not in blob
    assert SECRET_ATTACHMENT not in blob
    assert b"%PDF" not in blob
    assert b"status" not in blob
    assert b"confidential" not in blob


def test_the_plaintext_scanner_passes_on_a_real_private_layer(services: Services) -> None:
    """The tool CI runs, pointed at a layer this code actually produced."""
    layer_id, _note_id = build_private_workspace(services)

    findings = scan_layer(layer_root(services, layer_id), MARKERS)

    assert findings == [], "\n".join(str(finding) for finding in findings)


def test_object_filenames_are_opaque_and_unrelated_to_the_titles(services: Services) -> None:
    layer_id, _note_id = build_private_workspace(services)
    root = layer_root(services, layer_id)

    files = [path for path in (root / "objects").rglob("*") if path.is_file()]

    assert len(files) >= 3  # manifest + note + attachment
    for path in files:
        assert len(path.name) == 32
        assert all(char in "0123456789abcdef" for char in path.name)
        assert path.suffix == ""
        assert path.parent.name == path.name[:2]


def test_two_identical_notes_get_different_object_ids_and_ciphertexts(
    services: Services,
) -> None:
    """No deterministic naming and no deterministic encryption: identical content
    must not be identifiable as identical from the disk."""
    layer_id, _note_id = build_private_workspace(services)

    first = services.notes.create_note(
        layer_id=layer_id, folder_path="", title="Twin A", content="identical body"
    )
    second = services.notes.create_note(
        layer_id=layer_id, folder_path="", title="Twin B", content="identical body"
    )

    root = layer_root(services, layer_id)
    blob_a = (root / "objects" / first.metadata.id[:2] / first.metadata.id).read_bytes()
    blob_b = (root / "objects" / second.metadata.id[:2] / second.metadata.id).read_bytes()

    assert first.metadata.id != second.metadata.id
    assert blob_a != blob_b  # different nonces, different object ids in the AAD
    assert len(blob_a) == len(blob_b)  # ...but padded to the same bucket


# --- close, reopen, unlock, lock --------------------------------------------


def test_closing_the_workspace_locks_the_layer_and_forgets_the_key(
    services: Services,
) -> None:
    layer_id, _note_id = build_private_workspace(services)
    assert services.encryption.is_unlocked(layer_id)

    services.workspace.close()

    assert not services.encryption.is_unlocked(layer_id)


def test_reopening_finds_the_layer_locked_even_if_the_file_says_otherwise(
    services: Services, paths: Paths
) -> None:
    """`workspace.json` is not a security boundary. Editing it must not unlock."""
    import json

    layer_id, _note_id = build_private_workspace(services)
    services.workspace.close()

    descriptor_path = paths.default_workspace / "workspace.json"
    raw = json.loads(descriptor_path.read_text(encoding="utf-8"))
    for layer in raw["layers"]:
        if layer["id"] == layer_id:
            layer["state"] = "unlocked"  # a lie
    descriptor_path.write_text(json.dumps(raw), encoding="utf-8")

    reopened = Services(paths, environment="test")
    reopened.workspace.open(paths.default_workspace)

    layer = reopened.workspace.require_layer(layer_id)
    assert layer.state == "locked"
    with pytest.raises(LayerLockedError):
        reopened.workspace.require_readable_layer(layer_id)
    # The public layer is still readable; the private one contributes nothing.
    assert notes_from(reopened, layer_id) == []


def test_a_locked_layer_reveals_nothing_through_any_surface(
    services: Services, paths: Paths
) -> None:
    layer_id, note_id = build_private_workspace(services)
    services.workspace.close()

    reopened = Services(paths, environment="test")
    reopened.workspace.open(paths.default_workspace)

    # notes
    assert notes_from(reopened, layer_id) == []
    assert [f for f in reopened.notes.list_folders() if f.layer_id == layer_id] == []
    with pytest.raises(NotFoundError):
        reopened.notes.get_note(note_id)

    # search — not "fewer results", but no result that came from the locked layer,
    # and nothing containing the secrets.
    for marker in MARKERS:
        for result in reopened.search.search(marker):
            assert result.layer_id != layer_id
            assert SECRET_BODY not in result.snippet
            assert SECRET_TITLE != result.title

    # graph
    snapshot = reopened.graph.build()
    payload = snapshot.model_dump_json()
    for marker in MARKERS:
        assert marker not in payload
    locked_nodes = [node for node in snapshot.nodes if node.locked]
    assert len(locked_nodes) == 1
    assert locked_nodes[0].label == LOCKED_NODE_LABEL

    # export
    plan = reopened.exports.plan(object_ids=[note_id], prompt="Summarise")
    assert plan.sources == []
    assert plan.excluded_locked_count == 1

    # trash — a locked layer must not even say how much was deleted
    assert reopened.notes.list_trash() == []


def test_unlocking_reveals_the_content(services: Services, paths: Paths) -> None:
    layer_id, note_id = build_private_workspace(services)
    services.workspace.close()

    reopened = Services(paths, environment="test")
    reopened.workspace.open(paths.default_workspace)
    reopened.workspace.unlock_layer(layer_id, PASSWORD)

    note = reopened.notes.get_note(note_id)
    assert note.metadata.title == SECRET_TITLE
    assert SECRET_BODY in note.content
    assert SECRET_TAG in note.metadata.tags
    assert note.metadata.folder_path == SECRET_FOLDER
    assert note.metadata.properties["status"] == "confidential"

    assert reopened.search.search("Northwind")[0].title == SECRET_TITLE
    assert SECRET_TITLE in {node.label for node in reopened.graph.build().nodes}


def test_locking_again_forgets_everything(services: Services) -> None:
    layer_id, note_id = build_private_workspace(services)

    assert services.notes.get_note(note_id).metadata.title == SECRET_TITLE

    services.workspace.lock_layer(layer_id)

    with pytest.raises(NotFoundError):
        services.notes.get_note(note_id)
    assert services.search.search("Northwind") == []
    assert SECRET_TITLE not in services.graph.build().model_dump_json()
    # The decrypted manifest cache must be gone too — it holds every title.
    with pytest.raises(LayerLockedError):
        services.workspace.private_access(layer_id)


# --- wrong password ---------------------------------------------------------


def test_a_wrong_password_fails_generically_and_changes_nothing(services: Services) -> None:
    layer_id, _note_id = build_private_workspace(services)
    root = layer_root(services, layer_id)
    services.workspace.lock_layer(layer_id)

    before = {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}

    for attempt in ("", "x", "wrong password", PASSWORD + " "):
        with pytest.raises((DecryptionError, Exception)):
            services.workspace.unlock_layer(layer_id, attempt)

    after = {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}

    assert before == after  # no corruption, no lockout counter, no partial write
    assert not services.encryption.is_unlocked(layer_id)

    services.workspace.unlock_layer(layer_id, PASSWORD)
    assert services.encryption.is_unlocked(layer_id)


# --- password change and rotation -------------------------------------------


def test_changing_the_password_keeps_the_content(services: Services) -> None:
    layer_id, note_id = build_private_workspace(services)

    services.workspace.change_layer_password(layer_id, PASSWORD, "a whole new password")
    services.workspace.lock_layer(layer_id)

    with pytest.raises(DecryptionError):
        services.workspace.unlock_layer(layer_id, PASSWORD)

    services.workspace.unlock_layer(layer_id, "a whole new password")
    assert SECRET_BODY in services.notes.get_note(note_id).content


def test_rotating_the_key_keeps_the_content_and_invalidates_the_old_key(
    services: Services,
) -> None:
    layer_id, note_id = build_private_workspace(services)
    old_key = services.encryption.keys.key_for(layer_id)

    rewritten = services.workspace.rotate_layer_key(layer_id, PASSWORD)

    assert rewritten >= 3  # manifest, note, attachment
    assert services.encryption.keys.key_for(layer_id) != old_key
    assert SECRET_BODY in services.notes.get_note(note_id).content

    # The old key opens nothing any more — which is the point of rotation.
    from app.infrastructure.encryption.layer_header import LayerHeader
    from app.infrastructure.storage.encrypted_store import EncryptedLayerStore

    root = layer_root(services, layer_id)
    header = LayerHeader.load(root)
    store = EncryptedLayerStore(layer_id, root)
    with pytest.raises(DecryptionError):
        store.read_manifest(old_key, header.manifest_object_id)


def test_the_layer_still_scans_clean_after_a_rotation(services: Services) -> None:
    layer_id, _note_id = build_private_workspace(services)

    services.workspace.rotate_layer_key(layer_id, PASSWORD)

    assert scan_layer(layer_root(services, layer_id), MARKERS) == []


# --- private notes behave like notes -----------------------------------------


def test_private_notes_support_the_full_editing_surface(services: Services) -> None:
    layer_id, note_id = build_private_workspace(services)

    updated = services.notes.update_note(note_id, "A completely new body.\n")
    assert updated.content.strip() == "A completely new body."

    renamed, _rewritten = services.notes.rename_note(note_id, "Project Bluebird")
    assert renamed.metadata.title == "Project Bluebird"
    # The object id is random, not derived from the name, so a rename keeps it.
    assert renamed.metadata.id == note_id

    moved = services.notes.move_note(note_id, "")
    assert moved.metadata.folder_path == ""

    duplicate = services.notes.duplicate_note(note_id)
    assert duplicate.metadata.title == "Project Bluebird copy"

    entry = services.notes.delete_note(note_id)
    assert all(note.metadata.id != note_id for note in services.notes.list_notes())

    restored = services.notes.restore_from_trash(entry)
    assert restored.metadata.id == note_id


def test_deleting_a_private_note_does_not_decrypt_it(services: Services) -> None:
    """A private note must not be moved into the workspace's plaintext trash."""
    layer_id, note_id = build_private_workspace(services)

    services.notes.delete_note(note_id)

    trash_dir = services.paths.default_workspace / ".strata" / "trash"
    if trash_dir.exists():
        blob = all_bytes_under(trash_dir)
        for marker in MARKERS:
            assert marker.encode() not in blob

    # It is still there, still encrypted, and still listed as trash while unlocked.
    assert [entry["title"] for entry in services.notes.list_trash()] == [SECRET_TITLE]
    assert scan_layer(layer_root(services, layer_id), MARKERS) == []


def test_emptying_the_trash_destroys_the_ciphertext(services: Services) -> None:
    layer_id, note_id = build_private_workspace(services)
    root = layer_root(services, layer_id)
    before = len([p for p in (root / "objects").rglob("*") if p.is_file()])

    services.notes.delete_note(note_id)
    services.notes.empty_trash()

    after = len([p for p in (root / "objects").rglob("*") if p.is_file()])
    assert after == before - 1
    assert not (root / "objects" / note_id[:2] / note_id).exists()


def test_a_private_attachment_is_encrypted_and_opaquely_named(services: Services) -> None:
    layer_id, _note_id = build_private_workspace(services)

    reference = services.notes.save_attachment(layer_id, "passport-scan.pdf", b"very sensitive")

    assert reference.startswith("strata-object://")
    assert "passport" not in reference

    blob = all_bytes_under(layer_root(services, layer_id))
    assert b"very sensitive" not in blob
    assert b"passport" not in blob


# --- cross-layer ------------------------------------------------------------


def test_a_public_note_linking_to_a_private_one_reveals_nothing_while_locked(
    services: Services,
) -> None:
    layer_id, _note_id = build_private_workspace(services)
    public_layer = services.workspace.descriptor.layers[0].id

    services.notes.create_note(
        layer_id=public_layer,
        folder_path="",
        title="Deal Tracker",
        content=f"Depends on [[{SECRET_TITLE}]].\n",
    )
    services.workspace.lock_layer(layer_id)

    snapshot = services.graph.build()
    payload = snapshot.model_dump_json()

    # The public note keeps its own text (it is the user's own file), but the graph
    # must not resolve the link into a node that names the private note.
    assert SECRET_TITLE not in {node.label for node in snapshot.nodes}
    assert LOCKED_NODE_LABEL in payload

    results = services.search.search("Deal Tracker")
    assert len(results) == 1
    assert SECRET_BODY not in results[0].snippet
