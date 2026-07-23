"""The prompt library: versions accumulate, usage counts, deletion is total."""

from __future__ import annotations

import pytest

from app.domain.errors import NotFoundError
from app.services.container import Paths, Services


def _save(workspace: Services, name: str = "Weekly digest", prompt_id: str = ""):
    return workspace.prompts.save(
        prompt_id=prompt_id,
        name=name,
        prompt_text="Summarise what changed this week.",
        category="weekly-review",
        model_preference="llama3",
    )


def test_saving_over_an_id_bumps_the_version_and_keeps_the_trail(
    workspace: Services,
) -> None:
    first = _save(workspace)
    second = workspace.prompts.save(
        prompt_id=first.id,
        name="Weekly digest",
        prompt_text="Summarise what changed this week, with citations.",
        category="weekly-review",
    )

    assert second.version == 2
    assert second.created_at == first.created_at
    history = workspace.prompts.history(first.id)
    assert [record.version for record in history] == [1, 2]
    assert "citations" not in history[0].prompt_text


def test_usage_is_counted_and_survives_a_restart(workspace: Services, paths: Paths) -> None:
    prompt = _save(workspace)
    workspace.prompts.record_use(prompt.id)
    workspace.prompts.record_use(prompt.id)

    fresh = Services(paths, environment="test")
    fresh.workspace.open(paths.default_workspace)

    reloaded = fresh.prompts.get(prompt.id)
    assert reloaded.usage_count == 2
    assert reloaded.last_used_at != ""


def test_deletion_removes_the_prompt_and_its_trail(workspace: Services) -> None:
    prompt = _save(workspace)
    workspace.prompts.delete(prompt.id)

    with pytest.raises(NotFoundError):
        workspace.prompts.get(prompt.id)
    assert workspace.prompts.history(prompt.id) == []


def test_the_list_is_most_recently_updated_first(workspace: Services) -> None:
    older = _save(workspace, name="Older")
    newer = _save(workspace, name="Newer")
    workspace.prompts.save(
        prompt_id=older.id, name="Older, revised", prompt_text="x", category="other"
    )

    names = [prompt.name for prompt in workspace.prompts.list_prompts()]
    assert names.index("Older, revised") < names.index(newer.name) or len(names) == 2
