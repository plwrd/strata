# Strata — Product Requirements

Status: living document. Version 0.1.0 (pre-alpha).
Terminology is defined in [docs/product/glossary.md](docs/product/glossary.md); this document assumes it.

Every functional requirement has an ID (`FR-###`), an area, and a **milestone tag**. A milestone tag
is a commitment about *when*, not a claim that it exists. If an FR is tagged M5 and today's milestone
is M1, the feature **does not exist**.

## Milestone map

| Tag | Milestone | State |
| --- | --- | --- |
| **M0** | Foundations: repo, tooling, CI, packaging skeleton | Complete |
| **M1** | Shell + bridge: Qt shell, `strata://` scheme, QWebChannel, envelopes, error enum, jobs | Complete |
| **M2** | Editor & notes: CodeMirror 6, Markdown, public-layer objects | Next |
| **M3** | Encryption: private layers, Argon2id, XChaCha20-Poly1305, lock/unlock lifecycle | Planned |
| **M4** | Properties, schemas, templates | Planned |
| **M5** | Search: FTS + semantic; ephemeral private index | Planned |
| **M6** | Graph: 3D/2D explorer, Knowledge Lens | Planned |
| **M7** | AI Context Composer + providers + privacy receipts | Planned |
| **M8** | Transactional AI operation plans: diff, approve, apply, undo | Planned |
| **M9** | Collaboration: sharing modes, relay, CRDT (ADR due in M9) | Planned |
| **M10** | Snapshots, import/export, backup, database views | Planned |
| **M11** | Packaging, signing, auto-update, accessibility, performance hardening | Planned |

---

## 1. Workspace & layers

| ID | Requirement | M |
| --- | --- | --- |
| FR-001 | A workspace is a directory containing `workspace.json` and a `layers/` tree. The user can create, open, and close a workspace. | M1 |
| FR-002 | A workspace may contain any number of layers. Each layer is `public` or `private`. | M1 |
| FR-003 | A public layer stores knowledge objects as plain files (Markdown + attachments) plus a rebuildable `index.sqlite`. Files remain readable and editable by external tools. | M2 |
| FR-004 | A private layer stores every knowledge object as an independently encrypted object file. No plaintext names, titles, folder structure, tags, or links exist anywhere on disk. | M3 |
| FR-005 | Layers have four observable states: **mounted**, **unmounted**, **locked**, **unlocked**. State transitions are explicit user actions and are surfaced in the UI. | M3 |
| FR-006 | Mounting a layer registers it with the workspace; unmounting removes it from the session without deleting data. | M3 |
| FR-007 | Unlocking a private layer requires the layer password or the recovery key. Locking discards keys and derived state (see FR-011). | M3 |
| FR-008 | A private layer supports an optional **recovery key** (random 256-bit, Base32 grouped, displayed exactly once, never stored in plaintext by Strata). | M3 |
| FR-009 | Password change rewraps the layer data key only; it does not re-encrypt objects. Key rotation generates a new layer data key and re-encrypts every object as a resumable background job. | M3 |
| FR-010 | Sharing modes per layer: **personal**, **shared-password**, **identity-managed**. Personal is the default. | M9 |
| FR-011 | On lock, Strata zeroizes key material where the runtime permits, closes and discards private search/vector indexes, clears decrypted editor buffers, previews, thumbnails, graph labels, and AI context, and cancels in-flight AI operations touching that layer. | M3 |
| FR-012 | Auto-lock: configurable idle timeout, lock on screen lock, lock on suspend. Default: lock after 15 minutes idle. | M3 |
| FR-013 | The workspace can be relocated by moving the directory; no absolute paths are persisted inside workspace or layer files. | M2 |
| FR-014 | Trash: deleting an object moves it to a per-layer trash with a retention period rather than unlinking immediately. Private-layer trash remains encrypted. | M10 |

## 2. Editor & Markdown

| ID | Requirement | M |
| --- | --- | --- |
| FR-020 | Markdown editor built on CodeMirror 6 with syntax highlighting, soft wrap, and a live preview toggle. | M2 |
| FR-021 | CommonMark + GFM tables, task lists, footnotes, and fenced code blocks. | M2 |
| FR-022 | YAML front matter is parsed into object properties and round-trips losslessly for public layers. | M4 |
| FR-023 | Wiki links (`[[Target]]`, `[[Target|alias]]`) resolve within and across mounted, unlocked layers. Links into a locked layer render as an inert placeholder with no title leakage. | M2 |
| FR-024 | Backlinks panel listing inbound links and typed relations for the current object. | M2 |
| FR-025 | Embedded content: images and attachments render inline from the owning layer; private-layer attachments are decrypted to memory, never to a temp file (see FR-171). | M3 |
| FR-026 | Slash-command palette for block insertion (headings, callouts, tables, templates). | M4 |
| FR-027 | Autosave with debounce plus explicit save; no unsaved-state data loss on crash for public layers (journaled writes). | M2 |
| FR-028 | Imported Markdown is treated as **untrusted data**: no script execution, no remote resource loading, no automatic link following. Rendering happens under the app CSP. | M2 |
| FR-029 | Editor supports imperceptible typing latency at the NFR targets (see NFR-010) including on 1 MB documents. | M2 |

## 3. Properties & schemas

| ID | Requirement | M |
| --- | --- | --- |
| FR-040 | Objects carry typed properties: text, number, boolean, date, datetime, select, multi-select, relation, URL. | M4 |
| FR-041 | Schemas define a named property set with types, defaults, and required flags; objects may be assigned a schema. | M4 |
| FR-042 | Schema changes are non-destructive: adding/removing a property never deletes existing property values, which are retained as orphaned values until explicitly discarded. | M4 |
| FR-043 | Templates: an object (with properties, body, and relations) can be saved as a template and instantiated with variable substitution. | M4 |
| FR-044 | Property values in a private layer live inside the encrypted manifest, never in a sidecar file. | M3 |
| FR-045 | Validation errors on property assignment are reported through the bridge error envelope with `invalid_request` and per-field detail. | M4 |

## 4. Database views

| ID | Requirement | M |
| --- | --- | --- |
| FR-050 | Table view over a filtered object set with sortable, resizable, reorderable columns bound to properties. | M10 |
| FR-051 | Board view (grouped by a select property), calendar view (bound to a date property), and gallery view. | M10 |
| FR-052 | Filters compose predicates over properties, tags, layers, and relations, with AND/OR grouping. | M10 |
| FR-053 | **Saved views** are first-class knowledge objects; they persist filter, sort, grouping, and visible columns. | M10 |
| FR-054 | A view spanning multiple layers omits locked layers entirely and displays an explicit "N locked layers excluded" affordance rather than silently returning partial results. | M10 |
| FR-055 | Inline editing of property values from table and board views. | M10 |

## 5. Search

| ID | Requirement | M |
| --- | --- | --- |
| FR-060 | Full-text search across mounted, unlocked layers with prefix, phrase, and boolean operators. | M5 |
| FR-061 | Semantic (vector) search over object embeddings, with a hybrid rank combining FTS and vector scores. | M5 |
| FR-062 | Public layers persist an FTS index in `index.sqlite`. The index is derivable and may be deleted and rebuilt at any time. | M5 |
| FR-063 | Private layers use an **ephemeral-first** index: built in memory on unlock, discarded on lock. An encrypted persistent index is available behind a feature flag (see A-004 and the search ADR). | M5 |
| FR-064 | Search results never include content from locked layers, and never reveal the existence of a matching object in a locked layer. | M5 |
| FR-065 | Embeddings for private-layer objects are computed by a local model by default; sending private content to a remote embedding provider requires explicit opt-in per layer and produces a privacy receipt. | M7 |
| FR-066 | Search is cancellable; long searches report progress through `JobBridge`. | M5 |
| FR-067 | Index rebuild is incremental and resumable; a corrupt index is detected and rebuilt rather than surfaced as an error. | M5 |

## 6. Graph

| ID | Requirement | M |
| --- | --- | --- |
| FR-070 | Knowledge graph built with networkx from objects (nodes) and links, tags, folders, and typed relations (edges). | M6 |
| FR-071 | 3D graph explorer (Three.js / react-three-fiber) with force-directed layout, pan/orbit/zoom, node focus, and neighbourhood expansion. | M6 |
| FR-072 | **2D fallback** renderer, selectable manually and chosen automatically when GPU capability is insufficient or the OS signals reduced motion (see NFR-021). | M6 |
| FR-073 | Layer-aware colouring and filtering; locked layers contribute no nodes, no edges, and no labels. | M6 |
| FR-074 | **Knowledge Lens**: a named, saved multi-layer perspective (layer set + filters + graph camera/layout + visual encoding). Lenses are knowledge objects and can be shared. | M6 |
| FR-075 | Graph selection (single, box, subgraph-by-traversal-depth) feeds directly into the AI Context Composer. | M6 |
| FR-076 | Graph analytics: degree, betweenness, community detection, orphan detection, presented as filters not as automatic edits. | M6 |
| FR-077 | Graph layout computation runs off the UI thread and streams progress via `JobBridge`. | M6 |

## 7. AI Context Composer & export

| ID | Requirement | M |
| --- | --- | --- |
| FR-080 | The **AI Context Composer** is a single surface combining: a context selection, a prompt, a provider choice, and an export/action target. | M7 |
| FR-081 | The composer shows the exact context that will leave the machine: object list, resolved token estimate, and a byte-accurate preview of the assembled payload. No hidden context is ever appended. | M7 |
| FR-082 | Locked layers can never contribute context. Objects from a locked layer are not listed, not counted, and not silently substituted. | M7 |
| FR-083 | **Privacy receipt**: every remote AI call and every decrypted export writes an append-only receipt recording timestamp, provider, model, layers touched, object ids, byte count, and whether the content was private-layer material. Receipts are viewable and exportable. | M7 |
| FR-084 | Export surfaces: copy to clipboard, write to file, send to provider. Exporting decrypted private-layer content requires an explicit confirmation naming the layer. | M7 |
| FR-085 | Instruction/content separation: user instructions and workspace content are placed in structurally distinct parts of the request, and content is delimited and labelled as untrusted data. | M7 |
| FR-086 | Context assembly is deterministic and reproducible: the same selection and settings produce byte-identical payloads. | M7 |
| FR-087 | Token budgeting with explicit truncation strategy; truncation is shown, never silent. | M7 |

## 8. AI providers

| ID | Requirement | M |
| --- | --- | --- |
| FR-090 | Provider abstraction supporting local providers (e.g. a local model server) and remote HTTP providers via `httpx`. | M7 |
| FR-091 | Default provider is local/none. No AI request leaves the machine until a remote provider is configured by the user. | M7 |
| FR-092 | Provider credentials are stored in the OS keychain via `keyring`, never in `workspace.json` or any layer file. | M7 |
| FR-093 | Per-layer AI policy: `never`, `local-only`, `ask-each-time`, `allow`. Default for private layers is `ask-each-time`; a layer may be marked `never` and then no provider can be used against it. | M7 |
| FR-094 | Provider failures map to the `provider_error` bridge error code with a retryable flag; provider responses are validated before use. | M7 |
| FR-095 | Model cost/latency estimates are displayed before a remote call when the provider supplies enough information; where it does not, Strata says "unknown" rather than guessing. | M7 |
| FR-096 | Requests to remote providers are cancellable, and cancellation is honoured end-to-end (`cancelled` error code). | M7 |
| FR-097 | No provider SDK is granted filesystem or subprocess access; providers see only the assembled payload. | M7 |

## 9. Transactional AI operations

| ID | Requirement | M |
| --- | --- | --- |
| FR-100 | AI does not mutate the workspace directly. It proposes an **operation plan**: an ordered list of typed operations (create/update/delete object, set property, add/remove link, move, tag). | M8 |
| FR-101 | Operation plans are produced as structured output and validated against a Pydantic schema. Anything that fails validation is rejected, not repaired by heuristics. | M8 |
| FR-102 | Every operation is constrained to targets present in the user's selection and to layers the user has unlocked; path-restricted, with no ability to reference arbitrary filesystem locations. | M8 |
| FR-103 | The user sees a **visual diff** per affected object before anything is applied. | M8 |
| FR-104 | Apply is **transactional**: all operations succeed or none are committed. Partial application is a bug, not a mode. | M8 |
| FR-105 | Every applied plan is **undoable** as a single unit, and the undo entry names the plan and the prompt that produced it. | M8 |
| FR-106 | Prompt-injection defence: content originating from imported/untrusted notes can never escalate into tool execution, provider switching, policy change, export, or plan operations outside the selection. | M8 |
| FR-107 | Plans that touch more than a configurable number of objects require a second, explicit confirmation. | M8 |
| FR-108 | Plan application emits progress through `JobBridge` and is cancellable before commit. | M8 |

## 10. Collaboration

| ID | Requirement | M |
| --- | --- | --- |
| FR-120 | Sharing modes: **personal** (no sharing), **shared-password** (collaborators hold the layer password), **identity-managed** (per-collaborator keys, revocable). | M9 |
| FR-121 | Collaboration transports encrypted objects only. A relay server, if used, never receives layer keys or plaintext. | M9 |
| FR-122 | Concurrent editing uses a CRDT. The specific CRDT (Yjs-style vs. alternatives) is **deferred to an ADR in M9** (see A-005) and is not assumed by any earlier milestone. | M9 |
| FR-123 | Conflicts that a CRDT cannot resolve semantically (e.g. schema-invalid property merges) surface as a `conflict` bridge error with both versions retained. | M9 |
| FR-124 | Collaborator revocation removes future access and triggers a key rotation. Strata states plainly that revocation cannot retract data a collaborator already held. | M9 |
| FR-125 | Presence and awareness are optional and off by default (they leak activity timing). | M9 |
| FR-126 | Every collaboration session is visible in the UI with the list of participating identities and the layers exposed. | M9 |

## 11. Snapshots

| ID | Requirement | M |
| --- | --- | --- |
| FR-130 | A **snapshot** captures the state of one or more layers at a point in time, addressed by content hash. | M10 |
| FR-131 | Snapshots of a private layer are stored encrypted with the same layer data key; taking a snapshot does not require decrypting objects. | M10 |
| FR-132 | Restore is non-destructive: restoring creates a new state and preserves the pre-restore state as a snapshot. | M10 |
| FR-133 | Snapshots are monotonic and reject rollback to a state older than the recorded anti-rollback counter without an explicit, warned-about override. | M10 |
| FR-134 | Snapshot operations run as background jobs with progress and cancellation. | M10 |

## 12. Import & export

| ID | Requirement | M |
| --- | --- | --- |
| FR-140 | Import from a directory of Markdown/Obsidian-style vaults, preserving front matter, wiki links, and attachments. | M10 |
| FR-141 | Imported content is untrusted (see FR-028, FR-106) and is quarantined from AI instruction channels. | M10 |
| FR-142 | Export a layer or selection to plain Markdown + attachments, preserving relative links. | M10 |
| FR-143 | Exporting decrypted private-layer content writes a privacy receipt (FR-083) and requires explicit confirmation. | M10 |
| FR-144 | Export to a single-file bundle (HTML or Markdown archive) with all local links resolved and no remote resources. | M10 |
| FR-145 | Import and export report per-item failures without aborting the whole job, and produce a machine-readable report. | M10 |

## 13. Backup

| ID | Requirement | M |
| --- | --- | --- |
| FR-150 | Because a workspace is a directory of files, any file-level backup tool works. Strata documents this rather than reimplementing it. | M10 |
| FR-151 | Strata provides a verified backup command that copies a workspace and verifies object authentication tags on the copy. | M10 |
| FR-152 | Backups of private layers remain encrypted at all times. Strata has no "backup my keys to the cloud" feature. | M10 |
| FR-153 | Recovery-key export writes a printable sheet containing the Base32 recovery key and the layer id, with an explicit warning that it grants full access. | M3 |

## 14. Packaging & updates

| ID | Requirement | M |
| --- | --- | --- |
| FR-160 | Ship as a signed desktop application built with PyInstaller (Windows first; macOS and Linux to follow). | M11 |
| FR-161 | The frontend is bundled to `frontend/dist` and served by a custom `strata://` URL scheme handler at `strata://app/index.html`. No HTTP server, no `file://`, no network dependency at startup. | M1 |
| FR-162 | Content-Security-Policy is enforced exactly as specified in [SECURITY.md](SECURITY.md). External navigation is blocked; external links open in the OS browser after an explicit confirmation dialog. | M1 |
| FR-163 | DevTools and the remote debugging port are enabled in development builds only and are compiled out of release builds. | M1 |
| FR-164 | **No auto-update in M1.** Update checking is opt-in and lands in M11 with a signed update channel (see A-009). | M11 |
| FR-165 | **No telemetry by default**, and no opt-out-only analytics ever. Any future telemetry must be opt-in, documented, and locally inspectable. | M0 |
| FR-166 | Releases publish an SBOM and checksums; artifacts are signed (see SECURITY.md). | M11 |
| FR-167 | Crash reports are local-only by default and are scrubbed of object content, filesystem paths, and key material before being written. | M11 |
| FR-170 | Production bridge responses contain no stack traces and no filesystem paths. | M1 |
| FR-171 | Strata never writes decrypted private-layer content to a temporary file. Decryption targets in-memory buffers only. Where an OS API requires a file path, the feature is disabled for private layers rather than staged through disk. | M3 |

## 15. Accessibility

| ID | Requirement | M |
| --- | --- | --- |
| FR-180 | Full keyboard operability: every action reachable by pointer is reachable by keyboard, with a visible focus ring. | M11 |
| FR-181 | Screen-reader support: semantic roles and labels across the React UI; the graph exposes an accessible list/tree alternative to the canvas. | M11 |
| FR-182 | Honour OS reduced-motion: disable graph animation and transitions; the 2D graph renderer becomes the default (FR-072). | M6 |
| FR-183 | Text contrast meets WCAG 2.1 AA; the app respects OS light/dark preference and a user-selected theme. | M11 |
| FR-184 | UI scaling from 100% to 200% without clipping or overlap. | M11 |
| FR-185 | No colour-only encoding of meaning in the graph or in database views. | M11 |

## 16. Performance

Functional requirements about performance; targets are in the NFR table below.

| ID | Requirement | M |
| --- | --- | --- |
| FR-190 | All long-running work (indexing, graph layout, encryption/rotation, import/export, AI calls) runs off the Qt UI thread as a cancellable job reporting progress via `JobBridge`. | M1 |
| FR-191 | Object lists, tables, and search results are virtualized. | M2 |
| FR-192 | Graph rendering degrades explicitly (label culling → edge culling → 2D fallback), and the UI states which degradation is active. | M6 |
| FR-193 | A performance test suite (`tests/performance/`) generates synthetic workspaces at 1k / 10k / 100k objects and asserts the NFR targets in CI. | M11 |

---

## Non-functional requirements

### Performance targets

Targets are measured on the reference machine (mid-range 2023 laptop, 16 GB RAM, integrated GPU),
release build, warm start, unless stated. "p95" is the 95th percentile over the measured runs.

| ID | Requirement | Target |
| --- | --- | --- |
| NFR-001 | Cold start to interactive shell | ≤ 2.5 s |
| NFR-002 | Workspace open (1k objects) | ≤ 300 ms |
| NFR-003 | Workspace open (10k objects) | ≤ 1.5 s |
| NFR-004 | Workspace open (100k objects) | ≤ 8 s, with the UI interactive and indexing continuing as a background job |
| NFR-005 | Private layer unlock (Argon2id at default params) | 0.5–2.0 s by design; the KDF cost is deliberate and is not a regression when it is slow |
| NFR-006 | Note open (any layer, ≤ 1 MB) | p95 ≤ 100 ms |
| NFR-007 | FTS query, 1k objects | p95 ≤ 30 ms |
| NFR-008 | FTS query, 10k objects | p95 ≤ 80 ms |
| NFR-009 | FTS query, 100k objects | p95 ≤ 250 ms |
| NFR-010 | **Typing latency: imperceptible.** Keystroke-to-glyph p95 ≤ 16 ms (one 60 Hz frame), p99 ≤ 33 ms, on documents up to 1 MB. Autosave, indexing, and link resolution must never block the input path. | see left |
| NFR-011 | Graph interactive frame rate, 1k nodes (3D) | ≥ 60 fps |
| NFR-012 | Graph interactive frame rate, 10k nodes (3D) | ≥ 30 fps with label culling |
| NFR-013 | Graph, 100k nodes | Must remain usable: automatic 2D fallback, ≥ 30 fps, progressive/level-of-detail loading. A 3D 100k-node target is **not** promised. |
| NFR-014 | Graph layout job, 10k nodes | ≤ 5 s, off-thread, cancellable, with progress |
| NFR-015 | Bridge round-trip (no I/O) | p95 ≤ 5 ms |
| NFR-016 | Memory, 10k-object workspace, idle | ≤ 700 MB RSS |
| NFR-017 | Index rebuild, 10k objects | ≤ 60 s as a background job |

### Reliability, security, and platform

| ID | Requirement |
| --- | --- |
| NFR-020 | **2D fallback** is a first-class renderer, not a stub. Every graph feature except 3D camera controls works in 2D. |
| NFR-021 | **Reduced-motion / low-GPU**: when the OS signals reduced motion, or GPU capability probing fails or reports a software rasterizer, Strata selects the 2D renderer, disables animated transitions, and says so in the UI. The user can always override in both directions. |
| NFR-022 | The app is fully functional offline. No feature except remote AI providers and collaboration requires network access, and the app never blocks on network at startup. |
| NFR-023 | Bridge payload cap of 1 MiB per request; oversize requests fail with `payload_too_large` and are never partially processed. |
| NFR-024 | All bridge requests are validated with Pydantic before any side effect. Error codes come from the closed enum in [SECURITY.md](SECURITY.md). |
| NFR-025 | No cryptography is implemented or performed in JavaScript. |
| NFR-026 | Data durability: object writes are atomic (write to temp + fsync + rename). A crash mid-write must never corrupt an existing object. |
| NFR-027 | Authentication failure on decryption is reported as corruption or tampering, never ignored, and never silently returns partial plaintext. |
| NFR-028 | Python 3.10+ (CI matrixes 3.10, 3.11, 3.12). No 3.11/3.12-only syntax. See [A-001](ASSUMPTIONS.md). |
| NFR-029 | TypeScript `strict` mode; Python `mypy --strict`. `Any` requires an inline justification comment. |
| NFR-030 | Every dependency is pinned to an exact version; releases publish an SBOM. |
| NFR-031 | The test suite must include a `tests/security/` group that fails the build if plaintext appears in a private layer (`scripts/scan_plaintext.py`). |
| NFR-032 | Localization-ready: no concatenated UI strings; all user-facing text externalized by M11. |

---

## Explicit non-goals

| | |
| --- | --- |
| A hosted service | Strata has no backend account system. A relay for collaboration (M9) is optional, dumb, and never sees plaintext. |
| Protecting against local malware | Out of scope. If your OS is compromised while a layer is unlocked, Strata cannot help you. |
| "Zero knowledge" marketing | We do not use the phrase. We publish a leakage list instead. |
| Hiding layer existence | A private layer's existence, object count, approximate sizes, and mtimes are visible on disk. This is documented, not fixed. |
| Real-time multi-user editing before M9 | Not attempted; no partial "sync" will ship early. |
