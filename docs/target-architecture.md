# Target architecture — Strata as an AI-native knowledge & memory platform

> Companion to [current-architecture.md](current-architecture.md) (what exists) and
> [implementation-plan.md](implementation-plan.md) (the order of work).
> Design detail for the memory subsystem lives in [ai-memory-design.md](ai-memory-design.md).

## 1. Vision, translated to Strata's architecture

The product loop stays the one Strata already ships (`Capture → Organize → Connect → Explore →
Select → Ask → Review → Apply → Act`), extended so that **every AI interaction leaves a permanent,
private, locally-owned trace** and every useful output can be promoted into workspace knowledge:

```text
Capture ─▶ Process ─▶ Connect ─▶ Synthesize ─▶ Save ─▶ Reuse
   │          │           │            │          │       │
 Inbox    operation   discovered   synthesis   assets   retrieval +
 layer      plans     relations      notes    + prompts  AI history
```

Strata is local-first and serverless. The web-platform entity list (User, Workspace, Role, …)
maps onto desktop equivalents instead of being rebuilt:

| Requested entity | Strata representation |
| --- | --- |
| User / Workspace / WorkspaceMember / Role | The OS user owns the workspace directory. Multi-user roles arrive with M9 collaboration (identities, devices, roles are already designed there). No accounts are added. |
| Note / Folder / Tag / NoteTag / Link / Backlink | Exist today (`NoteMetadata`, typed `NoteLink`, backlinks, link health). |
| Collection | `KnowledgeLens` (saved perspective) + saved `ViewConfig` — no new entity needed. |
| NoteVersion | **New**: per-note version records (see §3). |
| Attachment | Exists (`save_attachment`, encrypted for private layers). |
| Source / SourceExcerpt | **New**: provenance frontmatter (`source_url`, `source_author`, `captured_at`, …) + `ExportSource`/`STRATA-SOURCE-NNN` excerpt ids already used by the citation pipeline. |
| Template / TemplateVersion | Note schemas already carry a `template`; extended into a Templates area with versioned template notes. |
| Project / Person / Organization / Concept | Builtin schemas (`project`, `person`; add `organization`, `concept`) — knowledge pages are notes with schemas, not new tables. |
| Relationship | Typed wiki relationships (12 kinds) — extend with graph edges of `origin: ai-suggested`. |
| AIConversation / AIMessage | **New**: persisted conversation threads in AI history (Phase 3). |
| AIExecution / AIOutput | **New**: `AIExecutionRecord` — persisted for every provider call (Phase 1). |
| SavedPrompt / PromptVersion | **New**: prompt library (Phase 3; TASKS.md already lists "prompt templates and prompt history" under M6). |
| ProcessingJob / ResearchJob / SynthesisJob | `JobRecord` (exists) — activated with real call sites and typed payloads. |
| WeeklyReview / Decision / ActionItem | Notes with schemas (`decision` schema exists; add `weekly-review`; `task` schema covers action items) + dedicated views. |
| AuditLog | **New**: persisted AI history (receipts + executions + applied plans) — Phase 1. |

**Design rule:** knowledge stays legible. Everything the user can read is Markdown + frontmatter in
their own workspace; everything the app must remember about AI activity is JSONL under `.strata/ai/`
(never containing private-layer plaintext). No opaque database is introduced.

## 2. The four knowledge areas

Implemented as **default folders + schemas + views**, not a new storage model (public layers keep
plain Markdown; the same conventions work inside private layers):

| Area | Representation |
| --- | --- |
| **Inbox / Raw** | Default `Inbox/` folder + `capture` schema (`source_url`, `source_author`, `published_at`, `captured_at`, `capture_reason`, `processing_status: raw|processing|processed|archived`). Quick-capture UI writes here; drag-drop import already lands files as notes/attachments. An "Inbox" view (existing views engine) filters `processing_status != processed`. |
| **Knowledge / Wiki** | Default `Knowledge/` folder + `concept`/`person`/`organization`/`project` schemas with `review_status: unverified|ai-inferred|reviewed`, `confidence`, `last_verified`, `aliases` (exists), `sources` (wiki links to captures). Backlinks/tags/relationships exist today. |
| **Reports** | Default `Reports/` folder + `report` schema (`generated_by` → AI execution id, `source_notes` wiki links, `regenerable: bool`). Reports keep `derived_from::` typed links to sources — the lineage convention AI note-generation already emits. |
| **Templates** | Default `Templates/` folder; template notes with a `template` schema (`for_schema`, `version`, optional `ai_prompt_id` linking a saved prompt). Duplicate-to-instantiate uses existing `duplicate_note`. |

## 3. Component architecture (target)

New services follow the existing container pattern (explicit construction in `services/container.py`,
narrow constructor dependencies, no service locator):

```text
app/services/
  ai_history_service.py    Phase 1  persistent AI memory: receipts, executions, applied plans
  capture_service.py       Phase 2  quick capture, URL/file import, processing status
  knowledge_service.py     Phase 2  "process into knowledge": extraction → operation plans
  retrieval_service.py     Phase 3  scoped hybrid retrieval for AI context (permissions-first)
  prompt_library_service.py Phase 3 saved prompts + versions + usage stats
  synthesis_service.py     Phase 4  multi-source synthesis → report/concept generation
  connection_service.py    Phase 4  discover connections, duplicates, contradictions
  memory_service.py        Phase 5  decision log, project memory, meeting memory
  review_service.py        Phase 6  weekly synthesis, knowledge health
```

Key architectural moves:

1. **Version history** (`NoteService` write path): every mutating write appends the *previous*
   content to a per-note version store — `.strata/versions/` for public layers; version objects
   inside the encrypted store for private layers (kind `version`, so plaintext never leaves the
   layer). Versions record author origin (`human | ai:<execution_id>`), timestamp, and change
   summary. Restore = normal `update_note` (itself versioned). Snapshots remain the workspace-level
   safety net; versions are the per-note narrative.
2. **AI execution records** (`AIHistoryService`, Phase 1): every provider call persists an
   `AIExecutionRecord` (prompt, response, sources, tokens, policy verdict, receipt) under
   `.strata/ai/`, with **redaction rules** when private layers are involved (see
   [ai-memory-design.md](ai-memory-design.md) §3). Receipts and the applied-plan audit log move
   from process memory to the same store; undo survives restart.
3. **Provenance model**: AI-generated claims carry `origin: ai-inferred` + execution id +
   confidence in frontmatter; the operation pipeline stamps `generated_by` on notes it creates.
   Nothing AI-authored is ever stored as verified — `review_status` starts at `ai-inferred` and
   only a human sets `reviewed`.
4. **Jobs, activated**: imports, bulk processing, embedding builds, connection discovery, and
   weekly reviews run through `JobService.submit()` (progress + cancel already wired to the
   frontend types). Idempotency: job payloads carry deterministic ids derived from inputs; re-runs
   detect existing outputs. A jobs panel UI joins the inspector.
5. **Retrieval before generation** (Phase 3): scoped retrieval (lens/folder/selection/date-range →
   permission filter → hybrid rank → budget-fit chunking with heading + character-range anchors)
   replaces "manual selection only". A real embedder (local Ollama/llama.cpp via the existing
   provider embeddings path, per-layer embeddings policy already in the domain) replaces
   `HashingEmbedder` behind the same `Embedder` ABC; private-layer vectors stay in-memory.
6. **Structured output, made real**: extraction/synthesis services define Pydantic schemas; the
   Anthropic provider gains tool-based structured output, OpenAI-compatible keeps `json_object`;
   responses are validated and **rejected, not repaired** (existing rule). Prompts live in
   `app/ai/prompts/` as versioned modules — never in UI components or bridges.
7. **Composer becomes a chat with memory**: conversation threads persist as `AIConversation`
   records; every response offers Save as note / report / decision / template / prompt, each
   implemented as a normal operation plan so approval, snapshots, and undo apply unchanged.

## 4. Bridge surface (target deltas)

New bridge methods extend **existing** bridge objects where possible (each addition logged as a
threat-model review item per THREAT_MODEL §6):

- `ai`: `list_history`, `get_execution`, `clear_history` (Phase 1); `list_conversations`,
  `continue_conversation`, `save_output` (Phase 3); prompt-library CRUD (Phase 3).
- `notes`: `list_versions`, `get_version`, `restore_version` (Phase 1–2); `capture` (Phase 2).
- `operations`: unchanged API; audit log becomes persistent underneath.
- `jobs`: unchanged API; records become real.
- New `knowledge` bridge only when Phase 4 needs it (connection review), with its own review.

Frontend additions reuse the inspector-tab pattern: AI history + jobs in the AI tab, versions in
the Properties tab, capture via command bar; new stage views (Inbox, Decision log, Knowledge
health) ride the existing views engine.

## 5. What does NOT change

- The trust model: Python owns everything; the renderer draws. No crypto/fs/network in JS.
- The storage philosophy: legible Markdown, encrypted private objects, no hidden database.
- The consent pipeline: policy gate → receipt → operation plan → human approval → undo.
- Locked layers as absolute walls, including for every new persisted artifact and index.
- The closed error enum, envelope protocol, and no-telemetry stance.
- Existing milestones M4/M5/M9/M11 remain valid; this project's phases interleave with them
  (mapping in [implementation-plan.md](implementation-plan.md)).
