# ADR-0011: Python version target (`requires-python >= 3.10`)

**Status:** Accepted, 2026-07-14

## Context

The product brief specifies **Python 3.12+**. That is a reasonable ask: 3.12 brings better error
messages, `TaskGroup`/`ExceptionGroup` and `except*`, PEP 695 type-parameter syntax, per-interpreter
GIL groundwork, and meaningful performance gains over 3.10.

The development machine on which M0/M1 are being built has exactly one CPython available:
**CPython 3.10.11**. Not "3.10 is the default" — 3.10 is what is installed, and installing another
interpreter is not currently within our control on that machine (managed environment).

We therefore have a choice between three unpleasant options: (a) target 3.12 and be unable to run our
own code on the machine that writes it; (b) target 3.12 and paper over it with a second toolchain that
only works in CI, meaning every local test run is a lie; or (c) target the interpreter we actually have,
and be explicit about what that costs.

This is not an architectural preference. It is a constraint, and this ADR exists so that nobody in six
months reads `requires-python = ">=3.10"` and assumes it was a considered enthusiasm for older Pythons.

## Decision

`pyproject.toml` declares:

```toml
requires-python = ">=3.10"
```

We target **CPython 3.10 as the floor** and we **do not use 3.11- or 3.12-only syntax or stdlib APIs**.
Specifically prohibited in the codebase:

- **PEP 695 generics** (3.12): no `class Foo[T]:`, no `def f[T](x: T) -> T:`, no `type Alias = …`.
  Use `TypeVar` and `Generic` from `typing`, and `TypeAlias` annotations.
- **`except*` / `ExceptionGroup`** (3.11): no exception groups. Use ordinary exceptions; where we need
  to aggregate failures (e.g. a fan-out search across layers), aggregate them explicitly into a result
  object.
- **`asyncio.TaskGroup`** (3.11): use `asyncio.gather` with explicit cancellation handling, or (more
  usually) our `JobBridge` worker model (ADR-0003), which is where concurrency actually lives.
- **`typing.Self`** (3.11): use a `TypeVar` bound to the class, or the string form.
- **`tomllib`** (3.11): use `tomli` (it is the same code) if we need TOML at runtime.
- **`enum.StrEnum`** (3.11): use `class Foo(str, Enum)`.
- **`datetime.UTC`** alias (3.11): use `datetime.timezone.utc`.

And required:

- **`from __future__ import annotations` at the top of every module.** This gives us PEP 604 unions
  (`int | None`), PEP 585 builtin generics (`list[str]`, `dict[str, int]`) and forward references in
  annotations, on 3.10, uniformly. It is enforced by lint (`ruff`'s `FA102`/`I002`), not by memory.
  Note the one real consequence: annotations become strings, so anything doing runtime introspection —
  **Pydantic**, which is everywhere in this codebase (ADR-0003) — must be able to resolve them. Pydantic
  v2 does this correctly, but it means type aliases used in models must be importable at module scope,
  not defined inside a function. This has bitten every project that does this and it will bite us.

**CI runs the matrix `3.10 / 3.11 / 3.12`.** 3.10 is the correctness gate (it is what the code must run
on); 3.11 and 3.12 are the forward-compatibility gate (they catch deprecations and behavioural drift
early, and they are what most users' machines will have if they ever run from source). A failure on any
of the three is a red build.

Runtime environments:

- **Development:** CPython 3.10.11 on the build machine.
- **Shipped app:** whatever CPython PyInstaller bundles (ADR-0001) — the user never provides an
  interpreter, so the *user-facing* Python version is our choice at packaging time and can be **3.12**
  independently of this floor. The floor constrains our *source*, not our *shipped runtime*.

## Consequences

### Positive

- The code runs on the machine it is written on. Local test runs mean something. This is not a small
  thing: a project whose test suite only passes in CI is a project whose developers stop running tests.
- `>=3.10` is a **wider** support window, which matters if we ever want the core (the crypto, storage,
  search and export packages, all of which are pure Python and Qt-free — see ADR-0001) to be usable as a
  library, or if a contributor is on an older distro.
- The prohibited features are, honestly, mostly syntax sugar. PEP 695 generics are nicer than `TypeVar`;
  they are not *load-bearing*. `except*` is genuinely useful for exception groups, and we do not have
  exception groups because our concurrency lives in a job/worker model, not in `TaskGroup` fan-outs.
- Because we can package a *newer* interpreter than we target, users are not stuck on 3.10's
  performance. We get 3.12's speed in the shipped binary and 3.10's compatibility in the source.

### Negative

- **We are constrained by a machine, not by a design.** This is the honest summary and it should sting a
  little. It is an accepted, documented, *temporary* constraint with a named exit (below), not a
  principle.
- **`from __future__ import annotations` + Pydantic is a known sharp edge.** Stringified annotations must
  be resolvable at model-build time; a `TypeAlias` defined locally, or a type imported under
  `if TYPE_CHECKING:` and used in a Pydantic field, produces a runtime `NameError` at import — far from
  the code that caused it. Every bridge model (ADR-0003) is a Pydantic model, so this surface is large.
  Mitigation: types used in Pydantic models are imported unconditionally at module scope; a test imports
  every bridge module and constructs every model, so a broken annotation fails the build rather than a
  user's unlock.
- We forgo `TaskGroup`'s structured cancellation, which is the best thing in modern asyncio. Our job
  model has to do that work by hand (cooperative cancellation flags, explicit cleanup), and hand-rolled
  cancellation is where concurrency bugs live. This is the most substantive functional cost.
- 3.10 is older, so we are behind on error-message quality (3.11's tracebacks with column pointers are a
  real productivity gain we do not have locally) and on performance during development.
- **Python 3.10 reaches end-of-life in October 2026** — three months from this ADR. Security patches for
  the *interpreter we develop on* stop. This does not endanger users (we ship our own interpreter), but
  it is a hard deadline on the exit condition below, and it is the single strongest argument for
  resolving the machine constraint sooner rather than later.
- A three-version CI matrix is 3× the CI minutes and 3× the flaky-test surface.

### Neutral

- The floor is on *our source*, not on our dependencies. PySide6 6.8, PyNaCl, argon2-cffi, Pydantic v2 and
  `pycrdt` all support 3.10, so nothing is blocked. If a dependency we need drops 3.10, that is an
  immediate trigger to revisit (see below) — it is not a reason to vendor or pin an old version.
- `ruff` is configured with `target-version = "py310"`, so it will flag 3.11+ syntax as an error rather
  than leaving it to a CI failure on the 3.10 leg. `mypy --python-version 3.10` likewise. The prohibition
  is enforced by tooling, which is the only kind of prohibition that works.
- Raising the floor later is a *non-breaking* change for users (they never see our Python) and a small
  mechanical change for us (drop the `__future__` import, adopt PEP 695 where it helps). There is no
  migration cost worth worrying about — which is precisely why accepting the constraint now is cheap.

## Alternatives considered

### Target 3.12 as the brief asks, and develop against it anyway

Install 3.12 on the dev machine; if that fails, use a container/WSL/venv sourced from a downloaded
build.

**Why rejected:** the machine is managed and 3.10.11 is what exists. A container or an alternate
interpreter that only works in some environments produces exactly the failure mode we most want to
avoid: the developer's local run diverges from CI, so the local run is not trusted, so it is not run.
Worse, with PySide6 + QtWebEngine in the loop, a containerised dev environment on Windows is not a
realistic way to run the actual app (GPU, WebEngine, native dialogs). We would end up unable to *run
Strata* on the machine building Strata. That is not a trade; it is a self-inflicted wound.

### Target 3.12, and rely on CI to catch anything 3.10 cannot run

Write 3.12 code, let CI be the only truth.

**Why rejected:** the code would not run locally at all — not the tests, not the app. This is strictly
worse than the option above.

### Target 3.12 syntactically but backport via `typing_extensions` and compatibility shims

Use `typing_extensions.Self`, `exceptiongroup`, `taskgroup` backports, and write near-3.12 code.

**Why rejected:** partially adopted, actually — `typing_extensions` is a dependency and we use it freely
for typing constructs, because that is what it is *for* and it is cheap. What we reject is the *runtime*
backports (`exceptiongroup`, `taskgroup`): they are shims around interpreter-level semantics, they behave
subtly differently, and adopting them means our concurrency semantics depend on a backport's fidelity. We
would rather have a plainer concurrency model that is the same on every version. PEP 695 syntax cannot be
backported at all — it is a parser change — so the syntactic prohibition stands regardless.

### Pin to 3.10 permanently (`>=3.10,<3.11`)

Be honest about the floor and just stay there.

**Why rejected:** it turns a temporary constraint into a permanent one, and 3.10 goes EOL in October
2026. `>=3.10` with a 3.10/3.11/3.12 CI matrix keeps us honest about the floor *and* forward-compatible,
which is what lets us raise the floor the moment the constraint lifts, with a one-line change and a green
build already proving it works.

## Revisit when

- **The build/development machines standardise on 3.12.** This is the exit condition. It is expected to be
  the trigger, and when it fires the change is: bump `requires-python` to `>=3.12`, bump `ruff`'s
  `target-version`, drop `from __future__ import annotations`, adopt PEP 695 where it improves clarity,
  and cut the CI matrix to `3.12/3.13`.
- **Before October 2026**, regardless — Python 3.10's end of life. Developing on an EOL interpreter is not
  a state we should be in past that date, and this deadline should be treated as real.
- Any dependency we need (PySide6, `pycrdt`, Pydantic, a provider SDK) drops 3.10 support. That forces the
  issue immediately, and it is a good reason to move rather than to pin.
- The absence of `TaskGroup`/`except*` is implicated in a real concurrency bug in the job system — that
  would be evidence that the hand-rolled cancellation is not carrying its weight.
