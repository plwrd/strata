# Architecture Decision Records

This directory holds the Architecture Decision Records (ADRs) for **Strata**, a local-first,
encrypted, collaborative, AI-native spatial knowledge workspace.

An ADR records **one** architecturally significant decision: the context that forced the decision,
the decision itself, the consequences we accept, the alternatives we rejected and why, and the
conditions under which we would reopen it. ADRs are immutable once accepted. If a decision changes,
write a new ADR that supersedes the old one and update the old one's status to `Superseded by
ADR-NNNN`; do not edit the substance of an accepted ADR in place.

## Status values

| Status | Meaning |
| --- | --- |
| `Proposed` | Written, not yet agreed. Not safe to build on. |
| `Accepted` | Agreed. Code is expected to conform. |
| `Accepted (provisional)` | Agreed as a design-time decision, but not yet validated against a working spike. A validation task is named in the ADR. |
| `Superseded by ADR-NNNN` | No longer in force. |
| `Deprecated` | No longer in force and not replaced. |

## Index

| # | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-python-pyside6-desktop-host.md) | Python + PySide6 as the desktop host | Accepted | 2026-07-14 |
| [0002](0002-qt-webengine-react-frontend.md) | Qt WebEngine + React/TypeScript for the UI | Accepted | 2026-07-14 |
| [0003](0003-qt-webchannel-bridge-protocol.md) | QWebChannel bridge protocol and `strata://` scheme | Accepted | 2026-07-14 |
| [0004](0004-private-object-storage.md) | Private object storage layout (opaque random object ids) | Accepted | 2026-07-14 |
| [0005](0005-encryption-primitives.md) | Encryption primitives (XChaCha20-Poly1305 + Argon2id) | Accepted | 2026-07-14 |
| [0006](0006-crdt-selection.md) | CRDT selection (Yjs semantics via `pycrdt`) | Accepted (provisional) | 2026-07-14 |
| [0007](0007-search-architecture.md) | Search architecture (per-layer indexes; ephemeral private index) | Accepted | 2026-07-14 |
| [0008](0008-ai-provider-abstraction.md) | AI provider abstraction | Accepted | 2026-07-14 |
| [0009](0009-context-export-format.md) | AI context export format | Accepted | 2026-07-14 |
| [0010](0010-3d-graph-architecture.md) | 2D/3D graph architecture | Accepted | 2026-07-14 |
| [0011](0011-python-version-target.md) | Python version target (`>=3.10`) | Accepted | 2026-07-14 |

## Conventions

- Filename: `NNNN-kebab-case-title.md`, zero-padded to four digits, allocated in order.
- One decision per ADR. If you find yourself writing "and also", split it.
- Write for a reader who joins the project in two years and asks "why is it like this?".
- No marketing language. Name the costs explicitly; an ADR with no negative consequences is an ADR
  that has not been thought through.
- Alternatives must be real alternatives that were genuinely on the table, each with a concrete
  rejection reason. "It was worse" is not a reason.
- `Revisit when` must be falsifiable: a measurement, a version, a milestone, or an event — not
  "when we have time".

## Template

Copy this into a new file and fill it in.

```markdown
# ADR-NNNN: <Short imperative title>

**Status:** Accepted, 2026-07-14

## Context

What forces are in play? What constraints (technical, security, team, schedule, licensing) exist?
What breaks if we do nothing? State facts and measurements, not opinions. If a constraint comes from
outside the team (a platform limit, a machine we must build on, a threat model requirement), say so.

## Decision

What we are doing, stated in the active voice and in enough detail that an engineer can implement it
without asking a follow-up question. Include the concrete parameters (versions, algorithms, limits,
names) that the code must match.

## Consequences

### Positive

- What this buys us.

### Negative

- What this costs us. Be specific: binary size, latency, attack surface, maintenance burden.

### Neutral

- Facts that follow from the decision but are neither good nor bad — things a future reader needs to
  know to reason about the system.

## Alternatives considered

### <Alternative A>

What it is, and **why rejected**.

### <Alternative B>

What it is, and **why rejected**.

## Revisit when

- A falsifiable trigger.
- Another falsifiable trigger.
```
