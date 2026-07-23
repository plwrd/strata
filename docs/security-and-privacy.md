# Security & privacy — AI knowledge platform addendum

> Scope: the AI-memory features added by [implementation-plan.md](implementation-plan.md).
> This document **extends** — never overrides — [SECURITY.md](../SECURITY.md) (non-negotiable
> rules), [THREAT_MODEL.md](../THREAT_MODEL.md), and [ASSUMPTIONS.md](../ASSUMPTIONS.md).
> Any conflict resolves in favour of those documents.

## 1. Invariants inherited by every new feature

1. **Locked layers are absolute.** No new index, history record, retrieval pass, job, or health
   metric may read, count, or name anything in a locked layer.
2. **No decrypted private content on disk.** New persisted artifacts (`.strata/ai/*`, versions,
   prompt library, retrieval caches) either contain no private content (enforced by redaction,
   §2) or live inside the encrypted layer store (per-note versions of private notes).
3. **Consent pipeline is mandatory.** Remote calls: policy gate → explicit confirmation →
   privacy receipt. Mutations: operation plan → visual diff → human approval → snapshot-backed
   apply → undo. New features add *profiles* of this pipeline, never bypasses.
4. **Untrusted content is data.** Imported files, fetched URLs, transcripts, and pasted material
   are wrapped in neutralised `<source>` blocks; instructions inside them must not steer the
   model. The model has no tools and no autonomy: its output is a proposal validated against a
   closed schema and a selection constraint.
5. **Closed error enum, allowlist logging.** New services raise `StrataError` subtypes only;
   nothing new logs content, titles, or paths.

## 2. Redaction rules for persisted AI memory

Normative specification in [ai-memory-design.md](ai-memory-design.md) §3. Summary: any record
whose execution involved a private layer is persisted with `redacted=True` — content fields
(prompt, response, source object ids, plan summaries/details) emptied; metadata (provider, model,
tokens, counts, layer ids, timestamps) retained. Redaction happens inside `AIHistoryService` at
write time, so no caller can forget it. Verified by security tests that run private content
through the AI path and grep the raw history files.

## 3. New attack surfaces and their controls

| Surface (phase) | Risk | Control |
| --- | --- | --- |
| Persisted AI history (1) | Private content leakage to disk; history as surveillance of the user | Redaction (§2); `clear_history`; files stay inside the user-owned workspace; no telemetry |
| Live-request source blocks (1, fix) | Prompt injection via `</source>` breakout in note content | `render_source_block` (delimiter neutralisation + attribute escaping) now used on the request path, matching the export path |
| URL import (2) | SSRF, private-network probing, huge/hostile payloads | Scheme allowlist (`https`), DNS-rebind-safe private-range block (resolve-then-connect check), redirect refusal, size + time caps, content-type validation, content stored as untrusted capture |
| File import (2) | Malicious files, path traversal, decompression bombs | Existing `safe_filename`/`resolve_within`; size caps (64 MB attachment cap exists); imports never execute; MIME sniffing on import; PDFs/images processed only by explicit user action |
| Extraction/synthesis output (2, 4) | Model proposes destructive or out-of-scope changes; invented citations | Existing selection constraint + per-item approval + destructive-never-preapproved; citation ids validated against the actual context/retrieval trace — unknown ids reject the output |
| Retrieval (3) | Scope creep pulling notes the user didn't intend; embeddings as plaintext-equivalent (T-09) | Permission filter runs first; scope is explicit and displayed ("what you see is what gets sent" preserved); private-layer vectors in-memory only, destroyed on lock; per-layer embeddings policy enforced |
| Saved prompts (3) | Prompt files as an injection vector if shared/imported | Prompts are user-authored config, rendered into the *instruction* channel only after explicit user action; imported prompts flagged for review before first run |
| Conversation replay (3) | Old private context resent to a remote model without fresh consent | Replay re-evaluates the policy gate per turn against *current* layer states; locked-since-then content drops out |
| Background jobs (2+) | Unbounded resource use; partial writes | Cooperative cancel (exists), concurrency limits in `JobService`, idempotent payload ids, all mutations still via transactional apply |
| Scheduled reviews (6) | Surprise AI runs / surprise spend | Manual-first; scheduling is local, opt-in, visible, and local-provider-only unless explicitly confirmed per schedule |

## 4. THREAT_MODEL §6 review log (new bridge surface & outbound calls)

Every entry here corresponds to a required review trigger. Add a row per addition — a missing
row fails review.

| Change | Phase | Review notes |
| --- | --- | --- |
| `ai.list_history` (read) | 1 | Returns persisted history for the open workspace only; respects redaction (redacted records stay redacted even in-session after restart); no content from locked layers by construction |
| `ai.clear_history` (delete) | 1 | Destructive but user-initiated privacy control; truncates `.strata/ai/*` only; cannot touch layer content |
| `notes.capture` (write) | 2 | Pending — must reuse `create_note` validation; capture metadata schema-validated |
| URL import (first new outbound call besides AI/relay) | 2 | Pending — requires new FR + receipt-equivalent record per A-010; controls in §3 |

## 5. Privacy controls roadmap

Users get, in order: clear AI history (1); per-layer AI policy editor UI, already modeled as
`LayerAIPolicy` (2); provider allowlist + URL-fetch kill-switch (2); retrieval exclusions
(`ai_readable_layer_ids` on lenses exists; per-note `ai: exclude` frontmatter added) (3);
conversation retention rules (3); redact-values pass for captures (7). Provider credentials
remain in the OS keychain (fails closed); model-training opt-out headers are sent where
providers support them (documented per provider, never claimed where unsupported).

## 6. Non-goals restated

Local malware and a compromised OS remain out of scope (THREAT_MODEL). AI memory does not make
Strata "zero-knowledge" — while unlocked, the app holds keys and plaintext in memory, and
public-layer history is plaintext on the user's own disk by design. We document leakage; we do
not market around it.
