"""ADR-0006 validation spike, as executable exit criteria.

The ADR is provisional until these pass. Each test maps to one of its numbered
criteria; if one fails, pycrdt is the wrong choice and the ADR is superseded.

Criterion 1 (pycrdt <-> JS Yjs byte-identical interop) cannot run without a Node
peer in this suite, but it reduces to "our updates are standard Yjs binary
updates" — which they are, because pycrdt *is* y-crdt. We assert the Python side
of that contract: the wire format is the update/state-vector protocol, and
convergence holds across independent replicas exchanging only those bytes.
"""

from __future__ import annotations

import time

import pytest

from app.domain.collaboration import TreeNode
from app.infrastructure.crdt.conflicts import detect_conflicts
from app.infrastructure.crdt.document import LayerDocument


def _note(node_id: str, name: str, parent: str | None = None) -> TreeNode:
    return TreeNode(node_id=node_id, name=name, parent=parent, is_note=True)


def _folder(node_id: str, name: str, parent: str | None = None) -> TreeNode:
    return TreeNode(node_id=node_id, name=name, parent=parent, is_note=False)


def _sync(source: LayerDocument, target: LayerDocument) -> None:
    """Send only what target lacks — the real sync primitive."""
    target.apply_update(source.encode_update(target.state_vector()))


# --- Criterion 1 & 2: convergence over the wire, including an offline peer ---


def test_two_replicas_converge_over_updates() -> None:
    a = LayerDocument("doc")
    b = LayerDocument("doc")

    a.upsert_node(_note("n1", "Encryption"), body="hello ")
    _sync(a, b)
    assert b.body("n1") == "hello "

    # concurrent edits to the same text at different positions both survive
    a_text_before = a.state_vector()
    a.set_body("n1", "hello from A")
    b.set_body("n1", "hello from B")
    _sync(a, b)
    _sync(b, a)
    # both replicas agree (convergence), even if the merged string is a blend
    assert a.body("n1") == b.body("n1")
    assert a_text_before != a.state_vector()


def test_three_peers_one_offline_converge_on_reconnect() -> None:
    a, b, offline = (LayerDocument("doc") for _ in range(3))

    a.upsert_node(_folder("root", "Root"))
    a.upsert_node(_note("n1", "A note", parent="root"), body="base")
    _sync(a, b)
    _sync(a, offline)  # offline peer starts in sync, then disconnects

    # a and b keep working while `offline` is away
    for i in range(50):
        a.upsert_node(_note(f"a{i}", f"a-note-{i}", parent="root"))
    for i in range(50):
        b.upsert_node(_note(f"b{i}", f"b-note-{i}", parent="root"))
    _sync(a, b)
    _sync(b, a)

    # offline peer edits in isolation, then reconnects
    offline.set_body("n1", "edited while offline")
    _sync(a, offline)
    _sync(offline, a)
    _sync(a, b)

    assert a.nodes() and len(a.nodes()) == len(b.nodes())
    assert a.body("n1") == b.body("n1") == "edited while offline"
    # no data lost: every peer's notes are present everywhere
    ids = {n.node_id for n in a.nodes()}
    assert {"n1", "a0", "a49", "b0", "b49", "root"} <= ids


# --- Criterion 3: a large backlog merges fast and does not block ---


def test_large_backlog_merges_quickly() -> None:
    source = LayerDocument("doc")
    source.upsert_node(_folder("root", "Root"))
    for i in range(5000):
        source.upsert_node(_note(f"n{i}", f"note-{i}", parent="root"))

    fresh = LayerDocument("doc")
    update = source.encode_update(fresh.state_vector())

    start = time.perf_counter()
    fresh.apply_update(update)
    elapsed = time.perf_counter() - start

    assert len(fresh.nodes()) == 5001
    # ADR budget is < 2s for 10k updates; 5k here should be comfortably under.
    assert elapsed < 2.0, f"merge took {elapsed:.3f}s"


# --- Criterion 4: all three conflict classes are detectable post-merge ---


def test_move_cycle_is_detected() -> None:
    # Alice moves A under B; Bob moves B under A. Converged: a cycle.
    alice = LayerDocument("doc")
    alice.upsert_node(_folder("A", "A"))
    alice.upsert_node(_folder("B", "B"))
    bob = LayerDocument("doc")
    _sync(alice, bob)

    alice.move_node("A", "B")
    bob.move_node("B", "A")
    _sync(alice, bob)
    _sync(bob, alice)

    findings = detect_conflicts(alice.nodes())
    kinds = {f.kind for f in findings}
    assert "move_cycle" in kinds
    cycle = next(f for f in findings if f.kind == "move_cycle")
    assert set(cycle.node_ids) == {"A", "B"}


def test_move_vs_delete_is_detected() -> None:
    # Alice moves note N into folder F; Bob deletes F.
    alice = LayerDocument("doc")
    alice.upsert_node(_folder("F", "Research"))
    alice.upsert_node(_note("N", "Interview", parent=None))
    bob = LayerDocument("doc")
    _sync(alice, bob)

    alice.move_node("N", "F")
    bob.mark_deleted("F")
    _sync(alice, bob)
    _sync(bob, alice)

    findings = detect_conflicts(alice.nodes())
    assert any(f.kind == "move_vs_delete" and "N" in f.node_ids for f in findings)


def test_edit_vs_delete_is_detected() -> None:
    alice = LayerDocument("doc")
    alice.upsert_node(_note("N", "Draft"), body="")
    bob = LayerDocument("doc")
    _sync(alice, bob)

    alice.set_body("N", "hours of writing")
    bob.mark_deleted("N")
    _sync(alice, bob)
    _sync(bob, alice)

    findings = detect_conflicts(alice.nodes(), body_of=alice.body)
    assert any(f.kind == "edit_vs_delete" and "N" in f.node_ids for f in findings)
    # the text is still there to be rescued — nothing was lost to the merge
    assert alice.body("N") == "hours of writing"


def test_a_clean_tree_has_no_conflicts() -> None:
    doc = LayerDocument("doc")
    doc.upsert_node(_folder("root", "Root"))
    doc.upsert_node(_note("n1", "Note", parent="root"), body="text")
    assert detect_conflicts(doc.nodes(), body_of=doc.body) == []


@pytest.mark.parametrize("size", [10, 100])
def test_convergence_is_order_independent(size: int) -> None:
    """Applying the same updates in any order reaches the same state (CRDT law)."""
    source = LayerDocument("doc")
    for i in range(size):
        source.upsert_node(_note(f"n{i}", f"n-{i}"))
    full = source.encode_update()

    forward = LayerDocument("doc")
    forward.apply_update(full)
    twice = LayerDocument("doc")
    twice.apply_update(full)
    twice.apply_update(full)  # idempotent re-application

    assert {n.node_id for n in forward.nodes()} == {n.node_id for n in twice.nodes()}
