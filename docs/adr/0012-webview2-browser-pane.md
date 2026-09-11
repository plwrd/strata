# ADR-0012: WebView2 for the research browser pane, bound over COM from Python

**Status:** Accepted, 2026-09-10

## Context

Strata's research pane is a second web view beside the workspace (ADR-0002), used to read a page
and capture it into the workspace. Two facts about it collided.

**One: the embedded engine cannot play the web's most common video.** The Qt WebEngine inside the
PySide6 wheel is built without proprietary codecs. Measured against the installed engine, not
assumed — `tests/e2e/test_codec_rationale.py` runs `canPlayType` and gets:

| Codec | Qt WebEngine 6.11.1 | WebView2 (Edge 152) |
| --- | --- | --- |
| H.264 (`avc1.42E01E`) | `no` | `probably` |
| AAC (`mp4a.40.2`) | `no` | `probably` |
| VP9 (`vp9`) | `probably` | `probably` |
| MSE H.264 | `no` | `yes` |

**Two: the workaround for that is incompatible with screen-capture protection.** The previous answer
was a "Chrome backend" that launches the user's *own* Chrome on a loopback DevTools port. It plays
everything — and it cannot be hidden from a recording. Strata excludes windows with
`SetWindowDisplayAffinity`, which is per top-level HWND; Strata can sweep the windows of the process
it launched, but a browser opens more windows over its lifetime, in processes Strata did not start,
and a "Hidden for sharing" that silently stops covering the pane is worse than one that never
claimed to.

*Hidden for sharing* is on by default and is the setting users are told to trust before they share a
screen. Video is a convenience. Where they conflict, protection wins — so the Chrome backend cannot
be the answer for video, and something inside Strata's own window has to be.

## Decision

**Host the research pane on Microsoft Edge WebView2, driven from Python through a hand-written
`ctypes` binding to its COM API.**

Three parts, each load-bearing:

1. **WebView2, not another Chrome.** It renders into a window Strata owns, so the existing display
   affinity covers it. It ships H.264/AAC because Edge does. The Evergreen runtime is already
   present on Windows 11 and most Windows 10 installs, and the only file Strata redistributes is
   `WebView2Loader.dll` (~166 KB, in `packaging/webview2/`).

2. **`ctypes` over COM, not pythonnet and not a .NET host process.** pythonnet is the usual route
   and it works, but it puts the CLR in the process and makes the PyInstaller/Inno build carry — or
   prompt for — the .NET Desktop Runtime. For an app whose pitch is "your notes are a directory on
   your disk", a second runtime is a real cost. comtypes cannot help: WebView2 ships a C header and
   no type library. A separate C#/C++ host process was rejected too — cross-process HWND reparenting
   brings focus and input-routing problems that an in-process child window does not have.

3. **Slot numbers are generated, never transcribed.** A COM interface is an ordered vtable. Calling
   slot 29 when `ExecuteScript` sits at 30 does not raise and does not fail a type check; it calls
   whatever is at that offset. `scripts/webview2_slots.py` reads Microsoft's `WebView2.h` and emits
   `app/desktop/webview2/_slots.py`. The generated file is committed, so a normal build needs
   neither the header nor the network.

## Consequences

### Positive

- In-pane H.264/AAC video, inside a window whose capture exclusion Strata controls.
- The Chrome backend stops being the answer for video (and, per the addendum below, for most
  extension use), with its capture limitation documented rather than implied.
- No new runtime dependency: no CLR, no bundled browser. One 166 KB DLL.
- WebView2's popups live in a `msedgewebview2.exe` that *Strata launches*, so
  `get_BrowserProcessId` gives the capture sweep a PID it can actually cover — the guarantee the
  Chrome backend could not make.

### Negative

- Strata now maintains a COM binding. The failure mode of a mistake in it is a corrupted process,
  not an exception. This is mitigated by generating the slot table, by
  `tests/unit/test_webview2_binding.py` pinning the IUnknown contract and string ownership, and by
  keeping the binding small — it implements only what the pane calls.
- A second browser engine's worth of processes when the pane is open.
- Windows only. macOS and Linux keep the Qt pane and its codec limits.
- The user-data folder is a second cookie store to reason about in the threat model (T-34), on the
  same footing as the Qt pane's.

### Neutral

- WebView2 **does not** make the pane capture-safe by itself. It is the same Chromium compositor, so
  the hardware-overlay problem is identical and the flags in `sdk.CAPTURE_SAFE_ARGUMENTS` are what
  actually keep video inside the exclusion. Display affinity is enforced by DWM; an overlay plane is
  composed beside DWM's output and escapes it. That rule governs any future engine change.
- `ICoreWebView2EnvironmentOptions` has to be implemented by the caller — the SDK's version is a C++
  helper class, not something the loader hands out. It must report a parseable
  `TargetCompatibleBrowserVersion`; an empty one fails creation with `E_INVALIDARG`, which presents
  as "WebView2 is not installed" and is not.

## Alternatives considered

### Rebuild Qt WebEngine with `-webengine-proprietary-codecs`

One engine, no new code, no new process — the cleanest architecture of the four. Rejected on cost
and licensing: a multi-hour Chromium build in CI, a PySide6 rebuilt against it, and an AVC/H.264
patent licence for distribution. Worth revisiting if Strata ever builds Qt itself for other reasons.

### pythonnet + the WinForms/WPF WebView2 control

Typed API, no vtable arithmetic, proven by pywebview. Rejected because of the .NET Desktop Runtime
in the installer, and because mixing a WinForms control's message handling into Qt's loop trades one
class of fiddliness for another.

### CEF with off-screen rendering

The only structurally airtight option: content renders to a texture Strata paints into its one
protected window, so there are no extra HWNDs and no overlay planes *at all* — capture protection
stops depending on flags. Rejected for effort: no maintained Python binding (cefpython is stuck on
Python 3.9), so it means a C++/C# host process and an IPC protocol. This is the fallback if the
overlay flags prove insufficient in the field.

### Keep the Chrome backend as the video answer

Rejected: it cannot be excluded from screen capture, which is the property the feature exists to
protect. See Context.

## Revisit when

- `tests/e2e/test_codec_rationale.py` starts failing — Qt WebEngine has gained proprietary codecs,
  and a second engine may no longer earn its place.
- The overlay flags are shown not to hold against a real recorder on some GPU or driver, which makes
  off-screen rendering (CEF) the only remaining answer rather than the expensive one.
- Strata needs the pane on macOS or Linux, where WebView2 does not exist.
- Microsoft changes the Evergreen runtime's distribution such that it can no longer be assumed
  present.

---

## Addendum, 2026-09-10: browser extensions

Recorded as an addendum rather than a body edit, per this directory's rule that an accepted ADR is
not rewritten. Nothing above is reversed; one consequence is extended.

WebView2 can load **unpacked** browser extensions, which the Qt pane cannot at any price — Qt ships
Chromium with the extensions subsystem compiled out. Verified end to end against the installed
runtime: an unpacked test extension loads, is reported back by the name in its manifest, and its
content script runs in the page.

Three API facts shape the implementation:

- It is opted into at **environment creation** via `ICoreWebView2EnvironmentOptions6`, which the
  runtime reaches by `QueryInterface` on the options object we pass in. That forced
  `com.Callback` to support an object exposing several interfaces — one vtable pointer per
  interface, `IUnknown` always resolving to the first, which is what a C++ object with multiple
  bases looks like in memory.
- Extensions are per **profile** (`ICoreWebView2Profile7`), reached from the view through
  `ICoreWebView2_13`. An older runtime simply lacks one of those interfaces, which is a "no
  extensions" answer rather than an error.
- They are **folders, not `.crx` files**. There is no store-install path, so the setting is a list
  of directories and the UI is a native folder picker.

Consequences accepted:

- **Third-party code now runs in the research pane, by explicit user action.** An extension sees
  every page the pane visits and can send what it sees anywhere. It cannot reach the workspace —
  the pane has no `QWebChannel` and no host object, which was already true and is now load-bearing
  for a second reason. Mitigated by: off unless a folder is named, one explicit choice per
  extension through a native dialog, a cap of ten, and the loaded names shown in the status.
- A configured folder that has moved is reported, not skipped silently. A user who believes their
  blocker is running when it is not is worse off than one who is told.
- Extensions load when the browser process is created, so adding or removing one takes effect at
  the next start. Stated in the UI rather than discovered.

This also narrows what the Chrome backend is *for*: store-installed extensions and sign-in flows
that refuse an embedded browser. Everything else it was carried for is now available inside a
window Strata can keep out of a recording.
