"""Process into knowledge — extraction parsing and proposal building.

The model boundary is stubbed; everything else (workspace, notes, exports) is
real. What matters: a valid extraction becomes a reviewable plan with
provenance on every AI-authored page, invented note ids are discarded, existing
titles are not recreated, and a garbage answer proposes nothing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.domain.ai import AIEvent
from app.services.container import Services
from app.services.knowledge_service import KnowledgeService


class StubAI:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> AsyncIterator[AIEvent]:
        self.calls.append(kwargs)
        yield AIEvent(kind="delta", text=self.reply)
        yield AIEvent(kind="done", input_tokens=5, output_tokens=5)


def _service(workspace: Services, reply: str) -> tuple[KnowledgeService, StubAI]:
    stub = StubAI(reply)
    service = KnowledgeService(stub, workspace.notes, workspace.exports)  # type: ignore[arg-type]
    return service, stub


def _capture(workspace: Services):
    return workspace.capture.capture(
        content="Persistent memory lets AI systems reuse past work.",
        title="Memory article",
    )


def _extraction_reply(related_ids: list[str]) -> str:
    return json.dumps(
        {
            "summary": "Notes about AI memory.",
            "concepts": [
                {
                    "name": "Persistent AI memory",
                    "description": "Context that outlives a chat.",
                    "confidence": 0.94,
                }
            ],
            "entities": [{"name": "Ada Lovelace", "kind": "person", "description": "Pioneer."}],
            "decisions": [],
            "action_items": [{"action": "Read the follow-up paper", "owner": "", "deadline": ""}],
            "open_questions": ["How large can memory grow?"],
            "suggested_tags": ["ai-memory"],
            "related_note_ids": related_ids,
            "claims_to_verify": [],
        }
    )


def test_a_valid_extraction_becomes_a_reviewable_plan(workspace: Services) -> None:
    capture = _capture(workspace)
    other = workspace.notes.list_notes()[0]
    service, stub = _service(workspace, _extraction_reply([other.metadata.id, "id_invented"]))

    proposal = service.process_sync(note_ids=[capture.metadata.id], provider_id="ollama", model="m")

    ops = proposal.plan.operations
    by_type = {op.type for op in ops}
    assert {"create_note", "create_task", "add_tag", "add_relationship", "set_property"} <= by_type

    concept = next(op for op in ops if op.type == "create_note" and "Persistent" in op.title)
    assert concept.folder_path == "Knowledge"
    assert concept.properties["type"] == "concept"
    assert concept.properties["review_status"] == "ai-inferred"
    assert concept.properties["generated_by"].startswith("exec_")
    assert concept.properties["confidence"] == "94"
    assert "derived_from:: [[Memory article]]" in concept.content

    stamp = next(op for op in ops if op.type == "set_property")
    assert stamp.property_value == "processed"

    # The invented id was discarded, and the discard was reported.
    relationships = [op for op in ops if op.type == "add_relationship"]
    assert all(op.target_note_id != "id_invented" for op in relationships)
    assert any("did not exist" in warning for warning in proposal.warnings)
    assert stub.calls[0]["kind"] == "processing"


def test_existing_titles_are_not_recreated(workspace: Services) -> None:
    capture = _capture(workspace)
    existing_title = workspace.notes.list_notes()[0].metadata.title
    reply = json.dumps(
        {
            "summary": "s",
            "concepts": [{"name": existing_title, "description": "dup", "confidence": 0.9}],
        }
    )
    service, _stub = _service(workspace, reply)

    proposal = service.process_sync(note_ids=[capture.metadata.id], provider_id="ollama", model="m")

    creates = [op for op in proposal.plan.operations if op.type == "create_note"]
    assert creates == []
    assert any("already exists" in warning for warning in proposal.warnings)


def test_garbage_proposes_nothing_not_even_the_processed_stamp(workspace: Services) -> None:
    capture = _capture(workspace)
    service, _stub = _service(workspace, "utter nonsense, no json")

    proposal = service.process_sync(note_ids=[capture.metadata.id], provider_id="ollama", model="m")

    assert proposal.plan.operations == []


def test_partial_garbage_is_salvaged_field_by_field(workspace: Services) -> None:
    capture = _capture(workspace)
    reply = json.dumps(
        {
            "summary": "Half valid.",
            "concepts": [{"name": "Good concept", "description": "kept", "confidence": 0.8}],
            "entities": "this is not a list",
        }
    )
    service, _stub = _service(workspace, reply)

    proposal = service.process_sync(note_ids=[capture.metadata.id], provider_id="ollama", model="m")

    creates = [op for op in proposal.plan.operations if op.type == "create_note"]
    assert [op.title for op in creates] == ["Good concept"]
