# Format conformance fixtures

Byte-stable known-answer tests (KATs) for the C# encryption / CRDT port.

## Regenerate

From the repo root (with the project venv / deps available):

```bash
python tools/format_fixtures/generate.py
```

Output is written to [`tests/fixtures/format/`](../../tests/fixtures/format/). Regeneration
must be byte-identical when inputs are unchanged.

## Source of truth

Shipping Python under `app/infrastructure/encryption/` and `app/infrastructure/crdt/`.
Do **not** treat `docs/security/encryption-format.md` as byte-accurate.

## Vectors

| File | Pins |
| --- | --- |
| `primitives.json` | XChaCha20-Poly1305-IETF encrypt under a fixed key/nonce/AAD |
| `container_note.{json,bin}` | A sealed `TYPE_NOTE` object; the 71-byte header is the AAD |
| `layer_header.json` | Argon2id KEK + both wrap envelopes (reduced CI costs) |
| `rotation_journal.json` | The new key wrapped under the old one |
| `crdt_update.{json,bin}` | A sealed pycrdt/Yjs update, content-derived object id |
| `store_layer/` + `store_layer.json` | A whole private layer on disk — header, manifest, notes, folder, attachment |
| `public_workspace/` + `public_workspace.json` | A public Markdown layer and a `workspace.json` — derived note ids, frontmatter, descriptor schema |

The last two are the end-to-end ones: a port that opens `store_layer/` with nothing
but the password, and reads `public_workspace/`, can open a real workspace.

Timestamps in a public layer come from file mtimes, so `public_workspace.json`
pins content, ids, tags and links — never `created_at`/`updated_at`.

## Status

- **Phase 0:** fixtures committed; C# tests discover metadata and header framing.
- **Phase 1 (done):** `Strata.Infrastructure` opens and re-seals every encrypted
  vector byte-for-byte.
- **Phase 2 (stores done):** the Markdown store and workspace store read the
  Python-written `public_workspace/` fixture.

See `dotnet/tests/Strata.FormatConformance`.
