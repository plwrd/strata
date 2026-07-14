# ADR-0009: AI context export format

**Status:** Accepted, 2026-07-14

> The normative specification lives in [`docs/export-format/README.md`](../export-format/README.md).
> This ADR records *why* the format is shaped the way it is. Where the two disagree, the spec is the
> contract and this ADR is the rationale.

## Context

The AI Context Composer (M6) lets a user select notes, folders, tags, graph neighbourhoods and search
results, and turn them into a context bundle for a language model. That bundle is used in two very
different ways:

1. **Inside Strata**, sent to a configured provider (ADR-0008).
2. **Outside Strata** — pasted into ChatGPT or Claude in a browser, dropped into a Projects/Knowledge
   area, committed into a repo for a coding agent, handed to a colleague, or archived as the record of
   *"this is the context I gave the model when it produced that answer"*.

(2) is the interesting one, and it is the reason this needs to be a *format* rather than a string we
build in memory. An exported context must be:

- **Human-readable and human-auditable.** The user must be able to see exactly what is about to leave
  their machine. This is a privacy requirement before it is a usability one.
- **Model-legible**, with instructions clearly separated from data — which is also our first line of
  defence against prompt injection (ADR-0008 §8).
- **Attributable.** Every claim a model makes from this context should be traceable back to a specific
  source note, by a stable id the user can look up.
- **Portable**, across providers and across time. A context exported today should be readable in five
  years by a human with a text editor.
- **Deterministic.** The same selection produces byte-identical output. This is what makes exports
  diffable, cacheable (prompt caching), and reviewable.
- **Honest about size.** Context windows are finite. The format must handle "this doesn't fit" by
  splitting predictably, never by quietly dropping the tail.

And the privacy rule that overrides everything: **a locked layer can never be exported, and private
content requires explicit, per-source, previewed confirmation.**

## Decision

**`strata_export_version: 1`.** Two shapes, three presets.

### Single-file Markdown export

One `.md` file, YAML frontmatter, then a fixed section order:

```
---
strata_export_version: 1
exported_at: 2026-07-14T09:31:07Z
preset: claude
… (see spec)
---

# Instructions          ← what the model should do; authored by the user/composer
# User Prompt           ← the actual question
# Selected Knowledge    ← the sources, each in a delimited block with a STRATA-SOURCE-### id
# Graph Summary         ← a Mermaid diagram of how the sources relate
# Source Index          ← a table: id | title | layer | visibility | modified | tokens
```

**Instructions before data, always.** Sources are delimited and each carries a stable
`STRATA-SOURCE-###` id (zero-padded, allocated in deterministic order). The Graph Summary is the piece
nobody else ships: it tells the model *how the notes relate*, which is the entire value of exporting from
a graph rather than from a folder. The Source Index makes the bundle auditable at a glance — a user can
read one table and know exactly what they are about to send.

### Multi-file package

```
strata-ai-context/
  README.md                       # what this is, how to use it, what it contains
  PROMPT.md                       # instructions + user prompt
  CONTEXT.md                      # all sources inline (or an index into SOURCES/)
  GRAPH.md                        # Mermaid graph + adjacency table
  MANIFEST.json                   # machine-readable: every source, id, hash, tokens, provenance
  SOURCES/
    STRATA-SOURCE-001.md
    …
  ATTACHMENTS/
    attachment-index.md           # names, types, sizes — bytes only if the user opted in
```

For agent tooling (which reads files, not pasted text), for large contexts, and for archiving. `MANIFEST.json`
is the machine-readable contract and has a published JSON Schema.

### Presets

| Preset | Shape | Why |
| --- | --- | --- |
| `chatgpt` | Clear top-level Markdown headings, instructions at the top, sources as `###` sections with fenced content. | Matches what the ChatGPT UI and Projects handle best; heavy structure, no XML. |
| `claude` | XML-ish `<source id="STRATA-SOURCE-003" title="…" layer="…">…</source>` boundaries, instructions and data explicitly separated. | Claude's documented preference for XML-tagged structure, and the strongest instruction/data boundary we can express in plain text — which is a prompt-injection mitigation, not a style choice. |
| `generic` | Portable Markdown, no provider-specific syntax, no XML. | Works everywhere; the archival/interop default. |

Presets change **framing**, never **content**. The same selection under three presets contains the same
sources with the same ids and the same text. This matters: a user must not have to reason about whether
switching preset changed what they are sending.

### Deterministic token-budget splitting

If the bundle exceeds the target budget, it splits into `context-part-001.md`, `context-part-002.md`, …
plus a `context-index.md` that lists the parts and which sources are in each. Splitting is:

- **Deterministic** — same input, same budget, same parts.
- **At source boundaries** where possible; oversize single sources split at heading/paragraph boundaries
  with explicit `(part 2 of 3 of STRATA-SOURCE-007)` continuation markers.
- **Never silent.** There is no truncation. If it does not fit, the user is told, shown the split, and
  can re-select. A model given a silently truncated context produces a confidently wrong answer, and the
  user has no way to know.

### ID stability

`STRATA-SOURCE-###` ids are **stable within an export** and are **derived from the export, not from
Strata's internals**. They are allocated deterministically (by the source ordering rules in the spec)
and they **never expose an internal object id** (ADR-0004) — because those ids are supposed to be
meaningless, and putting them in a document the user pastes into a cloud chat would make them meaningful
and correlatable. `MANIFEST.json` carries a per-export content hash for each source so a user can verify
what was sent; it carries no internal identifier.

### Privacy rules (normative)

1. **A locked layer can never be exported.** Not its content, not its titles, not its ids, not its
   existence in the Source Index. The export path does not have the key, so this is enforced by
   construction, not by a check.
2. **Including content from a private (unlocked) layer requires explicit confirmation**, per export, not
   per session, and the confirmation names the layers.
3. **Every included private source is previewed** — the user sees the actual text that will be included,
   before the export is written or sent. No "42 notes selected" and a Send button.
4. The export's frontmatter and Source Index **mark each source's visibility** (`public` / `private`), so
   the artefact itself is self-describing about how sensitive it is.
5. Attachment **bytes** are included only on explicit opt-in; by default `ATTACHMENTS/` contains an index
   (names, types, sizes) and nothing else.

## Consequences

### Positive

- The user can read, before sending, exactly what is being sent. That is the single most important
  privacy affordance in an AI feature, and the format is what makes it possible.
- Stable source ids give attributable answers: a model can cite `STRATA-SOURCE-012` and the user can
  click straight to the note.
- The Graph Summary carries relational structure that a folder-of-files export throws away. It is the
  thing that makes exporting *from Strata* better than exporting from anywhere else.
- Determinism buys diffability (what changed between two exports?), cacheability (prompt caching hits),
  and testability (golden-file tests on the exporter).
- The multi-file package is directly consumable by coding agents and by Projects/Knowledge features that
  take file uploads, without transformation.
- Plain Markdown + JSON means the artefact outlives Strata. That is a deliberate anti-lock-in property.

### Negative

- **Three presets are three things to test and keep good.** Golden-file tests for each, on every change.
- Splitting is more work than truncating, and the UX of "your context is in four parts, paste them in
  order" is genuinely worse than "it fit". We accept a worse experience over a silently wrong one.
- Token estimation is approximate for some providers (ADR-0008), so the budget has headroom and we will
  sometimes split when we did not strictly need to. Splitting conservatively is the correct error
  direction.
- Mermaid graph summaries degrade badly past a few dozen nodes. The spec caps the rendered graph and
  falls back to an adjacency table with a stated cap — this is a real limitation of exporting a graph
  into text.
- The format is a **contract**. Version 1 is going to have things we regret, and changing them means a
  version bump and a migration path for anyone who built on `MANIFEST.json`.

### Neutral

- The export is a snapshot, not a live link. A note edited after export is not reflected in the exported
  artefact, and the artefact records `exported_at` and a content hash so that divergence is detectable.
- Export IDs are per-export, so the same note has different ids in two exports. This is deliberate (see ID
  stability) and occasionally surprising; the `MANIFEST.json` content hash is the cross-export
  identity, not the id.
- The exporter runs in Python and writes to disk (it is not bounded by the bridge's 1 MiB envelope cap —
  ADR-0003 — because bytes never cross the bridge; the frontend gets a job id and a preview).

## Alternatives considered

### Just concatenate the selected notes into one blob

What most tools do.

**Why rejected:** no instruction/data separation (so, no injection boundary), no attribution (the model
cannot cite, the user cannot audit), no relational structure, and no story for exceeding the context
window except truncation. It is the thing we are trying to be better than.

### JSON-only export (structured, machine-first)

Cleanest for tooling.

**Why rejected:** the primary consumer is a language model, and models are demonstrably better at reading
structured Markdown than at reading dense JSON — and, critically, the **user** must be able to read it to
audit it. JSON is present where it belongs (`MANIFEST.json`, for tools), alongside the Markdown, rather
than instead of it.

### A single universal format, no presets

One shape, everywhere.

**Why rejected:** the instruction/data boundary is the one place where provider-specific framing measurably
changes behaviour (XML tags for Claude, headings for ChatGPT). Refusing to acknowledge that would mean
shipping a weaker injection boundary on purpose. The compromise — presets change framing, never content —
keeps the portability guarantee where it matters.

### Silent truncation to fit the context window

Simplest UX: it always "works".

**Why rejected:** it produces confidently wrong answers from a context the user believes is complete,
with no signal. This is the worst failure mode in the entire product and we design it out.

### Expose internal object ids as source ids

Free, stable, already unique.

**Why rejected:** ADR-0004's object ids are random precisely so that they mean nothing to anyone who does
not have the manifest. Exporting them into documents that get pasted into cloud services turns them into
correlatable identifiers across exports and across users, for no benefit. Export ids are export-scoped.

## Revisit when

- A provider ships a genuinely better context format (a standard, not a proprietary blob) that is worth
  adding as a fourth preset.
- Mermaid's limits bite in practice — the graph summary may need a different representation for large
  selections.
- Real usage shows the multi-file package or the single-file export going unused; drop the loser rather
  than maintaining both out of politeness.
- `strata_export_version: 2` becomes necessary. It will; the question is only when, and version 1 must
  make that migration possible (hence the version field being first, and mandatory).
