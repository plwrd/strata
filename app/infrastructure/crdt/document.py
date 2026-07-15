"""The authoritative CRDT document for one collaborative layer.

The document holds three top-level Yjs maps, exactly as ADR-0006 specifies:

- ``tree``   — ``node_id -> {parent, name, order, is_note, deleted}``. A flat map
  of *parent pointers*, so a move is a single-key write and the pathological
  merges (cycles, orphans) stay *detectable*.
- ``bodies`` — ``note_id -> Y.Text``. Character-level collaborative text, the
  same type the renderer binds to CodeMirror.
- ``meta``   — ``note_id -> {tags: Y.Array, props: Y.Map}``. Concurrent edits to
  different fields merge; same-field scalar edits are last-writer-wins.

Everything else (links, the graph, structured views) is *derived* from this and
is never itself a CRDT.

The document is deliberately ignorant of encryption, storage, and the relay: it
turns edits into Yjs updates and applies Yjs updates back. Sealing, persistence,
and conflict rescue are the store's and the service's jobs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from pycrdt import Array, Doc, Map, Subscription, Text

from app.domain.collaboration import TreeNode

# Any: a Yjs Map holds heterogeneous CRDT values (nested Maps, Texts, scalars);
# the value type is not statically knowable, which is inherent to the data model.
_YMap = Map[Any]


class LayerDocument:
    """A single layer's collaborative state as one Yjs ``Doc``."""

    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id
        self._doc: Doc[Any] = Doc()
        # Materialise the three roots up front so their types are fixed before any
        # remote update arrives (a root's type is part of the shared structure).
        self._doc["tree"] = Map()
        self._doc["bodies"] = Map()
        self._doc["meta"] = Map()

    # ---- roots -----------------------------------------------------------

    @property
    def _tree(self) -> _YMap:
        return cast(_YMap, self._doc.get("tree", type=Map))

    @property
    def _bodies(self) -> _YMap:
        return cast(_YMap, self._doc.get("bodies", type=Map))

    @property
    def _meta(self) -> _YMap:
        return cast(_YMap, self._doc.get("meta", type=Map))

    # ---- sync primitives -------------------------------------------------

    def state_vector(self) -> bytes:
        """This replica's state vector — what it already has, for diffing."""
        return self._doc.get_state()

    def encode_update(self, since: bytes | None = None) -> bytes:
        """A binary Yjs update carrying everything ``since`` does not have.

        With ``since=None`` this is the full document state, suitable for seeding
        a fresh peer.
        """
        if since is None:
            return self._doc.get_update()
        return self._doc.get_update(since)

    def apply_update(self, update: bytes) -> None:
        """Merge a remote (or restored) Yjs update. Convergent and idempotent."""
        self._doc.apply_update(update)

    def observe(self, callback: Callable[[], None]) -> Subscription:
        """Fire ``callback`` after every transaction that changes the document."""
        return self._doc.observe(lambda _event: callback())

    # ---- editing (local authoring, and seeding from an existing layer) ---

    def upsert_node(
        self,
        node: TreeNode,
        *,
        body: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Create or replace a tree node, and (for notes) its body and tags."""
        with self._doc.transaction():
            self._tree[node.node_id] = Map(
                {
                    "parent": node.parent,
                    "name": node.name,
                    "order": node.order,
                    "is_note": node.is_note,
                    "deleted": node.deleted,
                }
            )
            if node.is_note:
                if node.node_id not in self._bodies:
                    self._bodies[node.node_id] = Text(body or "")
                elif body is not None:
                    self._replace_text(node.node_id, body)
                self._meta[node.node_id] = Map({"tags": Array(list(tags or []))})

    def _replace_text(self, note_id: str, body: str) -> None:
        text = self._bodies[note_id]
        current = str(text)
        if current == body:
            return
        if len(current):
            del text[0 : len(current)]
        if body:
            text.insert(0, body)

    def set_body(self, note_id: str, body: str) -> None:
        """Replace a note body wholesale (used when a non-CRDT edit path saves)."""
        with self._doc.transaction():
            if note_id not in self._bodies:
                self._bodies[note_id] = Text(body)
            else:
                self._replace_text(note_id, body)

    def move_node(self, node_id: str, new_parent: str | None) -> None:
        if node_id in self._tree:
            self._tree[node_id]["parent"] = new_parent

    def rename_node(self, node_id: str, name: str) -> None:
        if node_id in self._tree:
            self._tree[node_id]["name"] = name

    def mark_deleted(self, node_id: str, deleted: bool = True) -> None:
        """Tombstone (or restore) a node. Content is retained until compaction."""
        if node_id in self._tree:
            self._tree[node_id]["deleted"] = deleted

    # ---- reads (projections) ---------------------------------------------

    def nodes(self) -> list[TreeNode]:
        out: list[TreeNode] = []
        tree = self._tree
        for node_id in tree.keys():
            raw = tree[node_id].to_py()
            out.append(
                TreeNode(
                    node_id=node_id,
                    parent=raw.get("parent"),
                    name=str(raw.get("name", "")),
                    order=str(raw.get("order", "a0")),
                    is_note=bool(raw.get("is_note", False)),
                    deleted=bool(raw.get("deleted", False)),
                )
            )
        return out

    def node(self, node_id: str) -> TreeNode | None:
        if node_id not in self._tree:
            return None
        raw = self._tree[node_id].to_py()
        return TreeNode(
            node_id=node_id,
            parent=raw.get("parent"),
            name=str(raw.get("name", "")),
            order=str(raw.get("order", "a0")),
            is_note=bool(raw.get("is_note", False)),
            deleted=bool(raw.get("deleted", False)),
        )

    def body(self, note_id: str) -> str:
        if note_id not in self._bodies:
            return ""
        return str(self._bodies[note_id])

    def tags(self, note_id: str) -> list[str]:
        if note_id not in self._meta:
            return []
        meta = self._meta[note_id]
        if "tags" not in meta:
            return []
        return [str(t) for t in meta["tags"].to_py()]
