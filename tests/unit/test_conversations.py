"""Conversations: the backend owns the thread, and redacted turns stay dark.

The replay rule is the point: what re-enters a model context comes only from
what Python stored, and a private-layer turn stored redacted contributes
nothing — on disk or in replay.
"""

from __future__ import annotations

from app.services.container import Paths, Services


def _turn(workspace: Services, conversation_id: str, prompt: str, private: bool = False):
    return workspace.conversations.append_turn(
        conversation_id,
        execution_id="exec_x",
        prompt=prompt,
        response_text=f"answer to {prompt}",
        provider="ollama",
        model="llama3",
        involves_private=private,
    )


def test_turns_accumulate_and_survive_a_restart(workspace: Services, paths: Paths) -> None:
    conversation = _turn(workspace, "", "first question")
    _turn(workspace, conversation.id, "second question")

    fresh = Services(paths, environment="test")
    fresh.workspace.open(paths.default_workspace)

    reloaded = fresh.conversations.get(conversation.id)
    assert reloaded.turn_count == 2
    assert reloaded.title == "first question"
    assert [t.prompt for t in fresh.conversations.replayable_turns(conversation.id)] == [
        "first question",
        "second question",
    ]


def test_private_turns_are_redacted_on_disk_and_never_replayed(
    workspace: Services,
) -> None:
    conversation = _turn(workspace, "", "public question")
    _turn(workspace, conversation.id, "SENTINEL-PRIVATE-Q-51", private=True)

    replayable = workspace.conversations.replayable_turns(conversation.id)
    assert [t.prompt for t in replayable] == ["public question"]

    raw = (workspace.workspace.root / ".strata" / "ai" / "conversations.jsonl").read_text(
        encoding="utf-8"
    )
    assert "SENTINEL-PRIVATE-Q-51" not in raw
    # The thread keeps its shape: the redacted turn exists, empty and marked.
    stored = workspace.conversations.get(conversation.id)
    assert stored.turn_count == 2
    assert stored.turns[1].redacted is True


def test_clear_deletes_the_conversations_file(workspace: Services) -> None:
    _turn(workspace, "", "to be forgotten")

    assert workspace.conversations.clear() == 1
    assert workspace.conversations.list_conversations() == []


def test_an_unknown_conversation_replays_nothing(workspace: Services) -> None:
    assert workspace.conversations.replayable_turns("conv_never_existed") == []
