# Current architecture

> Audit date: 2026-07-23, version 1.3.1. This document records what exists **today**, as built,
> including known gaps. The forward-looking design lives in [target-architecture.md](target-architecture.md).

Strata is a **local-first desktop application**, not a web service. Several categories a
conventional audit expects (backend framework, database server, authentication) resolve to
desktop-native equivalents. The table below maps the standard checklist onto reality.

## 1. Stack summary

| Concern | What Strata actually uses |
| --- | --- |
| Frontend framework | React 18.3 + TypeScript 5.7 (strict) + Vite 6, embedded in Qt WebEngine. Zustand 5 for view state. No router, no CSS framework (hand-rolled token system). |
| Backend framework | Python 3.10+ / PySide6 (Qt 6). One desktop process; Python owns all truth, keys, disk, and network. The renderer only draws. |
| API layer | QWebChannel bridge: 13 feature-scoped `QObject`s, JSON envelope `{v, requestId, payload}` → `{ok, data|error}`, closed 10-value error enum, 1 MiB request cap, Pydantic validation with `extra="forbid"`. Push events via Qt Signals only (`notes.changed`, `ai.aiEvent`, `operations.planEvent`, `collaboration.collabEvent`, `jobs.jobEvent`). |
| Database | None (deliberate). A workspace is a directory. Public layers: plain Markdown + YAML frontmatter. Private layers: per-object AEAD-encrypted blobs with an encrypted manifest (no plaintext names/paths). `workspace.json` for structure; SQLite is used **only** as an FTS index for public layers. |
| Authentication | No accounts. Per-layer passwords (Argon2id → KEK → wrapped layer key), OS keychain for AI provider credentials (`CredentialStore`, fails closed). Workspace permission = filesystem access. |
| File storage | `layers/<layer_id>/` per layer; `.strata/{trash,snapshots,exports,index,logs}` for app state. Atomic writes (`replace_atomic`) for workspace.json, settings, and all encrypted objects. **Gap: Markdown note bodies are written non-atomically.** |
| Markdown editor | CodeMirror 6 (source/live/reading modes), wiki links incl. typed relationships (`supports:: [[X]]`), slash commands, autocompletion, 800 ms debounced autosave with last-write-wins queue, optional Yjs collab binding. Preview: marked → DOMPurify allowlist, sandboxed KaTeX + Mermaid (`securityLevel: strict`). |
| Search | Hybrid 5-signal ranker (`SearchService`): BM25 lexical (SQLite FTS5 for public layers, in-memory BM25 for private — never persisted), semantic cosine over an in-memory `VectorStore`, graph proximity, tag/property match, recency. Per-result "why it matched" signals surface in the UI. **Gap: the embedder is `HashingEmbedder`, a deterministic hashed bag-of-words stand-in, not a language model.** |
| AI integrations | 4 real providers behind one `AIProvider` ABC: Anthropic API (SSE streaming), OpenAI-compatible (5 catalogued configs incl. Ollama/llama.cpp/LM Studio; the only one implementing embeddings), Claude CLI (sandboxed subprocess, env-allowlisted, treated as remote). Single choke point `AIService.run()` with a per-layer policy gate (`evaluate_policy`), prompt-injection framing, and a `PrivacyReceipt` written in `finally` for every request. |
| AI mutations | Transactional operation plans (`OperationService`): 14 operation types, review → per-item approval (destructive ops never pre-approved) → pre-AI snapshot → all-or-nothing apply with rollback → single-unit undo. Plan generation (`AIGenerationService`) parses free-text JSON defensively; invalid ops dropped individually. |
| Background jobs | `JobService` (Qt `QThreadPool`): progress + cooperative cancel, privacy-tagged records, `jobEvent` signal, `JobBridge`. **Gap: `submit()` has zero production call sites — the whole subsystem is dead infrastructure.** No retry, no persistence, no queueing. |
| Deployment | PyInstaller desktop bundles (Windows Inno Setup installer, Linux AppImage/deb), tag-triggered release pipeline with code signing when secrets present. No server component except an optional untrusted collaboration relay (ciphertext-only forwarder). |
| Testing | ~404 Python tests (unit / integration / security / e2e / performance) + 25 frontend Vitest files. Philosophy: real workspaces in `tmp_path`, no mocking of Strata's own behaviour; adversarial encryption tests; e2e loads the real bundle in Qt WebEngine offscreen. CI: ruff, mypy `--strict`, pytest matrix (3.10–3.12 × OS), frontend gates, plaintext scanner, dependency audit + SBOM. |

## 2. Domain model (as built)

Pydantic v2, `extra="forbid"` everywhere. Key models:

- **Workspace**: `WorkspaceDescriptor` (layers, lenses, saved views), `KnowledgeLens` (saved perspective incl. `ai_readable_layer_ids`).
- **Layers**: `LayerDescriptor` — public (`markdown`) or private (`encrypted-objects`), with `LayerAIPolicy` (access level + 7 capability booleans + embeddings policy). Locked layers are absolute walls: AI never reads them, graph nodes redact, search indexes are destroyed on lock.
- **Notes**: `Note` = `NoteMetadata` (id, layer, folder, title, aliases, tags, properties, typed links) + content. 12 typed relationship kinds (`supports`, `contradicts`, `derived_from`, …). 10 builtin schemas already include `meeting`, `project`, `person`, `decision`, `research-source`, `task`, `daily-note` — a head start for the knowledge model.
- **Operations**: `Operation`/`OperationPlan`/`PlanReview`/`AppliedPlan` (carries `snapshot_id` as the undo handle, plus provider/model/prompt for audit).
- **AI**: `AIRequest`/`AIEvent`, `ProviderCapabilities` (semantic `is_local`), `PolicyDecision`, `PrivacyReceipt` (metadata-only audit record of what left the device).
- **Views**: `ViewConfig`/`ViewResult` — table/cards/kanban/calendar/timeline as queries over frontmatter.
- **Graph**: `GraphSnapshot` with 12 node types, 10 edge types (incl. `semantic_similarity`, and `version_lineage` — defined but never produced).
- **Jobs**: `JobRecord` (progress, cooperative cancel, privacy tag).

## 3. What can be reused for the AI-memory platform

| Existing asset | Reuse |
| --- | --- |
| Operation-plan pipeline (review → approve → apply → undo) | The human-approval flow Features 2–5 require already exists and is transactional. |
| `PrivacyReceipt` + policy gate | The provenance/consent substrate for AI execution records. |
| Builtin schemas (`decision`, `meeting`, `person`, `project`, `research-source`) | The knowledge model's entity pages, already validated in the properties panel. |
| Typed relationships + backlinks + link health | Connection discovery output surface. |
| Hybrid search with explainable signals | Base for retrieval-augmented context selection. |
| Snapshots (content-addressed, pre-AI) | Version-history safety net; undo handle. |
| `JobService` + `JobBridge` + `JobRecord` types (frontend types already mirrored) | Background-job requirements — needs call sites, not new design. |
| Context export (plan/render split, `STRATA-SOURCE-NNN` ids, delimiter neutralisation) | Citation model for source-backed answers. |
| Views engine | Decision log, inbox, and knowledge-health dashboards can be view configs over frontmatter rather than new query engines. |
| Trash, atomic-write helpers, path safety, closed error enum | Foundation hygiene. |

## 4. Incomplete features, security concerns, technical debt

Found by audit (no TODO markers exist in code; gaps are milestone-deferred by docstring):

**Security concerns**
1. `ai_bridge.send_request` builds `<source>` blocks from raw `source.content` instead of
   `ContextExportService.render_source_block`, bypassing `_neutralise_delimiters`/`_escape` on the
   **live request path** (the export path is protected). Prompt-injection hardening gap.
2. Plan-generation instructions ride inside the untrusted `<sources>` block (`AIGenerationService`).
3. `AIService.policy_for` reaches into `WorkspaceService._holds_key` (private member).
4. `SECURITY.md` vulnerability contact is a placeholder (tracked release blocker).

**Amnesia (the core gap for this project)**
5. Privacy receipts (`AIService._receipts`), applied-plan audit log (`OperationService._applied`),
   and job records are all in-memory — every AI trace vanishes on restart. Undo does not survive
   restart even though its snapshot does.
6. No per-note version history (only workspace snapshots + trash).
7. No AI execution records, no conversation persistence (single-turn only), no saved prompts,
   no capture inbox, no provenance fields on notes.
8. Composer responses dead-end: no save-as-note, no promote-to-plan, no citation resolution.

**Technical debt**
9. `JobService.submit()` never called; long-running work runs on ad-hoc threads.
10. Structured output is decorative: `AIRequest.json_schema` ignored by `AnthropicProvider`,
    generic `json_object` only on OpenAI-compatible; plan parsing is regex-based.
11. No retry/backoff (`retryable` set on 429, never consumed); no mid-request fallback.
12. Token accounting rough (chunk-counting on OpenAI-compatible; chars/3.6 elsewhere); no cost tracking.
13. Markdown note writes are not atomic (unlike everything else).
14. Key rotation not crash-atomic (rotation journal deferred to M11).
15. Schema `formula` type stored, never evaluated.
16. Planning docs drift: ROADMAP status table and the PRD milestone map disagree with TASKS.md
    (which is current truth); M9 collaboration is further along than the docs admit.

## 5. Constraints every new feature inherits

From SECURITY.md (non-negotiable rules), THREAT_MODEL.md, ASSUMPTIONS.md:

- **AI never reads a locked layer** — not even a count or a filename.
- **No decrypted private content ever touches disk** — temp files, thumbnails, indexes, logs, and
  any new persisted artifact included. A feature that needs plaintext-on-disk is disabled for
  private layers or stores inside the encrypted layer.
- **Every remote AI call and decrypted export produces a privacy receipt.** No exceptions.
- **Every AI mutation is a transactional operation plan** (diff → approval → atomic apply → undo).
- Imported/shared content is untrusted **data**, never instructions.
- Logs use an allowlist schema; no content, paths, or stack traces cross the bridge (closed error enum).
- Any new bridge object/method, outbound call, or CSP/sanitizer change triggers threat-model review
  (THREAT_MODEL §6) — new features in this project must log those reviews in
  [security-and-privacy.md](security-and-privacy.md).
- No telemetry; offline-first; local AI preferred, never silently escalated to remote.
