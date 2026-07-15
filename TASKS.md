# Strata — task board

Legend: `[ ]` pending · `[~]` in progress · `[x]` complete · `[!]` blocked

Milestone definitions live in [ROADMAP.md](ROADMAP.md). A task is only `[x]` when
it meets the Definition of Done in [CONTRIBUTING.md](CONTRIBUTING.md): it builds,
its tests pass, types/lint/format pass, it is documented, and it is reachable
from the UI.

---

## Milestone 0 — Architecture and security foundation ✅

- [x] Product requirements (`PRODUCT_REQUIREMENTS.md`)
- [x] System architecture + Mermaid diagrams (`docs/architecture/system-architecture.md`)
- [x] Storage layout (`docs/architecture/storage-layout.md`)
- [x] Threat model, 33 threats with status and residual risk (`THREAT_MODEL.md`)
- [x] Encryption format, byte-level container spec (`docs/security/encryption-format.md`)
- [x] Security policy and non-negotiable rules (`SECURITY.md`)
- [x] Security-first defaults recorded (`ASSUMPTIONS.md`)
- [x] ADR-0001 Python + PySide6 desktop host
- [x] ADR-0002 Qt WebEngine + React frontend
- [x] ADR-0003 Qt WebChannel bridge protocol
- [x] ADR-0004 Private object storage
- [x] ADR-0005 Encryption primitives
- [x] ADR-0006 CRDT selection (provisional; spike in M9)
- [x] ADR-0007 Search architecture
- [x] ADR-0008 AI provider abstraction
- [x] ADR-0009 Context export format
- [x] ADR-0010 3D graph architecture
- [x] ADR-0011 Python version target (3.10+, not 3.12)
- [x] Export format specification (`docs/export-format/README.md`)
- [x] Repository scaffold, CI, test infrastructure
- [x] Design-token specification (`frontend/src/design-system/tokens.css`)
- [x] Glossary (`docs/product/glossary.md`)

## Milestone 1 — Python desktop shell ✅

- [x] PySide6 application, main window, no private data in the window title
- [x] `strata://` custom URL scheme (secure, local, CORS-enabled; no socket, no `file://`)
- [x] Bundled React frontend loaded offline from `frontend/dist`
- [x] Restricted Qt WebChannel bridge: 11 feature-scoped objects, no god object
- [x] Versioned request/response envelope, Pydantic-validated both ways
- [x] Closed error-code enum; production errors carry no stack trace or path
- [x] 1 MiB payload cap enforced before parsing
- [x] Strict CSP; external navigation blocked; external links confirmed then handed to the OS
- [x] Developer tools only in development builds
- [x] React-to-Python health check (`WorkspaceBridge.health`)
- [x] Responsive three-column layout (1920 → 1280, drawers below 1280/960)
- [x] Cyberpunk design-token system (dark, dim, high-contrast)
- [x] Reduced-motion support (OS preference *and* in-app setting; reaches the WebGL scene)
- [x] Interactive Three.js graph: instanced nodes, batched edges, worker layout
- [x] 2D graph on the same data pipeline (not a lesser view)
- [x] WebGL capability detection + error boundary → automatic 2D fallback
- [x] Accessible graph equivalent: real tree, keyboard-navigable, same actions
- [x] Real multi-node selection: click, ctrl-click, shift-path, select-all, neighbours
- [x] Selection constellation ring: counts, layers, private count, token estimate
- [x] AI Context Composer wired to the live selection
- [x] Context plan computed in Python (preview cannot drift from payload)
- [x] Generic / ChatGPT / Claude Markdown context export
- [x] Multi-file context package (README, PROMPT, CONTEXT, GRAPH, MANIFEST, SOURCES)
- [x] Token budgeting and predictable splitting (never silent truncation)
- [x] Privacy review before any decrypted private content leaves
- [x] Export written through a native dialog (the frontend never supplies a path)
- [x] Public Markdown workspace on disk: notes, folders, frontmatter, wiki links, typed relationships
- [x] Lexical search with "why this matched" explanations
- [x] Background job manager (thread pool, cancellable, privacy-classified)
- [x] Structured logging with path/secret redaction
- [x] Python bridge tests (`tests/unit`, `tests/integration`)
- [x] Markdown export tests (`tests/unit/test_context_export.py`)
- [x] Privacy-boundary tests: locked layers leak nothing (`tests/security`)
- [x] Prompt-injection tests: a note cannot forge a source boundary
- [x] Desktop-shell e2e: real bundle over `strata://`, real WebChannel round trip
- [x] Plaintext scanner + self-test (`scripts/scan_plaintext.py`)
- [x] Windows packaging (PyInstaller spec, Inno Setup installer, smoke test)
- [x] Ubuntu packaging (PyInstaller, AppImage, `.deb`, desktop entry, smoke test)
- [x] Lint, format, type check, tests, CI (`.github/workflows/ci.yml`)

### Known gaps at the end of M1 (deliberate, not forgotten)

- [x] The editor is read-only. CodeMirror 6, live preview and autosave are M2. — **done in M2**
- [ ] `LayerBridge.create_layer(visibility="private")` refuses with `unsupported`. M3.
- [ ] `AIComposerBridge.send_request` refuses with `unsupported`. M7.
- [ ] `SnapshotBridge.create_snapshot` refuses with `unsupported`. M8.
- [ ] `CollaborationBridge.invite` refuses with `unsupported`. M9.
- [ ] Knowledge Lenses are persisted and listed but not yet switchable in the UI. M10.
- [ ] Three.js chunk is 981 kB (271 kB gzipped). Acceptable for a bundled desktop
      app that never downloads it, but it should be lazy-loaded when Focus mode is
      the entry point. M11.

---

## Milestone 2 — Public Markdown workspace ✅

- [x] CodeMirror 6 editor: source, live (split) and reading modes
- [x] Wiki-link, tag and property autocomplete
- [x] Slash commands (15 Markdown + typed-relationship snippets)
- [x] Autosave (debounced, off the keystroke path) and flush-on-unmount
- [x] Tabs, with a dirty marker and a per-note undo history
- [x] File tree: create, rename, move (drag and drop), duplicate, delete, restore
- [x] Trash — deleting is never destruction
- [x] Automatic wiki-link rewriting on rename (aliases and heading anchors too),
      and prose is never rewritten
- [x] Backlinks, unlinked mentions, broken links, orphan notes
- [x] Attachments (path-sanitised, stored inside the layer)
- [x] File watching (watchdog, coalesced) and external-modification handling
- [x] Safe Markdown rendering: DOMPurify allowlist, Mermaid `securityLevel: strict`,
      KaTeX, and wiki links that carry no `href`
- [x] Property editor with 18 property types and 10 reusable schemas
- [x] Schema validation that *reports* rather than rewrites the user's file
- [x] 75 new tests (30 note-operation, 16 notes-bridge, 29 frontend)

### Known gaps at the end of M2

- [ ] Crash recovery beyond autosave (an unsaved buffer is lost if the process is
      killed between debounce ticks). Needs a WAL; scheduled with snapshots in M8.
- [ ] Split panes (two notes side by side). The *modes* split; the panes do not. M10.
- [ ] Command palette (Ctrl+P). M10.
- [ ] Bulk operations and favourites in the tree. M10.

## Milestone 3 — Private encrypted layers ✅

- [x] Layer key creation (random 256-bit LDK — the password never *is* the key)
- [x] Argon2id KEK derivation, versioned parameters (t=3, m=256 MiB, p=4)
- [x] Key wrapping; password change = rewrap (instant); separate full key rotation
- [x] Encrypted object container: XChaCha20-Poly1305, 71-byte header as AAD,
      padding buckets. Binds layer + object id + type + format version, so objects
      cannot be transplanted, swapped, type-confused or downgraded
- [x] Opaque object storage (`objects/<xx>/<32-hex>`), random ids, no extensions
- [x] Encrypted manifest: titles, folder tree, tags, properties, links, attachments
- [x] Lock/unlock; key zeroisation; teardown hooks so caches cannot outlive a lock
- [x] Recovery key (shown once, Crockford alphabet, wraps the same LDK; revoked on
      rotation)
- [x] Private attachments (opaque object ids, never a readable filename)
- [x] Private trash: a deleted private note stays encrypted, never lands in the
      workspace's plaintext trash folder
- [x] Wrong-password behaviour: one generic error, no oracle, no corruption, no lockout
- [x] `scripts/scan_plaintext.py` run against a real private layer, in the tests
- [x] Frontend: create/unlock/lock dialogs, key management, recovery-key display,
      and a lock that purges tabs, drafts, selection and search results
- [x] 74 new tests (54 crypto/service, 20 end-to-end against real ciphertext)

### Known gaps at the end of M3

- [ ] Key rotation is not crash-atomic. If the process dies part-way, some objects
      are under the new key and some under the old; the header is only written after
      every object succeeds, so the old key still opens the un-rotated ones. Needs a
      rotation journal — M11.
- [ ] "Remember on this device" (OS keychain) is accepted by the bridge but not yet
      wired to `keyring`. M7, with the credential store.
- [ ] Private search is a live scan of the decrypted manifest, not an index. The
      ephemeral in-memory index lands in M4.
- [ ] Cross-layer link rewriting skips *locked* layers (we will not unlock a layer
      to fix a link). The link goes stale until that layer is next unlocked.

## Milestone 4 — Search and indexes

- [ ] Per-layer SQLite FTS5 index for public layers
- [ ] Ephemeral in-memory private index, rebuilt on unlock
- [ ] Embeddings abstraction; private embeddings never leave encrypted storage
- [ ] Hybrid ranking: lexical + semantic + graph proximity + property + recency
- [ ] Result explanations backed by the real ranker

## Milestone 5 — 2D and 3D graph

- [ ] Lasso and volume selection
- [ ] Cluster aggregation to 100k nodes
- [ ] Timeline playback, shortest path, presentation paths
- [ ] Semantic-similarity edges
- [ ] Saved cameras, screenshot export

## Milestone 6 — AI Context Composer and export

- [ ] Context depth rules editor
- [ ] Prompt templates and prompt history
- [ ] Encrypted export package
- [ ] Privacy receipts (encrypted, inspectable, exportable)

## Milestone 7 — AI providers ✅

- [x] `AIProvider` protocol + declared capability profiles (UI disables what a
      provider cannot do)
- [x] Ollama, llama.cpp, LM Studio, OpenAI, Anthropic, generic OpenAI-compatible
- [x] Claude CLI process adapter: explicit path, no-shell exec, prompt on stdin
      (never argv), allow-listed env, sandboxed cwd, timeout, real cancellation —
      and labelled **remote**, because it sends content to Anthropic
- [x] Credentials in the OS keychain; fails **closed** (never a plaintext file)
- [x] Streaming over a Qt Signal, cancellation that kills the request, token estimates
- [x] The policy gate (`evaluate_policy`) in the domain, not the UI: locked layers
      never reach AI, local-only blocks every remote provider, remote-with-confirmation
      needs a confirmation, and the strictest layer in a selection wins
- [x] Privacy-aware routing that prefers local and never silently escalates to remote
- [x] Privacy receipts for every remote request (the fact of the content, not the content)
- [x] Prompt-injection framing applied once, in the service: sources are wrapped as
      untrusted data with an explicit "do not obey" instruction
- [x] Frontend: provider selector with health + inline policy verdict, credential
      entry, live streaming response, stop button, remote-confirmation dialog
- [x] 61 new tests (providers, policy, Claude CLI sandboxing, credentials, bridge, UI)

### Known gaps at the end of M7

- [ ] "Remember on this device" for a layer *password* still not wired to the keychain
      (the AI credential path is). Small follow-up.
- [ ] Structured-output validation (for M8 operation plans) is declared as a capability
      but the JSON-schema enforcement lands with the transactional engine in M8.
- [ ] Token estimates use the character-ratio fallback; a provider's own tokeniser is
      used only where it streams usage back. Fine for budgeting, noted for M11 polish.

## Milestone 8 — Transactional AI operations

- [ ] Declarative operation plans + schema and security validation
- [ ] Visual diff, per-operation approval, transactional apply, undo
- [ ] Workspace generation from a description
- [ ] Snapshots and branches
- [ ] AI audit log and privacy receipts

## Milestone 9 — Collaboration

- [ ] CRDT spike to validate ADR-0006 (pycrdt / Yjs)
- [ ] Identities, devices, roles, invitations, revocation + key rotation
- [ ] Encrypted sync; relay never sees plaintext
- [ ] Offline merge, conflict surfaces, comments, presence, history

## Milestone 10 — Structured views

- [ ] Table, Kanban, calendar, timeline, gallery
- [ ] Schemas, templates, relations, rollups, formulas
- [ ] Knowledge Lens switching without a workspace reload

## Milestone 11 — Production hardening

- [ ] Performance profiling at 1k / 10k / 100k notes and nodes
- [ ] Accessibility audit
- [ ] Fuzzing of the encrypted container and the Markdown parser
- [ ] Backup/restore verification
- [ ] Signed releases and a secure update channel
- [ ] Lazy-load the Three.js chunk
- [ ] Licensing review (PySide6 LGPL relinking under PyInstaller) — **release blocker**
