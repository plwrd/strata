# Implementation plan — AI-native knowledge & memory platform

> Phases for evolving Strata 1.3.1 into the platform described in
> [target-architecture.md](target-architecture.md). Each phase is shippable alone, preserves all
> working functionality, and passes the full existing gate (`scripts/check.ps1`).
> Status legend: ✅ done · 🔨 in progress · ⏳ pending.

Existing milestones M4 (search), M5 (graph), M9 (collaboration), M11 (hardening) continue in
parallel; where a phase depends on one, it is noted. TASKS.md remains the tactical tracker;
this file is the strategic map for the AI-memory work specifically.

## Phase 1 — Foundation: persistent AI memory ✅

The audit (see [current-architecture.md](current-architecture.md) §4) found the core gap:
every AI trace is amnesiac. Phase 1 made AI activity durable and fixed the live-path
injection gap. Shipped in PR #62:

- ✅ Repository audit + these five documents.
- ✅ `app/domain/history.py`: `AIExecutionRecord` + redaction rules
  ([ai-memory-design.md](ai-memory-design.md) §2–3).
- ✅ `app/services/ai_history_service.py`: JSONL persistence under `.strata/ai/` (receipts,
  executions, applied plans), corrupt-line tolerance, caps, `clear_history`.
- ✅ `AIService.run` records executions + receipts; `OperationService` audit log persists;
  **undo survives restart**.
- ✅ Security fix: live request path now uses `render_source_block` delimiter neutralisation.
- ✅ Bridge `ai.list_history`/`ai.clear_history`; AI history panel; tests (unit, security,
  frontend).

## Phase 2 — Capture and processing ✅

- ✅ Default knowledge areas: `Inbox/`, `Knowledge/`, `Reports/`, `Templates/` seeded on
  workspace create (existing workspaces are never forced; capture creates `Inbox/` lazily).
- ✅ New schemas: `capture`, `concept`, `organization`, `report`, `template`, `weekly-review`.
- ✅ `capture_service.py` + `notes.capture`/`notes.import_url`: quick capture with metadata
  (`source_url`, `capture_reason`, `processing_status: raw`), capture dialog in the command
  bar; URL import behind SSRF guards (scheme allowlist, private-range block via pre-request
  resolution, redirect refusal, size/time caps, `url_import_enabled` kill-switch) — reviewed
  in [security-and-privacy.md](security-and-privacy.md) §4.
- ✅ Per-note **version history** for Markdown layers: versioned write path in `NoteService`
  (`.strata/versions/`, trail follows renames/moves, capped), origin tags
  (`human` / `ai:<plan_id>` / `restore`), `notes.list_versions/get_version/restore_version`,
  History panel in the Properties tab. Private layers deliberately keep **no** plaintext
  version files (snapshots remain their recovery story) and the UI says so.
- ✅ `knowledge_service.py`: "Process into knowledge" — schema-validated extraction
  (summary, concepts, entities, decisions, action items, open questions, tags, related notes,
  claims to verify) materialised as an operation plan with `ai-inferred` + `generated_by`
  provenance on every page; invented note ids discarded; existing titles skipped; runs as a
  background job (**first production `JobService.submit()` call site**) surfaced in the
  Changes tab's new "Process into knowledge" mode.
- Deferred to Phase 3: provider-native structured output (extraction still uses
  prompt+validated-JSON; the validation layer is in place either way).

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
