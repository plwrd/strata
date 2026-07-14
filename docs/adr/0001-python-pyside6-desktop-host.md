# ADR-0001: Python + PySide6 as the desktop host

**Status:** Accepted, 2026-07-14

## Context

Strata is a local-first desktop application. Everything of consequence happens on the user's machine:
key derivation and AEAD encryption of private layers, filesystem I/O over an object store, full-text
and vector indexing, graph extraction, AI provider calls (including local model servers), and
packaging into a signed installer. There is no Strata server; a collaboration relay (M9) is a dumb
encrypted-blob forwarder and never sees plaintext.

That shapes the host process requirements:

1. **Crypto must run in a mature, auditable native binding.** Argon2id at our target parameters
   (m=256 MiB) and XChaCha20-Poly1305 over multi-megabyte attachments are not things we want to run
   in a scripting-language reimplementation.
2. **The rendering surface is non-negotiable.** The product's centrepiece is a WebGL 2D/3D knowledge
   graph (ADR-0010) and a real Markdown editor (CodeMirror 6). Both live in the browser ecosystem.
3. **The AI/ML ecosystem is Python.** Tokenizer libraries, embedding runtimes (`onnxruntime`,
   `sentence-transformers`), NumPy for the in-memory vector matrix (ADR-0007), and every provider SDK
   we care about have first-class Python support.
4. **The team ships Python.** Velocity matters more than theoretical runtime efficiency for an app
   whose hot paths are either in C (libsodium, SQLite) or in the GPU (the graph).

The decision is therefore: what owns the process, the window, the filesystem, and the crypto?

## Decision

The application host is **CPython (>=3.10, see ADR-0011) driving Qt 6 via PySide6 6.8** (the official
Qt for Python bindings, LGPLv3).

The Python/Qt host owns:

- **Process and window lifecycle.** `QApplication`, the main window, tray/menu integration, single-instance
  enforcement, graceful shutdown (which must flush and zeroize key material).
- **Filesystem.** All reads and writes to the workspace, including the private object store
  (ADR-0004). The frontend never touches a path; it only sees opaque ids over the bridge (ADR-0003).
- **Cryptography.** Argon2id via `argon2-cffi`, XChaCha20-Poly1305 via PyNaCl/libsodium, X25519/Ed25519
  identity via `cryptography` (ADR-0005). Key material exists only in Python memory, never in the
  WebEngine renderer process.
- **Indexing.** SQLite FTS5 for public layers, in-memory index + NumPy embedding matrix for unlocked
  private layers (ADR-0007).
- **Graph extraction.** `GraphService` produces the node/edge model; the frontend only renders it
  (ADR-0010).
- **AI providers.** All network egress and all subprocess execution (including the Claude CLI adapter)
  happen in Python, where the per-layer AI policy is enforced (ADR-0008).
- **Packaging.** PyInstaller produces the distributable; platform installers wrap it.

The UI is a Qt WebEngine view hosting a React/TypeScript bundle (ADR-0002), communicating over
QWebChannel (ADR-0003). PySide6 is pinned to the 6.8 LTS-adjacent line so that the bundled Chromium in
QtWebEngine has a known, patchable version.

## Consequences

### Positive

- Crypto, indexing, and AI live in the ecosystem with the strongest libraries for each, in one
  language, in one process, with one policy-enforcement point.
- The security boundary is clean and easy to reason about: **the renderer is untrusted-ish**. It has no
  filesystem access, no keys, no network credentials, and no plaintext of a locked layer. Everything it
  can do is enumerable as a list of validated bridge slots.
- Qt gives us native menus, file dialogs, DPI handling, tray, and a well-understood packaging story on
  Windows/macOS/Linux without three separate native codebases.
- PySide6 is Qt-Company-maintained and LGPL, which keeps licensing simple for a distributed desktop
  app (we dynamically link Qt and ship the relink-enabling artefacts).

### Negative

- **The bundle is large.** A PyInstaller build carrying CPython, Qt 6 core/gui/widgets/network, and
  QtWebEngine (a full Chromium) is realistically **~200–400 MB** installed, depending on platform and
  how aggressively we strip Qt modules and locales. This is the single biggest cost of this decision
  and it is not going away. We accept it; we mitigate it by excluding unused Qt modules, ICU data we do
  not need, and QtWebEngine locale packs beyond the shipped UI languages.
- **The GIL.** Any CPU-bound work — Argon2id derivation (deliberately ~0.5–2 s), index rebuilds on
  unlock, embedding computation, large-attachment encryption — will freeze the UI if run on the Qt main
  thread. This forces a discipline, not an option: **all CPU-heavy work runs in a `QThreadPool` worker
  or a `multiprocessing` worker, and every such call is exposed to the frontend as an async job with a
  job id (ADR-0003), not a blocking slot.** Reviewers should treat a blocking bridge slot doing real
  work as a defect.
- Python startup plus Qt plus Chromium initialisation makes cold start slower than a native app. We
  budget for it (splash + lazy WebEngine profile init) rather than pretending it away.
- Shipping a Python app means shipping an interpreter, which makes the binary trivially unpackable and
  the source effectively readable. We do not rely on obfuscation for any security property; see
  THREAT_MODEL.
- PyInstaller + QtWebEngine is a known-fiddly combination (resource paths, `QTWEBENGINEPROCESS_PATH`,
  code-signing the helper process on macOS). This is a real, recurring maintenance cost in CI.

### Neutral

- Qt's LGPL terms mean we ship Qt as dynamic libraries and must not statically link it without a
  commercial licence. PyInstaller's default one-folder build already satisfies this; a one-file build
  does too (it extracts and dynamically loads), but we prefer one-folder for startup time and for
  clarity about what we ship.
- Because the host is Python, the AI provider adapters and the export writer are ordinary Python
  modules that can be tested headlessly without Qt at all. Most of the test suite therefore never
  starts a `QApplication`.
- Qt WebEngine pins us to a Chromium version; security updates to the renderer arrive on Qt's release
  cadence, not Chrome's. See the "Revisit when" trigger below.

## Alternatives considered

### Electron + Node.js

A single JS/TS stack, excellent web tooling, the same Chromium renderer.

**Why rejected:** the security-critical work would move into Node. That means Argon2id and AEAD via
native npm addons (`argon2`, `sodium-native`) whose supply chain and maintenance we trust less than
libsodium-via-PyNaCl, and it means our key material lives in the same runtime as the UI code and its
transitive npm dependency tree — a far larger surface for a key-exfiltration bug than a Python process
that the renderer can only reach through eleven validated bridge slots. It is also *not* smaller:
Electron ships the same Chromium. And it would strand the Python AI/embedding ecosystem behind a
subprocess boundary anyway.

### Tauri + Rust

Small bundles (system webview), memory-safe host, excellent crypto crates.

**Why rejected:** two reasons, both practical. First, team velocity — the team is fluent in Python and
not in Rust, and M0–M6 are dense with product surface, not with systems programming. Second, the AI and
embedding ecosystem: we would end up hosting a Python sidecar for embeddings and tokenization anyway,
which reintroduces the Python runtime we were trying to avoid *plus* an IPC boundary. Tauri's use of the
**system** webview also means the renderer's capabilities (and bugs) vary per machine, which is hostile
to a product whose main view is a WebGL graph. This is the strongest rejected alternative and the one
most likely to be revisited.

### Pure Qt UI (QtWidgets or QML), no web layer

No Chromium, much smaller bundle, one language.

**Why rejected:** it cannot deliver the product. The 3D graph would mean writing an instanced-rendering
scene graph in Qt Quick 3D from scratch, and the editor would mean reimplementing CodeMirror 6's
Markdown editing, decorations, collaborative cursors, and extension model. Those two components are the
product. We would spend the entire roadmap rebuilding ecosystem that already exists, and still ship a
worse editor. QML's declarative UI is genuinely pleasant; it is simply the wrong tool for these two
surfaces.

### Python + a browser tab (local HTTP server, no desktop shell)

Ship a Python backend and tell the user to open `localhost`.

**Why rejected:** an open listening port is permanent attack surface on a machine that may be shared or
hostile-adjacent (see ADR-0003), the "app" has no window identity, no native file dialogs, no keychain
integration path, and offline/local-first guarantees become invisible to the user. Rejected on both
security and product grounds.

## Revisit when

- The PyInstaller + QtWebEngine artefact exceeds **500 MB installed** on any target platform, or cold
  start exceeds **3 s** on the reference machine — at which point the Tauri/system-webview trade
  deserves a fresh spike.
- Qt WebEngine's bundled Chromium falls more than **two stable Chromium majors** behind upstream for a
  sustained release cycle, making renderer CVE exposure unacceptable.
- The GIL stops being the constraint (a free-threaded CPython we can actually ship, with PySide6 and
  our native deps supporting it) — this would remove the worker-process complexity from indexing and
  embeddings.
- Team composition changes such that Rust is no longer a velocity risk **and** an acceptable Python-free
  embedding path exists.
