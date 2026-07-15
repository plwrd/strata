# ADR-0006: CRDT selection (Yjs semantics via `pycrdt`)

**Status:** Accepted (validated), 2026-07-14 · spike passed in M9

> Was provisional through M0–M8; the M9 validation spike has now run and passed, so this ADR is
> **confirmed**. `pycrdt==0.14.1` is adopted. The spike lives as executable exit criteria in
> `tests/unit/test_crdt_spike.py` — two- and three-peer convergence (including a peer that goes
> offline), a 5,000-node backlog merging well under the 2 s budget, all three conflict classes
> detectable post-merge, order-independence/idempotency, and sealed-update security
> (`tests/security/test_crdt_encryption.py`). The implementation is `app/infrastructure/crdt/`
> (document, updates, store, conflicts, relay) with `app/services/collaboration_service.py`
> orchestrating share/join/sync/rescue over an untrusted relay. If any exit criterion later regresses,
> the fallback remains Automerge; nothing before M9 depends on the specific library.

## Context

M9 introduces real-time collaboration: two or more people editing the same layer, possibly offline,
possibly on flaky networks, with **no trusted server** — the relay is a dumb forwarder of encrypted
blobs and must never see plaintext (that is the whole product premise; see ADR-0004, ADR-0005 and
THREAT_MODEL).

That last constraint is the one that kills most of the design space. Operational Transformation, and
every "just use a server to sequence edits" design, needs a server that can *read and transform the
operations*. We do not have one and will not have one. Convergence must therefore be a property of the
data structure itself, not of a central sequencer — which means a CRDT.

The data we must merge is not just text:

1. **Note bodies** — collaborative rich text with concurrent insertions at arbitrary positions,
   undo/redo, and (ideally) cursors and selections.
2. **Note metadata** — title, tags, properties, links. Concurrent field edits.
3. **The folder tree** — moves, renames, creates, deletes, across peers, offline.

(3) is the hard one, and it is the one most CRDT write-ups skate over.

## Decision

Adopt **Yjs semantics**, implemented on the Python side via **`pycrdt`** (Python bindings to the Rust
`y-crdt` implementation of the Yjs protocol).

### Why the Python side owns the CRDT

Because the encryption does (ADR-0005). A CRDT update must be sealed **before it leaves the process**,
and the key lives in Python. Putting the authoritative CRDT in the renderer would mean either shipping
the LDK into the renderer (absolutely not, ADR-0003) or encrypting in the renderer with a key it does
not have. So: **Python holds the authoritative `Doc`; the renderer holds a client-side Yjs `Doc` bound
to CodeMirror via `y-codemirror.next`, and the two are synchronised over the bridge with the *same*
binary update format** (Yjs updates are a well-specified binary encoding; both sides speak it). The
renderer's copy is a view, not a source of truth: it is what gives the editor sub-frame responsiveness,
while Python is what persists and what encrypts.

`pycrdt` being a binding to `y-crdt` (Rust) rather than a pure-Python reimplementation matters twice
over: it is fast enough to merge in the host process without a GIL disaster (ADR-0001), and it is
wire-compatible with the JS Yjs the editor uses. A pure-Python CRDT would be neither.

### Data model

| Strata concept | Yjs type | Notes |
| --- | --- | --- |
| Note body | `Y.Text` | Bound to CodeMirror 6 via `y-codemirror.next`. Character-level merge, real undo via `UndoManager` scoped per-origin. |
| Note metadata | `Y.Map` (one per note) | `title`, `tags` (`Y.Array`), `props` (`Y.Map`), `created`, `modified`. Concurrent edits to *different* fields merge cleanly; concurrent edits to the *same* field are last-writer-wins by Yjs's clock, which is acceptable for scalars. |
| Folder tree | `Y.Map` of **parent pointers**: `nodeId -> {parent: nodeId|null, name: str, order: str}` | **Not** a nested structure. See below. |
| Links / backlinks | Derived, not stored | Computed from note bodies by `GraphService`; never a CRDT, never merged. |
| Manifest (ADR-0004) | The above, as one `Doc` per layer | In M9 the manifest *becomes* this document, persisted as chunked encrypted update objects. |

**The folder tree is a `Y.Map` of parent pointers, deliberately.** A nested `Y.Map`-of-`Y.Map` tree
cannot represent a move atomically and is a well-known source of duplicated or vanished subtrees under
concurrency. Parent pointers make a move a single-key write, which is the smallest possible unit of
concurrency and the easiest to reason about. Sibling ordering uses a fractional-index string (`order`)
so that concurrent inserts between two siblings do not collide.

### The conflict surface — the part that actually matters

**A CRDT guarantees convergence, not correctness.** Every replica will agree on the result; the result
can still be semantically wrong. The three cases we know will occur:

1. **Move cycle.** Alice moves folder A under B; Bob concurrently moves B under A. Both operations are
   valid single-key writes; the merged state has A and B as each other's ancestors — a cycle, i.e. a
   detached loop that is no longer reachable from the root. Naive CRDT tree implementations either
   silently orphan the subtree or corrupt traversal.
2. **Move-vs-delete.** Alice moves note N into folder F; Bob deletes folder F. Converged state: N's
   parent points at a tombstoned folder. N is now unreachable — silently, invisibly, permanently. This
   is the classic "the CRDT merged successfully and my note disappeared" bug.
3. **Concurrent edit-vs-delete.** Alice is editing note N; Bob deletes it. Yjs will happily converge to
   "deleted, with some text updates applied to a tombstone". Alice's work evaporates.

Strata's position: **these are not merged silently. They are surfaced.** Concretely:

- After every merge, a `TreeIntegrity` pass runs in `GraphService`/`LayerService`: detect cycles,
  detect nodes whose parent is a tombstone, detect content edits to tombstoned notes.
- Anything it finds is **rescued, not resolved**: cycle members and orphans are re-parented into a
  system folder (`Conflicts/`, present only when non-empty), and a **`ConflictRecord`** is written —
  `{kind, nodeIds, peers, timestamps, previousParent}` — and pushed to the UI over `JobBridge.jobEvent`.
- The UI shows a conflict banner and a resolution view: "Bob deleted the folder *Research* while you
  moved *Interview notes* into it. The note is safe, in Conflicts/. Restore the folder, move the note
  elsewhere, or confirm the delete."
- **We never lose data to a merge.** A tombstoned-but-edited note is resurrected into `Conflicts/` with
  its content intact. Deletion is only *final* once no conflict record references it.

This "explicit conflict surface" is the actual product decision here. The CRDT library choice is
downstream of it. Any library we pick must let us inspect the merge outcome well enough to implement
it — which is itself a selection criterion, and one of the things the M9 spike must verify.

### Encryption of updates

A Yjs update is an opaque binary diff. Strata seals each update (or each batch — updates are merged and
flushed on a debounce, not per keystroke) with XChaCha20-Poly1305 under the LDK (ADR-0005), with AAD
binding `{fmt, layer_id, doc_id, seq}`. The relay stores and forwards ciphertext. A relay operator sees:
blob sizes, timing, peer identities (pseudonymous), and nothing else — no text, no titles, no
structure. This is stated as a requirement on the transport, and it is why the CRDT must live behind the
encryption boundary rather than in front of it.

Persistence: updates accumulate as encrypted objects (ADR-0004) and are periodically **compacted** into
a new base state, with the old update objects GC'd. Without compaction a Yjs document's update log grows
without bound; with it, a long-lived layer stays proportional to its live content plus its tombstones.

### Validation spike (M9, first task, timeboxed 2 weeks)

Exit criteria — all must pass, or this ADR is superseded:

1. `pycrdt` and JS Yjs interoperate over our bridge: a `Y.Text` edited in CodeMirror in the renderer and
   an edit applied in Python converge, byte-identically, in both directions.
2. Three peers, one of them offline for an hour, converge on reconnect with no data loss on a 5,000-note
   layer.
3. Merge of a 10,000-update backlog completes in < 2 s and does not block the Qt main thread.
4. All three conflict classes above are **detectable** post-merge with the information `pycrdt` exposes.
5. Document size after compaction is within ~2× the plaintext corpus size for a realistic edit history.
6. `pycrdt`'s maintenance status is still acceptable (releases within the last 6 months, y-crdt upstream
   alive).

## Consequences

### Positive

- **No server needed for correctness.** Convergence is a property of the data type; the relay can be a
  dumb, untrusted, encrypted-blob pipe, which is exactly what the threat model requires.
- **Offline editing is the default case, not a special case.** A peer that has been offline for a week
  reconnects and merges. There is no "sync conflict, choose a file" dialog, because there is no file-level
  conflict.
- **Yjs is the most battle-tested CRDT in production** for exactly our use case (collaborative text in
  editors), with a mature editor binding ecosystem (`y-codemirror.next` exists and works), a specified
  binary wire format, and a Rust core we can call from Python.
- Wire-format compatibility between `pycrdt` (Rust core) and JS Yjs means the renderer's editor and the
  Python authority speak the same updates with no translation layer — the largest single source of bugs
  in a split-CRDT design is simply absent.
- The parent-pointer tree makes moves atomic and makes the pathological cases *detectable*, which is what
  lets us build the conflict surface at all.

### Negative

- **`pycrdt` is a much smaller project than Yjs itself**, with a smaller maintainer base. We are taking a
  dependency on a binding, and if it is abandoned we are on the hook for maintaining a Rust-Python
  binding — which is not a skill the team has (see ADR-0001's rejection of Rust). This is the single
  biggest risk in this ADR and the reason it is provisional. Mitigation: the spike verifies maintenance
  status; the abstraction (`CRDTStore` interface) keeps `pycrdt` behind a seam; and the wire format is
  Yjs's, which is *specified*, so a worst-case fallback is calling into a Node/JS Yjs sidecar (ugly, but
  a known escape hatch).
- **Tombstones accumulate.** Yjs never truly forgets deleted content until compaction, and even then it
  retains deletion metadata. A layer with a long, churny history will carry a document larger than its
  live content. Compaction bounds this; it does not eliminate it. Users deleting sensitive text should be
  told plainly that the text may persist in the document history until compaction runs — **which is a
  privacy statement, not just a performance one, and it must be in the UI and in SECURITY.**
- **Undo across a merge is confusing.** Yjs's `UndoManager` scopes undo per-origin, which is right, but
  "undo" after a remote merge does not do what a naive user expects. Expect UX iteration here.
- **Metadata conflicts are LWW.** Two peers renaming a note concurrently: one rename wins, silently. We
  accept this for scalar fields (it is not data loss in any meaningful sense) but it is a real behaviour
  users will notice.
- Adding a CRDT means the manifest format changes in M9 (ADR-0004 anticipates this), which is a
  format-version bump and a migration.

### Neutral

- The CRDT applies **only to collaborative layers**. A single-user private layer does not need one and
  does not pay for one; the `CRDTStore` seam is inert until a layer is shared. This keeps M3–M8 free of
  CRDT complexity entirely.
- Yjs's document model is not a general database. Structured views (M10) are *projections* computed from
  the CRDT state, not stored in it.
- Fractional indexing for sibling order produces keys that grow under adversarial interleaving. Not a
  practical problem at human editing rates; a periodic re-index during compaction handles it.
- Public layers (plain Markdown on disk) are explicitly **out of scope** for collaboration. Two people
  editing the same public layer through a file-sync tool get whatever that tool does. We do not pretend
  otherwise.

## Alternatives considered

### Automerge

The other serious general-purpose CRDT. Excellent theory (columnar encoding, good rich-text support in
Automerge 2/3), a solid Rust core, and a genuinely thoughtful approach to the tree-move problem
(it has published work on move operations that our conflict surface would benefit from).

**Why rejected:** the Python binding story is materially less mature than `pycrdt`'s, and the *editor*
binding story is much weaker than Yjs's — we would be writing our own CodeMirror 6 binding, which is
precisely the kind of foundational work we chose the web stack (ADR-0002) to avoid. Yjs's ecosystem
advantage at the editor boundary is decisive for a product whose core surface is a text editor. This is
a close call and the strongest alternative; if the M9 spike fails on `pycrdt`, **Automerge is the first
thing to re-evaluate**, and the parent-pointer/conflict-surface design above transfers to it
essentially unchanged.

### Operational Transformation (OT)

The classic approach (Google Docs, ShareDB). Mature, well-understood, efficient wire format.

**Why rejected:** OT requires a **trusted central server** to sequence and transform operations. It has
to *understand* the operations, which means it has to *read* them, which means it has to have the
plaintext. That is fundamentally incompatible with an untrusted relay and end-to-end encryption. There
is no version of OT that works with a server that cannot decrypt. Rejected on architecture, not on
quality.

### Last-writer-wins on whole notes (file-level sync, à la Dropbox/Obsidian Sync)

Each note is a file; on conflict, newest timestamp wins (or write a `-conflict` copy).

**Why rejected:** it loses data. Two people editing the same note for ten minutes produces one winner and
one loser, and the loser's ten minutes are gone (or exiled to a `note (Bob's conflicted copy).md` that
nobody ever reconciles). For a product whose pitch is *collaborative* knowledge work, "we sometimes
silently discard your writing" is not a trade we can make. It is, however, the correct behaviour for
**public** layers synced by an external tool, and we say so.

### Server-authoritative with a trusted Strata backend

Just run a server, keep it simple.

**Why rejected:** it deletes the product. Local-first and end-to-end encrypted are not features here;
they are the thesis.

### Write our own CRDT

**Why rejected:** CRDTs are a field where the gap between "converges in my tests" and "converges" is
enormous and the failure mode is silent data corruption discovered months later. The same reasoning as
ADR-0005's "no custom crypto".

## Revisit when

- **The M9 validation spike runs.** Any exit criterion failing supersedes this ADR (first alternative:
  Automerge).
- `pycrdt` goes unmaintained (no release in 12 months, or y-crdt upstream stalls).
- A layer's compacted CRDT document exceeds ~3× its plaintext corpus in real user data — tombstone
  growth is then a product problem, not just a performance one.
- Users report the conflict surface as noise rather than safety (i.e. we are surfacing conflicts that
  are not real). Then the *detection* heuristics need tuning — not the CRDT.
