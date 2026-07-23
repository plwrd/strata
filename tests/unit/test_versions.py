"""Per-note version history: every mutation leaves a restorable trail.

The properties under test: the trail records the state *before* each change
with the origin of that change, follows the note across rename and move
(path-derived ids change), restores without silent overwrites, and never
exists at all for private layers.
"""

from __future__ import annotations

from app.domain.operations import Operation, OperationPlan
from app.services.container import Services
from app.services.version_service import MAX_VERSIONS


def _create(workspace: Services, title: str = "Versioned Note"):
    layer = workspace.workspace.descriptor.layers[0].id
    return workspace.notes.create_note(layer_id=layer, title=title, content="v1 body")


def test_updates_capture_the_previous_state(workspace: Services) -> None:
    note = _create(workspace)
    workspace.notes.update_note(note.metadata.id, "v2 body")
    workspace.notes.update_note(note.metadata.id, "v3 body")

    versions = workspace.notes.list_versions(note.metadata.id)
    assert [v.change for v in versions] == ["update", "update"]
    assert versions[0].origin == "human"
    oldest = workspace.notes.get_version(note.metadata.id, 0)
    assert oldest.content == "v1 body"


def test_restore_is_itself_versioned(workspace: Services) -> None:
    note = _create(workspace)
    workspace.notes.update_note(note.metadata.id, "v2 body")

    restored = workspace.notes.restore_version(note.metadata.id, 0)

    assert restored.content.strip() == "v1 body"
    # The v2 state was captured before being replaced — nothing silently lost.
    versions = workspace.notes.list_versions(note.metadata.id)
    assert versions[0].change == "restore"
    assert workspace.notes.get_version(note.metadata.id, versions[0].index).content == "v2 body"


def test_history_follows_a_rename(workspace: Services) -> None:
    note = _create(workspace)
    workspace.notes.update_note(note.metadata.id, "v2 body")

    renamed, _ = workspace.notes.rename_note(note.metadata.id, "New Name")

    assert renamed.metadata.id != note.metadata.id
    versions = workspace.notes.list_versions(renamed.metadata.id)
    assert [v.change for v in versions] == ["rename", "update"]


def test_history_follows_a_move(workspace: Services) -> None:
    note = _create(workspace)
    moved = workspace.notes.move_note(note.metadata.id, "Inbox")

    versions = workspace.notes.list_versions(moved.metadata.id)
    assert [v.change for v in versions] == ["move"]


def test_ai_mutations_are_attributed_to_their_plan(workspace: Services) -> None:
    note = _create(workspace)
    layer = note.metadata.layer_id
    plan = OperationPlan(
        id="plan_versions",
        summary="Update",
        operations=[
            Operation(
                type="update_note",
                layer_id=layer,
                note_id=note.metadata.id,
                content="rewritten by AI",
                rationale="test",
            )
        ],
    )
    review = workspace.operations.review(plan, allowed_layer_ids=[layer])
    workspace.operations.apply(review, approved_indexes=[0], allowed_layer_ids=[layer])

    versions = workspace.notes.list_versions(note.metadata.id)
    assert versions[0].origin == "ai:plan_versions"


def test_private_layers_have_no_version_files(workspace: Services) -> None:
    private, _recovery = workspace.workspace.create_layer(
        "Vault", visibility="private", password="a sound passphrase"
    )
    note = workspace.notes.create_note(layer_id=private.id, title="Secret", content="private body")
    workspace.notes.update_note(note.metadata.id, "private body 2")

    assert workspace.notes.list_versions(note.metadata.id) == []
    versions_root = workspace.workspace.root / ".strata" / "versions"
    if versions_root.exists():
        raw = "".join(p.read_text(encoding="utf-8") for p in versions_root.rglob("*.jsonl"))
        assert "private body" not in raw


def test_the_trail_is_capped(workspace: Services) -> None:
    note = _create(workspace)
    for i in range(MAX_VERSIONS + 5):
        workspace.notes.update_note(note.metadata.id, f"body {i}")

    versions = workspace.notes.list_versions(note.metadata.id)
    assert len(versions) == MAX_VERSIONS
    # The oldest surviving entries are the most recent ones — FIFO drop.
    assert workspace.notes.get_version(note.metadata.id, 0).content == "body 4"
