# ADR-0003: QWebChannel bridge protocol and the `strata://` scheme

**Status:** Accepted, 2026-07-14

## Context

ADR-0001 puts the keys, the filesystem, the indexes and the network egress in Python. ADR-0002 puts the
UI in a Chromium renderer. Every capability the user exercises therefore crosses a process boundary,
and **that boundary is the application's primary security perimeter**: it is the one place where a
compromised or buggy renderer (a malicious Markdown paste, a prompt-injected AI response rendered into
the DOM, an XSS in a third-party frontend dependency) can try to reach the user's decrypted workspace.

The design problem is not "how do we call Python from JS" — QWebChannel answers that. The problem is:

- How do we make the reachable surface **small, enumerable, and validated**?
- How do we keep long-running work (unlock/Argon2id, index rebuild, embedding, AI streaming, export)
  from blocking the UI thread while still being cancellable?
- What **origin** does the frontend run at, and what does that origin imply for CSP, storage isolation,
  and CORS?
- What may an error message contain?

## Decision

### 1. Feature-scoped bridge objects, not one god object

Exactly these `QObject`s are registered on the `QWebChannel`:

| Object | Responsibility |
| --- | --- |
| `WorkspaceBridge` | Open/close workspace, workspace metadata, recent workspaces. |
| `LayerBridge` | Create/list layers, lock/unlock private layers, layer settings and policy. |
| `NotesBridge` | Note CRUD, tree/folder operations, attachments, links. |
| `GraphBridge` | Graph model queries, filters, clustering parameters. |
| `SearchBridge` | Query, facets, autocomplete, "why this matched" explanations. |
| `AIComposerBridge` | Context selection, budget estimation, preview. |
| `ExportBridge` | Export execution (ADR-0009). |
| `CollaborationBridge` | Session join/leave, peer presence, encrypted update transport (M9). |
| `SettingsBridge` | Application and per-layer settings, keychain-backed credential *references*. |
| `SnapshotBridge` | Snapshots, history, restore. |
| `JobBridge` | Job status query, job cancellation, and the `jobEvent` signal. |

Each object exposes only the slots its feature needs. There is no generic `invoke(method, args)`, no
`readFile`, no `exec`. The complete capability surface of the renderer is the union of these slots, and
it can be printed on one page and reviewed.

### 2. One slot shape

Every slot is:

```python
@Slot(str, result=str)
def some_operation(self, request_json: str) -> str: ...
```

**Request envelope**

```json
{ "v": 1, "requestId": "0f0a…-uuid", "payload": { } }
```

**Success response**

```json
{ "v": 1, "requestId": "0f0a…-uuid", "ok": true, "data": { } }
```

**Error response**

```json
{ "v": 1, "requestId": "0f0a…-uuid", "ok": false,
  "error": { "code": "not_found", "message": "…", "retryable": false, "details": { } } }
```

- `v` is the envelope version. A request with an unknown `v` is rejected with `unsupported`.
- `requestId` is a client-generated UUIDv4. It is echoed on every response and on every job event, and
  it is the only correlation key. The frontend's query layer keys pending promises by it.
- Strings in, strings out. JSON is the only serialisation. We do not pass Qt variants, QObjects, or
  Python objects across the channel.

### 3. Closed error-code enum

```
invalid_request | payload_too_large | not_found | permission_denied |
layer_locked | conflict | unsupported | cancelled | provider_error | internal
```

This list is **closed**. Adding a code is an API change requiring a frontend update; it is not something
a service author does casually. The frontend `switch`es on it exhaustively (TypeScript union), so a new
code cannot be silently ignored. `retryable` tells the UI whether an automatic retry is even meaningful;
`details` is a small, code-specific, non-sensitive map (e.g. `{"field": "title"}` for
`invalid_request`).

### 4. Validation on both directions

Every payload is a **Pydantic** model. The request is parsed and validated before any service code runs;
a validation failure returns `invalid_request` with the offending field path and never reaches the
service. The **response is validated too**, before it is serialised. Response validation is not
paranoia-for-its-own-sake: it is what stops a service bug from leaking a filesystem path, a key, or an
internal object id into the renderer, and it is what keeps the generated TypeScript types honest.

**Payload cap: 1 MiB.** Any request whose JSON exceeds it is rejected with `payload_too_large` before
parsing. Large data does not travel as a JSON payload — attachments are transferred by id through the
`strata://` scheme handler, and exports are written to disk by Python.

### 5. Opaque ids only

The renderer never sees a filesystem path, a private object id (ADR-0004), a key, an absolute URL to a
provider, or an API key. It sees opaque, per-session-stable ids. Translation happens in the bridge
layer.

### 6. No stack traces or paths in production errors

An unexpected exception is logged (with full detail, locally) and returned as
`{"code":"internal","message":"An internal error occurred.","retryable":false,"details":{"traceId":"…"}}`.
The `traceId` correlates to the local log. In dev builds (`STRATA_DEV=1`) the message may carry the
exception text; that path is compiled/gated out of release.

### 7. Async work returns a job id; progress arrives on a signal

Any operation that can take longer than ~100 ms — unlock (Argon2id), index rebuild, embedding, AI
streaming, export, snapshot, large import — **returns immediately**:

```json
{ "v": 1, "requestId": "…", "ok": true, "data": { "jobId": "job_7f3a…", "kind": "layer.unlock" } }
```

The work runs on a `QThreadPool` worker or a `multiprocessing` worker (never on the Qt main thread —
see the GIL discussion in ADR-0001). Progress and completion are pushed over one signal:

```python
class JobBridge(QObject):
    jobEvent = Signal(str)  # JSON: {"v":1,"jobId":…,"requestId":…,"type":…,"data":{…}}
```

`type` is one of `started | progress | partial | completed | failed | cancelled`. `partial` carries
stream chunks (AI tokens, search results as they land). `JobBridge.cancel(jobId)` requests cooperative
cancellation; a cancelled job ends with `cancelled`, and any in-flight bridge call that was waiting on
it resolves with the `cancelled` error code.

This gives us exactly one async mechanism for the whole app. There is no second one.

### 8. `strata://` custom scheme, registered before `QApplication`

The frontend is served from a custom scheme registered via `QWebEngineUrlScheme` with:

- `SecureScheme` — so it counts as a secure context (workers, WASM, `crypto.subtle`, `OffscreenCanvas`
  all require this),
- `LocalScheme` / `LocalAccessAllowed` — controlled local access semantics,
- CORS-enabled — so that `fetch()` of same-origin app assets works predictably,
- and it is **registered before the `QApplication` is constructed**, which Qt requires.

A `QWebEngineUrlSchemeHandler` serves the bundled `frontend/dist` assets and, separately, attachment
bytes by opaque id (decrypting on the fly for an unlocked private layer, refusing for a locked one).

A **strict CSP** is served with every document response:

```
default-src 'none';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:;
font-src 'self';
connect-src 'self';
worker-src 'self' blob:;
frame-ancestors 'none';
base-uri 'none';
form-action 'none';
```

(`style-src 'unsafe-inline'` is a concession to CodeMirror/R3F inline styles and is tracked as debt;
`connect-src 'self'` means the renderer **cannot reach the network at all** — every provider call goes
through Python, which is the point.)

## Consequences

### Positive

- The attack surface is a finite, reviewable list of validated slots. A security review can enumerate it
  completely.
- The renderer has no network (`connect-src 'self'`), no filesystem, no keys, and no plaintext of a
  locked layer. A prompt-injection payload rendered into a note cannot exfiltrate anything, because
  there is nowhere for it to send data to and nothing sensitive for it to read.
- One async mechanism means one cancellation story, one progress story, one place to reason about
  backpressure.
- Bidirectional Pydantic validation makes the wire format the single source of truth; the TS types are
  generated from it.
- A secure custom scheme gives us a real origin: real CSP, real storage isolation, secure-context APIs,
  no `file://` weirdness.

### Negative

- **Boilerplate.** Every operation costs an envelope, a Pydantic request model, a Pydantic response
  model, a slot, a generated TS type, and a query-layer call. We accept this and reduce it with a
  decorator (`@bridge_slot`) that handles parse → validate → dispatch → validate → serialise and the
  error mapping, so a service author writes only the typed body.
- **The 1 MiB cap is a real constraint** on API design. Anything that might grow (a full graph model, a
  large search result set, an export preview) must be paginated or streamed as job `partial` events. This
  has to be designed for, not discovered at runtime.
- Every long operation is a job, which means the frontend needs job-lifecycle UI (progress, cancel,
  failure) for a lot of surfaces. That is more work than `await bridge.doThing()`.
- QWebChannel's transport is `qwebchannel.js` over an internal IPC; it is synchronous-looking but has
  real serialisation cost. Chatty per-keystroke slots (naive search-as-you-type) will be felt. Debounce
  and batch at the frontend query layer.
- Custom-scheme registration is process-global and must happen before `QApplication` — a fragile ordering
  constraint that has bitten every project that has tried it. It is asserted at startup.

### Neutral

- `requestId` is generated by the *untrusted* side. It is therefore treated purely as a correlation
  token, never as a capability or a nonce. Python does not trust it for anything; a duplicate or
  attacker-chosen id can only confuse the renderer's own bookkeeping.
- Because responses are validated, a service returning a field the schema does not know about is an
  `internal` error rather than a leak. Expect this to catch real bugs during M2–M4.
- The `strata://` origin is a single origin; there is no cross-origin isolation *within* the app. Any
  code we ship in the bundle has the full bridge surface. Third-party frontend dependencies are
  therefore in the trust boundary and their supply chain is a real risk (tracked in THREAT_MODEL, with
  lockfile pinning and dependency review as the mitigation).

## Alternatives considered

### One god-object bridge (`StrataBridge` with a generic `invoke`)

A single QObject, a single `invoke(method: str, args_json: str)` slot, dispatching to a registry.

**Why rejected:** it makes the attack surface unbounded and unreviewable — "what can the renderer do?"
becomes "whatever is in the registry today", which grows silently. Feature-scoped objects force a
deliberate act to widen the perimeter, and they let us reason per-feature (e.g. `CollaborationBridge`
is entirely absent until M9, so it is not a surface at all before then).

### Local WebSocket or HTTP RPC server (renderer talks to `127.0.0.1:PORT`)

The pattern most Electron-adjacent local apps use.

**Why rejected:** it opens a listening socket. On a shared or multi-user machine, any local process —
including a browser tab via DNS-rebinding or a plain `fetch` if we get CORS wrong — can now attempt to
talk to the process holding the user's decrypted notes. Defending it properly means per-session tokens,
origin checks, and CORS policy that must be perfect forever. QWebChannel's transport is in-process and
has no port. We do not open a port; that is a security property we get for free by not doing this.

### Injecting Python objects directly into JS (`page().setWebChannel` with rich objects, properties, direct method exposure)

Expose service objects with their methods and properties as-is.

**Why rejected:** it exports the *implementation* rather than a designed API. Every public method
becomes reachable, refactors silently change the wire contract, argument types are whatever Qt's
variant conversion decides, and there is no place to validate, authorise, cap size, or map errors. The
JSON-envelope-over-`@Slot(str, result=str)` shape is deliberately dumb so that the choke point is
explicit.

### `file://` origin for the frontend

Simplest possible: point the view at `dist/index.html`.

**Why rejected:** `file://` is an opaque/weak origin in Chromium. It is not a secure context (breaking
workers/WASM guarantees we need for graph layout), its CSP and storage semantics are inconsistent, and
`file://`-to-`file://` read access rules are a historical source of local-file-disclosure bugs. A
registered secure custom scheme costs one function call and removes the whole class.

### Free-form error strings instead of a closed enum

**Why rejected:** the frontend then either pattern-matches on English prose or treats every failure the
same. A closed enum plus `retryable` is what lets the UI distinguish "your layer is locked, here is the
unlock button" from "the provider is down, retry" from "this is our bug, here is a trace id".

## Revisit when

- The 1 MiB cap forces contortions in more than a couple of APIs — that is a signal that a proper
  streaming channel (a `strata://` streaming endpoint, or a shared-memory/ArrayBuffer path) is needed,
  not a bigger cap.
- QWebChannel serialisation shows up as a measurable frame-time cost in the graph or editor hot paths.
- M9 collaboration needs sustained high-frequency updates; if `jobEvent` becomes a bottleneck for CRDT
  update fan-out, a dedicated binary transport for encrypted CRDT payloads should be considered — but
  it must not open a port.
- We ever need more than the eleven bridge objects: that is the moment to ask whether the perimeter is
  still coherent.
