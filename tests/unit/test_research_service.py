"""Research filing — candidate scoping and proposal building.

The model boundary is stubbed; workspace, notes, retrieval and exports are real.
What matters here is that the *placement* is trustworthy: only nodes that were
actually offered can be attached to, appended context says in the note itself
that a model wrote it, subnodes inherit their parent's layer, and a plan can
never reach a layer the user did not tick.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.domain.ai import AIEvent
from app.domain.errors import InvalidRequestError
from app.domain.research import CandidateNode
from app.services.container import Services
from app.services.research_service import ResearchService


class StubAI:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> AsyncIterator[AIEvent]:
        self.calls.append(kwargs)
        yield AIEvent(kind="delta", text=self.reply)
        yield AIEvent(kind="done", input_tokens=5, output_tokens=5)


def _service(workspace: Services, reply: str) -> tuple[ResearchService, StubAI]:
    stub = StubAI(reply)
    service = ResearchService(
        stub,  # type: ignore[arg-type]
        workspace.notes,
        workspace.exports,
        workspace.search,
        workspace.workspace,
    )
    return service, stub


def _capture(workspace: Services):
    """A scraped page, as the browser bridge would have filed it."""
    return workspace.capture.capture(
        content=(
            "Vector indexes trade recall for latency. HNSW keeps a navigable "
            "small-world graph; IVF partitions the space into cells."
        ),
        title="Vector index tradeoffs",
        source_url="https://example.com/vector-indexes",
    )


def _public_layer(workspace: Services) -> str:
    return next(
        layer.id for layer in workspace.workspace.readable_layers() if layer.storage == "markdown"
    )


def _reply(*, target_id: str, parent_id: str) -> str:
    return json.dumps(
        {
            "summary": "How vector indexes trade recall against latency.",
            "key_points": ["HNSW is a navigable small-world graph."],
            "node_matches": [
                {"note_id": target_id, "relevance": 0.9, "reason": "Same subject"},
                {"note_id": "id_invented", "relevance": 0.9, "reason": "Hallucinated"},
            ],
            "subnodes": [
                {
                    "title": "HNSW",
                    "parent_note_id": parent_id,
                    "kind": "concept",
                    "content": "## What it is\n\nA navigable small-world graph.",
                },
                {
                    "title": "Orphan idea",
                    "parent_note_id": "",
                    "kind": "concept",
                    "content": "No parent fitted.",
                },
            ],
            "context_additions": [
                {
                    "note_id": target_id,
                    "heading": "Recall vs latency",
                    "content": "Cell count trades recall for speed.",
                },
                {
                    "note_id": "id_invented",
                    "heading": "Nope",
                    "content": "Should be dropped.",
                },
            ],
            "tags": ["vector-search"],
            "claims_to_verify": ["HNSW always beats IVF"],
            "open_questions": [],
        }
    )


def test_a_valid_analysis_files_material_under_existing_nodes(workspace: Services) -> None:
    capture = _capture(workspace)
    layer_id = _public_layer(workspace)
    existing = next(
        note
        for note in workspace.notes.list_notes([layer_id])
        if note.metadata.id != capture.metadata.id
    )
    service, stub = _service(
        workspace, _reply(target_id=existing.metadata.id, parent_id=existing.metadata.id)
    )
    # Retrieval ranks the real workspace; force the shortlist to hold the node
    # the reply names, so the test is about placement, not about the ranker.
    service._candidates = lambda sources, scope: [  # type: ignore[assignment]
        CandidateNode(
            note_id=existing.metadata.id,
            title=existing.metadata.title,
            layer_id=existing.metadata.layer_id,
            folder_path=existing.metadata.folder_path,
        )
    ]

    proposal = service.analyse_sync(
        note_ids=[capture.metadata.id],
        layer_ids=[layer_id],
        provider_id="ollama",
        model="m",
    )

    ops = proposal.plan.operations
    assert {"add_relationship", "create_note", "append_note", "add_tag", "set_property"} <= {
        op.type for op in ops
    }

    subnode = next(op for op in ops if op.type == "create_note" and op.title == "HNSW")
    assert subnode.layer_id == existing.metadata.layer_id
    assert subnode.properties["review_status"] == "ai-inferred"
    assert subnode.properties["generated_by"].startswith("exec_")
    assert f"parent:: [[{existing.metadata.title}]]" in subnode.content
    assert "derived_from:: [[Vector index tradeoffs]]" in subnode.content
    assert "https://example.com/vector-indexes" in subnode.content

    # The appended block names its origin inside the note, not only in the log.
    appended = next(op for op in ops if op.type == "append_note")
    assert appended.note_id == existing.metadata.id
    assert "## Recall vs latency" in appended.content
    assert "Added by Strata research" in appended.content
    assert "ai-inferred, unverified" in appended.content

    stamp = next(op for op in ops if op.type == "set_property")
    assert stamp.property_value == "processed"
    assert stub.calls[0]["kind"] == "processing"
    assert any("Needs verification" in warning for warning in proposal.warnings)


def test_nodes_that_were_never_offered_are_dropped(workspace: Services) -> None:
    capture = _capture(workspace)
    layer_id = _public_layer(workspace)
    existing = next(
        note
        for note in workspace.notes.list_notes([layer_id])
        if note.metadata.id != capture.metadata.id
    )
    service, _stub = _service(
        workspace, _reply(target_id=existing.metadata.id, parent_id=existing.metadata.id)
    )
    # Nothing is offered at all: every id in the reply is then a hallucination.
    service._candidates = lambda sources, scope: []  # type: ignore[assignment]

    proposal = service.analyse_sync(
        note_ids=[capture.metadata.id],
        layer_ids=[layer_id],
        provider_id="ollama",
        model="m",
    )

    ops = proposal.plan.operations
    assert [op for op in ops if op.type == "append_note"] == []
    assert [op for op in ops if op.type == "add_relationship"] == []
    # The unparented subnodes still land, in the target layer's Knowledge folder.
    creates = [op for op in ops if op.type == "create_note"]
    # The page's own node, plus the two subnodes that had no offered parent.
    assert {op.title for op in creates} == {
        "Vector index tradeoffs",
        "HNSW",
        "Orphan idea",
    }
    assert all(op.layer_id == layer_id and op.folder_path == "Knowledge" for op in creates)
    assert any("not offered" in warning for warning in proposal.warnings)


def test_every_operation_stays_inside_the_selected_layers(workspace: Services) -> None:
    capture = _capture(workspace)
    layer_id = _public_layer(workspace)
    existing = next(
        note
        for note in workspace.notes.list_notes([layer_id])
        if note.metadata.id != capture.metadata.id
    )
    service, _stub = _service(
        workspace, _reply(target_id=existing.metadata.id, parent_id=existing.metadata.id)
    )

    proposal = service.analyse_sync(
        note_ids=[capture.metadata.id],
        layer_ids=[layer_id],
        provider_id="ollama",
        model="m",
    )

    assert proposal.plan.operations, "the analysis should propose something"
    assert {op.layer_id for op in proposal.plan.operations} == {layer_id}


def test_a_target_layer_outside_the_selection_is_refused(workspace: Services) -> None:
    capture = _capture(workspace)
    layer_id = _public_layer(workspace)
    service, _stub = _service(workspace, "{}")

    with pytest.raises(InvalidRequestError):
        service.analyse_sync(
            note_ids=[capture.metadata.id],
            layer_ids=[layer_id],
            provider_id="ollama",
            model="m",
            target_layer_id="layer_not_selected",
        )


def test_a_page_is_always_filed_as_a_node_even_when_the_model_says_nothing(
    workspace: Services,
) -> None:
    """The bug this replaced: a weak answer produced an empty plan, so clicking
    Analyse appeared to do nothing at all. Keeping less is the right failure;
    keeping nothing is not."""
    capture = _capture(workspace)
    layer_id = _public_layer(workspace)
    service, _stub = _service(workspace, "no json here at all")

    proposal = service.analyse_sync(
        note_ids=[capture.metadata.id],
        layer_ids=[layer_id],
        provider_id="ollama",
        model="m",
    )

    node = next(op for op in proposal.plan.operations if op.type == "create_note")
    assert node.layer_id == layer_id
    assert node.folder_path != "Inbox"
    assert node.properties["type"] == "research-source"
    assert node.properties["review_status"] == "ai-inferred"
    # The page's own text stands in for the summary it did not get.
    assert "Vector indexes trade recall" in node.content
    assert "https://example.com/vector-indexes" in node.content
    assert any("did not return an analysis" in warning for warning in proposal.warnings)


def test_the_node_carries_the_analysis_when_there_is_one(workspace: Services) -> None:
    capture = _capture(workspace)
    layer_id = _public_layer(workspace)
    reply = json.dumps(
        {
            "summary": "How vector indexes trade recall against latency.",
            "key_points": ["HNSW is a navigable small-world graph."],
            "claims_to_verify": ["HNSW always beats IVF"],
            "open_questions": ["What about updates?"],
        }
    )
    service, _stub = _service(workspace, reply)

    proposal = service.analyse_sync(
        note_ids=[capture.metadata.id],
        layer_ids=[layer_id],
        provider_id="ollama",
        model="m",
    )

    node = next(op for op in proposal.plan.operations if op.type == "create_note")
    assert "How vector indexes trade recall against latency." in node.content
    assert "HNSW is a navigable small-world graph." in node.content
    assert "## Needs checking" in node.content
    assert "## Open questions" in node.content
    assert not any("page's own text" in warning for warning in proposal.warnings)


def test_a_raw_capture_is_never_offered_as_a_parent(workspace: Services) -> None:
    """Inbox material is the most textually similar thing to a new page and the
    least useful place to file it."""
    first = workspace.capture.capture(
        content="Vector indexes trade recall for latency, HNSW and IVF both.",
        title="An earlier page about vector indexes",
    )
    capture = _capture(workspace)
    layer_id = _public_layer(workspace)
    service, _stub = _service(workspace, "{}")

    candidates = service._candidates([capture], [layer_id])

    assert first.metadata.id not in {candidate.note_id for candidate in candidates}
    assert all(candidate.folder_path != "Inbox" for candidate in candidates)
