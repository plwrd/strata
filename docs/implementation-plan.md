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

## Phase 3 — Retrieval and AI chat ✅

- ✅ `retrieval_service.py`: "ask the workspace" — the prompt is hybrid-ranked (lexical +
  semantic + tags + recency) into a small bounded set of note ids that then flow through the
  *same* context-plan → policy-gate → neutralised-render path as a manual selection. Permission
  clean by construction (only readable layers are ever indexed; locked indexes are destroyed).
  The retrieval trace lands on the execution record (`source_object_ids`), and the UI shows
  exactly which notes were used.
- ✅ Conversations (`conversation_service.py`): persisted multi-turn threads under
  `.strata/ai/conversations.jsonl`. The backend owns the thread — replay comes from Python's
  store, never the client; private-layer turns persist redacted and never re-enter a context;
  the policy gate re-runs on every turn. `clear_history` wipes conversations too.
- ✅ Save-as-asset: every finished answer offers Save as note / Save as report / Append —
  implemented as a one-operation plan through the operation engine (snapshot, audit, undo)
  with `ai-inferred` + `generated_by` + `derived_from::` provenance; an existing title is
  never overwritten (uniquified instead).
- ✅ Prompt library (`prompt_library_service.py`): versioned saved prompts
  (`.strata/ai/prompts.jsonl` — append-only, the file is the version trail), categories, model
  preference, usage counts, last-used; composer panel to fill-from/save-to the library.
- Deferred (tracked for Phase 7 / M4): a model embedder behind the `Embedder` ABC. Mixing
  per-layer embedding policies with per-provider vector dimensions needs its own design pass;
  the hashing embedder remains the honest local default and the retrieval pipeline is
  unchanged by the swap. Citation-id validation lands with synthesis (Phase 4), where cited
  ids become load-bearing.

## Phase 4 — Synthesis and connections ✅

- ✅ `synthesis_service.py`: multi-source synthesis (summary / concept / comparison /
  research-brief / project-plan / FAQ / timeline) with dedicated agreement / disagreement /
  contradiction / missing-information sections, and the model's own inferences quarantined
  into a labelled "AI inferences" section. **Citation validation is load-bearing**: a cited
  `STRATA-SOURCE-NNN` that was never in the context is stripped and reported as an attempted
  invention. Output is one `create_note` plan into `Reports/` with `report_kind`,
  `ai-inferred`, `generated_by`, and `derived_from::` links; source notes are never touched.
  Surfaced as the "Synthesize selection" mode in the Changes tab (background job).
- ✅ `connection_service.py`: "Discover connections" — deliberately model-free (semantic
  similarity, duplicate threshold, unlinked mentions), so every suggestion carries a score
  and an inspectable reason and nothing can be hallucinated. Suggestions surface in the
  Links tab with Accept (a one-op `add_relationship` plan through the standard review/apply
  engine — snapshot, audit, undo) and Dismiss. **Duplicates propose a `supersedes`
  relationship; merging stays human.** `workspace_duplicates()` feeds the Phase 6 health
  dashboard.

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
