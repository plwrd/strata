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

- [ ] The editor is read-only. CodeMirror 6, live preview and autosave are M2.
- [ ] `LayerBridge.create_layer(visibility="private")` refuses with `unsupported`. M3.
- [ ] `AIComposerBridge.send_request` refuses with `unsupported`. M7.
- [ ] `SnapshotBridge.create_snapshot` refuses with `unsupported`. M8.
- [ ] `CollaborationBridge.invite` refuses with `unsupported`. M9.
- [ ] Knowledge Lenses are persisted and listed but not yet switchable in the UI. M10.
- [ ] Three.js chunk is 981 kB (271 kB gzipped). Acceptable for a bundled desktop
      app that never downloads it, but it should be lazy-loaded when Focus mode is
      the entry point. M11.

---

## Milestone 2 — Public Markdown workspace (next)

- [ ] CodeMirror 6 editor: source, live preview, reading mode
- [ ] Wiki-link, heading, tag and property autocomplete
- [ ] Slash commands and the command palette
- [ ] Autosave and crash recovery
- [ ] Tabs, split panes, focus mode
- [ ] File tree: create, rename, move, copy, delete, trash, restore, drag and drop
- [ ] Automatic link updates on rename
- [ ] Backlinks, unlinked mentions, broken links, orphan notes
- [ ] Attachment drag-and-drop
- [ ] File watching and external-modification handling
- [ ] Property editor and reusable schemas

## Milestone 3 — Private encrypted layers

- [ ] Layer key creation (random 256-bit LDK)
- [ ] Argon2id KEK derivation with versioned parameters
- [ ] Key wrapping; password change = rewrap; separate full key rotation
- [ ] Encrypted object container (XChaCha20-Poly1305, AAD-bound, padded)
- [ ] Opaque object storage (`objects/<xx>/<32-hex>`)
- [ ] Encrypted manifest (names, tree, tags, properties, links, relationships)
- [ ] Lock/unlock; key zeroisation; index/preview/graph-label teardown on lock
- [ ] Recovery key (shown once, wraps the same LDK independently)
- [ ] Private attachments
- [ ] Corruption and wrong-password tests (generic error, no data loss, no existence oracle)
- [ ] `scripts/scan_plaintext.py` run against a real private layer in CI

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

## Milestone 7 — AI providers

- [ ] `AIProvider` protocol + capability profiles
- [ ] Ollama, llama.cpp, LM Studio, OpenAI, Anthropic, generic OpenAI-compatible
- [ ] Claude CLI process adapter (restricted cwd, sanitised env, no shell interpolation)
- [ ] Credentials in the OS keychain
- [ ] Streaming, cancellation, token estimation from the provider's own tokeniser
- [ ] Privacy-aware routing that never silently moves local → remote

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
