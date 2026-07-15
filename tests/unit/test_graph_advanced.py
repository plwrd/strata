"""Advanced graph: semantic edges, clusters, shortest path."""

from __future__ import annotations

import pytest

from app.services.container import Services


def node_id(workspace: Services, title: str) -> str:
    snapshot = workspace.graph.build()
    return next(node.id for node in snapshot.nodes if node.label == title)


def test_semantic_edges_are_derived_and_distinct(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id
    # Two notes that share vocabulary but do not link to each other.
    workspace.notes.create_note(
        layer_id=layer,
        folder_path="",
        title="Payments One",
        content="idempotency key prevents duplicate payment charges on retry",
    )
    workspace.notes.create_note(
        layer_id=layer,
        folder_path="",
        title="Payments Two",
        content="a retry of a payment must not charge twice; use an idempotency key",
    )
    workspace.search.invalidate()

    snapshot = workspace.graph.build(
        include_tags=False, include_folders=False, semantic_edges=True, semantic_threshold=0.4
    )

    semantic = [edge for edge in snapshot.edges if edge.type == "semantic_similarity"]
    assert semantic, "expected at least one semantic edge between the payment notes"
    assert all(edge.origin == "derived" for edge in semantic)
    assert all(edge.confidence is not None for edge in semantic)


def test_semantic_edges_do_not_duplicate_an_explicit_link(workspace: Services) -> None:
    snapshot = workspace.graph.build(
        include_tags=False, include_folders=False, semantic_edges=True, semantic_threshold=0.1
    )

    # For any pair, there is at most one edge from the explicit+semantic pass.
    pairs = [frozenset((edge.source, edge.target)) for edge in snapshot.edges]
    explicit_pairs = [
        frozenset((edge.source, edge.target))
        for edge in snapshot.edges
        if edge.origin == "explicit"
    ]
    for pair in explicit_pairs:
        # A pair that already had an explicit link is not *also* given a semantic one.
        semantic_for_pair = [
            edge
            for edge in snapshot.edges
            if frozenset((edge.source, edge.target)) == pair
            and edge.type == "semantic_similarity"
        ]
        assert semantic_for_pair == []
    assert len(pairs) >= len(explicit_pairs)


def test_clustering_annotates_nodes(workspace: Services) -> None:
    clusters = workspace.search.clusters(count=3)

    snapshot = workspace.graph.build(cluster_assignments=clusters)

    note_nodes = [node for node in snapshot.nodes if node.type in ("note", "concept", "decision")]
    assert any(node.cluster >= 0 for node in note_nodes)


def test_shortest_path_between_linked_notes(workspace: Services) -> None:
    snapshot = workspace.graph.build(include_tags=False, include_folders=False)
    source = node_id(workspace, "Encryption Architecture")
    target = node_id(workspace, "Threat Model")

    path = workspace.graph.shortest_path(snapshot, source, target)

    assert path[0] == source
    assert path[-1] == target
    assert len(path) >= 2


def test_shortest_path_is_empty_when_unconnected(workspace: Services) -> None:
    layer = workspace.workspace.descriptor.layers[0].id
    workspace.notes.create_note(
        layer_id=layer, folder_path="", title="Island", content="No links here."
    )
    snapshot = workspace.graph.build(include_tags=False, include_folders=False)
    island = node_id(workspace, "Island")
    other = node_id(workspace, "Threat Model")

    assert workspace.graph.shortest_path(snapshot, island, other) == []


@pytest.mark.security()
def test_semantic_edges_never_touch_a_locked_layer(workspace: Services) -> None:
    private, _recovery = workspace.workspace.create_layer(
        "Deals", visibility="private", password="correct horse battery"
    )
    workspace.notes.create_note(
        layer_id=private.id,
        folder_path="",
        title="Secret Payments",
        content="idempotency key prevents duplicate payment charges",
    )
    workspace.search.invalidate()
    workspace.workspace.lock_layer(private.id)

    snapshot = workspace.graph.build(semantic_edges=True, semantic_threshold=0.1)

    ids = {node.id for node in snapshot.nodes if not node.locked}
    for edge in snapshot.edges:
        assert edge.source in ids
        assert edge.target in ids
    assert "Secret Payments" not in snapshot.model_dump_json()
