# C#/.NET migration assessment

**Question asked:** the app feels heavy and flickers — would rewriting it in C#
fix that?

**Short answer:** No, not on its own — and a full rewrite is a multi-month
project that would re-implement ~35k lines of tested, security-reviewed code for
a gain that, for the stated symptoms, is close to zero. The heaviness and the
flicker come from the **embedded Chromium** (Qt WebEngine), and every realistic
C#/.NET path that keeps the current web UI embeds Chromium too. The language
above the engine is not the cause.

This document lays out the facts so the decision is made from them, not from a
hunch. It is an assessment, not a plan of record.

---

## 1. What the symptoms actually are

| Symptom | Real cause | Does a C# rewrite change it? |
| --- | --- | --- |
| "Heavy" (RAM, install size) | Qt WebEngine bundles a full Chromium — its own GPU + renderer processes, ~150 MB on disk. Python + deps are ~30–40 MB on top. | **No** if the UI stays web-based: WPF+**WebView2** runs Edge/Chromium, CEF *is* Chromium. Same engine, same weight. Only a **native** UI (WinUI/WPF, no webview) is lighter — and that means rewriting the 24k-line React frontend too. |
| Flicker (video, blurred media) | Traced in this repo: Chromium compositing of a `filter`-blurred `<video>` layer, and H.264 not decoding at all (the Qt build ships no proprietary codecs). | **No.** WebView2 uses the *same* Chromium compositor; it would flicker identically. (It *would* fix H.264 playback, because Edge WebView2 ships the codecs — but so does our existing "Chrome backend", already built.) |
| General sluggishness | I/O- and render-bound: file reads, Argon2 (deliberately slow), encryption, Qt painting. Not CPU-bound Python. | **Marginal.** C# is faster at CPU loops, but those are not the hot path here. Argon2 is *designed* to be slow and would be identical. |

The one thing C#/WebView2 buys that's real — in-pane H.264 video — we already
deliver through the Chrome backend and the "Open in browser" button, without a
rewrite.

---

## 2. What a migration would actually cost

Current code in scope (measured):

| Area | Python LOC | Migration difficulty |
| --- | --- | --- |
| `app/services` (35 services) | 10,473 | High — the whole app's behaviour; re-implement and re-test each. |
| `app/infrastructure` | 4,665 | **Highest** — see §3; format-compatible crypto and CRDT. |
| `app/bridge` (14 bridges) | 3,723 | Medium — maps to WebView2 host messaging, but every Pydantic model re-expressed. |
| `app/domain` | 2,531 | Medium. |
| `app/desktop` (Qt shell) | 1,524 | Rewritten entirely against WPF/WinUI + WebView2. |
| **app total** | **23,021** | |
| `tests` | 11,795 | Rewritten in a .NET test framework; 664 tests' worth of behaviour re-pinned. |
| `frontend` (React/TS) | 24,361 | **Carries over unchanged** *if* the UI stays web-based (WebView2). Thrown away if going native. |

A faithful port of ~35k lines of Python + 12k lines of tests, including
cryptography and a CRDT, is a **multi-month effort for an experienced .NET
engineer**, during which the Python app keeps moving and the two diverge.

---

## 3. The parts that are genuinely hard (and risky)

These are not line counts — they are correctness-critical and
**format-compatibility** traps. A rewrite that gets them subtly wrong makes
every existing user's workspace unreadable.

- **Encryption (`app/infrastructure/encryption`).** XChaCha20-Poly1305 (via
  libsodium/PyNaCl) with 24-byte random nonces, Argon2id KDF, a versioned
  `layer.header`, AAD binding layer/object/type/format-version, and a rotation
  journal. A C# port must produce **byte-identical** ciphertext framing and AAD
  or it cannot open a workspace written by the Python app. `.NET` has
  `AesGcm`/`ChaCha20Poly1305` built in, but **not XChaCha20** (the 24-byte-nonce
  variant) — you'd need libsodium via P/Invoke (the `libsodium` NuGet) to match.
  This is the single highest-risk item; it has its own threat model and test
  suite (`tests/security/`) that would all need re-establishing.

- **CRDT (`app/infrastructure/crdt`).** Built on `pycrdt`, which is a binding to
  **Yjs** (the Rust/`y-crdt` core). Collaboration updates are Yjs binary
  updates. A .NET port needs a Yjs-compatible library (e.g. `Ycs`, a C# Yjs
  port) that **interoperates at the update-binary level** with existing data and
  with any Python peers during a transition. Interop bugs here corrupt shared
  documents silently.

- **The `strata://` scheme + bridge trust boundary.** Re-expressed against
  WebView2's `WebMessageReceived` / virtual-host mapping. The security model
  (envelope validation, size caps, per-method Pydantic models, the
  THREAT_MODEL §6 review log) has to be rebuilt and re-reviewed, because the
  trust boundary is exactly where bugs become vulnerabilities.

- **Tray, taskbar, the browser pane.** All the native-window work from recent
  milestones (`WS_EX_TOOLWINDOW`, tray, the embedded `QWebEngineView` research pane with
  blur/mobile/digest) is Qt-specific and would be rebuilt against WPF/WinUI +
  WebView2.

---

## 4. The realistic .NET options, ranked

1. **WPF/WinUI + WebView2, reuse the React frontend.**
   The only option where the 24k-line frontend survives. Re-implements all
   Python backend + the Qt shell in C#. **Does not fix heaviness or flicker**
   (WebView2 = Chromium). Fixes in-pane H.264 (Edge ships codecs). Effort:
   months. Net benefit for the stated problem: ~nil.

2. **Photino / Blazor Hybrid.** Same story — still a Chromium/WebView under the
   hood on Windows. Same weight.

3. **Fully native WinUI 3 / WPF UI (no webview).** The *only* genuinely lighter
   result. Requires throwing away the React frontend and rebuilding the entire
   UI natively, plus all the backend. This is a **new product**, not a port.
   Largest effort by far; loses cross-platform potential.

There is no C#/.NET path that is both "light" and "keeps the work."

---

## 5. Recommendation

**Don't rewrite. Fix the measured problems in place.** Concretely, in the
current app, in roughly ascending effort:

1. **Flicker:** stop putting CSS `filter` on `<video>` (the compositing culprit);
   blur media with an overlay instead, or exclude video from blur. Try
   `--disable-gpu-compositing` / tune `QTWEBENGINE_CHROMIUM_FLAGS` for the pane.
2. **H.264 video:** already handled — Chrome backend + "Open in browser". If
   in-pane playback is required, that's a proprietary-codec Qt build (an H.264
   **licensing** decision), independent of language.
3. **Cold start / weight:** lazy-build the browser pane and its Chromium profile
   so they cost nothing until first use; audit startup work on the Qt thread;
   trim Chromium features via flags.
4. **Measure first:** add a startup/RAM profile so "heavy" becomes a number we
   can move, not a feeling.

If, after that, the embedded web engine is still judged too heavy for the
product's goals, the honest conclusion is not "rewrite in C#" but "move off an
embedded browser to a native UI" — and that is a product decision to make
deliberately, with this cost in view, not a language swap.

---

## 6. If a migration is chosen anyway

Do it as a **strangler**, never a big-bang rewrite:

- Freeze the on-disk formats first: write an **encryption & CRDT format
  conformance suite** (fixtures written by the Python app, asserted byte-for-byte
  by the .NET port) *before* porting logic. Interop is the whole ballgame.
- Port infrastructure (crypto, storage, CRDT) first, behind the same interfaces,
  and validate against the conformance suite.
- Keep the React frontend; stand up the WebView2 host and bridge; move services
  across a few at a time, with the behaviour pinned by ported tests.
- Only delete Python for a subsystem once its .NET replacement passes the same
  tests against the same data.

Expect the two codebases to coexist for the duration, and budget for it.
