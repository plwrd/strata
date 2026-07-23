"""Connection discovery — computed, scored, and never auto-applied."""

from __future__ import annotations

from app.services.container import Services

LONG_TEXT = (
    "Distributed consensus protocols coordinate replicated state machines. "
    "Leader election, log replication and quorum overlap decide safety. "
    "Raft and Paxos differ mainly in how understandable their proofs are."
)


def _note(workspace: Services, title: str, content: str):
    layer = workspace.workspace.descriptor.layers[0].id
    return workspace.notes.create_note(layer_id=layer, title=title, content=content)


def test_similar_notes_are_suggested_with_a_reason(workspace: Services) -> None:
    first = _note(workspace, "Consensus overview", LONG_TEXT)
    _note(
        workspace,
        "Raft notes",
        LONG_TEXT + " Raft splits consensus into leader election and replication.",
    )

    suggestions = workspace.connections.suggest_for_note(first.metadata.id)

    similar = [s for s in suggestions if s.kind in ("similar", "duplicate")]
    assert any(s.note_b_title == "Raft notes" for s in similar)
    chosen = next(s for s in similar if s.note_b_title == "Raft notes")
    assert chosen.score > 0.4
    assert "similar" in chosen.explanation


def test_near_identical_notes_are_flagged_as_duplicates_never_merged(
    workspace: Services,
) -> None:
    first = _note(workspace, "Original", LONG_TEXT)
    _note(workspace, "Original copy", LONG_TEXT)

    suggestions = workspace.connections.suggest_for_note(first.metadata.id)

    duplicate = next(s for s in suggestions if s.kind == "duplicate")
    assert duplicate.suggested_relationship == "supersedes"
    assert "nothing merges automatically" in duplicate.explanation
    # And the notes are untouched — discovery never writes.
    assert len([n for n in workspace.notes.list_notes() if "Original" in n.metadata.title]) == 2


def test_unlinked_mentions_are_suggested(workspace: Services) -> None:
    target = _note(workspace, "Quorum Overlap", "The property itself.")
    _note(
        workspace,
        "Meeting notes",
        "We discussed Quorum Overlap at length but never linked it.",
    )

    suggestions = workspace.connections.suggest_for_note(target.metadata.id)

    mention = next(s for s in suggestions if s.kind == "mention")
    assert mention.note_a_title == "Meeting notes"
    assert mention.suggested_relationship == "references"
    assert "Quorum Overlap" in mention.excerpt


def test_already_linked_notes_are_not_resuggested(workspace: Services) -> None:
    first = _note(workspace, "Alpha topic", LONG_TEXT)
    _note(
        workspace,
        "Beta topic",
        LONG_TEXT + "\n\nrelates_to:: [[Alpha topic]]\n",
    )

    suggestions = workspace.connections.suggest_for_note(first.metadata.id)

    assert all(
        s.note_b_title != "Beta topic" and s.note_a_title != "Beta topic" for s in suggestions
    )


def test_workspace_duplicates_finds_the_pair(workspace: Services) -> None:
    _note(workspace, "Dup A", LONG_TEXT)
    _note(workspace, "Dup B", LONG_TEXT)

    duplicates = workspace.connections.workspace_duplicates()

    titles = {(d.note_a_title, d.note_b_title) for d in duplicates}
    assert any({"Dup A", "Dup B"} == set(pair) for pair in titles)
