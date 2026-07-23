# Implementation plan — AI-native knowledge & memory platform

> Phases for evolving Strata 1.3.1 into the platform described in
> [target-architecture.md](target-architecture.md). Each phase is shippable alone, preserves all
> working functionality, and passes the full existing gate (`scripts/check.ps1`).
> Status legend: ✅ done · 🔨 in progress · ⏳ pending.

Existing milestones M4 (search), M5 (graph), M9 (collaboration), M11 (hardening) continue in
parallel; where a phase depends on one, it is noted. TASKS.md remains the tactical tracker;
this file is the strategic map for the AI-memory work specifically.

## Phase 1 — Foundation: persistent AI memory 🔨

The audit (see [current-architecture.md](current-architecture.md) §4) found the core gap:
every AI trace is amnesiac. Phase 1 makes AI activity durable and fixes the live-path
injection gap.

- ✅ Repository audit + these five documents.
- 🔨 `app/domain/history.py`: `AIExecutionRecord` + redaction rules
  ([ai-memory-design.md](ai-memory-design.md) §2–3).
- 🔨 `app/services/ai_history_service.py`: JSONL persistence under `.strata/ai/` (receipts,
  executions, applied plans), corrupt-line tolerance, caps, `clear_history`.
- 🔨 Wire `AIService.run` (records execution + receipt) and `OperationService`
  (persistent audit log; **undo survives restart**).
- 🔨 Security fix: `ai_bridge.send_request` renders sources through
  `ContextExportService.render_source_block` (delimiter neutralisation on the live path).
- 🔨 Bridge: `ai.list_history`, `ai.clear_history` (+ threat-model review entries).
- 🔨 Frontend: AI history list in the AI inspector tab (provider, model, remote badge, tokens,
  result, redaction badge); loading/empty/error states.
- 🔨 Tests: unit (redaction, round-trip, corrupt lines, caps), integration (receipts and undo
  across service reconstruction), security (no private plaintext in `.strata/ai/`), frontend.
- Not in scope: note version history (moved to Phase 2 alongside the write-path changes capture
  needs; snapshots remain the safety net until then).

## Phase 2 — Capture and processing ⏳

- Default knowledge areas: `Inbox/`, `Knowledge/`, `Reports/`, `Templates/` folders seeded on
  workspace create (existing workspaces: offered via a one-time operation plan, never forced).
- New schemas: `capture`, `concept`, `organization`, `report`, `template`, `weekly-review`
  (extend `BUILTIN_SCHEMAS`; `decision`, `meeting`, `person`, `project`, `task` exist).
- `capture_service.py` + `notes.capture` bridge: quick capture (text/paste), capture metadata
  (`source_url`, `capture_reason`, `processing_status: raw`), mobile-size capture dialog in UI;
  URL import **behind SSRF guards** (scheme allowlist, private-network block, size/time caps,
  content treated as untrusted) — the first new outbound call, so full threat-model review.
- Per-note **version history**: versioned write path in `NoteService` (public: `.strata/versions/`;
  private: encrypted `version` objects), `notes.list_versions/get_version/restore_version`,
  version browser UI, origin tags (`human` / `ai:<execution_id>`).
- `knowledge_service.py`: "Process into knowledge" — structured extraction (summary, concepts,
  entities, decisions, action items, open questions, suggested tags, related notes) returned as
  a schema-validated proposal, materialised as an **operation plan** for per-item approval.
  Bulk processing via `JobService.submit()` (first production job call sites).
- Make structured output real for extraction: tool-use on Anthropic, `json_object` on
  OpenAI-compatible, validated-and-rejected on failure; prompts move to `app/ai/prompts/`.

## Phase 3 — Retrieval and AI chat ⏳ (depends on M4 search)

- Real embedder behind the existing `Embedder` ABC (local via Ollama/llama.cpp embeddings path;
  per-layer embeddings policy already modeled); private vectors stay in-memory.
- `retrieval_service.py`: scope (selection/folder/lens/date-range) → permission filter → hybrid
  rank → chunking with heading + character-range anchors → token-budget fit; retrieval trace
  stored on the execution record.
- Composer → conversation: persisted `AIConversation` threads, multi-turn context replay,
  "which notes did the AI see" for every turn.
- Save-as-asset actions on every response (note/report/concept/decision/template/append) — each a
  one-op plan with provenance frontmatter; regeneration produces a diff, never an overwrite.
- Prompt library (`prompt_library_service.py`): saved prompts with versions, categories, model
  preference, usage stats; runnable from editor and composer; attachable to templates.
- Citation validation: cited source ids must exist in the retrieval trace.

## Phase 4 — Synthesis and connections ⏳

- `synthesis_service.py`: multi-source synthesis (concept page / summary / comparison / brief /
  FAQ / timeline) with agreement/disagreement/contradiction sections and per-claim citations;
  output distinguishes source-supported fact vs user opinion vs AI inference.
- `connection_service.py`: "Discover connections" job — similarity (embeddings + graph),
  duplicates, contradictions, missing links; suggestions surface as reviewable cards mapped to
  operations (`add_link`, `add_relationship`, merge-via-plan). **No automatic merges.**
- Graph: `origin: ai-suggested` edges rendered distinctly (edge model already supports it).

## Phase 5 — Structured memory ⏳

- Meeting memory: transcript processing profile of the extraction service (participants,
  decisions + rationale, action items with owners/deadlines, risks, follow-ups), each item
  anchored to a transcript passage; person/project page linking.
- Decision log: `decision` schema extended (options, rationale, review date, superseded-by);
  decision-log view; AI detects candidate decisions but **user confirms** each.
- Project memory: per-project memory page + "Refresh project memory" action (diff-based update
  via operation plan).
- Action items: `task` schema + views; extraction wired to meeting/processing pipelines.

## Phase 6 — Recurring intelligence ⏳

- `review_service.py`: weekly synthesis (manual "Generate review" first; optional local
  scheduling later — no cloud, no telemetry) saved as a cited `weekly-review` note.
- Knowledge-health dashboard: unprocessed captures, stale/orphan notes, broken links (exists via
  `link_health`), unreviewed AI outputs (`review_status: ai-inferred`), decisions due for review,
  unused prompts — as a views-engine dashboard with recommended actions, each an operation plan.

## Phase 7 — Hardening ⏳ (merges with existing M11)

- Privacy controls panel: per-layer AI policy editor UI (model exists), clear-history, provider
  allowlist, URL-fetch restriction toggle.
- Prompt-injection test suite expansion (imported-document adversarial corpus); fuzz the capture
  and import paths; performance at 10k+ notes with retrieval; accessibility audit of new UI;
  backup/recovery docs covering `.strata/ai/`; deployment docs.

## Sequencing rationale

Memory before capture (Phase 1 before 2) because provenance ids must exist before anything can
cite them. Capture before retrieval (2 before 3) because retrieval needs the knowledge areas and
version history to anchor citations. Synthesis and connections (4) need retrieval. Structured
memory (5) reuses extraction from 2 and saving from 3. Recurring intelligence (6) is a consumer
of everything. Hardening (7) is continuous but gets a closing sweep.
