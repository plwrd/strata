# Strata .NET host (native migration)

The C# / native UI migration. See
[`docs/csharp-migration-plan.md`](../docs/csharp-migration-plan.md) for the plan
and the parity ledger — this tree is **not** a full port of the app yet.

- **Phase 0 (done)** — solution, WPF shell stub proving the Win32 privacy APIs,
  frozen format fixtures.
- **Phase 1 (done)** — encryption, object container, layer header, rotation
  journal, sealed CRDT updates and the encrypted store. Every committed vector in
  `tests/fixtures/format` opens and re-seals byte-for-byte, including a whole
  private layer written by the Python app.

```bash
dotnet build Strata.sln
dotnet test Strata.sln
dotnet run --project src/Strata.App
```
