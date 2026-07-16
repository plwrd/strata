"""Post-merge conflict detection — the part that actually matters.

A CRDT guarantees every replica converges; it does not guarantee the converged
state is *correct*. Three semantic conflicts can survive a clean merge, and
Strata's promise is that none of them silently loses data (ADR-0006):

1. **Move cycle** — two folders become each other's ancestors; the subtree
   detaches from the root.
2. **Move-vs-delete** — a live node's ancestor was tombstoned by another peer, so
   the node is unreachable.
3. **Edit-vs-delete** — a note was edited by one peer and deleted by another; the
   edits land on a tombstone.

This module *detects*; the service *rescues* (re-parents into ``Conflicts/`` and
writes a :class:`~app.domain.collaboration.ConflictRecord`). Detection is pure
and deterministic so it is trivially testable — which, for the one component
standing between a merge and lost work, is the whole point.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain.collaboration import ConflictKind, TreeNode

# The system folder that holds rescued nodes. Present only when non-empty.
CONFLICTS_FOLDER_ID = "__conflicts__"
CONFLICTS_FOLDER_NAME = "Conflicts"


@dataclass(frozen=True)
class ConflictFinding:
    """A detected conflict, before it is assigned an id and rescued."""

    kind: ConflictKind
    node_ids: tuple[str, ...]
    summary: str
    previous_parent: str | None = None
    rescue: tuple[str, ...] = field(default_factory=tuple)


def _by_id(nodes: list[TreeNode]) -> dict[str, TreeNode]:
    return {n.node_id: n for n in nodes}


def _ancestry_status(node: TreeNode, by_id: dict[str, TreeNode]) -> tuple[str, list[str]]:
    """Walk parent pointers from ``node`` to the root.

    Returns ``(status, path)`` where status is one of ``rooted``, ``cycle``,
    ``deleted-ancestor`` or ``missing-ancestor``, and ``path`` is the chain of
    node ids visited (for reporting the cycle members / the orphan chain).
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    current: TreeNode | None = node
    while current is not None:
        if current.node_id in seen_set:
            return "cycle", seen
        seen.append(current.node_id)
        seen_set.add(current.node_id)
        parent_id = current.parent
        if parent_id is None:
            return "rooted", seen
        if parent_id == CONFLICTS_FOLDER_ID:
            return "rooted", seen
        parent = by_id.get(parent_id)
        if parent is None:
            return "missing-ancestor", seen
        if parent.deleted:
            return "deleted-ancestor", seen
        current = parent
    return "rooted", seen


def detect_conflicts(
    nodes: list[TreeNode],
    body_of: Callable[[str], str] | None = None,
    acknowledged_deletes: set[str] | None = None,
) -> list[ConflictFinding]:
    """Detect every surviving semantic conflict in a converged tree.

    ``acknowledged_deletes`` are node ids whose deletion is intentional and
    already handled; they are never re-flagged as edit-vs-delete, so a confirmed
    delete cannot resurrect itself on the next reconcile.
    """
    known_deleted = acknowledged_deletes or set()
    by_id = _by_id(nodes)
    findings: list[ConflictFinding] = []
    cycles_reported: set[frozenset[str]] = set()

    for node in nodes:
        if node.deleted:
            continue
        status, path = _ancestry_status(node, by_id)
        if status == "cycle":
            # Report the cycle once, keyed by its member set.
            members = frozenset(path)
            if members in cycles_reported:
                continue
            cycles_reported.add(members)
            findings.append(
                ConflictFinding(
                    kind="move_cycle",
                    node_ids=tuple(sorted(path)),
                    summary=(
                        "Folders "
                        + ", ".join(by_id[i].name or i for i in sorted(path) if i in by_id)
                        + " were moved into each other and formed a loop. "
                        "They have been moved to Conflicts/."
                    ),
                    rescue=tuple(sorted(path)),
                )
            )
        elif status in ("deleted-ancestor", "missing-ancestor"):
            reason = (
                "into a folder that another peer deleted"
                if status == "deleted-ancestor"
                else "into a folder that no longer exists"
            )
            findings.append(
                ConflictFinding(
                    kind="move_vs_delete",
                    node_ids=(node.node_id,),
                    summary=(
                        f"“{node.name or node.node_id}” was moved {reason}. "
                        "It is safe in Conflicts/."
                    ),
                    previous_parent=node.parent,
                    rescue=(node.node_id,),
                )
            )

    if body_of is not None:
        for node in nodes:
            if node.node_id in known_deleted:
                continue
            if node.deleted and node.is_note and body_of(node.node_id).strip():
                findings.append(
                    ConflictFinding(
                        kind="edit_vs_delete",
                        node_ids=(node.node_id,),
                        summary=(
                            f"“{node.name or node.node_id}” was deleted by one "
                            "peer while another was still writing in it. The "
                            "text was recovered into Conflicts/."
                        ),
                        previous_parent=node.parent,
                        rescue=(node.node_id,),
                    )
                )

    return findings
