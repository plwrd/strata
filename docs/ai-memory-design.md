# AI memory design

> How Strata remembers AI work — permanently, locally, and without leaking private layers.
> Phase 1 implements §2–§5 (execution records, persistence, redaction). Later phases build on them.

## 1. Principles

1. **AI is not a chat window.** Every provider call becomes a durable `AIExecutionRecord`; every
   useful output can be promoted to a note, report, concept page, decision, template, or saved
   prompt through the existing operation-plan pipeline (approval + undo included).
2. **Memory is owned data.** All AI memory lives inside the workspace directory (`.strata/ai/`),
   in line-oriented JSON the user can read, back up, or delete. No cloud, no opaque DB.
3. **Private layers stay private — even from memory.** A persisted record may never contain
   private-layer plaintext. Records touching private layers are stored **redacted** (metadata,
   counts, ids — never content). This extends the existing rule "no decrypted private content
   ever touches disk" to the AI subsystem.
4. **Provenance over confidence-theatre.** AI-authored content is marked `ai-inferred` with an
   execution id and confidence; only a human review flips it to `reviewed`. Citations refer to
   real `STRATA-SOURCE-NNN` ids from the context plan — a cited id that was not in the context is
   a validation error, not a footnote.

## 2. The record model (Phase 1)

`app/domain/history.py`:

```text
AIExecutionRecord
  id: str (exec_<16 hex>)          kind: "ai-request" | "plan-generation"
  provider, model, is_remote        layer_ids: list[str]
  prompt: str                       response_text: str
  source_object_ids: list[str]      source_count / private_source_count: int
  input_tokens / output_tokens      result: "completed" | "cancelled" | "failed"
  error_message: str | None         redacted: bool
  created_at / duration_ms
```

Alongside it, two existing models become persistent (unchanged shape):
- `PrivacyReceipt` — already metadata-only, safe to persist verbatim.
- `AppliedPlan` — the operations audit log; persisting it makes **undo survive restart**
  (its `snapshot_id` already survives on disk).

## 3. Redaction rules (normative)

A record is **content-bearing** if any of `prompt`, `response_text`, `source_object_ids`, or
plan operation payloads could contain layer content. Rule, applied at write time by
`AIHistoryService` (not by callers):

| Condition | What is persisted |
| --- | --- |
| All involved layers are public | Full record. |
| Any involved layer is private (or unknown) | `redacted=True`; `prompt`, `response_text`, `source_object_ids` emptied; for `AppliedPlan`: `prompt`, `summary`, and per-result `detail` emptied. Counts, ids, provider, model, tokens, timestamps, layer ids remain. |

Rationale: layer ids and counts already leak via `workspace.json` and receipts (accepted leakage,
THREAT_MODEL §5). Titles, content, and object ids of private notes do not — and must not start.
The in-memory session copy stays unredacted, so the *current* session's UI remains fully useful;
only the disk representation is redacted. `scan_plaintext.py` semantics extend to `.strata/ai/`:
a security test writes private content through the AI path and asserts the history files never
contain it.

Deletion: `clear_history()` (bridge: `ai.clear_history`) truncates all history files — the
"delete AI conversation history" privacy control. Individual receipts are append-only until then.

## 4. Storage format (Phase 1)

```text
<workspace>/.strata/ai/
  receipts.jsonl        one PrivacyReceipt per line
  executions.jsonl      one AIExecutionRecord per line
  applied_plans.jsonl   one AppliedPlan per line
```

- Append-only JSONL; each line is a complete Pydantic-serialized record (`model_dump_json`).
- Written with the same atomic-append discipline as logs (single process; `O_APPEND` semantics).
- Loaded lazily per workspace; corrupt lines are **skipped, never fatal** (same stance as
  frontmatter parsing). A cap (default 1000 records/file) is enforced by compaction on load-write.
- Files live under `.strata/` so they are excluded from layer content, snapshots of `layers/`,
  and the plaintext scanner's private-layer sweep — but *included* in workspace backups.

## 5. Wiring (Phase 1)

- `AIService.run()` accumulates streamed deltas and, in the same `finally` that writes the
  receipt, records an `AIExecutionRecord`. Receipts write through `AIHistoryService`.
- `OperationService.apply()/undo()` write through `AIHistoryService`; `audit_log()` and `undo()`
  rehydrate persisted plans on first use per workspace, so a plan applied yesterday can be undone
  today (its snapshot still exists).
- `ai_bridge` gains `list_history` / `clear_history`; the AI inspector tab shows the persisted
  trail (provider, model, local/remote badge, tokens, result, redaction badge, time).

## 6. Building on the foundation (later phases)

- **Conversations (Phase 3):** `AIConversation` = ordered execution ids + scope; the composer
  becomes multi-turn by replaying prior turns from history (local, explicit, visible context).
- **Save-as-asset (Phase 3):** "Save as note/report/decision/template" generates a one-operation
  plan whose `create_note` content embeds provenance frontmatter:
  `generated_by: exec_…`, `origin: ai-inferred`, `model`, `sources: [[…]]`, `confidence`.
  Regeneration re-runs the recorded prompt against *current* source content and produces a
  **diff for approval** — never a silent overwrite (user edits are detected via note versions).
- **Saved prompts (Phase 3):** prompt library entries are versioned records in
  `.strata/ai/prompts/`; running one records `prompt_id`+`prompt_version` on the execution,
  which is what makes outputs reproducible and usage statistics honest.
- **Retrieval (Phase 3):** retrieval logs the chunk ids + character ranges it injected into
  context, so citations in answers resolve to exact sections, and "which notes did the AI see"
  is answerable for every historical execution.
- **Weekly reviews / decision log / project memory (Phases 5–6):** all are generated notes with
  `generated_by` provenance; their "what changed" claims cite note versions, which exist because
  every write is versioned from Phase 2 onward.

## 7. Threat-model deltas introduced by AI memory

| New artifact | Leakage if disk is read | Mitigation |
| --- | --- | --- |
| `executions.jsonl` | Prompts/responses about **public** content; timing and provider metadata for all | Redaction rule §3 for private; `clear_history`; workspace dir is the user's own trust boundary |
| `applied_plans.jsonl` | Plan summaries for public layers; op counts for private | Redaction rule §3 |
| `receipts.jsonl` | Same metadata the in-memory receipts already held | Unchanged model, now durable — this is the *purpose* (an audit trail that survives restart) |

New bridge methods (`ai.list_history`, `ai.clear_history`) return only what the session user could
already see; they are read/delete, never write; both are logged in
[security-and-privacy.md](security-and-privacy.md) §4 as THREAT_MODEL §6 review entries.
