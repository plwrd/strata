"""Hybrid search: the ranker, the indexes, and the explanations."""

from __future__ import annotations

import numpy as np

from app.infrastructure.search.index import MemoryIndex, SqliteIndex, tokenize
from app.infrastructure.vector.embeddings import HashingEmbedder, VectorStore
from app.services.container import Services


def note_id(services: Services, title: str) -> str:
    return next(
        note.metadata.id for note in services.notes.list_notes() if note.metadata.title == title
    )


# --- the indexes ------------------------------------------------------------


def test_the_memory_index_ranks_by_bm25(workspace: Services) -> None:
    index = MemoryIndex("layer_a")
    count = index.rebuild(workspace.notes.list_notes())

    assert count == index.size() > 5

    results = index.search(tokenize("encryption"), limit=5)

    assert results
    top = index.document(results[0][0])
    assert top is not None
    assert "Encryption" in top.title


def test_a_title_match_outranks_a_body_match(workspace: Services) -> None:
    index = MemoryIndex("layer_a")
    index.rebuild(workspace.notes.list_notes())

    ranked = index.search(tokenize("threat"), limit=10)
    titles = [index.document(object_id).title for object_id, _ in ranked]  # type: ignore[union-attr]

    # "Threat Model" has it in the title; others only mention it.
    assert titles[0] == "Threat Model"


def test_the_sqlite_index_persists_and_reopens(workspace: Services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "index.sqlite"
    index = SqliteIndex("layer_a", path)
    index.rebuild(workspace.notes.list_notes())
    size = index.size()
    index.close()

    assert path.is_file()

    reopened = SqliteIndex("layer_a", path)
    assert reopened.size() == size
    assert reopened.search(tokenize("encryption"), 5)
    reopened.close()


def test_an_fts_operator_in_the_query_is_data_not_syntax(workspace: Services, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A user searching for a quote or `OR` must not inject FTS5 syntax."""
    index = SqliteIndex("layer_a", tmp_path / "i.sqlite")
    index.rebuild(workspace.notes.list_notes())

    hostile_queries = [
        '" OR "',
        "NEAR(",
        "*",
        'x" AND documents MATCH "y',
        "encryption OR 1=1",
    ]
    for hostile in hostile_queries:
        # The contract is "does not raise, does not misbehave" — the query is data.
        index.search(tokenize(hostile), 5)

    # And a real term still works afterwards: the index was not corrupted.
    assert index.search(tokenize("encryption"), 5)
    index.close()


def test_closing_a_memory_index_empties_it(workspace: Services) -> None:
    index = MemoryIndex("layer_a")
    index.rebuild(workspace.notes.list_notes())

    index.close()

    assert index.size() == 0
    assert index.search(tokenize("encryption"), 5) == []


# --- embeddings -------------------------------------------------------------


def test_embeddings_are_normalised_and_deterministic() -> None:
    embedder = HashingEmbedder(64)

    first = embedder.embed(["encryption architecture"])
    second = embedder.embed(["encryption architecture"])

    assert first.shape == (1, 64)
    assert np.allclose(np.linalg.norm(first, axis=1), 1.0)
    assert np.array_equal(first, second)  # deterministic across calls


def test_similar_text_has_a_higher_cosine_than_unrelated_text() -> None:
    embedder = HashingEmbedder(256)

    vectors = embedder.embed(
        [
            "encryption keys and argon2id key derivation",
            "argon2id derives an encryption key from a password",
            "the graph renders nodes with instanced meshes",
        ]
    )

    related = float(vectors[0] @ vectors[1])
    unrelated = float(vectors[0] @ vectors[2])

    assert related > unrelated


def test_the_vector_store_finds_the_nearest_neighbours() -> None:
    embedder = HashingEmbedder(128)
    store = VectorStore("layer_a", 128)
    texts = ["password argon2id", "argon2id key derivation", "three.js instanced mesh"]
    store.replace(["a", "b", "c"], embedder.embed(texts))

    results = store.search(embedder.embed(["argon2id"])[0], limit=2)

    assert {object_id for object_id, _ in results} == {"a", "b"}


def test_clustering_is_deterministic() -> None:
    embedder = HashingEmbedder(64)
    store = VectorStore("layer_a", 64)
    store.replace(
        [f"n{i}" for i in range(10)],
        embedder.embed([f"topic {'crypto' if i % 2 else 'graph'} note {i}" for i in range(10)]),
    )

    first = store.cluster(2)
    second = store.cluster(2)

    # A graph whose clusters reshuffle on every open is not a map of anything.
    assert first == second
    assert len(set(first.values())) == 2


def test_closing_the_vector_store_empties_it() -> None:
    store = VectorStore("layer_a", 32)
    store.replace(["a"], np.ones((1, 32), dtype=np.float32))

    store.close()

    assert store.size() == 0
    assert store.search(np.ones(32, dtype=np.float32), 5) == []


# --- the hybrid ranker ------------------------------------------------------


def test_search_returns_results_with_signals_and_reasons(workspace: Services) -> None:
    results = workspace.search.search("encryption")

    assert results
    top = results[0]
    assert top.title == "Encryption Architecture"
    assert top.signals, "a result with no signals cannot be explained"
    assert top.reasons


def test_every_reason_corresponds_to_a_signal_that_actually_fired(
    workspace: Services,
) -> None:
    """An explanation not derived from the score is a decoration, not an explanation."""
    for result in workspace.search.search("encryption security"):
        if "lexical" in result.signals:
            assert any("Contains" in reason for reason in result.reasons)
        else:
            assert not any("Contains" in reason for reason in result.reasons)

        if "tag" in result.signals:
            assert any("Tagged" in reason for reason in result.reasons)
        else:
            assert not any("Tagged" in reason for reason in result.reasons)

        if "semantic" not in result.signals:
            assert not any("Semantically" in reason for reason in result.reasons)


def test_semantic_matching_finds_a_paraphrase(workspace: Services) -> None:
    """What the *local* embedder can honestly do.

    It is a hashed bag-of-words, so it finds documents that overlap the query's
    vocabulary — a paraphrase that shares terms, not an unrelated wording of the
    same idea. A real model (Milestone 7) drops into the same pipeline and does
    better; the test says what today's code actually delivers rather than what we
    would like it to.
    """
    layer_id = workspace.workspace.descriptor.layers[0].id
    workspace.notes.create_note(
        layer_id=layer_id,
        folder_path="",
        title="Payment Deduplication",
        content="Use an idempotency key so a retried payment request cannot charge twice.\n",
    )
    workspace.search.invalidate()

    results = workspace.search.search("idempotency key for retried payment")

    assert results[0].title == "Payment Deduplication"
    assert "semantic" in results[0].signals


def test_a_one_word_query_does_not_use_the_hashing_embedder(workspace: Services) -> None:
    """A single word is one hash bucket, so every collision would look like a match.

    A one-word query is a lexical query, and this asserts we treat it as one rather
    than dressing hash noise up as semantic insight.
    """
    results = workspace.search.search("encryption")

    assert results
    assert all("semantic" not in result.signals for result in results)


def test_semantic_can_be_switched_off(workspace: Services) -> None:
    results = workspace.search.search("encryption architecture", semantic=False)

    assert all("semantic" not in result.signals for result in results)


def test_graph_proximity_boosts_neighbours(workspace: Services) -> None:
    anchor = note_id(workspace, "Encryption Architecture")

    near = workspace.search.search("model", near_object_id=anchor)

    boosted = [result for result in near if "graph" in result.signals]
    assert boosted, "the neighbours of the anchor should be boosted"
    # Threat Model is linked from Encryption Architecture.
    assert any(result.title == "Threat Model" for result in boosted)


def test_a_tag_filter_narrows_without_a_query(workspace: Services) -> None:
    results = workspace.search.search("", tags=["encryption"])

    assert results
    assert all("encryption" in result.tags for result in results)


def test_recency_is_a_signal(workspace: Services) -> None:
    results = workspace.search.search("encryption")

    # The seeded notes were written moments ago, so recency must contribute.
    assert any("recency" in result.signals for result in results)
    assert any("day(s) ago" in reason for result in results for reason in result.reasons)


def test_similar_to_finds_related_notes(workspace: Services) -> None:
    anchor = note_id(workspace, "Encryption Architecture")

    similar = workspace.search.similar_to(anchor, limit=3)

    assert similar
    assert all(result.object_id != anchor for result in similar)
    assert all(result.reasons for result in similar)


def test_clusters_cover_the_workspace(workspace: Services) -> None:
    clusters = workspace.search.clusters(count=3)

    assert len(clusters) == len(workspace.notes.list_notes())
    assert len(set(clusters.values())) <= 3


def test_an_empty_query_with_no_filters_returns_everything_readable(
    workspace: Services,
) -> None:
    results = workspace.search.search("")

    assert len(results) == len(workspace.notes.list_notes())


def test_a_query_that_matches_nothing_is_empty_not_an_error(workspace: Services) -> None:
    assert workspace.search.search("zzzznotathingatall") == []


def test_the_index_rebuilds_after_a_note_changes(workspace: Services) -> None:
    layer_id = workspace.workspace.descriptor.layers[0].id
    assert workspace.search.search("zebracrossing") == []

    workspace.notes.create_note(
        layer_id=layer_id, folder_path="", title="Zebra", content="zebracrossing appears here"
    )
    workspace.search.invalidate()

    results = workspace.search.search("zebracrossing")
    assert [result.title for result in results] == ["Zebra"]


def test_the_public_index_is_persisted_to_disk(workspace: Services) -> None:
    workspace.search.search("encryption")

    index_dir = workspace.workspace.root / ".strata" / "index"
    assert index_dir.is_dir()
    assert list(index_dir.glob("*.sqlite"))
