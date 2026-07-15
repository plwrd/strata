# Contributing to Strata

Read [SECURITY.md](SECURITY.md) and [THREAT_MODEL.md](THREAT_MODEL.md) first. Strata is a tool that
holds people's private notes and their decryption keys. The rules below are stricter than you may be
used to, and that is deliberate.

Two things to internalize before writing code:

1. **The non-negotiable security rules in [SECURITY.md](SECURITY.md) are invariants, not guidelines.**
   A PR that violates one is not merged, no matter how good the feature is. If you think one is wrong,
   write an ADR — don't route around it in a diff.
2. **Say "not implemented" honestly.** Every requirement carries a milestone tag. Shipping a UI control
   that does nothing, or a docstring describing behaviour that does not exist, is worse than shipping
   nothing.

---

## Repository layout

```
strata/
├── app/                          # Python (PySide6). The trusted side. Keys live only here.
│   ├── main.py                   # entry point
│   ├── shell/                    # QMainWindow, QWebEngineView, strata:// scheme handler, CSP
│   ├── bridge/                   # QWebChannel objects — the trust boundary
│   │   ├── envelope.py           # request/response envelopes + the closed error enum
│   │   ├── workspace_bridge.py   layer_bridge.py       notes_bridge.py
│   │   ├── graph_bridge.py       search_bridge.py      ai_composer_bridge.py
│   │   ├── export_bridge.py      collaboration_bridge.py
│   │   ├── settings_bridge.py    snapshot_bridge.py    job_bridge.py
│   ├── core/                     # domain models (pydantic v2), workspace, layers, objects
│   ├── crypto/                   # M3. Argon2id, XChaCha20-Poly1305, envelopes, zeroization
│   ├── storage/                  # on-disk layout, atomic writes, trash, snapshots
│   ├── search/                   # FTS + vector index
│   ├── graph/                    # networkx
│   ├── ai/                       # providers, context composer, operation plans, receipts
│   └── jobs/                     # background job runner
├── frontend/                     # React 18 + TS strict + Vite + Zustand + R3F + CodeMirror 6
│   ├── src/
│   │   ├── bridge/               # the ONLY place that talks to QWebChannel
│   │   ├── stores/               # Zustand
│   │   ├── features/             # feature modules (view + hooks)
│   │   └── components/           # presentational only
│   └── dist/                     # built bundle → served at strata://app/index.html
├── docs/
│   ├── adr/                      # architecture decision records
│   ├── architecture/             # system-architecture.md, storage-layout.md
│   ├── security/                 # encryption-format.md
│   └── product/                  # glossary.md
├── scripts/
│   └── scan_plaintext.py         # CI guard: no plaintext in a private layer
└── tests/
    ├── unit/          integration/    security/
    ├── e2e/           performance/    fixtures/
```

---

## Development setup

Exact, verified commands live in the marked block in [README.md](README.md)
(`<!-- COMMANDS:START -->`). **That block is the single source of truth** — do not duplicate commands
here, and do not add a second list somewhere else that will drift.

Environment notes:

| | |
| --- | --- |
| Python | **3.10+**. The dev machine runs 3.10.11; CI matrixes 3.10, 3.11, 3.12. See [A-001](ASSUMPTIONS.md). |
| **Do not use 3.11/3.12-only syntax** | No `except*`, no PEP 695 generics (`class C[T]`, `type X = ...`), no `typing.override`. `mypy` is pinned to `python_version = "3.10"` and will catch you. |
| Node | For the frontend build only. The shipped app contains no Node runtime. |
| Qt | PySide6 6.8 / Qt 6, Qt WebEngine, Qt WebChannel. |

---

## Coding rules

### Everywhere

- **No crypto in JavaScript.** Not a hash, not a nonce, not a key. `app/crypto/` or nowhere.
- **Nothing arbitrary crosses the bridge.** No `eval`, `exec`, `subprocess`, no "open this path", no
  "fetch this URL", no generic `call(method, args)` dispatcher. The bridge surface must stay greppable.
- **Fail closed.** An unhandled state is an error, not a fallback to permissive behaviour. A
  decryption authentication failure is corruption — never a warning, never a partial return.
- **No disconnected UI.** A button that does nothing, a toggle bound to nothing, a menu item that
  opens a "coming soon" toast — none of these merge. If the feature is not implemented, the control
  does not exist yet. This is a hard rule, not a style preference: fake UI is how a security product
  ends up lying to a user about whether something is encrypted.
- **Never disable, skip, or weaken a test to make CI green.** Not `@pytest.mark.skip`, not
  `it.skip`, not a loosened assertion, not a widened tolerance. If a test is wrong, fix the test in its
  own commit with an explanation. If a test is flaky, fix the flake or delete it — a muted test is
  worse than no test because it looks like coverage.

### Python

- `mypy --strict` passes. `ruff` passes (`E, F, I, UP, B, S, ANN, RUF`).
- **Every function is fully annotated**, including `-> None`.
- **No `Any` without a justification.** If you genuinely need one, it gets a comment on the line:
  ```python
  # Any: PySide6 signal payloads are untyped at the Qt boundary; validated by Pydantic immediately below.
  def _on_signal(self, payload: Any) -> None: ...
  ```
  A bare `Any` with no comment is a review blocker. `ANN401` is disabled in ruff *precisely so that the
  justification comment is the gate*, not the linter.
- **Pydantic v2 models at every boundary.** Bridge input is parsed into a model before any side effect.
  Dicts do not travel through business logic.
- **Structured logging only** (`structlog`). **Never log object content, plaintext, key material, or
  filesystem paths.** The log schema is an allowlist.
- **No business logic in bridge objects.** A bridge method validates, delegates to a service, and maps
  the result into an envelope. If it is longer than about 15 lines, the logic is in the wrong place.
- **Anything slow runs as a job**, off the UI thread, cancellable, reporting progress via `JobBridge`.
  Blocking the Qt event loop is a bug.

### TypeScript / React

- `strict: true`. No `any`, no non-null `!` to silence the compiler, no `@ts-ignore` without a comment
  explaining why and a linked issue.
- **No business logic in React components.** Components render. Domain decisions — what is valid, what
  a policy permits, what an operation means — live in Python. If the frontend is deciding whether
  something is *allowed*, that is a security bug: the renderer is semi-trusted (see the threat model),
  and any check it performs is advisory only and **must** be enforced again in Python.
- All bridge traffic goes through `frontend/src/bridge/`. Components never touch `QWebChannel`
  directly, and never construct envelopes by hand.
- Zustand stores hold view state. They are not a cache of truth — Python is the truth.
- Every user-visible string is externalized (no concatenation) — see NFR-032.

---

## Commits and pull requests

**Commits.** Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `perf:`,
`build:`, `ci:`. Use the `!` suffix or a `BREAKING CHANGE:` footer for anything that changes the
storage format, the encryption format, or the bridge contract. Small and reviewable beats large and
atomic.

**Pull requests must state:**

| | |
| --- | --- |
| **What & why** | Not just what changed — what problem it solves. |
| **Requirement / milestone** | The `FR-###` / `NFR-###` it implements, and the milestone tag. |
| **Security impact** | Explicitly, even if the answer is "none". Anything touching `app/crypto/`, `app/bridge/`, the scheme handler, the CSP, the sanitizer, or any outbound network call requires a **threat-model review** and a link to the affected `T-##` entries. |
| **New dependencies** | Justified individually. A new dependency is a permanent liability ([T-27](THREAT_MODEL.md)), not a convenience. |
| **Assumptions** | If you made a decision the docs don't cover, add an `A-###` to [ASSUMPTIONS.md](ASSUMPTIONS.md) in the same PR. |

Anything that changes the **encryption format**, the **storage layout**, or the **bridge contract**
needs an **ADR** in `docs/adr/` and an update to the relevant spec document *in the same PR*. Specs
that lag the code are worse than no specs.

---

## Branching and releases

Two long-lived branches:

| Branch | Meaning | Who writes to it |
| --- | --- | --- |
| `main` | **Production.** Every commit is a released, tagged state. | Only the release merge from `dev`. |
| `dev` | **Integration.** The next release as it accumulates. | Only squash-merges of feature PRs. |

The flow for a unit of work (typically one milestone):

1. Branch from `dev`: `feat/m5-advanced-graph`, `fix/cross-platform-paths`, `chore/…`.
2. Open a PR **into `dev`**. CI runs the full gate (see the [`ci.yml`](.github/workflows/ci.yml)
   jobs: Python matrix, frontend, e2e desktop shell, no-plaintext, dependency audit).
3. Enable **auto-merge (squash)**. The PR merges itself the moment the required checks are green;
   the branch is deleted automatically. Nothing merges red.
4. **Release** cuts `dev` → `main` and tags it `vMAJOR.MINOR.PATCH` (one minor per milestone), with a
   GitHub Release summarising what shipped. `main` never receives a direct push.

The dependency audit is **advisory** — it reports but does not block the merge, because CVEs surface
independently of any PR. A production-affecting advisory (the Markdown sanitizer, a crypto library) is
fixed by bumping the dependency in its own PR, never by muting the check.

---

## Definition of Done

A change is not done until **every** box is true. "I'll do it in a follow-up" is how a security product
rots.

- [ ] **It works** — exercised end-to-end in the running app, not only in tests.
- [ ] **No disconnected UI** — every control shipped is wired to real behaviour.
- [ ] **Tests written and passing** — unit; integration if it crosses a boundary; a `tests/security/`
      test if it touches crypto, the bridge, layer state, or anything that could leak plaintext.
- [ ] **No test skipped, muted, or weakened** to make the suite pass.
- [ ] **`mypy --strict` clean, `ruff` clean, TypeScript `strict` clean.** No new `Any` without a
      justification comment.
- [ ] **Errors use the closed enum.** No new error codes; no stack traces or filesystem paths in
      production responses.
- [ ] **Slow work is a cancellable job** with progress via `JobBridge`; the UI thread never blocks.
- [ ] **Locked-layer behaviour verified** — if the feature touches layers, prove it does nothing, shows
      nothing, and leaks nothing when the layer is locked.
- [ ] **No plaintext on disk** — if it touches private layers, `scripts/scan_plaintext.py` passes and a
      negative test asserts it.
- [ ] **Privacy receipt written** — if it makes a remote call or a decrypted export.
- [ ] **Docs updated in the same PR** — spec, ADR, `ASSUMPTIONS.md`, `PRODUCT_REQUIREMENTS.md`, and the
      `<!-- COMMANDS -->` block in the README if the commands changed.
- [ ] **Accessibility** — keyboard-reachable, focus-visible, labelled; no colour-only meaning.
- [ ] **Performance** — meets the relevant NFR at the stated scale, or the gap is recorded with an
      issue. Typing latency is never regressed.
- [ ] **Milestone honesty** — nothing in the UI, docs, or docstrings implies a capability that does not
      exist yet.

---

## Tests

| Directory | What belongs there | Must be |
| --- | --- | --- |
| `tests/unit/` | Pure logic: models, envelopes, crypto primitives, padding, AAD construction, graph algorithms. | Fast, no Qt, no disk. |
| `tests/integration/` | Across boundaries: bridge → service → storage; lock/unlock lifecycle; index rebuild; import/export. | Real temp workspaces; no network. |
| `tests/security/` | The rules in [SECURITY.md](SECURITY.md), as executable assertions. | **The suite that must never be weakened.** |
| `tests/e2e/` | The app driven through the real Qt shell and the real bridge (`pytest-qt`). | Marked `gui`. |
| `tests/performance/` | Synthetic workspaces at 1k / 10k / 100k objects, asserting the NFR targets. | Marked `slow`. |
| `tests/fixtures/` | Workspace/layer/object builders, known-answer vectors, malicious-Markdown and prompt-injection corpora. | Deterministic; seeded RNG. |

Markers are declared in `pyproject.toml`: `security`, `gui`, `slow`.

### `tests/security/` must cover, at minimum

- **No plaintext on disk.** Create a private layer, write objects with known distinctive strings, lock,
  then scan every byte of the layer directory for those strings, for their UTF-16 forms, and for
  common encodings. This is what `scripts/scan_plaintext.py` automates; run it in CI on every PR.
- **AAD binding (negative tests).** Transplant an object into another layer → decryption **fails**.
  Swap two object files within a layer → **fails**. Flip one byte of the AAD, the nonce, the header, or
  the ciphertext → **fails**. A test that asserts decryption *succeeds* after tampering is a red alert.
- **Nonce uniqueness.** Encrypt the same plaintext N times; assert N distinct nonces and N distinct
  ciphertexts.
- **Lock semantics.** After lock: keys cleared, private index closed, editor buffers/previews/graph
  labels/AI context cleared, in-flight AI operations for that layer cancelled. Assert that a search, a
  graph query, and an AI context request all return **nothing** and reveal no existence.
- **Bridge hardening.** Oversize payload → `payload_too_large` and no side effect. Malformed envelope →
  `invalid_request`. Unknown field → rejected. Assert no bridge method can reach the filesystem, the
  shell, or the network. Assert production errors carry no traceback and no path.
- **CSP & navigation.** Assert the exact CSP string is applied. Assert external navigation is refused.
  Assert DevTools are absent from a release build.
- **Untrusted content.** Feed the malicious-Markdown corpus through the renderer path: no script
  execution, no remote resource fetch, no `javascript:` link. Feed the prompt-injection corpus through
  the composer: assert no plan escapes the selection, no policy change, no provider switch, no export.
- **Recovery & rewrap.** Password change rewraps only (objects untouched, LDK unchanged). Key rotation
  produces a new LDK and objects that no longer decrypt under the old one. Recovery key independently
  unwraps the same LDK.

Every one of these is a test that should *fail loudly* if someone quietly relaxes a security property.
That is the entire point of the directory.
