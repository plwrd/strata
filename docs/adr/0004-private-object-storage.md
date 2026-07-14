# ADR-0004: Private object storage layout (opaque random object ids)

**Status:** Accepted, 2026-07-14

## Context

A private layer's contents must remain confidential against an adversary who has **the encrypted
workspace directory but not the password** — a stolen laptop, a backup in a cloud sync folder, a
snapshot of a shared drive, a forensic image. That adversary sees the directory tree, every filename,
every file size, and every mtime, and can watch the tree change over time if the workspace lives in a
sync folder.

Encrypting *file contents* is the easy half (ADR-0005). The hard half is that a filesystem tree is
itself a rich side channel:

- **Names leak.** `notes/2026/Q3-layoffs-plan.md.enc` is a catastrophic leak regardless of how strong
  the AEAD is.
- **Deterministic name encryption leaks equality.** If the ciphertext name is a pure function of the
  plaintext name (e.g. `AES-SIV(name)` or `HMAC(key, name)`), then identical names produce identical
  filenames — across the layer and, worse, across users and across time. That gives an attacker an
  equality oracle and a dictionary attack: precompute the encrypted form of `README.md`,
  `Inbox.md`, `Daily/2026-07-14.md`, or a target's known filename set, and confirm presence.
- **Structure leaks.** A directory tree that mirrors the user's folder hierarchy tells an attacker the
  shape of their thinking — how many projects, how deep, how they nest — even with every name
  encrypted.
- **Extensions leak.** `.md` vs `.png` vs `.pdf` partitions the corpus for free.
- **Sizes leak.** Exact ciphertext lengths correlate with note lengths; over time, size *deltas* on a
  synced file reveal editing activity and edit magnitude.

We need a layout where the directory tree tells the adversary as close to nothing as we can practically
manage, while remaining a plain filesystem (no custom container format, no FUSE, no database) so that
it survives ordinary backup, sync, and copy tools.

## Decision

### Object store

A private layer is a **flat, unstructured bag of encrypted objects**. There is no hierarchy on disk that
corresponds to any hierarchy in the user's head.

```
<workspace>/layers/<layer-id>/
  layer.header          # small, cleartext-framed header (see below)
  objects/
    3f/3f9c1e7b0a4d5f62c8e10b7a4d9e2f61
    3f/3fa2…
    a0/a0d41e…
    …
```

- **Object id: 16 cryptographically random bytes, rendered as 32 lowercase hex characters.** Generated
  with `secrets.token_bytes(16)` / libsodium's RNG. Not derived from content. Not derived from the
  name. Not a hash of anything. It *looks* content-addressed — which is deliberate, because it should
  reveal nothing by its shape — but it carries **zero** information about the object.
- **Path: `objects/<first 2 hex chars>/<full 32 hex chars>`.** The two-character fan-out exists solely
  to keep directory entry counts manageable on filesystems that degrade with tens of thousands of
  entries in one directory (256 buckets; at 100k objects that is ~390 entries per bucket). It is not a
  security feature and it leaks nothing beyond the object count it is already leaking.
- **No file extension.** Ever. A note, an attachment, an embedding shard, a snapshot delta, and the
  manifest are indistinguishable on disk.
- **No plaintext names anywhere.** Not in the filename, not in an xattr, not in a sidecar.
- Objects are **immutable once written**; an update writes a new object and rewrites the manifest to
  point at it. Writes are atomic (`write temp → fsync → rename`). Garbage collection of unreferenced
  objects is a deliberate, explicit operation (it is observable, so it is scheduled and batched rather
  than immediate — see "Leakage" below).

### Where the structure actually lives

**All structure is in an encrypted manifest object.** The manifest maps the user's world onto object
ids:

```jsonc
// plaintext of the manifest object, before encryption
{
  "manifest_version": 1,
  "layer_id": "…",
  "notes":   { "<note-uuid>": { "object": "3f9c…", "title": "…", "parent": "<folder-uuid>",
                                "created": …, "modified": …, "tags": [...], "props": {...} } },
  "folders": { "<folder-uuid>": { "name": "…", "parent": "<folder-uuid>|null" } },
  "attachments": { "<att-uuid>": { "object": "a0d4…", "name": "…", "mime": "…", "size": 12345 } },
  "indexes": { "embeddings": ["…","…"], "search": ["…"] }
}
```

The manifest itself is stored as **one more encrypted object with a random id**, indistinguishable from
any other. Nothing on disk points at it except `layer.header`.

`layer.header` is the only file with structure an attacker can parse, and it deliberately contains only
what is needed to attempt an unlock:

```jsonc
{
  "strata_layer_version": 1,
  "layer_id": "<opaque uuid>",
  "kdf": { "alg": "argon2id", "v": 19, "t": 3, "m_kib": 262144, "p": 4, "salt": "<b64 16B>" },
  "wrapped_ldk": { "alg": "xchacha20poly1305", "nonce": "<b64 24B>", "ct": "<b64>" },
  "recovery": { "nonce": "<b64 24B>", "ct": "<b64>" } | null,
  "manifest_object": "<32 hex>",         // encrypted with the LDK like any other object
  "created": "2026-07-14T…Z"
}
```

The header names the manifest object id in the clear. That is fine: the id is random, and the manifest's
*contents* are AEAD-sealed under the layer data key. Knowing which of 40,000 identical-looking files is
the manifest buys an attacker nothing without the key.

### Manifest scaling

In **M3** the manifest is a **single encrypted object**, rewritten in full on every structural change.
This is simple, atomic, and correct. It is also O(manifest size) per edit, and it is a single point of
write contention — which is fine for one user on one machine but is exactly wrong for M9 collaboration.

The documented path is: in **M9**, the manifest becomes a **CRDT document** (ADR-0006 — the folder tree
is already specified as a Y.Map of parent pointers), materialised on disk as **chunked encrypted
objects** (an object per update batch / per shard, plus a compacted base), so that a concurrent edit
produces a small new object rather than a full rewrite, and so that two peers can merge manifests
without a lock. The header gains a `manifest_shards` list alongside `manifest_object`. We are recording
this now so that M3's code does not bake in assumptions (e.g. "the manifest is one blob you load
whole") that M9 would have to unpick. **Deferred to M9 deliberately; the M3 format version will be
bumped when it lands.**

### Padding

Ciphertext is padded to a **bucket boundary** before writing, and the true length is recorded inside the
encrypted plaintext (an ISO/IEC 7816-4 style padding marker, applied *before* encryption, so the
padding is itself confidential):

| Bucket | |
| --- | --- |
| 256 B | |
| 1 KiB | |
| 4 KiB | |
| 16 KiB | |
| 64 KiB | |
| 256 KiB | |
| 1 MiB | |
| then 1 MiB steps | (2 MiB, 3 MiB, … for attachments) |

This blunts, but does not eliminate, size correlation: a 3 KiB note and a 900 B note both land in
buckets, but a 40 MB video is still visibly a 40 MB-ish thing. Padding costs storage — worst case a
257 B object occupies 1 KiB (4×). For a text corpus the amortised overhead is modest; for large
attachments the 1 MiB step makes it negligible in relative terms. We accept the trade.

### Leakage — the honest list

An attacker with the encrypted directory and no password learns:

1. **That a Strata layer exists**, and its `layer_id` (an opaque UUID, but a stable identifier that can
   be correlated across backups/machines).
2. **The KDF parameters and salt**, and the fact that a recovery key does or does not exist. (Necessary
   to unlock; not secret.)
3. **The number of objects.** This bounds the number of notes + attachments + index shards. It does not
   distinguish between them.
4. **Bucketed object sizes**, and therefore an approximate size distribution. A layer of ten 256 B
   objects and a layer of ten 1 MiB objects are distinguishable.
5. **Total size on disk.**
6. **mtimes** of every object, and, for an attacker who can observe the directory **over time** (cloud
   sync, repeated backups), an *activity trace*: when the user worked, how often, how many objects
   changed per session, and — because the manifest is rewritten on structural change — when structure
   changed. This is the most under-appreciated leak in the design and we state it plainly.
7. **Which object is the manifest** (from `layer.header`), and hence roughly how large the structure is.

The attacker does **not** learn: note titles, folder names, folder structure, tags, properties, note
contents, attachment names, attachment MIME types, link structure, which objects are notes vs
attachments vs index shards, or which objects reference which.

Mitigations we apply: padding buckets (blunts 4), batched writes and deferred GC (blunts 6 — an editing
session produces one flush, not one write per keystroke). Mitigations we do **not** apply and the reason:
constant-size objects (unusable storage cost), decoy/dummy objects (a moving target with no principled
stopping point), mtime randomisation (breaks backup and sync tooling in ways users will experience as
data-loss risk). These are listed in THREAT_MODEL as accepted residual risk.

## Consequences

### Positive

- Filenames, extensions, and directory structure carry no information about the user's content. The
  equality/dictionary attack that deterministic filename encryption enables is impossible by
  construction, because the id is random and unrelated to the name.
- The store is an ordinary directory of ordinary files. Backup, `rsync`, Dropbox/OneDrive/iCloud, and a
  plain `cp -r` all work, with no format-aware tooling and no risk of a proprietary container becoming
  a data-recovery liability.
- Objects are immutable and content-independent, which makes atomic writes, snapshots, and
  deduplication-free reasoning simple. There is no partial-write window that can corrupt an existing
  object.
- Because *nothing* on disk is self-describing, an attacker cannot even mount a targeted attack (e.g.
  "find and destroy the note about X") without the key.

### Negative

- **The manifest is a single point of failure in M3.** Lose or corrupt the manifest object and the layer
  is a bag of unattributed ciphertext — recoverable in principle (each object's AAD names its own type
  and id; see ADR-0005) but structurally lost: no titles, no tree, no links. Mitigations, all required
  before M3 ships: the manifest is written with the same atomic temp→fsync→rename discipline; the
  previous **N manifest objects are retained** (they are just objects, and GC skips them) so a corrupt
  manifest can be rolled back; and the snapshot system (M2/M11) treats manifest history as
  first-class.
- **Full manifest rewrite per structural change** is O(n) and will be felt on a large layer — a 100k-note
  manifest is on the order of tens of MiB of JSON. This is the main pressure that forces the M9 chunked
  design, and it may force it earlier if profiling on a large corpus says so. Interim mitigation:
  debounce/batch structural writes, and keep the manifest as compact binary-ish JSON (short keys) rather
  than pretty-printed.
- **Padding wastes storage**, up to 4× for the smallest objects. A layer of 10,000 short notes
  (~500 B each) occupies ~10 MiB instead of ~5 MiB. Acceptable; stated so nobody "optimises" it away.
- **No content addressing means no deduplication.** Attaching the same 20 MB PDF to two notes stores it
  twice. This is a direct, deliberate cost of not hashing content: a content-addressed store would give
  us dedup *and* give an attacker a confirmation oracle ("does this user have *this exact file*?"),
  which is precisely the property we are refusing. If dedup becomes necessary it must be built on a
  *keyed*, per-layer hash (HMAC under the LDK), never a plain content hash.
- GC of unreferenced objects is a real subsystem that must be got right (mark from the manifest chain,
  never delete anything reachable from a retained manifest or snapshot), and getting it wrong deletes
  user data.

### Neutral

- The 2-hex fan-out gives 256 buckets. At 1M objects that is ~4k entries per directory, still fine. If
  we ever exceed that, a 4-hex fan-out is a format-version bump, not a redesign.
- Object ids are 128 bits of randomness; collision probability is negligible (birthday bound puts a 50%
  collision at ~2^64 objects). The writer nevertheless checks for an existing file before writing, and
  treats a collision as an `internal` error rather than silently overwriting.
- Because ids are random rather than derived, the same note synced to two machines *keeps* its object id
  (the id travels in the manifest); ids are not re-derived per machine. This matters for M9.
- Public (unencrypted) layers do **not** use this store. They are plain Markdown files on disk with real
  names, because their entire premise is that they are readable by other tools. The two storage backends
  live behind one `LayerStore` interface.

## Alternatives considered

### Deterministic filename encryption (AES-SIV / HMAC of the path)

Encrypt each path component with a deterministic, key-committing scheme so the tree structure is
preserved but names are unreadable. This is roughly what gocryptfs and Cryptomator (in filename mode)
do.

**Why rejected:** it preserves the directory *structure* (leak 3 in the list above), and, being
deterministic, it yields an equality oracle and a dictionary attack — an attacker who guesses a
plausible filename can encrypt it and check for its presence, and identical names across the corpus are
visibly identical. Those tools accept this because they are general-purpose filesystem overlays that
must support `readdir` and path lookup; Strata does not need path lookup at the filesystem level,
because it has a manifest. We should not pay a leak for a capability we do not use.

### Content-addressed store (id = SHA-256 of ciphertext or plaintext)

The obvious Git-shaped design; gives dedup and integrity for free.

**Why rejected:** if the id is a hash of the **plaintext**, it is a confirmation oracle — an adversary
can test "does this layer contain *this exact known document*?" without the key, which breaks
confidentiality for any file the attacker already has a copy of (a leaked PDF, a known template, a
public document). If it is a hash of the **ciphertext**, it leaks nothing directly but requires
deterministic encryption (same key + same nonce for the same content) to actually deduplicate, which
reintroduces the same oracle plus nonce reuse — a fatal AEAD misuse (ADR-0005). Random ids give us the
same *shape* with none of the oracle. We give up dedup; we consider that a correct trade for a notes
app.

### One encrypted container file (SQLCipher, or a custom archive)

Put everything in a single encrypted blob.

**Why rejected:** a single large file that is rewritten on every edit is hostile to every incremental
sync and backup tool (a 2 GB workspace re-uploads on every keystroke-flush), it makes partial corruption
catastrophic rather than local, and it makes concurrent access (M9, or just two Strata windows) a
locking problem. It also does not actually hide much more than the object store does: the container's
size and mtime leak exactly the same activity trace. See also ADR-0005's rejection of SQLCipher for the
crypto side.

### Encrypted filenames in a sidecar index file per directory

Keep the directory structure, put a small encrypted name-map in each directory.

**Why rejected:** it still leaks the tree shape and the per-directory entry counts, and it multiplies
the number of small mutable files that must be kept consistent. It is the worst of both designs.

### Store nothing on disk in structure — put the manifest in the header

Inline the manifest into `layer.header`.

**Why rejected:** `layer.header` must be readable and parseable *before* unlock (it carries the KDF
params). Growing it to hold the whole manifest means a multi-MiB file whose size directly leaks the
structure size, and it conflates "the thing you parse before you have a key" with "the most sensitive
object in the layer". Keeping them separate keeps the pre-authentication parse surface tiny — which is
itself a security property, since that parser runs on attacker-controlled bytes.

## Revisit when

- Profiling on a **>25k-note layer** shows manifest rewrite dominating save latency (>150 ms p95) —
  pull the M9 chunked manifest forward.
- A user-visible need for **attachment deduplication** appears (e.g. large shared media libraries); the
  answer is a keyed HMAC-under-LDK content id, not a plain hash, and it needs its own ADR.
- The activity-trace leak (leak 6) is judged unacceptable for a target user (e.g. a journalist under
  targeted surveillance whose cloud-sync folder is observed) — the mitigation would be write batching
  with cover traffic, which needs its own design and its own ADR.
- Any filesystem we must support degrades badly at 256-way fan-out, or a platform imposes a path-length
  limit we breach.
