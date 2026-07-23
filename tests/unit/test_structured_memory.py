"""Structured memory: meeting extraction, project refresh, weekly review, health.

The common thread: everything the AI proposes is a plan (never a direct write),
every extracted item carries its evidence, and the health report is arithmetic
with a recommended action — not a model's opinion.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain.ai import AIEvent
from app.domain.errors import ProviderError
from app.domain.prompts import SavedPrompt
from app.services.container import Services
from app.services.knowledge_service import KnowledgeService
from app.services.memory_service import MemoryService
from app.services.review_service import ReviewService


class StubAI:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> AsyncIterator[AIEvent]:
        self.calls.append(kwargs)
        yield AIEvent(kind="delta", text=self.reply)
        yield AIEvent(kind="done", input_tokens=5, output_tokens=5)


def _layer(workspace: Services) -> str:
    return workspace.workspace.descriptor.layers[0].id


# -- meeting memory -------------------------------------------------------------

MEETING_REPLY = json.dumps(
    {
        "summary": "We planned the Q3 migration.",
        "participants": ["Ada Lovelace", "Grace Hopper"],
        "decisions": [
            {
                "decision": "Migrate to PostgreSQL",
                "rationale": "Operational familiarity",
                "owner": "Ada Lovelace",
                "date": "",
                "excerpt": "Ada: let's just commit to Postgres, we know how to run it.",
            }
        ],
        "action_items": [
            {
                "action": "Draft the migration runbook",
                "owner": "Grace Hopper",
                "deadline": "2026-08-01",
                "excerpt": "Grace: I'll write the runbook by the first.",
            }
        ],
        "risks": ["Downtime window is only 2 hours"],
        "open_questions": ["Who owns the rollback plan?"],
        "suggested_tags": ["migration"],
        "concepts": [],
        "entities": [],
        "related_note_ids": [],
        "claims_to_verify": [],
    }
)


def test_meeting_profile_extracts_anchored_memory(workspace: Services) -> None:
    transcript = workspace.capture.capture(
        content="Ada: let's just commit to Postgres…", title="Q3 planning meeting"
    )
    service = KnowledgeService(StubAI(MEETING_REPLY), workspace.notes, workspace.exports)  # type: ignore[arg-type]

    proposal = service.process_sync(
        note_ids=[transcript.metadata.id],
        provider_id="ollama",
        model="m",
        profile="meeting",
    )

    ops = proposal.plan.operations
    titles = {op.title for op in ops if op.type == "create_note"}
    # Participants become person pages; the meeting gets its summary page.
    assert {"Ada Lovelace", "Grace Hopper"} <= titles
    assert any(title.startswith("Meeting summary:") for title in titles)

    summary = next(op for op in ops if op.title.startswith("Meeting summary:"))
    assert summary.properties["type"] == "meeting"
    assert "## Risks and blockers" in summary.content
    assert "## Unresolved" in summary.content

    # The decision quotes its transcript passage — evidence, not paraphrase.
    decision = next(op for op in ops if op.title.startswith("Decision:"))
    assert "> Ada: let's just commit to Postgres" in decision.content
    assert decision.properties["status"] == "proposed"  # the user confirms, not the AI

    task = next(op for op in ops if op.type == "create_task")
    assert "> Grace: I'll write the runbook" in task.content
    assert task.properties["assignee"] == "Grace Hopper"


# -- project memory -------------------------------------------------------------


def test_refresh_project_proposes_a_reviewable_update(workspace: Services) -> None:
    project = workspace.notes.create_note(
        layer_id=_layer(workspace),
        title="Project Atlas",
        content="## Goal\n\nShip Atlas.\n",
        properties={"type": "project", "status": "in progress"},
    )
    workspace.notes.create_note(
        layer_id=_layer(workspace),
        title="Atlas standup",
        content="Milestone 1 done.\n\nrelates_to:: [[Project Atlas]]\n",
    )
    reply = json.dumps(
        {
            "content": "## Goal\n\nShip Atlas.\n\n## Recent changes\n\n- Milestone 1 done.\n",
            "changes": ["Recorded milestone 1 completion"],
        }
    )
    service = MemoryService(StubAI(reply), workspace.notes, workspace.exports)  # type: ignore[arg-type]

    proposal = service.refresh_project_sync(
        note_id=project.metadata.id, provider_id="ollama", model="m"
    )

    assert len(proposal.plan.operations) == 1
    operation = proposal.plan.operations[0]
    assert operation.type == "update_note"  # destructive → diffed, never pre-approved
    assert operation.is_destructive
    assert "Milestone 1 done" in operation.content
    assert proposal.changes == ["Recorded milestone 1 completion"]
    # The project note itself is untouched until the user applies.
    assert "Recent changes" not in workspace.notes.get_note(project.metadata.id).content


def test_refresh_without_neighbours_is_refused(workspace: Services) -> None:
    lonely = workspace.notes.create_note(
        layer_id=_layer(workspace), title="Lonely project", content="Nothing links here."
    )
    service = MemoryService(StubAI("{}"), workspace.notes, workspace.exports)  # type: ignore[arg-type]

    with pytest.raises(ProviderError, match="no new material"):
        service.refresh_project_sync(note_id=lonely.metadata.id, provider_id="ollama", model="m")


# -- weekly review --------------------------------------------------------------

WEEKLY_REPLY = json.dumps(
    {
        "learned": ["Consensus is mostly about quorum overlap"],
        "decided": ["Migrate to PostgreSQL"],
        "completed": ["Milestone 1"],
        "unresolved": ["Rollback ownership"],
        "themes": ["migration"],
        "next": ["Draft the runbook"],
        "promote": ["Q3 planning meeting"],
    }
)


def _review_service(workspace: Services, reply: str) -> ReviewService:
    return ReviewService(
        StubAI(reply),  # type: ignore[arg-type]
        workspace.notes,
        workspace.exports,
        workspace.connections,
        workspace.prompts,
    )


def test_weekly_review_saves_a_cited_note(workspace: Services) -> None:
    service = _review_service(workspace, WEEKLY_REPLY)

    proposal = service.generate_weekly_sync(provider_id="ollama", model="m", days=7)

    assert len(proposal.plan.operations) == 1
    operation = proposal.plan.operations[0]
    assert operation.folder_path == "Reports"
    assert operation.properties["type"] == "weekly-review"
    assert operation.properties["review_status"] == "ai-inferred"
    assert "## Learned" in operation.content
    assert "## Promote from the Inbox" in operation.content
    assert "derived_from::" in operation.content  # citations to what it read


def test_an_empty_week_review_proposes_nothing(workspace: Services) -> None:
    service = _review_service(workspace, "{}")

    proposal = service.generate_weekly_sync(provider_id="ollama", model="m", days=7)

    assert proposal.plan.operations == []
    assert any("empty review" in warning for warning in proposal.warnings)


# -- knowledge health -----------------------------------------------------------


def test_health_counts_come_with_recommendations(workspace: Services) -> None:
    workspace.capture.capture(content="raw material", title="Unprocessed capture")
    workspace.notes.create_note(
        layer_id=_layer(workspace),
        title="AI concept page",
        content="Body.",
        properties={"type": "concept", "review_status": "ai-inferred"},
    )
    # A prompt that has never been used, created long ago.
    workspace.prompts._append(
        SavedPrompt(
            id="prompt_old",
            name="Old prompt",
            prompt_text="x",
            created_at="2020-01-01T00:00:00+00:00",
            updated_at="2020-01-01T00:00:00+00:00",
        )
    )

    report = workspace.review.health(locked_layers=1)

    by_key = {item.key: item for item in report.items}
    assert by_key["unprocessed"].count >= 1
    assert "Process into knowledge" in by_key["unprocessed"].recommendation
    assert by_key["unreviewed"].count >= 1
    assert "review_status" in by_key["unreviewed"].recommendation
    assert by_key["unused_prompts"].count == 1
    assert report.locked_layers == 1
    assert report.total_notes > 0
    # Sourceless: the AI concept page has no derived_from links.
    assert by_key["sourceless"].count >= 1
