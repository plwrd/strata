# Strata — On-Disk Storage Layout

**Status:** the public-layer layout lands in **M2**; the private-layer layout lands in **M3**. This
document is normative and is written to be implementable — and to be *readable by a tool that is not
Strata*.

That is the point. A local-first product whose data you can only get at through its own binary has not
given you your data; it has given you a hostage. You should be able to open a public layer in any text
editor, and write your own decryptor for a private one from
[encryption-format.md](../security/encryption-format.md).

Related: [system-architecture.md](system-architecture.md) · [THREAT_MODEL.md](../../THREAT_MODEL.md)

---

## 1. A workspace is a directory

```
my-workspace/
├── workspace.json                 # workspace descriptor (plaintext, no secrets)
├── .strata/                       # workspace-local state (all of it derivable or disposable)
│   ├── state.json                 # UI state: open tabs, last layer, window geometry
│   └── logs/                      # local-only, scrubbed. Never uploaded (A-008)
├── layers/
│   ├── journal/                   # a PRIVATE layer   (see §3)
│   └── notes/                     # a PUBLIC layer    (see §2)
└── snapshots/                     # see §5
```

There is no hidden application database elsewhere on your machine. Copy this directory and you have
copied your workspace — including, for a private layer, the ciphertext and the wrapped keys, which are
useless without your password.

### `workspace.json`

```jsonc
{
  "format_version": 1,
  "workspace_id": "8c1d…",             // random 128-bit, hex
  "name": "Research",
  "created_at": "2026-07-14T09:00:00Z",
  "layers": [
    { "id": "3f9a…", "dir": "layers/notes",   "kind": "public",  "name": "Notes",   "mounted": true },
    { "id": "6f1a…", "dir": "layers/journal", "kind": "private", "name": "Journal", "mounted": true }
  ],
  "default_layer": "3f9a…"
}
```

**`dir` is always relative.** No absolute path is ever persisted in a workspace or layer file
([FR-013](../../PRODUCT_REQUIREMENTS.md)) — you can move the directory, put it on a USB stick, or sync
it to another machine and it still opens.

**What `workspace.json` leaks, deliberately:** the *names* of your layers and *that a private layer
exists*. We could encrypt the layer name, but the existence of the directory gives it away regardless,
and a workspace you cannot inspect without the app is a workspace you cannot trust. See
[THREAT_MODEL.md §5](../../THREAT_MODEL.md) and [A-016](../../ASSUMPTIONS.md). **If you name a private
layer "Divorce Papers", that name is on disk in plaintext.** Name it "Journal".

---

## 2. A public layer — plain Markdown

```
layers/notes/
├── layer.json                     # layer descriptor (plaintext)
├── content/
│   ├── Inbox/
│   │   └── Reading list.md
│   ├── Projects/
│   │   ├── Strata.md
│   │   └── Strata/
│   │       └── architecture-sketch.png
│   └── Daily/
│       └── 2026-07-14.md
├── attachments/
│   └── diagram.png
├── .trash/                        # see §4
└── index.sqlite                   # DERIVED CACHE — delete it any time, it rebuilds
```

- **Files are the truth.** Real names, real folders, human-readable Markdown with YAML front matter.
  Another editor can open them; Git can version them; `grep` works.
- **`index.sqlite` is a cache.** FTS + link graph + property index. Delete it and Strata rebuilds it.
  Never put anything in it that does not exist in the files ([FR-062](../../PRODUCT_REQUIREMENTS.md)).
- **External edits are expected, not fought.** `watchdog` reconciles them ([A-014](../../ASSUMPTIONS.md)).

> **A public layer offers no confidentiality.** It is not "lightly protected" or "encrypted at rest by
> your OS". It is plaintext. The UI must never blur this line.

### `layer.json` (public)

```jsonc
{
  "format_version": 1,
  "layer_id": "3f9a…",
  "kind": "public",
  "name": "Notes",
  "created_at": "2026-07-14T09:00:00Z",
  "ai_policy": "ask-each-time",       // never | local-only | ask-each-time | allow
  "sharing_mode": "personal"
}
```

---

## 3. A private layer — ciphertext only

```
layers/journal/
├── layer.header                   # KDF params + wrapped LDK envelopes. NO SECRETS IN THE CLEAR.
├── layer.header.bak               # previous header, kept across every rewrite
├── layer.json                     # MINIMAL descriptor — kind + id only (see below)
├── objects/
│   ├── 00/
│   │   └── 00c4e1f5a97b2d3e4f5061728394a5b6
│   ├── 3a/
│   │   ├── 3a7f0c19d84e2b6a5c93f1e0d7b84a26
│   │   └── 3af2b81c6d9047e5a1b3c8d2e6f70915
│   ├── 9e/
│   │   └── 9e51d0a3c7b2486f9d1e5a0c3b8f2647
│   └── …                          # 256 shards, 00..ff
├── .trash/
│   └── objects/                   # tombstoned objects — STILL ENCRYPTED
└── rotation.state                 # present ONLY during a key rotation (see §3.4)
```

**That is the entire layer.** There is no note list, no folder tree, no `.md` file, no `index.sqlite`,
no thumbnail cache, no preview directory, and no temp file — anywhere, ever.

### 3.1 Object files

Path: `objects/<first 2 hex chars of the object id>/<object id, 32 lowercase hex chars>`

| | |
| --- | --- |
| **Object id** | **16 random bytes** from the CSPRNG → **32 hex characters** as the filename. |
| **Shard** | The first 2 hex characters, giving a 256-way fan-out so no directory holds 100k entries. |
| **Contents** | The container from [encryption-format.md](../security/encryption-format.md): a 71-byte authenticated cleartext header (`magic · format_version · alg · object_type · flags · layer_id · object_id · nonce · plaintext_len`), then ciphertext, then a 16-byte Poly1305 tag. |
| **Filename meaning** | **None.** It is random. It is *not* an encryption of the title, and it is *not* a hash of the content. |

**Why the filename is random and not a deterministic encryption of the name.** Deterministic filename
encryption (SIV-style) leaks *equality*: an observer could tell that two workspaces contain a file with
the same name, detect a rename as a delete-plus-create, and **confirm a guessed filename** by
recomputing its ciphertext. "Is there a note called `resignation-letter`?" becomes a question the disk
can answer. Random ids answer nothing. See [A-015](../../ASSUMPTIONS.md), [T-10](../../THREAT_MODEL.md).

The filename **must** equal `hex(object_id)` from the authenticated header. A reader checks this before
attempting decryption — that, plus the AAD binding, is what makes swapping two object files fail
([T-32](../../THREAT_MODEL.md)).

### 3.2 The manifest

Everything that would be a directory listing in a normal filesystem lives in **one encrypted object**
(`object_type = 0x01`, id recorded in `layer.header`):

- Real names and titles
- The folder tree
- Tags, properties, and schema assignments
- Wiki links, backlinks, and typed relations
- The attachment map (which object id is which file, and its real filename and MIME type)
- Saved views, Knowledge Lenses, task metadata

**Nothing in that list touches disk in the clear.** Locked, the layer is a pile of same-shaped
random-named blobs. There is no structure to read.

The manifest is a hot object — every rename and every new link rewrites it — so it is written
atomically, snapshotted, and integrity-checked. If it becomes a write-amplification problem at 100k
objects, the fix is to **shard the manifest**, never to move names back into filenames
([A-015](../../ASSUMPTIONS.md)).

### 3.3 `layer.json` (private) — deliberately almost empty

```jsonc
{
  "format_version": 1,
  "layer_id": "6f1a…",
  "kind": "private"
}
```

Note what is **not** here: no name, no timestamps beyond what `layer.header` needs, no object count, no
AI policy, no sharing mode. Those live inside the encrypted manifest. The display name a private layer
shows in the sidebar while locked comes from `workspace.json`, which is why we say: **choose a boring
one.**

### 3.4 `rotation.state`

Present **only** while a key rotation is in flight. It records the rotation id, the new LDK's wrapped
envelope, and which object shards are already migrated, so an interrupted rotation is resumable and so
readers know a dual-key read window is open ([encryption-format.md §6.2](../security/encryption-format.md)).
It is deleted the moment rotation completes. It contains no plaintext key material.

### 3.5 What is *not* here, and why that is the whole design

| Absent | Because |
| --- | --- |
| `index.sqlite` | A search index is a near-perfect reconstruction of the corpus. The private index is built in memory on unlock and destroyed on lock ([A-004](../../ASSUMPTIONS.md), [T-08](../../THREAT_MODEL.md)). An encrypted persistent index is opt-in, off by default, and stored as `object_type 0x0A`. |
| Embeddings on disk | Text is substantially recoverable from embeddings. They are treated as **plaintext-equivalent** ([T-09](../../THREAT_MODEL.md)). |
| Thumbnails / previews | Rendered from memory; cleared on lock. Qt WebEngine's disk cache is disabled and the profile is off-the-record ([T-12](../../THREAT_MODEL.md)). |
| Temp / staging files | **Hard rule** ([FR-171](../../PRODUCT_REQUIREMENTS.md)): decrypted private content never touches disk. If an OS API needs a path, the feature is *disabled* for private layers rather than staged through `%TEMP%`. |
| Plaintext filenames or folders | See §3.1, §3.2. |

---

## 4. Trash

```
layers/<layer>/.trash/
├── manifest.json          # PUBLIC layers only: original path, deleted_at, retention
└── objects/               # PRIVATE layers: tombstoned objects, STILL ENCRYPTED
```

Deleting moves an object to the layer's trash with a retention period; it does not unlink immediately
([FR-014](../../PRODUCT_REQUIREMENTS.md)).

- **Public layer:** the file moves into `.trash/` with its original path recorded so it can be restored.
- **Private layer:** the object is rewritten with the tombstone flag (`flags` bit 2) and moved into
  `.trash/objects/`. **It stays encrypted.** Deleting something is not an excuse to decrypt it.
- **Purge** removes the file. Strata does *not* claim to securely erase it: on an SSD with wear
  levelling and copy-on-write, overwriting a file's blocks is theatre. The honest mitigation is that
  what remains on the platter is **ciphertext**, which is the reason the encryption exists.

---

## 5. Snapshots

```
snapshots/
├── index.json                          # snapshot list: id, layers, created_at, content hashes
└── 2026-07-14T10-30-00Z_a91c3f/
    ├── manifest.json                   # what this snapshot covers + anti-rollback counter
    ├── notes/                          # public layer: content-addressed copies
    └── journal/
        └── objects/                    # private layer: STILL-ENCRYPTED objects (+ layer.header)
```

- Snapshots are **content-addressed** and deduplicated: an unchanged object is referenced, not copied.
- **A private-layer snapshot requires no decryption.** We copy ciphertext. Taking a snapshot of a locked
  layer is therefore possible and safe ([FR-131](../../PRODUCT_REQUIREMENTS.md)).
- Restore is **non-destructive**: it snapshots the current state first ([FR-132](../../PRODUCT_REQUIREMENTS.md)).
- Each snapshot records the `anti_rollback_counter`. Restoring to a state older than the layer's current
  counter demands an explicit, warned override ([T-20](../../THREAT_MODEL.md)) — this is what stops a
  stale backup from silently reinstating a revoked collaborator's access.
- A snapshot is taken automatically before any format migration
  ([encryption-format.md §8](../security/encryption-format.md)).

---

## 6. Atomic writes

Every write, in every layer, public or private:

```
1. write to  <target>.tmp.<random>
2. fsync the file
3. os.replace(tmp, target)      # atomic rename on the same filesystem
4. fsync the parent directory   # so the rename itself is durable
```

A crash therefore leaves an object either **fully old** or **fully new** — never a half-written file
that fails authentication and looks like tampering ([NFR-026](../../PRODUCT_REQUIREMENTS.md),
[T-21](../../THREAT_MODEL.md)). `layer.header` additionally keeps `layer.header.bak`, because losing it
loses the layer.

---

## 7. `scripts/scan_plaintext.py` — the test that must never be deleted

This is the script that keeps everything above honest. Every claim in this document is a claim about
bytes on a disk, and a claim about bytes can be *tested*.

**What it does.** Given a workspace path and a set of canary strings, it walks **every byte of every
file** under each private layer — objects, header, trash, snapshots, and anything else that happens to
be there — and asserts that none of the canaries appear, in any of:

- UTF-8, UTF-16-LE, and UTF-16-BE
- Base64 and hex encodings
- zlib/zstd-compressed forms (in case something compressed a buffer before writing it)

It also asserts the structural invariants:

| Check | Fails the build if… |
| --- | --- |
| **File allowlist** | Any file exists in a private layer that is not `layer.header`, `layer.header.bak`, `layer.json`, `rotation.state`, or `objects/<xx>/<32-hex>`. **An unexpected file is a leak until proven otherwise.** |
| **Filename shape** | Any object filename is not exactly 32 lowercase hex characters, or does not sit in the shard matching its first 2 characters. |
| **Header sanity** | Any object does not begin with `STRATA1`, or has an unknown `format_version` / `alg` / `object_type` / `flags` bit. |
| **Entropy** | Any object's ciphertext region has anomalously low entropy (a cheap smoke test for "somebody wrote plaintext into an object file"). |
| **No plaintext markers** | Any file contains `# `, `---\ntitle:`, `[[`, or other Markdown/YAML markers at a position where the format says ciphertext must be. |
| **No index files** | Any `.sqlite`, `.db`, `.idx`, `.cache`, or `.tmp` file survives in a private layer. |

**How it is used.**

- In `tests/security/`, as an assertion on a fixture workspace: create a private layer, write notes
  containing distinctive canary strings, lock, and scan.
- In **CI on every pull request**, so that a well-meaning refactor that adds a "small cache for
  performance" fails immediately, loudly, and with the exact file named.
- Run by hand against a real workspace before any release.

It is listed in [CONTRIBUTING.md](../../CONTRIBUTING.md) under the tests that may **never** be skipped,
muted, or weakened to make a build green. If this script fails, the product's central promise is false,
and there is no feature worth shipping over it.

---

## 8. Reading a Strata workspace without Strata

Because that is the whole point:

| Layer | How |
| --- | --- |
| **Public** | Open the `.md` files. That's it. Delete `index.sqlite` if it bothers you. |
| **Private** | Read `layer.header` (JSON). Derive the KEK from the password with Argon2id using the parameters in the file. Unwrap the LDK. For each object file: take the first 71 bytes as the AAD, read the nonce at offset 43, and XChaCha20-Poly1305-decrypt the remainder with the LDK. Strip padding using `plaintext_len` at offset 67. Start with the manifest object to get the names back. |

Every constant you need is in [encryption-format.md](../security/encryption-format.md), and known-answer
test vectors ship in `tests/fixtures/` (M3). If you ever find that you *cannot* do this, that is a bug
in Strata, and a serious one.
