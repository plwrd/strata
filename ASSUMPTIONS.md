# Strata — Recorded Assumptions

These are decisions taken **without asking the user**, because building required an answer and the
cost of waiting exceeded the cost of writing the decision down. Every one of them is reversible; each
entry states what would make us reverse it.

The bias throughout is **security-first**: when a choice traded convenience against exposure, we took
the less exposed option and recorded the inconvenience here.

| Field | Meaning |
| --- | --- |
| **ID** | Stable identifier. Cite these in code comments, ADRs, and PRs. |
| **Status** | `Active`, `Superseded by <ID>`, or `Revisit at <milestone>`. |
| **Revisit** | The concrete trigger that should reopen the decision. |

---

## A-001 — Python 3.10+ as the floor, not 3.12

**Status:** Active (revisit at M11)

**Decision.** `requires-python = ">=3.10"`. The development machine has only **Python 3.10.11**
installed. CI matrixes **3.10, 3.11, and 3.12**, and the 3.12 job is the one that gates release
builds where a 3.12 runtime is available.

**Consequences.**
- **No 3.11/3.12-only syntax.** No `except*`, no PEP 695 `type` statements or generic syntax
  (`class C[T]`), no `typing.override`, no `Self` without a `typing_extensions` fallback.
- `mypy` is configured with `python_version = "3.10"` so the type checker enforces the floor rather
  than trusting review.
- PySide6 6.8 supports 3.9–3.13, so this costs us nothing on the Qt side.

**Rationale.** Writing 3.12-only code on a machine that cannot run it produces code that is typechecked
but never executed. A floor we can actually run locally is worth more than a ceiling we can only lint.

**Revisit when.** The dev machine standardizes on 3.12+, or a dependency requires 3.11+. Then raise
the floor deliberately and drop the 3.10 CI job in the same commit.

---

## A-002 — XChaCha20-Poly1305 via PyNaCl (libsodium) for object encryption

**Status:** Active

**Decision.** Object and key-wrapping AEAD is **XChaCha20-Poly1305**, provided by **PyNaCl**
(libsodium bindings). `cryptography` remains a dependency for hashing, HKDF, and constant-time
comparison, but is not the object-encryption primitive.

**Rationale.**
- 24-byte nonces make **random nonce generation safe** without a counter, a nonce database, or
  per-object state we would have to keep consistent across crashes, snapshots, and collaboration.
  With AES-GCM's 96-bit nonce we would have to manage a counter, and a counter is a bug waiting for a
  restore-from-backup.
- libsodium is a well-reviewed, misuse-resistant implementation. Its `secretbox`-style API has few
  ways to hold it wrong.
- No dependence on AES hardware acceleration, so performance is predictable across the machines
  people actually own.

**Trade-off accepted.** XChaCha20-Poly1305 is not a NIST/FIPS algorithm. If a customer ever requires
FIPS-validated crypto, this is the decision to reopen — and it means an AES-GCM (or AES-GCM-SIV)
profile with an explicit nonce strategy, gated by the `alg` byte already present in the container
format.

**Revisit when.** A compliance requirement (FIPS/CC) appears, or libsodium's maintenance status
changes materially.

---

## A-003 — Argon2id at t=3, m=256 MiB, p=4, 16-byte salt

**Status:** Active (revisit at M3 with real measurements)

**Decision.** Password → key-encryption-key derivation uses **Argon2id** (the `id` variant) via
`argon2-cffi`, with **versioned parameters** stored in `layer.header`. Defaults:

| Parameter | Default |
| --- | --- |
| Variant | Argon2id |
| Time cost `t` | 3 |
| Memory cost `m` | 262144 KiB (256 MiB) |
| Parallelism `p` | 4 |
| Salt | 16 random bytes, per layer |
| Output | 32 bytes |

**Rationale.** Argon2id resists both GPU/ASIC parallelism (via memory hardness) and side-channel
attacks on the memory access pattern (via the hybrid `id` construction). 256 MiB is a serious cost for
an offline cracker while remaining tolerable on a laptop; it lands unlock in the 0.5–2.0 s band
(NFR-005). **A slow unlock is the feature, not a regression.**

**Parameters are stored in the header, not hardcoded**, so a future release can raise them and rewrap
existing layers without a format break. Increasing cost on password change is planned for M3.

**Trade-off accepted.** 256 MiB peak allocation at unlock. On memory-constrained machines this may
need a documented lower profile — that profile must be a *named, visible* choice, never a silent
downgrade.

**Revisit when.** M3 benchmarks on the reference machine land; and thereafter every 2 years, or when
OWASP/RFC 9106 guidance moves.

---

## A-004 — Ephemeral-first search index for private layers

**Status:** Active (encrypted persistent index behind a flag; see the search ADR, due M5)

**Decision.** A private layer's FTS and vector indexes are **built in memory on unlock and destroyed
on lock**. There is no plaintext index on disk, ever. An **encrypted persistent index** is available
behind a feature flag for large workspaces, and is off by default.

**Rationale.** A search index is a near-perfect reconstruction of the content it indexes: terms,
frequencies, positions, and for vector indexes, embeddings from which text can often be recovered.
Persisting one in plaintext would defeat the whole encryption design more effectively than any
cryptographic mistake we could make. Rebuilding on unlock costs seconds; leaking the corpus costs
everything.

**Trade-off accepted.** Unlock is slower on large private layers (index build), and memory use scales
with corpus size. That is why the encrypted persistent index exists as an escape hatch — but it is
opt-in, because an encrypted index still leaks update patterns and is a larger attack surface.

**Revisit when.** M5 measures index-build time at 10k and 100k objects. If unlock exceeds ~10 s at
10k objects, the encrypted persistent index gets promoted from flag to a recommended option (still not
a silent default).

---

## A-005 — CRDT choice deferred to an M9 ADR

**Status:** Deferred by design

**Decision.** Collaboration (M9) will use a CRDT, but **which** CRDT — Yjs-style (via a Python
implementation or a Rust binding such as y-crdt) versus an Automerge-style or a bespoke
last-writer-wins-per-property model — is **not decided** and will be settled in an ADR during M9.

**Rationale.** The choice depends on facts we do not have yet: the final object model (M2–M4), how
much of a note is rich text versus structured properties, and whether we can tolerate a native
dependency in a PyInstaller bundle. Committing now would be guessing, and the wrong guess is expensive
to unwind because it shapes the on-disk update format.

**What we are committing to now**, so earlier milestones do not paint us into a corner:
- Objects have stable, random 128-bit ids (not path-derived), so a rename is not a delete+create.
- The encrypted object container is versioned (`format_version`) and the `object_type` byte can
  introduce a CRDT-update object type without a format break.
- Collaboration transports **ciphertext only**; the CRDT operates on plaintext inside the process.

**Revisit when.** M9 begins. The ADR must cover: bundle size, native-dependency risk, memory per
document, and whether the CRDT's internal metadata leaks authorship/timing when encrypted.

---

## A-006 — Custom `strata://` URL scheme, not `file://` and not localhost HTTP

**Status:** Active

**Decision.** The bundled frontend is served by a **custom `QWebEngineUrlSchemeHandler`** registered
for the `strata` scheme, at `strata://app/index.html`. Not `file://`. Not `http://127.0.0.1:<port>`.

**Rationale.**

| Option | Why not |
| --- | --- |
| `file://` | Broken/inconsistent origin semantics; CSP, module imports, and `fetch` behave differently across Chromium versions; directory traversal risk if a path escapes the bundle root. |
| localhost HTTP | Opens a **listening TCP port on the machine**. Any other local process — including a browser tab on any website, via DNS rebinding or a permissive CORS mistake — can reach it. A knowledge workspace with a bridge to the filesystem must not have a network listener. It also breaks in restrictive network environments and adds a startup failure mode. |
| `strata://` | A real, opaque origin. Serves bytes straight from `frontend/dist` in-process, works offline with no ports, and the handler is a single choke point where we can enforce path normalization and MIME types. |

**Consequences.** The scheme is registered as secure, local, and CORS-enabled *for itself only*. The
handler resolves paths under `frontend/dist` and rejects anything that normalizes outside it. External
navigation is blocked (see SECURITY.md).

**Revisit when.** Qt WebEngine changes custom-scheme semantics in a way that breaks the CSP or the
handler contract.

---

## A-007 — Per-feature bridge objects, not one god object

**Status:** Active

**Decision.** Eleven feature-scoped `QObject`s are exposed over `QWebChannel`: `WorkspaceBridge`,
`LayerBridge`, `NotesBridge`, `GraphBridge`, `SearchBridge`, `AIComposerBridge`, `ExportBridge`,
`CollaborationBridge`, `SettingsBridge`, `SnapshotBridge`, `JobBridge`. Not one `StrataBridge` with a
`call(method, args)` dispatcher.

**Rationale.**
- A generic `call(name, args)` dispatcher is a **remote procedure gateway**: the reachable surface
  becomes whatever the dispatch table happens to contain, and one careless registration exposes
  something dangerous. Enumerated slots on scoped objects make the entire attack surface greppable.
- Each bridge validates its own Pydantic request models, so "which schema applies" is never ambiguous.
- Scoped objects let us **withhold** a bridge: an AI bridge can be denied access to layer keys
  structurally, rather than by a runtime check somebody can forget.

**Trade-off accepted.** More boilerplate, and cross-cutting operations must be composed on the
frontend or in a service layer beneath the bridges (not in the bridges themselves).

**Revisit when.** The bridge count exceeds ~15, at which point group them by domain rather than
collapsing them.

---

## A-008 — No telemetry, by default and by construction

**Status:** Active (permanent absent explicit user opt-in)

**Decision.** Strata ships with **no analytics, no crash-report upload, no update ping, no usage
counters**. Not "off by default with a toggle" — absent from the build.

**Rationale.** A tool whose entire premise is "your private notes stay on your disk" cannot also open
an outbound channel we control. The reputational and actual risk of a telemetry bug leaking a note
title, a filesystem path, or a workspace name is not worth any product insight we would gain.

**Consequences.** We will be flying blind on crashes and usage. Crash reports are written **locally**,
scrubbed of content and paths (FR-167), and the user may attach one to a bug report by hand.

**Revisit when.** Never, for opt-out telemetry. Opt-in, locally-inspectable diagnostics may be
proposed post-1.0, and would require: explicit consent, a preview of the exact payload, and a
documented retention policy.

---

## A-009 — No auto-update in M1

**Status:** Active (auto-update lands in M11)

**Decision.** M1 builds have **no update mechanism at all** — no check, no download, no prompt.

**Rationale.** An update channel is a **code-execution channel**. Shipping one before we have release
signing, a verified update manifest, key management for the signing key, and a rollback story would
hand an attacker a better exploit than any bug in the app. A pre-alpha does not need auto-update;
users can download a new build.

**When it lands (M11), it must have:** signed artifacts, signature verification before execution, a
signed and versioned update manifest, protection against downgrade attacks, and an update that is
*offered*, never silently applied.

**Revisit when.** M11 planning.

---

## A-010 — Offline-first is the default, and the network is opt-in per feature

**Status:** Active

**Decision.** Startup, workspace open, editing, search, and graph work with **no network access at
all**. Network is used only by (a) a configured remote AI provider, and (b) collaboration when a
session is active. The app never blocks on the network, and never makes a request the user did not
initiate.

**Rationale.** Local-first is a security property, not just a UX one: the number of bytes that leave
the machine should be a small, enumerable, user-visible set. That is what makes privacy receipts
(FR-083) meaningful — if background requests existed, a receipt would be a lie by omission.

**Revisit when.** Never for the default. Any new outbound call requires a receipt and an FR.

---

## A-011 — AI defaults to local/none, and private layers default to `ask-each-time`

**Status:** Active

**Decision.** No AI provider is configured out of the box. A private layer's AI policy defaults to
**`ask-each-time`**, not `allow`. A locked layer can never be read by AI under any policy.

**Rationale.** The failure mode we most want to prevent is a user discovering that their private
journal was embedded and sent to a remote API because a default was permissive. An extra confirmation
click is cheap; that discovery is unrecoverable.

**Revisit when.** Usage shows the confirmation is being click-throughed reflexively — in which case the
fix is a better-designed consent surface (per-session grants with a visible indicator), not a
permissive default.

---

## A-012 — Model cost and capability are provider-reported, never estimated

**Status:** Active

**Decision.** Strata displays token counts it computed itself, and cost/latency **only** where the
provider supplies the data. Where it does not, the UI says **"unknown"**.

**Rationale.** Model prices, context windows, and names change faster than our release cycle. A
hardcoded price table would be wrong within months, and a wrong cost estimate on a privacy-sensitive
action is worse than no estimate. Token *counts* we can compute honestly; money we cannot.

**Assumption embedded here:** users pay providers directly with their own API keys (stored in the OS
keychain, A-011/FR-092). Strata is not a reseller, holds no billing relationship, and proxies nothing.

**Revisit when.** We ever consider bundling model access — which would make us a data processor and
would invalidate large parts of the threat model.

---

## A-013 — Licensing: proprietary placeholder, decision deferred

**Status:** Open question — decide before first public release

**Decision (interim).** `pyproject.toml` declares `Proprietary`. This is a **placeholder to avoid
accidentally granting rights we have not thought about**, not a considered position.

**What must be settled before any public artifact ships:**
- The license for the source (proprietary vs. source-available vs. an OSI license).
- Whether the *format* specs (`docs/security/encryption-format.md`, `storage-layout.md`) are released
  under a permissive license regardless — **they should be.** A local-first tool whose storage format
  is legally encumbered is a lock-in trap, which contradicts the product's premise. Users must be able
  to write their own reader.
- Dependency license compatibility (PySide6 is LGPL/commercial-dual — dynamic linking and the ability
  to relink must be preserved, which PyInstaller's default packaging complicates and which must be
  reviewed before distribution).

**Revisit when.** Before M11 / first public build. This is a blocker for release, not for development.

---

## A-014 — Public layers are readable and writable by other tools; we do not fight it

**Status:** Active

**Decision.** A public layer is plain Markdown on disk. External edits (another editor, a Git pull, a
sync client) are expected. Strata watches the filesystem (`watchdog`) and reconciles rather than
treating external writes as corruption. `index.sqlite` is a **derived cache** and may be deleted at
any time.

**Rationale.** The promise of local-first is that the files are yours. A tool that corrupts or
overwrites external edits to defend its cache has broken the promise. The cost is reconciliation
complexity, which we accept.

**Explicit consequence:** a public layer offers **no confidentiality**. It is not "less encrypted" —
it is not encrypted. The UI must never imply otherwise.

**Revisit when.** Never for the guarantee. The reconciliation strategy may change.

---

## A-015 — Opaque random object ids; no deterministic filename encryption

**Status:** Active

**Decision.** Private-layer object files are stored at `objects/<first 2 hex>/<32-byte random opaque
id, hex>`. Filenames are **random**, not a deterministic encryption of the title or path.

**Rationale.** Deterministic filename encryption (SIV-style) leaks equality: an observer can tell that
two workspaces contain the same-named file, can detect renames, and can confirm a guessed filename by
recomputing the ciphertext. Random ids leak nothing but count. The real names, the folder tree, tags,
properties, and links live only inside the encrypted **manifest** object.

**Trade-off accepted.** The manifest is a hot object (every rename touches it) and a single point of
loss. It is therefore written atomically, snapshotted, and integrity-checked, and its structure is the
first thing to review for scalability at 100k objects.

**Revisit when.** M3 implementation shows manifest write amplification is unacceptable at scale — the
answer is then to shard the manifest, **not** to move names into filenames.

---

## A-016 — We name our leakage instead of claiming zero knowledge

**Status:** Active (permanent)

**Decision.** Strata will never use the phrases "zero knowledge", "military-grade encryption", or
"unbreakable". The threat model publishes what a private layer leaks to someone holding the disk:
object count, approximate object sizes (blunted by padding buckets), object mtimes, total layer size,
access patterns over time, and the fact that a private layer exists at all.

**Rationale.** These claims are unverifiable and, in our case, false. Documenting real leakage is both
honest and useful — a user who knows mtimes leak can decide whether that matters to them. A user told
"zero knowledge" cannot.

**Revisit when.** Never.

---

## Summary

| ID | Assumption | Status |
| --- | --- | --- |
| A-001 | Python 3.10 floor; CI matrixes 3.10–3.12 | Active |
| A-002 | XChaCha20-Poly1305 via PyNaCl | Active |
| A-003 | Argon2id t=3, m=256 MiB, p=4 | Active |
| A-004 | Ephemeral-first private index; encrypted persistent index behind a flag | Active |
| A-005 | CRDT choice deferred to M9 ADR | Deferred |
| A-006 | Custom `strata://` scheme | Active |
| A-007 | Per-feature bridge objects | Active |
| A-008 | No telemetry | Active |
| A-009 | No auto-update in M1 | Active |
| A-010 | Offline-first; network opt-in per feature | Active |
| A-011 | AI defaults to local/none; private layers `ask-each-time` | Active |
| A-012 | Model cost provider-reported, never estimated; users bring their own keys | Active |
| A-013 | Licensing placeholder | **Open — blocks release** |
| A-014 | Public layers are externally editable and unencrypted | Active |
| A-015 | Opaque random object ids | Active |
| A-016 | Publish leakage; no "zero knowledge" claims | Active |
