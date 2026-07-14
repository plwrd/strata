# ADR-0010: 2D/3D graph architecture

**Status:** Accepted, 2026-07-14

## Context

The spatial knowledge graph is Strata's centrepiece. It is not a decoration on a notes app; it is the
navigation model. It therefore has to be *good* — smooth, legible, and interactive — at sizes where
naive implementations collapse.

The naive implementation collapses fast. One `THREE.Mesh` per node and one `THREE.Line` per edge means
one draw call each; at 1,000 nodes and 3,000 edges that is 4,000 draw calls per frame, which will not
hold 60 fps on integrated graphics. Running a force-directed layout on the main thread means the UI
freezes for the several seconds the simulation takes to settle. Driving per-frame position updates
through React's reconciler means 60 renders per second of a component tree.

Targets we are designing to:

| Scale | Requirement |
| --- | --- |
| **1,000 nodes** | Smooth (60 fps) interaction, full labels, full edges. |
| **10,000 nodes** | Usable with clustering; edges bundled/aggregated; labels on demand. |
| **100,000 nodes** | Navigable via progressive aggregation — you never render 100k nodes; you render clusters and drill in. |

And two constraints that are easy to forget and expensive to retrofit:

- Some users have no usable GPU (VMs, remote desktops, old integrated graphics, Linux without drivers).
- Some users cannot use a 3D force-directed graph *at all* — vestibular disorders (motion), low vision,
  screen-reader users, keyboard-only users. A knowledge tool whose primary navigation is inaccessible is
  an inaccessible tool.

## Decision

### Rendering: Three.js + react-three-fiber, batched

- **Nodes: a single `InstancedMesh`.** One geometry, one material, one draw call for every node. Per-node
  position, scale, and colour go in instance attributes; selection/hover state is a per-instance
  attribute the shader reads, not a re-render. Picking is done with a GPU picking pass (render instance
  ids to an offscreen target) rather than raycasting a scene graph.
- **Edges: batched `LineSegments`** (a single `BufferGeometry` with all endpoints) for the plain case,
  and **instanced quads** (camera-facing billboarded segments) where we need width, since WebGL's native
  line width is effectively 1px everywhere. Again: one or two draw calls total, not one per edge.
- **Labels:** an SDF/atlas text approach (instanced, GPU-rendered) rather than one DOM element or one
  `Sprite` per node. Labels are culled aggressively — below a zoom threshold, and beyond a count
  threshold, only cluster labels and hovered/selected labels are drawn.
- **react-three-fiber** provides the declarative scene *structure* (what exists), but **per-frame updates
  bypass React entirely**: `useFrame` writes into instance buffers via refs and calls
  `needsUpdate = true`. React reconciles when the *graph model* changes, not when the *camera* or the
  *simulation* moves. This is a discipline, and a review rule: **if a `setState` runs at frame rate, it
  is a bug.**

### Layout: force-directed, in a Web Worker

- The simulation **never runs on the main thread**. It runs in a Web Worker, receives the graph
  (nodes/edges as flat typed arrays), runs the force simulation, and posts back positions as a
  `Float32Array` — ideally via a `SharedArrayBuffer` where the environment permits it, otherwise by
  transfer. The main thread's job each frame is: copy positions into the instance buffer, draw.
- **M1/M5: `d3-force`** (in the worker). It is well-understood, tunable, and fast enough for 1k–10k nodes.
- **Path to 100k:** the same worker interface, backed by a faster implementation — a WASM force
  simulation (or, if it must be, a Python worker computing layout and shipping positions over the bridge
  as a job, ADR-0003). The interface is fixed now (`postGraph(nodes, edges) → onPositions(Float32Array)`),
  so swapping the engine is not a rewrite. **The specific engine for 100k is deferred to M5's performance
  work; the seam is not.**
- Layout is **incremental and interruptible**: positions stream to the main thread as the simulation
  settles (so the user sees it organise, which is also good UX), and a new selection/filter reheats the
  simulation rather than restarting it.

### Scale strategy

- **LOD.** Distant nodes lose their labels, then their geometry detail, then merge into their cluster.
- **Viewport culling.** Off-screen nodes are excluded from the instance buffer draw range (the buffer is
  kept sorted by cluster so ranges are contiguous).
- **Cluster aggregation.** Above a node-count threshold, the graph renders *communities* (computed in
  Python by `GraphService` — Louvain/Leiden or a link-density heuristic) as single aggregate nodes with a
  size proportional to their membership, edges aggregated into weighted bundles. Drilling into a cluster
  expands it and collapses its siblings. This is how 100k becomes navigable: **you never render 100k
  nodes.** Progressive aggregation is not a fallback, it is the design.

### 2D mode is the same pipeline

2D is **not a separate implementation**. It is the same data, the same instance buffers, the same worker,
with an **orthographic camera** and a Z-locked layout. This is a deliberate constraint: two graph
implementations would diverge within a month, and the 2D view is the one most users will live in.

A **Canvas2D fallback** exists for machines where WebGL is unavailable or broken (it is checked at startup,
not assumed). It renders the same node/edge model at reduced fidelity and reduced scale limits, and it says
so.

### Low-GPU and reduced-motion modes

- **Low-GPU mode** (auto-detected from the WebGL renderer string / a startup benchmark, and manually
  overridable): lower instance counts, no antialiasing, no bloom/glow, static layout (compute once,
  freeze), fewer labels.
- **Reduced-motion mode** (honours `prefers-reduced-motion`, and is independently settable): **no
  animated simulation** — the layout is computed and then presented in its final state; no camera easing;
  no auto-rotation; transitions are instant. This is a genuine accessibility requirement, not a
  preference toggle: an animated force-directed graph is nauseating for a real population of users.

### The accessible alternative view is first-class

There is a **tree/list view of the same graph model** — nodes, their links, their backlinks, their
clusters — that is fully keyboard-navigable and screen-reader-legible, with the same filters, the same
search integration, and the same selection model feeding the AI Context Composer.

It is **not** a degraded fallback shown to people who "can't use the real thing". It is a peer view, in
the same view switcher as 2D and 3D, and **it is a shipping requirement for M5, not a follow-up**. A user
who navigates entirely by keyboard and screen reader must be able to do everything the graph does. If a
feature only exists in the 3D view, that feature is not done.

### Extraction in Python, rendering in TypeScript. Never mixed.

`GraphService` (Python) owns: parsing wiki-links and Markdown links, resolving them to note ids,
computing backlinks, detecting orphans, computing communities/clusters, computing centrality and
graph-proximity scores (which ADR-0007's ranking consumes), and applying filters. It emits a
**GraphModel** — flat arrays of nodes and edges with ids, types, weights, and cluster assignments — over
the bridge.

The frontend owns: layout, camera, picking, instancing, LOD, and interaction. It does **not** parse
Markdown, does **not** resolve links, and does **not** decide what a node *is*.

The boundary is load-bearing:
- Link extraction has to agree with search and with export (ADR-0009's Graph Summary), which are Python.
  Two implementations would disagree, and the disagreement would surface as "the graph shows a link that
  search doesn't".
- Private-layer content is decrypted in Python. Graph extraction over a private layer must happen there;
  the renderer only ever receives a model for layers it is allowed to see (a locked layer contributes no
  nodes and no edges — ADR-0007 §7).
- The graph model is bounded and paginated for the 1 MiB envelope cap (ADR-0003): large graphs arrive as
  a job with `partial` chunks, not as one payload.

## Consequences

### Positive

- Instancing plus batching means the draw-call count is **O(1) in the number of nodes**, which is the only
  way the targets are reachable. 10k nodes is two or three draw calls, not 13,000.
- The worker keeps the main thread free: the UI stays responsive *while the graph is settling*, which is
  most of the time the user spends looking at it.
- One data pipeline for 2D and 3D means one set of bugs, one set of filters, one selection model.
- Doing extraction in Python means the graph, search ranking, and export all agree about what a link is —
  by construction, because there is one implementation.
- The accessible view being a peer view (and a shipping requirement) means accessibility is designed in,
  where it costs a data-model constraint, rather than bolted on later, where it costs a rewrite.
- Cluster aggregation is what makes 100k *meaningful* rather than merely renderable — a hairball of 100k
  points conveys nothing even at 60 fps.

### Negative

- **Instanced rendering is materially harder to write and debug** than a scene graph of meshes. Picking is
  a GPU pass, not a raycast. Per-node state is a shader attribute, not a prop. Hover highlighting means
  writing into a buffer. New contributors will find this code unfamiliar, and the temptation to "just add
  a Mesh for this one thing" will be constant and must be resisted in review.
- **The React/Three boundary is a discipline, not a guarantee.** Nothing in react-three-fiber stops
  someone putting node positions in React state. It will happen, it will tank the frame rate, and it will
  be found by profiling rather than by the type system. Mitigation: a frame-time budget assertion in dev
  builds that warns when React renders during a settling simulation.
- **Force-directed layouts are non-deterministic and unstable.** The same graph laid out twice looks
  different; adding one node can reorganise everything. Users form spatial memory of their graph and will
  (rightly) be annoyed when it moves. Mitigation: seeded RNG, position persistence per layer (positions
  are stored — encrypted, as an object, for private layers), and "reheat" rather than "restart" on
  change. This does not fully solve it, and it is a known product-level irritation.
- **The 100k target is not met by M5** and we should be honest about that: M5 delivers 1k smooth and 10k
  clustered with d3-force. 100k needs the faster layout engine and the aggregation pipeline to be mature,
  and it is realistically an M11 performance-hardening outcome.
- **SDF text is a chunk of work** and text rendering in WebGL is never as good as the DOM's. Labels will
  be slightly worse than a DOM-based graph's, at any zoom.
- `SharedArrayBuffer` requires cross-origin isolation. Our custom `strata://` scheme (ADR-0003) makes this
  achievable, but it is a constraint on the scheme's headers (`COOP`/`COEP`) that must be got right —
  and if it cannot be, we fall back to transferring `ArrayBuffer`s, which costs a copy per frame update
  (acceptable at 10k, less so at 100k).

### Neutral

- Cluster computation (Louvain/Leiden) is O(E log V)-ish and runs in Python on a worker as a job. It is
  recomputed on graph change, debounced. For very large graphs it is cached alongside the layer.
- Node positions for a private layer are private content (a graph layout is a picture of your knowledge
  structure) and are persisted only inside encrypted objects, like everything else (ADR-0004).
- The GPU picking pass costs one extra render target and one read-back. The read-back is asynchronous
  (`readPixelsAsync` / a fence) so it does not stall the pipeline; a one-frame-stale hover is
  imperceptible.
- Edge bundling for the 10k case is a visual-quality feature, not a performance one — the batched draw
  is already cheap; the *legibility* is the problem.

## Alternatives considered

### One `Mesh` per node, one `Line` per edge (the naive Three.js scene graph)

The way every tutorial does it.

**Why rejected:** it does not reach 1,000 nodes at 60 fps on the hardware our users have. Draw calls are
the bottleneck and this design maximises them. Rejected on measurement, not taste.

### A ready-made library (`react-force-graph`, `3d-force-graph`, Sigma.js, Cytoscape)

Excellent libraries; would get us to a working graph in a day.

**Why rejected:** they are built for the 1k-node case and they own the data pipeline. Getting them to do
progressive cluster aggregation to 100k, GPU picking, our LOD policy, our accessible peer view, and our
"positions persist per layer, encrypted" requirement means fighting the library at every point. `d3-force`
(the layout algorithm) we *do* use — it is the part worth borrowing. The renderer is the part we need to
own. (Sigma.js is genuinely close for the 2D case, and if 3D were dropped it would deserve another look.)

### Render the graph in Python/Qt (Qt Quick 3D, or a QOpenGLWidget beside the web view)

No WebGL, no worker, native performance.

**Why rejected:** ADR-0002 — it means building an instanced scene graph, a picking system, and a text
renderer from primitives, and it means the graph lives in a different window layer than the rest of the
UI (a native widget over a web view is a compositing and hit-testing nightmare, and every panel/overlay
interaction breaks at the seam).

### GPU-side layout (compute the force simulation in a shader)

The fastest possible layout; genuinely how you would do 100k.

**Why rejected for now, not forever:** WebGL2 has no compute shaders, so it means transform-feedback
gymnastics; WebGPU has compute but its availability inside the Qt-bundled Chromium is not something we
want to bet M5 on. The worker interface is deliberately engine-agnostic so this can land later without
touching the renderer. This is the most likely eventual answer for the 100k target.

### Skip 3D; ship 2D only

Cheaper, and 2D is what most users use anyway.

**Why rejected:** the spatial 3D graph is a differentiator and a genuine navigation aid at scale (three
dimensions of separation beat two for dense clusters). And since 2D is the same pipeline with an
orthographic camera, 3D costs us the camera controls and the LOD tuning — not a second implementation.
The honest risk is that we over-invest in 3D polish; the mitigation is that 2D is the default view.

## Revisit when

- Frame time exceeds **16 ms at 10k clustered nodes** on the reference low-GPU machine — the LOD and
  aggregation thresholds are wrong, or d3-force has run out of road.
- The 100k target becomes real (a user with a 100k-note corpus) — that triggers the WASM/WebGPU layout
  spike.
- WebGPU is reliably available in the Qt-bundled Chromium across all three platforms — GPU-side layout
  and compute-based aggregation become straightforwardly available.
- Users report layout instability ("my graph keeps rearranging") as a top complaint — position
  persistence needs to become stronger (pinned nodes, stable seeds per node id).
- The accessible tree view is measurably unused *by the people it is for* — that would mean it is not
  actually good, and the answer is to fix it, not to drop it.
