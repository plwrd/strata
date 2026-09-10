# Review: performance, security, screen protection

A grounded pass over the three areas, ranked by impact. Findings are tied to
specific code; each says what it is, why it matters, the fix, and my confidence.
Two low-risk performance fixes are **already applied** (§1); the rest are
reported for a decision or need real measurement I can't do headless.

---

## 1. Applied in this pass (performance)

**P1 — the capture event filter ran on every app event, always.**
`MainWindow` installed an app-wide `QApplication.installEventFilter` unconditionally,
so a Python callback fired for *every* Qt event in the process even when
Hidden-for-sharing was off. Now it is installed only while the feature is on and
removed when off (`_sync_capture_filter`). Directly trims per-event overhead —
the kind of thing that reads as "heavy."

**P2 — capture re-assert fired on every focus change.**
`_reapply_hide_for_sharing` was triggered by `ActivationChange` (every focus
gain/loss), and for the Chrome backend that ran `EnumWindows` over *all* OS
windows each time. Display affinity persists on the HWND, so only
`WindowStateChange` (minimize/restore) and the taskbar cycle actually need a
re-assert. `ActivationChange` is no longer a trigger. Removes frequent,
pointless work.

Both verified: 670 backend + 13 e2e pass, lint/type clean.

---

## 2. Performance — remaining

**P3 — the engine is the weight, not the code. (architectural, known)**
Qt WebEngine bundles a full Chromium (~150 MB, its own GPU/renderer processes).
That dominates RAM and start time; Python is the small part. Covered in
`docs/csharp-migration-assessment.md` — a language change does not fix it, only
dropping the embedded browser would. *Confidence: high.*

**P4 — cold start does eager work.** The browser pane + its WebEngine profile
are built during `MainWindow.__init__` even for users who never open research.
Deferring their construction to first use would cut startup cost for the common
case. *Confidence: medium — worth profiling first.*

**P5 — no startup/RAM budget exists.** "Heavy" is a feeling, not a number. A
small startup+RSS measurement (even a dev-only log line) would let us target and
defend changes instead of guessing. *Recommended before any larger perf work.*

---

## 3. Security

The trust boundary is healthy: **119 `@Slot` methods across 14 bridge objects**,
each Pydantic-validated at the envelope, size-capped, with the THREAT_MODEL §6
review-log convention. The recent browser methods hold up — `open_external` and
all navigation validate `http`/`https` via `_validate_web_url` and are gated on
`require_enabled`.

**S1 — `open_external` is a content-reachable app-launch path, and has no
review-log row.** It hands a URL to the OS default browser (`QDesktopServices.openUrl`).
It is scheme-validated and feature-gated, so risk is low, but it is exactly the
kind of "page content can cause an outbound action" surface the project tracks.
*Fix: add a THREAT_MODEL §6 / security-and-privacy §4 row (the project's own
rule: a missing row fails review). Confidence: high, small.*

**S2 — history deletion is correctly scoped; keep it that way.** `_clear_history`
only touches Strata's own profile (skips a user-supplied `browser_profile_path`)
and unlinks a fixed allowlist of files. No traversal risk. *No action; noted so a
future edit doesn't widen it.*

**S3 — Chrome capture-exclusion matches by exact PID.** Correct, but it misses
Chrome's child windows / a relaunch handoff (already a documented T-34 caveat).
Not a vulnerability — a coverage gap. See §4.

**S4 — at-rest: the Chrome/embedded-pane cookie store is plaintext-adjacent.**
Already disclosed in T-34 — research sign-ins live in the profile dir under
whatever the OS gives them, not a layer key. Unchanged; restated so it is not
forgotten when "keep session/cookies" is in play.

---

## 4. Screen protection

**SP1 — WebEngine's composited web surface is the real residual.** `WDA` is set
on the window HWND, which covers ordinary painting, but Chromium composites web
content through a separate GPU surface that, on some drivers/paths, is **not**
covered by the parent window's display affinity. This is the most likely "content
still shows in a recording" case, and I **cannot verify it headless**. If it
leaks, the mitigations are: force software compositing for the pane
(`--disable-gpu-compositing`, a real perf cost), or accept it and rely on the
per-window affinity for everything else. *Confidence: this is the honest unknown;
needs a real screen recording to confirm or rule out.*

**SP2 — Chrome PID handoff.** If Chrome was already running, Strata's launch hands
off to the existing process and exits, so the PID-based exclusion never attaches.
*Fix: match the launched process's window *and its child processes' windows*, or
detect the handoff and warn. Confidence: medium.*

**SP3 — one-frame popup exposure.** A popup is excluded on its `Show` event, which
is after it first appears — a single frame where a just-opened dropdown could be
captured. Negligible in practice, unfixable without pre-creation hooks Qt doesn't
offer. *Accept and document.*

**SP4 — Windows-only, by design.** All of this is `SetWindowDisplayAffinity`;
macOS/Linux are a logged no-op. Fine as long as the UI never claims protection on
those platforms. *Verify the settings copy is platform-honest.*

---

## 5. Recommendation

1. **Done:** P1, P2 (applied).
2. **Small, do next:** S1 (review-log row), P5 (a startup/RSS number).
3. **Needs your call / a recording:** SP1 (confirm the WebEngine surface leak
   with a real capture before deciding whether to pay the software-compositing
   cost), P4 (profile, then lazy-build the pane).
4. **Not a fix:** a C# rewrite — see the migration assessment; it moves the
   weight, it does not remove it.
