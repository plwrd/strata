"""File operations, link maintenance, and schemas (Milestone 2)."""

from __future__ import annotations

import pytest

from app.domain.errors import ConflictError, NotFoundError
from app.domain.schema import BUILTIN_SCHEMAS, schema_by_id, validate_properties
from app.services.container import Services


def note_id(services: Services, title: str) -> str:
    return next(
        note.metadata.id for note in services.notes.list_notes() if note.metadata.title == title
    )


# --- editing ---------------------------------------------------------------


def test_updating_a_note_preserves_its_frontmatter(workspace: Services) -> None:
    target = note_id(workspace, "Encryption Architecture")
    before = workspace.notes.get_note(target).metadata.properties

    updated = workspace.notes.update_note(target, "Completely new body.\n")

    assert updated.content.strip() == "Completely new body."
    assert updated.metadata.properties == before
    assert "encryption" in updated.metadata.tags  # tags come from frontmatter


def test_updating_properties_preserves_the_body(workspace: Services) -> None:
    target = note_id(workspace, "Threat Model")
    body = workspace.notes.get_note(target).content

    updated = workspace.notes.update_properties(
        target, {"type": "security-threat", "status": "mitigated"}
    )

    assert updated.content == body
    assert updated.metadata.properties["status"] == "mitigated"


def test_creating_a_duplicate_name_is_a_conflict(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id

    with pytest.raises(ConflictError):
        workspace.notes.create_note(layer_id=layer, folder_path="Security", title="Threat Model")


# --- rename and link maintenance -------------------------------------------


def test_renaming_a_note_repoints_every_link_to_it(workspace: Services) -> None:
    target = note_id(workspace, "Threat Model")

    renamed, rewritten = workspace.notes.rename_note(target, "Adversary Model")

    assert renamed.metadata.title == "Adversary Model"
    # "Encryption Architecture" and "Marketing Claims" both link to it.
    assert rewritten >= 2

    # No note is left pointing at the old title.
    for note in workspace.notes.list_notes():
        assert "[[Threat Model" not in note.content

    # And the graph edge survived the rename.
    edges = workspace.graph.build().edges
    assert any(edge.relationship == "depends_on" for edge in edges)


def test_rename_rewrites_aliased_and_heading_links(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id
    workspace.notes.create_note(
        layer_id=layer,
        folder_path="",
        title="Pointer",
        content="See [[Threat Model|the threats]] and [[Threat Model#Assets]].\n",
    )

    _renamed, rewritten = workspace.notes.rename_note(note_id(workspace, "Threat Model"), "Risks")

    pointer = workspace.notes.get_note(note_id(workspace, "Pointer"))
    assert rewritten >= 2
    assert "[[Risks|the threats]]" in pointer.content
    assert "[[Risks#Assets]]" in pointer.content


def test_rename_does_not_touch_prose_that_merely_mentions_the_title(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id
    workspace.notes.create_note(
        layer_id=layer,
        folder_path="",
        title="Prose",
        content="The Threat Model is discussed here, unlinked.\n",
    )

    workspace.notes.rename_note(note_id(workspace, "Threat Model"), "Risks")

    prose = workspace.notes.get_note(note_id(workspace, "Prose"))
    # Rewriting prose would be editing the user's words, not their links.
    assert "The Threat Model is discussed here" in prose.content


def test_renaming_to_an_existing_name_is_a_conflict(workspace: Services) -> None:
    with pytest.raises(ConflictError):
        workspace.notes.rename_note(note_id(workspace, "Threat Model"), "Marketing Claims")


# --- move, duplicate, trash -------------------------------------------------


def test_moving_a_note_changes_its_folder(workspace: Services) -> None:
    moved = workspace.notes.move_note(note_id(workspace, "Threat Model"), "Architecture")

    assert moved.metadata.folder_path == "Architecture"
    assert moved.metadata.display_path == "Architecture/Threat Model.md"


def test_duplicating_a_note_does_not_collide(workspace: Services) -> None:
    first = workspace.notes.duplicate_note(note_id(workspace, "Threat Model"))
    second = workspace.notes.duplicate_note(note_id(workspace, "Threat Model"))

    assert first.metadata.title == "Threat Model copy"
    assert second.metadata.title == "Threat Model copy 2"


def test_delete_moves_to_trash_and_restore_brings_it_back(workspace: Services) -> None:
    target = note_id(workspace, "Marketing Claims")
    original = workspace.notes.get_note(target)

    entry = workspace.notes.delete_note(target)

    assert "Marketing Claims" not in {n.metadata.title for n in workspace.notes.list_notes()}
    assert len(workspace.notes.list_trash()) == 1

    restored = workspace.notes.restore_from_trash(entry)

    assert restored.metadata.title == "Marketing Claims"
    assert restored.metadata.folder_path == original.metadata.folder_path
    assert restored.content == original.content
    assert workspace.notes.list_trash() == []


def test_deleting_a_note_never_destroys_it(workspace: Services) -> None:
    target = note_id(workspace, "Marketing Claims")
    workspace.notes.delete_note(target)

    trash = workspace.workspace.root / ".strata" / "trash"
    survivors = list(trash.glob("*.md"))

    assert len(survivors) == 1
    assert "military-grade" in survivors[0].read_text(encoding="utf-8")


def test_emptying_the_trash_removes_the_files(workspace: Services) -> None:
    workspace.notes.delete_note(note_id(workspace, "Marketing Claims"))

    assert workspace.notes.empty_trash() == 1
    assert workspace.notes.list_trash() == []


def test_restoring_over_a_recreated_note_is_a_conflict(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id
    entry = workspace.notes.delete_note(note_id(workspace, "Marketing Claims"))
    workspace.notes.create_note(layer_id=layer, folder_path="Security", title="Marketing Claims")

    with pytest.raises(ConflictError):
        workspace.notes.restore_from_trash(entry)


# --- folders ---------------------------------------------------------------


def test_creating_and_renaming_a_folder(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id

    folder = workspace.notes.create_folder(layer, "", "Research")
    assert folder.path == "Research"

    renamed = workspace.notes.rename_folder(folder.id, "Deep Research")
    assert renamed.path == "Deep Research"
    assert (workspace.workspace.root / "layers" / layer / "Deep Research").is_dir()


def test_deleting_a_folder_trashes_its_notes(workspace: Services) -> None:
    folder = next(f for f in workspace.notes.list_folders() if f.name == "Security")

    trashed = workspace.notes.delete_folder(folder.id)

    assert trashed == 3  # Encryption Architecture, Threat Model, Marketing Claims
    assert len(workspace.notes.list_trash()) == 3
    assert not (workspace.workspace.root / "layers" / folder.layer_id / "Security").exists()


def test_creating_a_duplicate_folder_is_a_conflict(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id

    with pytest.raises(ConflictError):
        workspace.notes.create_folder(layer, "", "Security")


# --- links -----------------------------------------------------------------


def test_backlinks_report_the_relationship_and_the_context(workspace: Services) -> None:
    backlinks = workspace.notes.backlinks(note_id(workspace, "Threat Model"))

    by_title = {backlink.source_title: backlink for backlink in backlinks}
    assert "Encryption Architecture" in by_title
    assert by_title["Encryption Architecture"].relationship == "depends_on"
    assert by_title["Encryption Architecture"].context


def test_unlinked_mentions_find_prose_references(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id
    workspace.notes.create_note(
        layer_id=layer,
        folder_path="",
        title="Mentions It",
        content="We should revisit the Threat Model before shipping.\n",
    )

    mentions = workspace.notes.unlinked_mentions(note_id(workspace, "Threat Model"))

    assert "Mentions It" in {mention.source_title for mention in mentions}


def test_a_note_that_already_links_is_not_an_unlinked_mention(workspace: Services) -> None:
    mentions = workspace.notes.unlinked_mentions(note_id(workspace, "Threat Model"))

    # Encryption Architecture links to it, so it is a backlink, not a mention.
    assert "Encryption Architecture" not in {mention.source_title for mention in mentions}


def test_link_health_reports_broken_links_and_orphans(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id
    workspace.notes.create_note(
        layer_id=layer, folder_path="", title="Dangling", content="[[Nowhere At All]]\n"
    )
    workspace.notes.create_note(
        layer_id=layer, folder_path="", title="Alone", content="No links.\n"
    )

    health = workspace.notes.link_health()

    assert ("Nowhere At All") in [target for _source, target in health.broken]
    assert note_id(workspace, "Alone") in health.orphans
    assert note_id(workspace, "Threat Model") not in health.orphans


# --- attachments -----------------------------------------------------------


def test_attachments_are_stored_inside_the_layer(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id

    path = workspace.notes.save_attachment(layer, "diagram.png", b"\x89PNG\r\n\x1a\n fake")

    assert path == "attachments/diagram.png"
    stored = workspace.workspace.root / "layers" / layer / path
    assert stored.is_file()


def test_attachment_names_are_sanitised(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id

    path = workspace.notes.save_attachment(layer, "../../escape.png", b"x")

    assert ".." not in path
    assert path.startswith("attachments/")


def test_duplicate_attachments_do_not_overwrite(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id

    first = workspace.notes.save_attachment(layer, "a.png", b"one")
    second = workspace.notes.save_attachment(layer, "a.png", b"two")

    assert first != second
    root = workspace.workspace.root / "layers" / layer
    assert (root / first).read_bytes() == b"one"


# --- schemas ---------------------------------------------------------------


def test_every_builtin_schema_is_well_formed() -> None:
    for schema in BUILTIN_SCHEMAS:
        assert schema.properties, f"{schema.id} has no properties"
        assert schema.id == schema.id.lower()


def test_schema_validation_reports_missing_required_properties() -> None:
    schema = schema_by_id("task")
    assert schema is not None

    issues = validate_properties(schema, {"type": "task"})

    assert {issue.key for issue in issues} == {"status"}
    assert issues[0].problem == "required"


def test_schema_validation_rejects_a_value_outside_its_options() -> None:
    schema = schema_by_id("task")
    assert schema is not None

    issues = validate_properties(schema, {"type": "task", "status": "vibing"})

    assert issues[0].key == "status"
    assert "not an allowed option" in issues[0].problem


def test_schema_validation_checks_ranges_and_formats() -> None:
    schema = schema_by_id("research-source")
    assert schema is not None

    issues = validate_properties(
        schema,
        {"type": "research-source", "url": "not-a-url", "credibility": 9},
    )

    problems = {issue.key: issue.problem for issue in issues}
    assert problems["url"] == "expected a URL"
    assert "above the maximum" in problems["credibility"]


def test_a_valid_note_produces_no_issues() -> None:
    schema = schema_by_id("decision")
    assert schema is not None

    assert validate_properties(schema, {"type": "decision", "status": "accepted"}) == []


def test_validation_never_rewrites_the_note(workspace: Services) -> None:
    """A note that violates its schema is reported, never corrected."""
    target = note_id(workspace, "Marketing Claims")
    workspace.notes.update_properties(target, {"type": "decision", "status": "nonsense"})

    note = workspace.notes.get_note(target)

    assert note.metadata.properties["status"] == "nonsense"  # the file still wins
    schema = schema_by_id("decision")
    assert schema is not None
    assert validate_properties(schema, note.metadata.properties)


def test_missing_note_operations_report_not_found(workspace: Services) -> None:
    for call in (
        lambda: workspace.notes.update_note("0" * 32, "x"),
        lambda: workspace.notes.rename_note("0" * 32, "x"),
        lambda: workspace.notes.delete_note("0" * 32),
        lambda: workspace.notes.backlinks("0" * 32),
    ):
        with pytest.raises(NotFoundError):
            call()
