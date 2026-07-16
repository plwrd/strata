"""Persistence for a collaborative document: sealed updates + compaction.

Updates accumulate as sealed objects on disk (ADR-0004/0006). Rebuilding the
document means applying the compacted base state, then every sealed update after
it, in sequence. Without compaction a Yjs update log grows without bound; with
it, a long-lived layer stays proportional to its live content plus its
tombstones.

Everything on disk is ciphertext. The seq number in each filename is the same
seq bound into that object's AAD, so a blob cannot be renamed to pose as another.
"""

from __future__ import annotations

from pathlib import Path

from pycrdt import merge_updates

from app.infrastructure.crdt.document import LayerDocument
from app.infrastructure.crdt.updates import open_update, seal_update
from app.infrastructure.storage.paths import replace_atomic


class CRDTStore:
    """The on-disk update log and base state for one document."""

    def __init__(self, root: Path, *, layer_id: str, doc_id: str) -> None:
        self._dir = root
        self._layer_id = layer_id
        self._doc_id = doc_id
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---- layout ----------------------------------------------------------

    def _update_path(self, seq: int) -> Path:
        return self._dir / f"u{seq:016d}.blob"

    def _base_path(self, seq: int) -> Path:
        return self._dir / f"base-{seq:016d}.blob"

    def _update_seqs(self) -> list[int]:
        return sorted(int(p.stem[1:]) for p in self._dir.glob("u*.blob"))

    def _base(self) -> tuple[int, Path] | None:
        bases = sorted(self._dir.glob("base-*.blob"))
        if not bases:
            return None
        latest = bases[-1]
        return int(latest.stem.split("-")[1]), latest

    def head_seq(self) -> int:
        base = self._base()
        base_seq = base[0] if base else 0
        seqs = self._update_seqs()
        return max([base_seq, *seqs], default=0)

    # ---- writing ---------------------------------------------------------

    def append(self, key: bytes, update: bytes) -> int:
        """Seal and persist one update. Returns its sequence."""
        seq = self.head_seq() + 1
        blob = seal_update(
            key=key,
            layer_id=self._layer_id,
            doc_id=self._doc_id,
            update=update,
        )
        target = self._update_path(seq)
        tmp = self._dir / f".u{seq:016d}.tmp"
        tmp.write_bytes(blob)
        replace_atomic(tmp, target)
        return seq

    # ---- reading ---------------------------------------------------------

    def load_document(self, key: bytes) -> LayerDocument:
        """Rebuild the document: base state, then every update after it."""
        document = LayerDocument(self._doc_id)
        base = self._base()
        base_seq = 0
        if base is not None:
            base_seq, path = base
            plaintext = open_update(
                key=key,
                layer_id=self._layer_id,
                doc_id=self._doc_id,
                blob=path.read_bytes(),
                is_state=True,
            )
            document.apply_update(plaintext)
        for seq in self._update_seqs():
            if seq <= base_seq:
                continue
            plaintext = open_update(
                key=key,
                layer_id=self._layer_id,
                doc_id=self._doc_id,
                blob=self._update_path(seq).read_bytes(),
            )
            document.apply_update(plaintext)
        return document

    def raw_updates_after(self, key: bytes, after_seq: int) -> list[tuple[int, bytes]]:
        """Decrypted updates past ``after_seq`` — what a peer needs to catch up."""
        out: list[tuple[int, bytes]] = []
        for seq in self._update_seqs():
            if seq <= after_seq:
                continue
            out.append(
                (
                    seq,
                    open_update(
                        key=key,
                        layer_id=self._layer_id,
                        doc_id=self._doc_id,
                        blob=self._update_path(seq).read_bytes(),
                    ),
                )
            )
        return out

    # ---- compaction ------------------------------------------------------

    def compact(self, key: bytes) -> int:
        """Merge the whole log into one sealed base state; GC the old updates.

        Returns the number of update objects reclaimed. Merging uses Yjs's own
        ``merge_updates`` so the result is a single, equivalent update — tombstone
        metadata is retained (deletion is not forgetting), but the log stops
        growing.
        """
        base = self._base()
        seqs = self._update_seqs()
        if not seqs and base is None:
            return 0

        pieces: list[bytes] = []
        if base is not None:
            _base_seq, path = base
            pieces.append(
                open_update(
                    key=key,
                    layer_id=self._layer_id,
                    doc_id=self._doc_id,
                    blob=path.read_bytes(),
                    is_state=True,
                )
            )
        for seq in seqs:
            pieces.append(
                open_update(
                    key=key,
                    layer_id=self._layer_id,
                    doc_id=self._doc_id,
                    blob=self._update_path(seq).read_bytes(),
                )
            )

        merged = merge_updates(*pieces)
        new_seq = self.head_seq()
        blob = seal_update(
            key=key,
            layer_id=self._layer_id,
            doc_id=self._doc_id,
            update=merged,
            is_state=True,
        )
        tmp = self._dir / f".base-{new_seq:016d}.tmp"
        tmp.write_bytes(blob)
        replace_atomic(tmp, self._base_path(new_seq))

        reclaimed = 0
        for seq in seqs:
            self._update_path(seq).unlink(missing_ok=True)
            reclaimed += 1
        if base is not None and base[0] != new_seq:
            base[1].unlink(missing_ok=True)
        return reclaimed
