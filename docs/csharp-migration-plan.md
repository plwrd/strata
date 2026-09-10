# C# / native WinUI migration plan

**Decision (2026-09-10):** proceed with a **native UI** rewrite (option B) via a
**strangler**, starting with Phase 0 deliverable 1 (solution + stub shell +
frozen format fixtures). See also
[csharp-migration-assessment.md](csharp-migration-assessment.md).

## Why native

Heaviness and flicker are dominated by embedded Chromium (Qt WebEngine today).
Keeping a web UI on WebView2 would not fix those symptoms. The chosen path
retires React/Qt WebEngine for the **app UI**. An optional research browser may
later use an isolated WebView2 island; it must never share the app trust surface.

## Phase 0 status (this milestone)

| Item | Location |
| --- | --- |
| .NET solution | [`dotnet/Strata.sln`](../dotnet/Strata.sln) (also `Strata.slnx`) |
| Core (settings, errors, crypto interfaces) | [`dotnet/src/Strata.Core`](../dotnet/src/Strata.Core) |
| Infrastructure (Win32 shell helpers, settings store) | [`dotnet/src/Strata.Infrastructure`](../dotnet/src/Strata.Infrastructure) |
| Host stub | [`dotnet/src/Strata.App`](../dotnet/src/Strata.App) — **WPF** host proving the same Win32 privacy APIs; WinUI 3 packaging lands when the Windows App SDK workload/templates are available on the build agents |
| Format KATs | [`tests/fixtures/format`](../tests/fixtures/format), generator [`tools/format_fixtures`](../tools/format_fixtures) |
| Conformance discovery tests | [`dotnet/tests/Strata.FormatConformance`](../dotnet/tests/Strata.FormatConformance) |

### Stub shell checklist (manual)

- [ ] **Hidden for sharing** — window omitted from Snipping Tool / Teams capture
- [ ] **Minimize to tray** — close/minimize hides; restore from tray; Quit only from tray menu
- [ ] **Hide from taskbar** — no taskbar button; tray remains as restore path
- [ ] **Start in tray** — process starts hidden when enabled (requires minimize-to-tray)

### Screen / privacy options matrix (vs Python)

| Option / behaviour | Python | Phase 0 .NET |
| --- | --- | --- |
| `hide_for_sharing` (default on) | yes | **yes** |
| Capture affinity on all process top-level HWNDs | yes | **yes** (`SetProcessWindowsExcludedFromCapture`) |
| Affinity on newly shown windows / dialogs | yes (Qt event filter) | **yes** (WPF class `Loaded` handler while enabled) |
| Re-assert after taskbar style cycle | yes | **yes** |
| Re-assert on every focus/`Activated` | no (removed for perf) | **no** (state change only) |
| `minimize_to_tray` / `start_in_tray` / `hide_from_taskbar` | yes | **yes** |
| `start_in_tray` disabled unless minimize-to-tray | yes | **yes** |
| Tray notify-once on first hide | yes | **yes** |
| Static title `"Strata"` (no note titles) | yes | **yes** |
| `browser_blur_media` / `browser_blur_amount` | yes (live in pane) | **settings persisted**; live blur + `Ctrl+Shift+X` when research pane ships |
| Capture exclusion on Chrome backend PID | yes | **deferred** (no browser process yet; API ready) |

## Phase 1 status — encryption + encrypted store (**done**)

The highest-risk item in the assessment is closed: the .NET side opens and
re-seals every committed vector **byte-for-byte**, and opens a whole private
layer written by the Python store using nothing but the password.

| Python module | .NET port |
| --- | --- |
| `encryption/primitives.py` | [`Encryption/AeadPrimitives.cs`](../dotnet/src/Strata.Infrastructure/Encryption/AeadPrimitives.cs), [`Encryption/KdfParams.cs`](../dotnet/src/Strata.Core/Encryption/KdfParams.cs) |
| `encryption/container.py` | [`Encryption/ObjectContainer.cs`](../dotnet/src/Strata.Infrastructure/Encryption/ObjectContainer.cs) |
| `encryption/layer_header.py` | [`Encryption/LayerHeader.cs`](../dotnet/src/Strata.Infrastructure/Encryption/LayerHeader.cs) |
| `encryption/rotation_journal.py` | [`Encryption/RotationJournal.cs`](../dotnet/src/Strata.Infrastructure/Encryption/RotationJournal.cs) |
| `crdt/updates.py` | [`Crdt/UpdateSealing.cs`](../dotnet/src/Strata.Infrastructure/Crdt/UpdateSealing.cs) |
| `storage/encrypted_store.py` | [`Storage/EncryptedLayerStore.cs`](../dotnet/src/Strata.Infrastructure/Storage/EncryptedLayerStore.cs), [`Storage/Manifest.cs`](../dotnet/src/Strata.Infrastructure/Storage/Manifest.cs) |
| `storage/paths.py::replace_atomic` | [`Storage/AtomicFile.cs`](../dotnet/src/Strata.Infrastructure/Storage/AtomicFile.cs) |
| `domain/note.py` (parsing slice) | [`Notes/Note.cs`](../dotnet/src/Strata.Core/Notes/Note.cs), [`Notes/NoteParsing.cs`](../dotnet/src/Strata.Core/Notes/NoteParsing.cs) |
| `domain/errors.py` | [`Errors/`](../dotnet/src/Strata.Core/Errors) |

**No native libsodium dependency.** .NET has no XChaCha20 (24-byte nonce), so
`AeadPrimitives` composes the two published halves of the same construction:
HChaCha20 derives a subkey from the first 16 nonce bytes, then the in-box
`ChaCha20Poly1305` (RFC 8439) runs with nonce `00000000 || nonce[16..24]`. That
*is* XChaCha20-Poly1305-IETF, and `primitives.json` proves it against libsodium's
output. BLAKE2b and Argon2id come from the managed `Konscious.Security.Cryptography`
packages; the fixtures pin both against `hashlib` and `argon2-cffi`.

Argon2id at production cost (t=3, m=256 MiB, p=4) runs in managed code here — it
has not been benchmarked against `argon2-cffi` yet. Measure before shipping an
unlock path; the fixtures deliberately use reduced costs so tests stay fast.

### Build / test

```bash
dotnet build dotnet/Strata.sln
dotnet test dotnet/Strata.sln
python tools/format_fixtures/generate.py   # must be byte-identical
```

Target framework is **net10.0** (SDK available on the migration machine). Align
to LTS later if packaging requires it.

## Later phases

1. ~~C# encryption + encrypted store — pass all KATs open/seal~~ **done**
2. Domain + workspace/layer/notes (public Markdown first, then private) — the
   note-parsing slice and the encrypted store landed with Phase 1; still to come:
   `markdown_store`, `workspace_store`, and the workspace/layer/note services
3. Native feature UI: navigator + editor + layer unlock
4. Search, graph, snapshots, views
5. AI + operations (fix known injection gaps while porting)
6. CRDT/Ycs interop + collab; delete Python per subsystem only after parity
7. Packaging; retire Qt/PyInstaller

Keep the Python app runnable until each subsystem is replaced.

## Parity ledger

Measured 2026-09-10, after Phase 1. The .NET tree is **not** a clone of the app
yet; it is the format-critical floor plus the shell stub.

| Subsystem | Python LOC | .NET status |
| --- | --- | --- |
| `app/infrastructure/encryption` + `crdt/updates` | ~900 | **ported**, KAT-verified |
| `app/infrastructure/storage/encrypted_store` + `paths` | ~500 | **ported** (encrypted store; `markdown_store`/`workspace_store` outstanding) |
| `app/domain` | 2,531 | note models + parsing only (~180) |
| `app/desktop` (Qt shell) | 1,648 | privacy/tray/taskbar behaviours proven in the WPF stub; no feature UI |
| `app/infrastructure` (rest: search, vector, crdt store/relay, ai providers, keychain, logging) | ~3,300 | not started |
| `app/services` (35 services) | 10,536 | not started |
| `app/bridge` (14 bridges) | 3,723 | not started — and the trust boundary must be re-reviewed, not just re-typed |
| `frontend` (React/TS) | 24,361 | discarded by the native decision; nothing replaces it yet |

Known gaps carried forward from Phase 0, unchanged:

- Capture exclusion on the Chrome backend PID — no browser process in .NET yet.
- Live media blur and `Ctrl+Shift+X` — settings persist; needs the research pane.
- WinUI 3 packaging — host is still WPF pending the Windows App SDK workload.

New in Phase 1, worth tracking:

- Argon2id cost/latency in managed code is unmeasured at production parameters.
- `Ycs` (Yjs interop) is still unevaluated; `UpdateSealing` seals and opens update
  bytes but nothing in .NET yet *interprets* them.
