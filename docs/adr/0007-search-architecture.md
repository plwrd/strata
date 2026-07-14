# ADR-0007: Search architecture (per-layer indexes; ephemeral private index)

**Status:** Accepted, 2026-07-14

## Context

Search is the primary way anyone navigates a knowledge base of any size. It must be fast, it must be
good (lexical *and* semantic), and it must be explainable. It must also not become the hole through
which encrypted layers leak.

That last point is the crux. **A search index is a summary of the corpus it indexes.** An inverted index
contains every term, every term's document frequency, and every document's length. A vector index
contains an embedding per note — and embeddings are *not* one-way: embedding-inversion attacks
reconstruct substantial fragments of the source text from the vector alone. If we persist a private
layer's index in plaintext, we have carefully encrypted the notes (ADR-0004, ADR-0005) and then written
a searchable, invertible summary of them next door.

Encrypting the index does not automatically fix this either. An encrypted-but-structured inverted index
— posting lists as encrypted files, one per term — leaks:

- **the number of distinct terms** (vocabulary size, which correlates with corpus size and language),
- **the length of each posting list** (term document frequency — and term frequency distributions are
  extremely identifying; a Zipf curve with a conspicuous spike is a fingerprint),
- **access patterns at query time** (which posting list was read → which term was searched), unless we
  read all of them or use ORAM.

Real searchable-symmetric-encryption schemes exist and are a genuine research field; they trade
leakage against performance, and the honest summary is that the practical ones leak more than their
users expect (see the literature on leakage-abuse attacks against SSE). We are a notes app, not a
cryptography research project, and we should not pretend to have solved this.

There is also a hard product rule: **a locked layer must contribute nothing.** Not a result. Not a
count. Not "3 results in a locked layer". Not a facet. Not an autocomplete term. Not a graph edge hint.
Any of those is an oracle.

## Decision

### 1. Per-layer indexes. Never a shared one.

Each layer has its own index, wholly separate, with its own lifecycle. Cross-layer search is a
**query-time fan-out and merge** across the indexes of the layers that are currently *unlocked and
searchable*, not a query against one combined index.

This is not a performance decision; it is a security decision. A shared index would mean a private
layer's terms sit in the same file as a public layer's, and every unlock/lock would mean surgically
adding and removing them — with residue (freed pages, WAL, tombstones) left behind. Separate indexes
make "this layer contributes nothing right now" a matter of *not opening a file*, which is a property we
can actually guarantee.

### 2. Public layers: SQLite FTS5, persisted

`<layer>/index.sqlite`, FTS5 with a Porter/unicode61 tokenizer, BM25 ranking, external-content table
referencing the note ids. Persisted, incrementally updated on save, rebuilt on demand. Public layers are
plaintext Markdown on disk by definition, so a plaintext index next to them leaks nothing that `grep`
would not.

### 3. Private layers: **ephemeral in-memory index, rebuilt on unlock** (the default)

When a private layer is unlocked:

1. Objects are decrypted (they are already being read to populate the manifest and the note cache).
2. An **in-memory** index is built: an inverted index (term → postings) held in Python process memory,
   plus the embedding matrix (below). Implementation: an in-memory SQLite FTS5 database
   (`file::memory:`) so we reuse the same query and BM25 code path as public layers, with a pure-Python
   fallback for platforms where in-memory FTS5 is unavailable.
3. On **lock** (explicit lock, workspace close, or app exit), the index is dropped and its memory
   overwritten to the extent Python permits (same caveat as ADR-0005: we cannot guarantee erasure; we
   drop references and overwrite the backing buffers we control).

**Nothing about the index is ever written to disk.** No temp files, no swap-friendly mmap, no SQLite
temp store on disk (`PRAGMA temp_store = MEMORY` is set explicitly, and it is a review checklist item).

**Cost:** rebuilding on unlock is O(corpus). Measured target: **≤ 3 s for 10,000 notes** on the
reference machine, run on a worker thread with a progress job (ADR-0003) so the app is usable while it
builds (search is simply unavailable-for-that-layer until it completes, and the UI says so — it does not
lie by returning partial results as if they were complete). The dominant cost at unlock is already
Argon2id (0.5–1.5 s) and object decryption; the index build overlaps with the latter.

**Leakage: zero at rest.** There is no index at rest. That is the entire point, and it is worth three
seconds.

### 4. Optional encrypted persistent index (opt-in, per layer)

For very large private layers (tens of thousands of notes, where a rebuild is tens of seconds), a
per-layer setting enables an **encrypted persistent index**: posting lists sharded and sealed as
ordinary objects in the object store (ADR-0004), so they are indistinguishable from notes; shard
boundaries chosen by hashing terms into a **fixed number of buckets** (so vocabulary size does not leak
via shard count) and padded to the standard buckets (so posting-list length leaks only coarsely); the
whole shard set loaded and decrypted into memory at unlock (so query-time *access patterns* do not leak,
because we never do a selective read).

This is a **strictly worse security posture** than option 3 and the UI says so, in those words, at the
moment the user enables it:

> "Persisting the search index writes an encrypted summary of this layer to disk. Even encrypted and
> padded, the number and size of index objects reveal coarse information about how much you have
> written and how varied your vocabulary is. Rebuilding on unlock reveals nothing. Persist only if
> unlock time is a genuine problem for you."

It is off by default, per-layer, and never enabled automatically. **Deferred to M4 for design detail;
the shard/padding scheme gets its own spec before it ships, and it does not ship at all if we cannot
articulate its leakage as precisely as ADR-0004 articulates the object store's.**

### 5. Vectors and embeddings are private content

An embedding is a lossy but **invertible-ish** encoding of the text. We treat it with the same
seriousness as the text.

- **Private layers:** embeddings live in an **in-memory NumPy `float32` matrix** (`N × D`, with an id
  vector alongside) while the layer is unlocked. Cosine similarity is a single matmul; at 10k notes ×
  768 dims that is a 30 MB matrix and a sub-millisecond query. They are persisted **only inside
  encrypted objects** (as a small number of large embedding shards, so a note's embedding is not a
  one-to-one file), and only if the layer has index persistence enabled; otherwise they are recomputed
  on unlock, or (default) computed lazily on first semantic search.
- **They are never sent to a remote embedding provider unless the layer's AI policy allows it**
  (ADR-0008). Computing embeddings for a private layer with a cloud provider means sending the note text
  to that provider. The default for a private layer is a **local** embedding model, and if none is
  available, semantic search for that layer is simply off, with an honest explanation, rather than
  silently uploading the user's notes.
- **Public layers:** embeddings may be persisted in `index.sqlite` (or a sidecar `.npy`) in plaintext.

### 6. Hybrid ranking, with the reasons shown

A result's score is a weighted combination of five signals:

| Signal | Source | Rough weight |
| --- | --- | --- |
| **Lexical** | BM25 from FTS5 | highest for exact/quoted queries |
| **Semantic** | cosine similarity against the embedding matrix | highest for natural-language queries |
| **Graph proximity** | distance in the link graph from the current note / current selection | tie-breaker and context-sensitiser |
| **Property / tag match** | exact match on tags, frontmatter properties, note type | strong boost when present |
| **Recency** | modified time, with a gentle decay | small, but decisive between otherwise-equal results |

Scores are normalised per-signal before combination (BM25 and cosine are not on the same scale, and
pretending they are is the classic hybrid-search bug). Weights are query-adaptive: a quoted phrase
pushes weight to lexical; a long natural-language question pushes it to semantic.

**Every result carries its per-signal contributions**, and the UI surfaces them as *"why this
matched"* — "matched **budget** (lexical), semantically close to your query, 2 links from *Q3
Planning*, edited yesterday". This is not a debug feature; it is the feature. A ranking a user cannot
interrogate is a ranking they cannot trust, and in a knowledge base, an unexplained missing result is
worse than a bad one. It also gives us a way to debug ranking regressions with real user reports.

### 7. Locked layers contribute **nothing**

Enumerated, because "nothing" must be exhaustive:

- No results. No snippets.
- **No counts.** Not even "N results in locked layers". A count is a term-presence oracle, repeatable
  across a dictionary.
- No facet entries, no tag list, no property keys, no folder names.
- No autocomplete or query suggestions derived from the layer's vocabulary.
- No graph nodes, no graph edges, no "linked from a locked layer" hints, no orphan-node placeholders.
- No contribution to global statistics (total note count, tag clouds, "most linked" lists).
- Timing must not leak either: search latency must not measurably vary with the *content* of a locked
  layer (trivially satisfied, since we do not touch it at all).

The UI may say *"You have 2 locked layers"* — that fact is already visible from the workspace directory
(ADR-0004 leak #1) — with an unlock affordance. It may not say anything about what is *in* them.

## Consequences

### Positive

- **A locked private layer is genuinely inert.** There is no index file, no vector file, no cached
  snippet, no shared table with residue. The security property is enforced by the absence of an
  artefact, which is the only kind of enforcement that survives a code change by someone who has not
  read this document.
- Per-layer indexes make lock/unlock cheap and total, make "this layer is corrupt, rebuild it" a local
  operation, and make cross-layer search a merge we control (including its ranking normalisation).
- Reusing SQLite FTS5 for both the persisted (public) and in-memory (private) case means **one query
  path, one BM25 implementation, one snippet generator** — not two subtly different search engines.
- The hybrid signal breakdown is a product differentiator and a debugging tool at the same time.
- Keeping private embeddings in a NumPy matrix in memory is both the most private option and, at our
  scale, the fastest one. There is no trade here; a brute-force matmul over 10k × 768 beats any ANN
  index's overhead and complexity.

### Negative

- **Unlock costs seconds.** Argon2id + decrypt + index build. For a 10k-note layer, expect ~3–5 s
  wall-clock before search is available. Users who unlock and immediately search will notice. We show
  progress, we never block the UI, and we never fake completeness.
- **Rebuild cost is O(corpus) on every unlock**, including an unlock that happens because the user
  toggled a layer for ten seconds. Mitigation: an unlock-session cache — a layer that is locked and
  re-unlocked within the same app session, with no intervening writes, reuses the in-memory index if it
  is still resident (which means "lock" must decide whether it is a *privacy* lock, which drops
  everything, or a *UI* lock, which does not — **it is always a privacy lock; the cache is only valid
  while the layer was never actually locked**. We are calling this out because it is exactly the kind of
  optimisation that would silently destroy the property.)
- **RAM.** An unlocked 50k-note layer holds its full text index and a 50k × 768 float32 matrix
  (~150 MB) in memory, plus the decrypted note cache. Several large layers unlocked at once is a real
  memory footprint on a 8–16 GB machine. This bounds how far the ephemeral design scales and is the
  honest reason the opt-in persistent index exists at all.
- **The optional persistent index is a security downgrade** and we will have to resist pressure to make
  it the default, or to enable it "automatically for large layers". It stays opt-in.
- Semantic search on private layers requires a local embedding model, which means shipping or
  downloading one (an ONNX MiniLM-class model, ~90 MB). Users without one get lexical-only search on
  private layers. That is the correct default and it will still be reported as a bug.
- Query-adaptive weighting is a tuning surface with no ground truth. Expect it to be wrong, expect to
  need an evaluation set of realistic queries, and expect "why this matched" to be how we find out it is
  wrong.

### Neutral

- Search results are paginated over the bridge (the 1 MiB envelope cap, ADR-0003), and streamed as job
  `partial` events for large fan-outs, so a slow layer does not hold up a fast one.
- FTS5's tokenizer choice affects CJK and other non-space-delimited languages badly. `unicode61` with
  trigram fallback for CJK is the plan; it is not great, and proper CJK segmentation is deferred (to
  M11, with an explicit note that it needs a real tokenizer, not a hack).
- Because private embeddings are computed lazily by default, the *first* semantic search in an unlocked
  layer is slow (it embeds the corpus). This is a job with progress, not a hang.
- Nothing here precludes an ANN index (HNSW/FAISS) later; at 100k+ notes the brute-force matmul stops
  being free. That is a performance change within the private-in-memory boundary, not a security change.

## Alternatives considered

### One shared index across all layers, with a `layer_id` column and query-time filtering

The obvious design. One FTS5 table, filter by which layers are unlocked.

**Why rejected:** it puts private terms in a plaintext file. Even if every query filters correctly
(and one missed `WHERE` clause is a total confidentiality break — a single-line bug with catastrophic
blast radius), the file *itself* sitting on disk contains the private layer's vocabulary and document
frequencies in the clear. Locking a layer would mean deleting rows, which leaves them in freelist pages
and in the WAL. There is no way to make this safe, and the failure mode is a silent, permanent leak that
no test would catch. This is the design we are most explicitly rejecting.

### Encrypted persistent inverted index as the *default* for private layers

Build it, encrypt it, keep it.

**Why rejected as the default:** as set out in Context, an encrypted structured index still leaks
term/document-frequency structure, and defending that properly requires padding so aggressive that the
index approaches the size of the corpus — at which point rebuilding from the corpus is nearly free
anyway, and leaks nothing. The persistent option survives only as an explicit, informed, per-layer
opt-in for users whose corpus is large enough that unlock time genuinely hurts. Choosing the
zero-leakage default and offering the trade honestly is the right shape.

### Searchable Symmetric Encryption (SSE) / encrypted search schemes

Use a real SSE construction with formal leakage bounds.

**Why rejected:** the practical SSE schemes leak query patterns and access patterns, and the literature
on leakage-abuse attacks shows those leaks are exploitable in realistic settings — often *more*
exploitable than their proponents assumed. The ones with strong bounds (ORAM-based) are orders of
magnitude too slow. Adopting an SSE scheme would let us *claim* encrypted search while shipping a
leakage profile we could not honestly characterise to users, which is worse than shipping a rebuild that
leaks nothing. If the field produces something practical with characterisable leakage, we will look
again.

### Persist embeddings in plaintext, encrypt only the notes

"They're just numbers."

**Why rejected:** they are not just numbers. Embedding inversion reconstructs meaningful text from
vectors, and even without inversion, a plaintext embedding matrix lets an attacker cluster the corpus,
measure its topical spread, and test "is a document semantically like *this one* in here?" — a
confirmation oracle. Embeddings are ciphertext-grade content and are treated as such.

### No semantic search at all in private layers

Simplest, safest.

**Why rejected:** semantic search over your own private knowledge is one of the main reasons to use an
AI-native notes app. The in-memory-matrix design gives us the capability with no at-rest leakage. Where
we *do* refuse is sending private text to a remote embedding provider by default — that refusal stands.

### Send queries to a remote provider for reranking

Cheap quality win.

**Why rejected for private layers:** the query *and* the candidate snippets would leave the machine.
That is an exfiltration channel dressed as a ranking feature. Reranking is permitted only where the
layer's AI policy (ADR-0008) already permits remote processing of that layer's content, and never
implicitly.

## Revisit when

- Unlock-plus-rebuild exceeds **5 s at p95** on real user corpora — that is the point where the
  persistent-index opt-in stops being an edge case and needs to be genuinely good.
- Memory pressure from unlocked layers is reported in the field (several large layers, low-RAM
  machines).
- A private layer exceeds ~**100k notes**, where brute-force cosine stops being cheap and an
  in-memory ANN index (HNSW over the same matrix) becomes worth its complexity.
- A practical, characterisable SSE scheme appears that we could explain to a user in two sentences.
- CJK/Thai/Arabic users report search as unusable — the tokenizer debt comes due.
