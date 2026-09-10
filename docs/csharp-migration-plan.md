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

**Argon2id cost, measured** (2026-09-10, this machine, Release, best of three):

| Parameters | `argon2-cffi` | Konscious (managed) |
| --- | --- | --- |
| t=3, m=256 MiB, p=4 (production) | 180 ms | 357 ms |
| t=3, m=256 MiB, p=1 | 554 ms | 1,197 ms |
| t=1, m=64 MiB, p=4 | 22 ms | 29 ms |

Managed Argon2id is roughly **2× slower** than the C implementation, which lands
production unlock at ~0.36 s — inside the 0.5–1 s the parameters were chosen for.
Not a blocker, and not a reason to raise costs either: the Python app sets the
budget, and both must stay unlockable at the same parameters. The fixtures
deliberately use reduced costs so tests stay fast.

## Phase 2 status — domain + storage (**stores done, services outstanding**)

| Python module | .NET port |
| --- | --- |
| `domain/layer.py` | [`Layers/LayerDescriptor.cs`](../dotnet/src/Strata.Core/Layers/LayerDescriptor.cs) |
| `domain/workspace.py` | [`Workspaces/WorkspaceDescriptor.cs`](../dotnet/src/Strata.Core/Workspaces/WorkspaceDescriptor.cs) |
| `domain/views.py` | [`Views/ViewConfig.cs`](../dotnet/src/Strata.Core/Views/ViewConfig.cs) (models; the query engine lives in the view service, not yet ported) |
| `storage/paths.py` | [`Storage/Paths.cs`](../dotnet/src/Strata.Infrastructure/Storage/Paths.cs) |
| `storage/markdown_store.py` | [`Storage/MarkdownLayerStore.cs`](../dotnet/src/Strata.Infrastructure/Storage/MarkdownLayerStore.cs), [`Storage/Frontmatter.cs`](../dotnet/src/Strata.Infrastructure/Storage/Frontmatter.cs) |
| `storage/workspace_store.py` | [`Storage/WorkspaceStore.cs`](../dotnet/src/Strata.Infrastructure/Storage/WorkspaceStore.cs) |

A second cross-language fixture, `public_workspace/`, holds a Markdown layer and
a `workspace.json` written by Python; the .NET tests read both.

Three things worth knowing about this slice:

- **Pydantic's `extra="forbid"` is preserved.** Every persisted model carries
  `[JsonUnmappedMemberHandling(Disallow)]`, so a descriptor from a newer Strata
  fails loudly instead of losing fields on the next save.
- **Path safety got *stronger* than a literal port.** Python's `Path.resolve()`
  follows symlinks; .NET's `Path.GetFullPath` does not. `Paths.RealPath` resolves
  every existing component to its final target, so a symlink planted inside a
  layer and pointing outside it cannot defeat the containment check.
- **Divergence: record equality.** Pydantic models compare by field value; the C#
  records hold `IReadOnlyList`/`IReadOnlyDictionary` members, so `==` is reference
  equality on those — a descriptor that round-tripped through disk is never `==` to
  the one that was saved. Use `StrataJson.ValueEquals` when you mean value
  equality; it is derived from the same property set the file is, so unlike a
  hand-written `Equals` across a dozen records it cannot drift.

Frontmatter is emitted by YamlDotNet, not PyYAML. The *data* round-trips exactly
and both apps read each other's files, but byte-level formatting of a rewritten
frontmatter block may differ from PyYAML's. That is diff churn, not data loss.

## Phase 3 status — the service spine (**started**)

The services everything else stands on. New project
[`dotnet/src/Strata.Services`](../dotnet/src/Strata.Services), mirroring
`app/services/`.

| Python module | .NET port |
| --- | --- |
| `domain/ids.py` | [`Ids.cs`](../dotnet/src/Strata.Core/Ids.cs) |
| `domain/schema.py` (areas) | [`Schema/KnowledgeAreas.cs`](../dotnet/src/Strata.Core/Schema/KnowledgeAreas.cs) |
| `infrastructure/logging/logger.py` | [`Logging/IStrataLogger.cs`](../dotnet/src/Strata.Core/Logging/IStrataLogger.cs) (seam + null/recording impls) |
| `infrastructure/encryption/keyholder.py` | [`Encryption/KeyHolder.cs`](../dotnet/src/Strata.Infrastructure/Encryption/KeyHolder.cs) |
| `infrastructure/keychain/credentials.py` | [`Keychain/CredentialStore.cs`](../dotnet/src/Strata.Infrastructure/Keychain/CredentialStore.cs) |
| `services/encryption_service.py` | [`EncryptionService.cs`](../dotnet/src/Strata.Services/EncryptionService.cs) |
| `services/private_layer_access.py` | [`PrivateLayerAccess.cs`](../dotnet/src/Strata.Services/PrivateLayerAccess.cs) |
| `services/workspace_service.py` | [`WorkspaceService.cs`](../dotnet/src/Strata.Services/WorkspaceService.cs) |
| `services/view_service.py` | [`ViewService.cs`](../dotnet/src/Strata.Services/ViewService.cs) |
| `services/note_service.py` (**read half only**) | [`NoteReader.cs`](../dotnet/src/Strata.Services/NoteReader.cs) |

The whole private-layer lifecycle now works end to end in .NET: create a layer,
get a recovery key once, lock, reopen the workspace, unlock by password or
recovery key, remember the password in the OS keychain, rotate the key, and read
and write notes, folders and attachments inside it.

- **`keyring` → Windows Credential Manager** via `advapi32` (`DllImport`, not
  `LibraryImport`: the source generator needs `AllowUnsafeBlocks` and cannot
  marshal `CREDENTIAL`, and turning unsafe code on across the project that holds
  the crypto is not worth a keychain binding). It **fails closed** off Windows,
  exactly like the Python original — never a file fallback.
- **`NoteReader` is deliberately named for what it does.** It is the read half of
  `note_service.py` (list/locate across both storage kinds); the mutation half —
  create, update, rename, move, trash, link maintenance, versions — is not ported.
  Calling it `NoteService` would have implied otherwise.
- **`demo_content.py` is not ported.** `WorkspaceService.SeedDemoContent` is an
  injection point so `OpenOrCreate` keeps working; it seeds nothing by default.
- **Test-only KDF seam.** `KdfParams.Defaults` mirrors the Python suite's
  `cheap_kdf` fixture, so a suite that creates dozens of private layers does not
  ask for 256 MiB dozens of times. Only *new* layers are affected — unlock reads
  the parameters from the header. `ProductionKdfTests` asserts the production
  constants directly, so a real weakening cannot hide behind the seam.

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
2. Domain + workspace/layer/notes — **storage layer done** (see Phase 2 below);
   still to come: the workspace/layer/note **services** above these stores
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
| `app/infrastructure/storage` (all four modules) | ~830 | **ported**, cross-language fixtures for both layer kinds |
| `app/domain` | 2,531 | note, layer, workspace, view and id **models** ported (~750 of it); the rest is AI/graph/ops/history/etc. |
| `app/desktop` (Qt shell) | 1,648 | privacy/tray/taskbar behaviours proven in the WPF stub; no feature UI |
| `app/infrastructure` (rest: search, vector, crdt store/relay, ai providers) | ~2,700 | keychain + logging + keyholder ported; the rest not started |
| `app/services` (35 services) | 10,536 | **4 of 35 ported** (encryption, private-layer access, workspace, view) plus the read half of notes — ~1,700 lines of the Python. The remaining 31 are the app's behaviour and the bulk of the work |
| `app/bridge` (14 bridges) | 3,723 | not started — and the trust boundary must be re-reviewed, not just re-typed |
| `frontend` (React/TS) | 24,361 | discarded by the native decision; nothing replaces it yet |

Known gaps carried forward from Phase 0, unchanged:

- Capture exclusion on the Chrome backend PID — no browser process in .NET yet.
- Live media blur and `Ctrl+Shift+X` — settings persist; needs the research pane.
- WinUI 3 packaging — host is still WPF pending the Windows App SDK workload.

Resolved since Phase 1:

- ~~Argon2id cost in managed code is unmeasured~~ — measured above; ~2× the C
  implementation, still inside the intended budget.
- ~~`Ycs` unevaluated~~ — **`Ycs` is not on nuget.org at all.** The available
  candidate is **`YDotNet` 0.6.0** (plus `YDotNet.Native*`), which binds the same
  Rust `y-crdt` core that `pycrdt` binds. That is the structurally right choice:
  interop would be through one shared implementation rather than two independent
  ports of the Yjs update format. It does mean a native dependency, unlike the
  crypto path. Not yet taken — `UpdateSealing` seals and opens update bytes, but
  nothing in .NET *interprets* them.

Resolved since Phase 2:

- ~~`ViewConfig` models exist; the query engine does not~~ — `ViewService` ports
  the filter/sort/group engine, including the pseudo-fields and the
  numbers-first/empties-last sort key.
- ~~Record equality is a divergence to work around~~ — `StrataJson.ValueEquals`
  is the one way to compare persisted models by value.

Still open:

- Frontmatter re-emission differs from PyYAML byte-for-byte (data is identical).
- `note_service.py`'s mutation half, and 31 of 35 services.
- `demo_content.py` — the seam exists, the content does not.
- The credential store is Windows-only; macOS/Linux report unavailable and fail
  closed, which is correct but means no remembered passwords there.
