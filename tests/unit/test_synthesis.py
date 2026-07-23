"""Multi-source synthesis — structure, provenance, and citation honesty.

The citation rule is the heart of it: a cited source id that was never sent is
stripped and reported, never printed as if it were real.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain.ai import AIEvent
from app.domain.errors import ProviderError
from app.services.container import Services
from app.services.synthesis_service import SynthesisService


class StubAI:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> AsyncIterator[AIEvent]:
        self.calls.append(kwargs)
        yield AIEvent(kind="delta", text=self.reply)
        yield AIEvent(kind="done", input_tokens=5, output_tokens=5)


def _service(workspace: Services, reply: str) -> SynthesisService:
    return SynthesisService(StubAI(reply), workspace.notes, workspace.exports)  # type: ignore[arg-type]


def _two_note_ids(workspace: Services) -> list[str]:
    return [note.metadata.id for note in workspace.notes.list_notes()[:2]]


GOOD_REPLY = json.dumps(
    {
        "title": "How the sources fit together",
        "main_idea": "Both sources describe one system [STRATA-SOURCE-001].",
        "sections": [
            {
                "heading": "The core claim",
                "body": "Supported [STRATA-SOURCE-002]. Invented [STRATA-SOURCE-099].",
            }
        ],
        "agreements": ["They agree on the basics [STRATA-SOURCE-001][STRATA-SOURCE-002]"],
        "disagreements": [],
        "contradictions": [],
        "examples": [],
        "missing_information": ["Neither covers pricing"],
        "open_questions": [],
        "inferences": ["The system probably predates both documents"],
    }
)


def test_synthesis_becomes_a_cited_report_plan(workspace: Services) -> None:
    service = _service(workspace, GOOD_REPLY)

    proposal = service.synthesize_sync(
        note_ids=_two_note_ids(workspace), kind="comparison", provider_id="ollama", model="m"
    )

    assert len(proposal.plan.operations) == 1
    operation = proposal.plan.operations[0]
    assert operation.type == "create_note"
    assert operation.folder_path == "Reports"
    assert operation.properties["type"] == "report"
    assert operation.properties["report_kind"] == "comparison"
    assert operation.properties["review_status"] == "ai-inferred"
    assert operation.properties["generated_by"].startswith("exec_")
    assert "## Main idea" in operation.content
    assert "## Agreements between sources" in operation.content
    assert "## Missing information" in operation.content
    # Inference is separated and labelled, not blended into sourced claims.
    assert "## AI inferences" in operation.content
    assert "model's inference" in operation.content
    # Every source note is linked back.
    assert operation.content.count("derived_from::") == 2


def test_invented_citations_are_stripped_and_reported(workspace: Services) -> None:
    service = _service(workspace, GOOD_REPLY)

    proposal = service.synthesize_sync(
        note_ids=_two_note_ids(workspace), kind="summary", provider_id="ollama", model="m"
    )

    content = proposal.plan.operations[0].content
    assert "STRATA-SOURCE-099" not in content
    assert "STRATA-SOURCE-001" in content  # the real ones survive
    assert any("invent" in warning for warning in proposal.warnings)


def test_garbage_synthesis_proposes_nothing(workspace: Services) -> None:
    service = _service(workspace, "no json here at all")

    proposal = service.synthesize_sync(
        note_ids=_two_note_ids(workspace), kind="summary", provider_id="ollama", model="m"
    )

    assert proposal.plan.operations == []
    assert any("no usable synthesis" in warning for warning in proposal.warnings)


def test_one_note_is_not_a_synthesis(workspace: Services) -> None:
    service = _service(workspace, GOOD_REPLY)

    with pytest.raises(ProviderError, match="at least two"):
        service.synthesize_sync(
            note_ids=_two_note_ids(workspace)[:1],
            kind="summary",
            provider_id="ollama",
            model="m",
        )
