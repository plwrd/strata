"""The transactional AI change engine: validation, diff, apply, rollback, undo."""

from __future__ import annotations

import pytest

from app.domain.errors import ConflictError, InvalidRequestError
from app.domain.operations import Operation, OperationPlan
from app.services.container import Services


def note_id(services: Services, title: str) -> str:
    return next(
        note.metadata.id for note in services.notes.list_notes() if note.metadata.title == title
    )


def plan(services: Services, *operations: Operation, summary: str = "Test plan") -> OperationPlan:
    return OperationPlan(id="plan_test", summary=summary, operations=list(operations))


def layer_id(services: Services) -> str:
    return services.workspace.descriptor.layers[0].id


# --- validation and containment ---------------------------------------------


def test_a_valid_plan_reviews_cleanly(workspace: Services) -> None:
    p = plan(
        workspace,
        Operation(type="create_folder", layer_id=layer_id(workspace), folder_path="Research"),
        Operation(
            type="create_note",
            layer_id=layer_id(workspace),
            folder_path="Research",
            title="New Idea",
            content="# New Idea\n\nBody.",
            rationale="capture the idea",
        ),
    )

    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    assert review.valid_count == 2
    assert review.invalid_count == 0
    assert review.entries[1].after.startswith("# New Idea")


@pytest.mark.security()
def test_an_operation_outside_the_allowed_layers_is_rejected(workspace: Services) -> None:
    """Containment: the AI cannot reach a layer the user did not put in scope."""
    p = plan(
        workspace,
        Operation(type="create_note", layer_id="layer_not_in_scope", title="Sneaky"),
    )

    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    assert review.valid_count == 0
    assert "not in scope" in review.entries[0].problem


@pytest.mark.security()
def test_an_operation_targeting_a_locked_layer_is_rejected(workspace: Services) -> None:
    private, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )
    note = workspace.notes.create_note(
        layer_id=private.id, folder_path="", title="Secret", content="BLUEJAY"
    )
    workspace.workspace.lock_layer(private.id)

    p = plan(
        workspace,
        Operation(type="update_note", layer_id=private.id, note_id=note.metadata.id, content="x"),
    )

    review = workspace.operations.review(p, allowed_layer_ids=[private.id])

    assert review.valid_count == 0
    assert "locked" in review.entries[0].problem


def test_an_operation_targeting_a_nonexistent_note_is_rejected(workspace: Services) -> None:
    p = plan(
        workspace,
        Operation(type="update_note", layer_id=layer_id(workspace), note_id="0" * 32, content="x"),
    )

    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    assert review.entries[0].valid is False
    assert "does not exist" in review.entries[0].problem


def test_a_note_operation_without_a_target_is_rejected(workspace: Services) -> None:
    p = plan(workspace, Operation(type="rename_note", layer_id=layer_id(workspace), title="X"))

    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    assert review.entries[0].valid is False


def test_destructive_operations_are_counted_and_flagged(workspace: Services) -> None:
    target = note_id(workspace, "Marketing Claims")
    p = plan(
        workspace,
        Operation(type="delete_note", layer_id=layer_id(workspace), note_id=target),
        Operation(type="update_note", layer_id=layer_id(workspace), note_id=target, content="new"),
    )

    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    assert review.destructive_count == 2
    assert all(entry.is_destructive for entry in review.entries)
    assert any("undoable" in warning for warning in review.warnings)


# --- apply ------------------------------------------------------------------


def test_applying_a_plan_creates_the_content(workspace: Services) -> None:
    p = plan(
        workspace,
        Operation(type="create_folder", layer_id=layer_id(workspace), folder_path="Ideas"),
        Operation(
            type="create_note",
            layer_id=layer_id(workspace),
            folder_path="Ideas",
            title="First Idea",
            content="# First Idea\n",
        ),
    )
    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    applied = workspace.operations.apply(
        review, approved_indexes=[0, 1], allowed_layer_ids=[layer_id(workspace)]
    )

    assert applied.applied_count == 2
    titles = {note.metadata.title for note in workspace.notes.list_notes()}
    assert "First Idea" in titles


def test_only_approved_operations_are_applied(workspace: Services) -> None:
    p = plan(
        workspace,
        Operation(type="create_note", layer_id=layer_id(workspace), title="Approved"),
        Operation(type="create_note", layer_id=layer_id(workspace), title="Rejected"),
    )
    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    workspace.operations.apply(
        review, approved_indexes=[0], allowed_layer_ids=[layer_id(workspace)]
    )

    titles = {note.metadata.title for note in workspace.notes.list_notes()}
    assert "Approved" in titles
    assert "Rejected" not in titles


def test_add_relationship_creates_a_typed_link(workspace: Services) -> None:
    source = note_id(workspace, "Strata Overview")
    p = plan(
        workspace,
        Operation(
            type="add_relationship",
            layer_id=layer_id(workspace),
            note_id=source,
            target_title="Threat Model",
            relationship="depends_on",
        ),
    )
    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    workspace.operations.apply(
        review, approved_indexes=[0], allowed_layer_ids=[layer_id(workspace)]
    )

    note = workspace.notes.get_note(source)
    assert "depends_on:: [[Threat Model]]" in note.content
    # And the graph now has that edge.
    edges = workspace.graph.build().edges
    assert any(edge.relationship == "depends_on" for edge in edges)


def test_applying_with_no_approved_operations_is_rejected(workspace: Services) -> None:
    p = plan(workspace, Operation(type="create_note", layer_id=layer_id(workspace), title="X"))
    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    with pytest.raises(InvalidRequestError):
        workspace.operations.apply(
            review, approved_indexes=[], allowed_layer_ids=[layer_id(workspace)]
        )


# --- the transactional guarantee --------------------------------------------


def test_a_failed_operation_rolls_the_whole_plan_back(workspace: Services) -> None:
    """All-or-nothing: if operation 2 fails, operation 1 is undone too."""
    before = {note.metadata.title for note in workspace.notes.list_notes()}

    p = plan(
        workspace,
        Operation(
            type="create_note", layer_id=layer_id(workspace), title="Should Vanish", content="x"
        ),
        # This one fails: a rename of a note that does not exist. It passes review
        # only because we hand-craft it to reference a real id, then delete the note
        # first — but simpler: target a note that review misses. Instead, force a
        # conflict: create a note whose title collides with an existing one.
        Operation(
            type="create_note",
            layer_id=layer_id(workspace),
            folder_path="Security",
            title="Threat Model",
        ),
    )
    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    with pytest.raises(InvalidRequestError):
        workspace.operations.apply(
            review, approved_indexes=[0, 1], allowed_layer_ids=[layer_id(workspace)]
        )

    after = {note.metadata.title for note in workspace.notes.list_notes()}
    # "Should Vanish" was created then rolled back — the workspace is unchanged.
    assert after == before
    assert "Should Vanish" not in after


def test_the_workspace_changing_after_review_is_a_conflict(workspace: Services) -> None:
    target = note_id(workspace, "Marketing Claims")
    p = plan(
        workspace,
        Operation(type="update_note", layer_id=layer_id(workspace), note_id=target, content="new"),
    )
    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])

    # The note is deleted between review and apply.
    workspace.notes.delete_note(target)

    with pytest.raises(ConflictError):
        workspace.operations.apply(
            review, approved_indexes=[0], allowed_layer_ids=[layer_id(workspace)]
        )


# --- undo -------------------------------------------------------------------


def test_undo_restores_the_pre_plan_state(workspace: Services) -> None:
    before = {note.metadata.title for note in workspace.notes.list_notes()}
    p = plan(
        workspace,
        Operation(type="create_note", layer_id=layer_id(workspace), title="Temporary", content="x"),
    )
    review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])
    applied = workspace.operations.apply(
        review, approved_indexes=[0], allowed_layer_ids=[layer_id(workspace)]
    )

    assert "Temporary" in {note.metadata.title for note in workspace.notes.list_notes()}

    workspace.operations.undo(applied.plan_id)

    after = {note.metadata.title for note in workspace.notes.list_notes()}
    assert after == before
    assert "Temporary" not in after


def test_the_audit_log_records_every_applied_plan(workspace: Services) -> None:
    for i in range(2):
        p = plan(
            workspace,
            Operation(type="create_note", layer_id=layer_id(workspace), title=f"Audit {i}"),
            summary=f"plan {i}",
        )
        review = workspace.operations.review(p, allowed_layer_ids=[layer_id(workspace)])
        workspace.operations.apply(
            review, approved_indexes=[0], allowed_layer_ids=[layer_id(workspace)]
        )

    log = workspace.operations.audit_log()

    assert len(log) == 2
    assert log[0].summary == "plan 1"  # newest first
    assert all(entry.applied_count == 1 for entry in log)
