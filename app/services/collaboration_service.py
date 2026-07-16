"""Collaboration orchestration (M9, ADR-0006).

This service owns the live collaborative documents. It shares a layer, syncs it
through an untrusted relay, and — the part that carries the product's promise —
runs conflict detection after every merge and *rescues* rather than resolves.

It deliberately depends on narrow callables (get the key, locate the doc dir,
check the layer is readable, read the layer's content to seed) rather than on the
whole service graph, so its logic is testable with a temp directory and a key.
The container wires those callables to the real workspace and key holder.

Security posture:

- A locked layer collaborates with nothing. ``ensure_readable`` gates every entry
  point, and ``forget_layer`` (called on lock) drops the in-memory document and
  key material for that layer.
- A viewer cannot write. Role is checked here, in Python — the renderer's role is
  advisory (THREAT_MODEL).
- Nothing reaches the relay except sealed blobs; the key never leaves this
  process.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from app.domain.collaboration import (
    CollaborationState,
    ConflictRecord,
    PresencePeer,
    ShareRole,
    TreeNode,
)
from app.domain.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from app.infrastructure.crdt.conflicts import (
    CONFLICTS_FOLDER_ID,
    CONFLICTS_FOLDER_NAME,
    detect_conflicts,
)
from app.infrastructure.crdt.document import LayerDocument
from app.infrastructure.crdt.relay import LocalRelay, Relay
from app.infrastructure.crdt.store import CRDTStore

KeyFor = Callable[[str], bytes]
DocRootFor = Callable[[str], Path]
EnsureReadable = Callable[[str], None]
SeedContent = Callable[[str], tuple[list[TreeNode], dict[str, str]]]
Emit = Callable[[str, dict[str, object]], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _channel_for(layer_id: str, doc_id: str) -> str:
    """A pseudonymous channel id — a hash, so the relay learns no layer name."""
    material = f"{layer_id}:{doc_id}".encode()
    return hashlib.blake2b(material, digest_size=16).hexdigest()


class _Active:
    """One live collaborative document."""

    def __init__(
        self,
        *,
        layer_id: str,
        doc_id: str,
        role: ShareRole,
        document: LayerDocument,
        store: CRDTStore,
        channel: str,
    ) -> None:
        self.layer_id = layer_id
        self.doc_id = doc_id
        self.role = role
        self.document = document
        self.store = store
        self.channel = channel
        self.relay_cursor = 0
        self.conflicts: dict[str, ConflictRecord] = {}
        # Notes whose deletion has been acknowledged (confirmed by the user, or
        # deleted deliberately). detect_conflicts must not re-flag these as
        # edit-vs-delete on every reconcile — that is how a confirmed delete
        # resurrected itself into an endless conflict.
        self.acknowledged_deletes: set[str] = set()


class CollaborationService:
    def __init__(
        self,
        *,
        key_for: KeyFor,
        doc_root_for: DocRootFor,
        ensure_readable: EnsureReadable,
        seed_content: SeedContent,
        relay: Relay | None = None,
        emit: Emit | None = None,
    ) -> None:
        self._key_for = key_for
        self._doc_root_for = doc_root_for
        self._ensure_readable = ensure_readable
        self._seed_content = seed_content
        self._relay = relay or LocalRelay()
        self._emit = emit
        self._active: dict[str, _Active] = {}

    # ---- lifecycle -------------------------------------------------------

    def set_emitter(self, emit: Emit) -> None:
        """Register the bridge's event sink (conflict / presence / changed)."""
        self._emit = emit

    def forget_layer(self, layer_id: str) -> None:
        """Drop all collaborative state for a layer. Called when it locks."""
        self._active.pop(layer_id, None)

    def _require(self, layer_id: str) -> _Active:
        active = self._active.get(layer_id)
        if active is None:
            raise NotFoundError("This layer is not being collaborated on.")
        self._ensure_readable(layer_id)
        return active

    # ---- sharing ---------------------------------------------------------

    def share_layer(self, layer_id: str, *, role: ShareRole = "owner") -> CollaborationState:
        """Turn a layer into a shared document, seeding it from current content."""
        self._ensure_readable(layer_id)
        if layer_id in self._active:
            return self.status(layer_id)

        key = self._key_for(layer_id)
        doc_id = secrets.token_hex(8)
        root = self._doc_root_for(layer_id) / doc_id
        store = CRDTStore(root, layer_id=layer_id, doc_id=doc_id)
        document = LayerDocument(doc_id)

        nodes, bodies = self._seed_content(layer_id)
        for node in nodes:
            document.upsert_node(node, body=bodies.get(node.node_id))
        # Persist the seed as the first update so a reload reconstructs it.
        store.append(key, document.encode_update())

        active = _Active(
            layer_id=layer_id,
            doc_id=doc_id,
            role=role,
            document=document,
            store=store,
            channel=_channel_for(layer_id, doc_id),
        )
        self._active[layer_id] = active
        self._publish_local(active, key, document.encode_update())
        return self.status(layer_id)

    def join_layer(
        self, layer_id: str, doc_id: str, *, role: ShareRole = "editor"
    ) -> CollaborationState:
        """Join an already-shared document (from an invite), then catch up."""
        self._ensure_readable(layer_id)
        if layer_id in self._active:
            return self.status(layer_id)

        root = self._doc_root_for(layer_id) / doc_id
        store = CRDTStore(root, layer_id=layer_id, doc_id=doc_id)
        # Reconstruct whatever we already have on disk, then the relay fills the rest.
        key = self._key_for(layer_id)
        document = store.load_document(key) if root.exists() else LayerDocument(doc_id)
        active = _Active(
            layer_id=layer_id,
            doc_id=doc_id,
            role=role,
            document=document,
            store=store,
            channel=_channel_for(layer_id, doc_id),
        )
        self._active[layer_id] = active
        self.sync(layer_id)
        return self.status(layer_id)

    def status(self, layer_id: str) -> CollaborationState:
        active = self._active.get(layer_id)
        if active is None:
            return CollaborationState(layer_id=layer_id, mode="personal", enabled=False)
        peers = self._read_presence(active)
        return CollaborationState(
            layer_id=layer_id,
            mode="shared",
            enabled=True,
            role=active.role,
            doc_id=active.doc_id,
            peers=peers,
            pending_conflicts=sum(1 for c in active.conflicts.values() if not c.resolved),
            uncompacted_updates=active.store.head_seq(),
        )

    # ---- editing (authoritative, role-checked) ---------------------------

    def apply_local_update(self, layer_id: str, update: bytes) -> list[ConflictRecord]:
        """Apply a Yjs update produced by this peer's editor, then sync it out."""
        active = self._require(layer_id)
        if active.role == "viewer":
            raise PermissionDeniedError("A viewer cannot edit a shared layer.")
        key = self._key_for(layer_id)
        active.document.apply_update(update)
        active.store.append(key, update)
        self._publish_local(active, key, update)
        return self._reconcile(active, key, peer="local")

    def set_body(self, layer_id: str, note_id: str, body: str) -> None:
        """Author a note body through the CRDT (e.g. when saving in the editor)."""
        active = self._require(layer_id)
        if active.role == "viewer":
            raise PermissionDeniedError("A viewer cannot edit a shared layer.")
        key = self._key_for(layer_id)
        before = active.document.state_vector()
        active.document.set_body(note_id, body)
        update = active.document.encode_update(before)
        active.store.append(key, update)
        self._publish_local(active, key, update)

    def delete_node(self, layer_id: str, node_id: str) -> list[ConflictRecord]:
        """Delete a note or folder in a shared layer.

        The deleter *acknowledges* the deletion, so its own reconcile does not
        rescue it back (a tombstoned note keeps its body, which otherwise looks
        exactly like an edit-vs-delete conflict). Another peer who was editing the
        same note still gets the rescue — their edits are never lost — but a plain
        deletion by the owner simply deletes.
        """
        active = self._require(layer_id)
        if active.role == "viewer":
            raise PermissionDeniedError("A viewer cannot edit a shared layer.")
        key = self._key_for(layer_id)
        before = active.document.state_vector()
        active.document.mark_deleted(node_id, True)
        active.acknowledged_deletes.add(node_id)
        update = active.document.encode_update(before)
        active.store.append(key, update)
        self._publish_local(active, key, update)
        return self._reconcile(active, key, peer="local")

    # ---- syncing through the relay --------------------------------------

    def sync(self, layer_id: str) -> list[ConflictRecord]:
        """Pull everything new from the relay, apply it, push our own head."""
        active = self._require(layer_id)
        key = self._key_for(layer_id)
        from app.infrastructure.crdt.updates import open_update
        from app.infrastructure.encryption.primitives import DecryptionError

        applied = False
        for seq, blob in self._relay.fetch(active.channel, active.relay_cursor):
            active.relay_cursor = seq
            try:
                update = open_update(
                    key=key,
                    layer_id=layer_id,
                    doc_id=active.doc_id,
                    blob=blob,
                )
            except DecryptionError:
                # A blob we cannot open is one sealed for a different doc — it is
                # not ours to apply. Skip it rather than fail the whole sync.
                continue
            # Apply, and only persist if it actually advanced our state. This
            # skips our own re-fetched updates (already applied → no-op → not
            # re-appended), keeping the on-disk log from growing on every sync.
            before = active.document.state_vector()
            active.document.apply_update(update)
            if active.document.state_vector() != before:
                active.store.append(key, update)
                applied = True

        conflicts = self._reconcile(active, key, peer="remote") if applied else []
        if applied:
            self._emit_event("changed", {"layerId": layer_id})
        return conflicts

    # ---- conflicts -------------------------------------------------------

    def conflicts(self, layer_id: str) -> list[ConflictRecord]:
        active = self._require(layer_id)
        return [c for c in active.conflicts.values() if not c.resolved]

    def resolve_conflict(self, layer_id: str, conflict_id: str, action: str) -> CollaborationState:
        """Resolve a rescued conflict. ``action`` is 'keep' or 'confirm_delete'."""
        active = self._require(layer_id)
        record = active.conflicts.get(conflict_id)
        if record is None:
            raise NotFoundError("No such conflict.")
        if action not in ("keep", "confirm_delete"):
            raise InvalidRequestError("Unknown resolution.")
        if action == "confirm_delete":
            key = self._key_for(layer_id)
            before = active.document.state_vector()
            for node_id in record.node_ids:
                active.document.mark_deleted(node_id, True)
                # Remember the intent so the next reconcile does not rescue it back.
                active.acknowledged_deletes.add(node_id)
            update = active.document.encode_update(before)
            active.store.append(key, update)
            self._publish_local(active, key, update)
        else:
            # "keep" also resolves the conflict: the node stays in Conflicts/, so
            # do not surface it again.
            active.acknowledged_deletes.update(record.node_ids)
        record.resolved = True
        self._emit_event(
            "conflict", {"layerId": layer_id, "pending": len(self.conflicts(layer_id))}
        )
        return self.status(layer_id)

    # ---- renderer-facing reads -------------------------------------------

    def document_update(self, layer_id: str, since: bytes | None = None) -> bytes:
        """The full (or incremental) Yjs update, for the renderer's client Doc."""
        active = self._require(layer_id)
        return active.document.encode_update(since)

    def tree(self, layer_id: str) -> list[TreeNode]:
        """The current tree as plain nodes, for a non-Yjs rendering of the layer."""
        active = self._require(layer_id)
        return active.document.nodes()

    def body(self, layer_id: str, note_id: str) -> str:
        active = self._require(layer_id)
        return active.document.body(note_id)

    # ---- presence --------------------------------------------------------

    def announce(self, layer_id: str, peer: PresencePeer) -> None:
        active = self._require(layer_id)
        self._relay.announce(active.channel, peer.peer_id, peer.model_dump_json().encode())

    def presence(self, layer_id: str) -> list[PresencePeer]:
        active = self._require(layer_id)
        return self._read_presence(active)

    # ---- compaction ------------------------------------------------------

    def compact(self, layer_id: str) -> int:
        active = self._require(layer_id)
        key = self._key_for(layer_id)
        return active.store.compact(key)

    # ---- internals -------------------------------------------------------

    def _publish_local(self, active: _Active, key: bytes, update: bytes) -> None:
        from app.infrastructure.crdt.updates import seal_update

        blob = seal_update(
            key=key,
            layer_id=active.layer_id,
            doc_id=active.doc_id,
            update=update,
        )
        # Do NOT advance the fetch cursor past our own publication: another peer's
        # updates may sit at lower sequences we have not fetched yet, and jumping
        # over them would drop them permanently. Our own blob will be re-fetched
        # by sync() and applied as a no-op (Yjs is idempotent), so there is no
        # harm in seeing it again.
        self._relay.publish(active.channel, blob)

    def _reconcile(self, active: _Active, key: bytes, *, peer: str) -> list[ConflictRecord]:
        findings = detect_conflicts(
            active.document.nodes(),
            body_of=active.document.body,
            acknowledged_deletes=active.acknowledged_deletes,
        )
        if not findings:
            return []

        before = active.document.state_vector()
        new_records: list[ConflictRecord] = []
        for finding in findings:
            self._ensure_conflicts_folder(active)
            for node_id in finding.rescue:
                node = active.document.node(node_id)
                if node is None:
                    continue
                # Rescue: re-parent under Conflicts/, and for a deleted-but-edited
                # note, un-tombstone it so the text is reachable again.
                active.document.move_node(node_id, CONFLICTS_FOLDER_ID)
                if finding.kind == "edit_vs_delete":
                    active.document.mark_deleted(node_id, False)
            record = ConflictRecord(
                conflict_id=secrets.token_hex(8),
                kind=finding.kind,
                node_ids=list(finding.node_ids),
                peers=[peer],
                detected_at=_now(),
                previous_parent=finding.previous_parent,
                summary=finding.summary,
            )
            active.conflicts[record.conflict_id] = record
            new_records.append(record)

        # The rescue is itself a set of edits; persist and publish it so every
        # peer converges on the rescued state, not on the broken one.
        update = active.document.encode_update(before)
        active.store.append(key, update)
        self._publish_local(active, key, update)
        self._emit_event(
            "conflict",
            {"layerId": active.layer_id, "pending": len(self.conflicts(active.layer_id))},
        )
        return new_records

    def _ensure_conflicts_folder(self, active: _Active) -> None:
        if active.document.node(CONFLICTS_FOLDER_ID) is None:
            active.document.upsert_node(
                TreeNode(
                    node_id=CONFLICTS_FOLDER_ID,
                    name=CONFLICTS_FOLDER_NAME,
                    parent=None,
                    is_note=False,
                )
            )

    def _read_presence(self, active: _Active) -> list[PresencePeer]:
        peers: list[PresencePeer] = []
        for _peer_id, blob in self._relay.presence(active.channel).items():
            try:
                peers.append(PresencePeer.model_validate_json(blob))
            except (ValueError, ConflictError):
                continue
        return peers

    def _emit_event(self, kind: str, payload: dict[str, object]) -> None:
        if self._emit is not None:
            self._emit(kind, payload)
