# ADR-0002: Qt WebEngine + React/TypeScript for the UI

**Status:** Accepted, 2026-07-14

## Context

ADR-0001 fixes the host as Python + Qt 6. That leaves the rendering surface open. Strata's UI has three
demanding surfaces:

1. **A 2D/3D knowledge graph** with 1k nodes smooth, 10k with clustering, and 100k via progressive
   aggregation (ADR-0010). This requires WebGL with instanced rendering, and a worker thread for
   layout.
2. **A Markdown editor** with wiki-links, backlinks, decorations, per-layer read-only states, and (in
   M9) collaborative cursors and CRDT-bound text. CodeMirror 6 is the only editor that does all of this
   well and has a real extension API.
3. **A conventional but dense application shell** — command palette, panels, virtualised lists,
   drag-and-drop, structured table/board/timeline views (M10).

The application must work **fully offline**. There is no Strata server. Nothing may be fetched from a
network at runtime for the UI to function.

## Decision

The UI is a **React 18 + TypeScript (strict) single-page application, built with Vite, bundled to
`frontend/dist`, and loaded into a `QWebEngineView` from a bundled local resource** over the custom
`strata://` scheme (ADR-0003). The build output is embedded in the PyInstaller bundle; there is no dev
server, CDN, or remote origin in a shipped build.

Concretely:

- **React 18** with function components and hooks. Concurrent features (`useDeferredValue`,
  `startTransition`) are used deliberately for search-as-you-type and graph filtering, not
  reflexively.
- **TypeScript `strict: true`**, plus `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`. The
  bridge envelope types (ADR-0003) are generated from the Python Pydantic models so the two sides
  cannot drift silently; a type mismatch is a build failure, not a runtime `undefined`.
- **Vite** for dev (HMR against a running Python host that points the WebEngine view at the dev server
  when `STRATA_DEV=1`) and for the production bundle (Rollup, `base: './'`, no code-splitting across
  origins, no dynamic remote imports).
- **CodeMirror 6** for the editor, **Three.js + react-three-fiber** for the graph (ADR-0010).
- State: local component state plus a small store (Zustand-style) for cross-panel state. No Redux
  ceremony. Server state — i.e. anything that comes over the bridge — goes through a single query layer
  with request-id correlation, so cancellation and job events (ADR-0003) have exactly one place to
  land.
- The frontend is **a rendering and interaction layer only**. It holds no keys, no plaintext of locked
  layers, no filesystem paths, and no provider credentials. It cannot construct a filesystem operation;
  it can only send a validated envelope to a named bridge slot.

## Consequences

### Positive

- We inherit the two ecosystems the product depends on (CodeMirror 6, Three.js/R3F) rather than
  rebuilding them. This is the entire justification for carrying Chromium.
- Chromium's renderer is a real sandbox. Because we deliberately keep secrets out of it, a renderer
  compromise costs the attacker the UI, not the workspace.
- The UI is testable with the standard web toolchain — Vitest, Testing Library, Playwright against the
  Vite dev build with a mocked bridge. Most UI tests never start Qt.
- Hot reload during development is genuinely fast, which matters for the amount of UI in M2, M5, M6 and
  M10.
- WebGL, workers, `OffscreenCanvas`, WASM are all available and consistent across platforms, because
  we ship the renderer rather than borrowing the system's.

### Negative

- QtWebEngine is the dominant cost in the bundle (ADR-0001) and the dominant source of packaging pain.
- Chromium's version is Qt's to choose. Renderer security patches arrive on Qt's cadence. We mitigate by
  pinning PySide6 6.8.x and tracking Qt security advisories as a release-blocking input, and by keeping
  the renderer free of secrets.
- Two languages, two type systems, one protocol between them. Without generated types this becomes a
  bug farm; hence the generation requirement above, which is itself a build-system cost.
- Web-tech UI on the desktop has an inherent "not quite native" feel unless actively fought: we own
  focus rings, keyboard navigation, context menus, scroll physics and drag behaviour ourselves. Native
  menus and file dialogs come from Qt, not from the web layer, which means some UI is split across the
  boundary.
- React's rendering model is a poor fit for a 60 fps 3D scene. We solve this by keeping the Three.js
  scene out of React's reconciliation for per-frame updates (imperative refs, `useFrame`), which is a
  discipline the team must hold (ADR-0010).

### Neutral

- No SSR, no hydration, no router-with-history in the web sense. The app is a single document with an
  in-memory view stack. URLs are not part of the product surface; deep links (if any) go through Qt.
- `localStorage`/`IndexedDB` in the renderer are used **only** for non-sensitive UI preferences
  (panel sizes, last view). Anything durable and meaningful is persisted by Python. This must be
  enforced in review; the CSP and the `strata://` origin do not enforce it for us.
- The dev-mode path (loading `http://localhost:5173` in the WebEngine view) is a different origin with
  different CSP behaviour than production. Dev-only code paths are gated behind `STRATA_DEV` and are
  compiled out of release builds; the release build asserts at startup that its page origin is
  `strata://`.

## Alternatives considered

### Qt Quick / QML

Native Qt declarative UI, no Chromium, small bundle, one toolchain.

**Why rejected:** there is no CodeMirror and no Three.js/R3F in QML. The Markdown editor and the graph
are the two hardest, most differentiating surfaces of the product, and QML would mean building both
from primitives (Qt Quick 3D for the graph; a `TextArea` plus a hand-rolled decoration/lint/link system
for the editor). That is a multi-quarter detour to arrive at a worse result. QML remains the right
choice for a Qt app whose hard parts are *not* a code editor and a WebGL graph; ours are.

### Server-side rendering / templated HTML from Python

Render HTML in Python, minimal JS.

**Why rejected:** the graph and editor are inherently client-side, stateful, 60 fps surfaces. SSR buys
nothing (there is no server, no first-paint-over-network problem, no SEO) and costs us the entire
component model.

### Remote-hosted frontend (load the UI from `https://app.strata.…`)

Ship a thin shell; serve the UI from a CDN so it can be updated independently.

**Why rejected:** it destroys the product's core promise. The app would not start offline, every launch
would phone home, and the code that has access to the bridge — and therefore to the user's decrypted
notes — would be whatever a server chose to send today. That is a remote-code-execution channel into a
process that holds the user's keys, which is precisely the thing the local-first, encrypted design
exists to prevent. Non-negotiable rejection.

### Vanilla TS / Svelte / Solid instead of React

Smaller runtimes, less reconciliation overhead.

**Why rejected:** not on correctness grounds — any of them would work. React wins on ecosystem
adjacency (react-three-fiber is the reason; it is by far the most mature declarative Three.js binding)
and on hiring/velocity. We accept React's runtime cost because the frame-critical path deliberately
bypasses React anyway.

## Revisit when

- react-three-fiber stops being maintained, or a comparably mature binding appears for a lighter
  framework — the graph is the only hard dependency on React specifically.
- The Chromium version shipped by Qt becomes a release blocker (see ADR-0001's trigger).
- We find ourselves writing more than ~20% of the UI in Qt/QML for native-feel reasons; at that point
  the split is not paying for itself and the boundary should be redrawn.
