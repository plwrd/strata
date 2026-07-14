# Strata — Glossary

The canonical vocabulary. **Use these words, in code, in the UI, in commits, and in docs.** Where a
term has a tempting synonym, the synonym is listed as *not* the word we use — consistent naming is a
security property in a product where "locked" and "unmounted" mean genuinely different things and
confusing them means someone believes their data is protected when it is not.

Milestone tags (M0–M11) say when a concept becomes real. See
[PRODUCT_REQUIREMENTS.md](../../PRODUCT_REQUIREMENTS.md).

---

## Core structure

**Workspace** — A directory containing `workspace.json` and a `layers/` tree. The unit you back up,
move, or copy to a USB stick. There is no hidden application database elsewhere on the machine, and no
account. *Not:* "vault", "notebook", "library".

**Layer** — A collection of knowledge objects with a single storage and confidentiality policy. A
workspace has many. A layer is **public** or **private** — this is fixed at creation and is not a
setting you toggle.

**Public layer** — Stores objects as plain Markdown files plus attachments, with a derived
`index.sqlite` cache. Readable and editable by any other tool. **It offers no confidentiality.** It is
not "less encrypted"; it is not encrypted. ([A-014](../../ASSUMPTIONS.md))

**Private layer** — Stores every object as an independently encrypted file with an opaque random
filename. No plaintext names, folder tree, tags, links, or index exist on disk. Requires a password (or
recovery key) to unlock. ([docs/security/encryption-format.md](../security/encryption-format.md), M3)

**Knowledge object** — Anything Strata stores as a unit: notes, folders, attachments, tags, relations,
templates, saved views, tasks, Knowledge Lenses, privacy receipts. Every one has a stable random
128-bit id that does not change when it is renamed or moved. *Not:* "document", "item", "entity".

**Manifest** — The single encrypted object inside a private layer that holds everything a filesystem
would normally reveal: real names and titles, the folder tree, tags, properties, links, relations, and
the attachment map. Locked, a private layer is a pile of same-shaped random-named blobs; the manifest
is the only thing that makes them a workspace. (M3)

---

## Layer states

Four states. They are not synonyms, and conflating them in the UI is a bug.

| State | Meaning |
| --- | --- |
| **Unmounted** | The layer exists on disk but is not part of the current session. It contributes nothing to search, the graph, or AI. |
| **Mounted** | Registered with the workspace and visible in the UI. A public layer that is mounted is readable. A private layer that is mounted may still be **locked**. |
| **Locked** | *Private layers only.* The Layer Data Key is not in memory. No content, no titles, no counts, no existence-of-a-matching-object. Search returns nothing, the graph shows nothing, and **AI cannot read it under any policy**. |
| **Unlocked** | *Private layers only.* The LDK is in memory, the in-memory index is built, and content is readable subject to the layer's AI policy. |

**Mount** and **unlock** are different actions. Mounting makes a layer *present*. Unlocking makes it
*readable*. A mounted-but-locked private layer is the normal resting state.

**Lock** — Not a UI state change. Locking **destroys derived state**: keys zeroized where the runtime
permits, in-memory index closed, editor buffers, previews, thumbnails, and graph labels cleared, AI
context dropped, in-flight AI operations for that layer cancelled.
([FR-011](../../PRODUCT_REQUIREMENTS.md))

---

## Keys

**LDK — Layer Data Key** — A random 256-bit key, one per private layer. Encrypts every object in that
layer. Exists in plaintext **only in RAM, only while the layer is unlocked**. On disk it exists only
wrapped.

**KEK — Key-Encryption Key** — A 256-bit key derived from the layer password (Argon2id) or from the
recovery key (HKDF). Its only job is to wrap and unwrap the LDK. It is discarded immediately after
unlock.

**Recovery key** — An optional random 256-bit key, displayed as grouped Base32 **exactly once**, never
stored by Strata. It independently wraps the same LDK, so it is a second door to the same room — not a
backup of the password.

**Wrap / rewrap** — Encrypting the LDK under a KEK. **Password change is a rewrap only**: the LDK does
not change and no object is re-encrypted. Fast, and it is why changing your password does not take an
hour.

**Key rotation** — Generating a **new LDK** and re-encrypting every object under it, as a resumable
background job. Required after revoking a collaborator. Rotation prevents *future* access; it cannot
retract what someone already read. ([T-25](../../THREAT_MODEL.md))

**Zeroization** — Overwriting key material on lock. Strata does this where the runtime permits and
**does not claim it is reliable**: Python cannot guarantee a secret is gone from RAM.
([T-04](../../THREAT_MODEL.md))

---

## Sharing

**Sharing mode** — A per-layer property. One of:

| Mode | Meaning |
| --- | --- |
| **personal** | Not shared. The default. |
| **shared-password** | Collaborators hold the layer password. Simple, and **it provides no attribution** — everyone holds the same key, so any member's writes are indistinguishable from any other's. |
| **identity-managed** | Per-collaborator keys. Revocable, attributable. (M9) |

**Relay** — An optional dumb server that forwards collaboration traffic. It never receives layer keys
or plaintext, holds no authoritative state, and can be unavailable indefinitely without stopping you
from working. (M9)

**Revocation** — Removing a collaborator's future access, followed by a key rotation. **You cannot
un-share a secret.** The UI says this in plain words at the moment of revocation.
([T-25](../../THREAT_MODEL.md))

---

## Exploration

**Knowledge Lens** — A **saved multi-layer perspective**: a set of layers, plus filters, plus a graph
camera/layout and visual encoding. It is itself a knowledge object, so it can be saved, shared, and
fed to the AI Context Composer as a selection. *Not:* "workspace view", "profile", "preset". (M6)

**Saved view** — A persisted database view: filters, sort, grouping, and visible columns over a
filtered object set (table, board, calendar, gallery). Also a knowledge object. (M10)

**Graph** — Objects as nodes; links, tags, folders, and typed relations as edges. Rendered in 3D
(Three.js / react-three-fiber) with a **first-class 2D fallback** — not a stub — used automatically
under reduced-motion or low-GPU conditions. Locked layers contribute no nodes, no edges, and no
labels. (M6)

---

## AI

**AI Context Composer** — The single surface where **selection + prompt + provider + export target**
come together. It shows the **exact bytes** that will leave the machine before they leave: the object
list, the token count, and any truncation. No hidden context is ever appended. (M7)

**Context selection** — The explicit set of objects, subgraph, or Knowledge Lens the user chose. It is
also a **security boundary**: an AI operation plan may only touch objects inside the selection, which
is what stops an injected instruction from reaching the rest of your workspace.

**AI policy** — Per-layer: `never`, `local-only`, `ask-each-time`, or `allow`. Private layers default
to `ask-each-time`. A layer marked `never` cannot be used with any provider. **No policy permits
reading a locked layer.** ([A-011](../../ASSUMPTIONS.md))

**Privacy receipt** — An append-only record written for **every** remote AI call and **every** decrypted
export: timestamp, provider, model, layers touched, object ids, byte count, and whether the content came
from a private layer. It records what left. It cannot recall it. (M7)

**Operation plan** — A validated, ordered list of typed operations (create/update/delete object, set
property, add/remove link, move, tag) that the AI **proposes**. AI never writes to the workspace
directly. The plan is schema-validated, constrained to the selection, shown as a **visual diff**,
explicitly approved, applied **transactionally** (all or nothing), and **undoable as a single unit**.
(M8)

**Prompt injection** — An attack where content inside a note (imported, or written by a malicious
collaborator) is read by the model as *instructions* rather than as *data*. Strata's defence is
architectural: the model has **no capabilities** — it cannot call a tool, touch the filesystem, or make
a request. It can only emit a plan, which is validated, bounded to the selection, and reviewed by a
human. **We do not claim prompt injection is solved.** Nobody has solved it.
([T-16](../../THREAT_MODEL.md))

**Untrusted data** — Any content Strata did not itself generate: imported Markdown, shared-layer
content, AI output. It is never concatenated into an instruction channel, never executed, and never
trusted to be well-formed.

---

## Platform

**Bridge** — The QWebChannel boundary between the renderer (semi-trusted) and Python (trusted). Eleven
feature-scoped `QObject`s — `WorkspaceBridge`, `LayerBridge`, `NotesBridge`, `GraphBridge`,
`SearchBridge`, `AIComposerBridge`, `ExportBridge`, `CollaborationBridge`, `SettingsBridge`,
`SnapshotBridge`, `JobBridge` — **not** one god object with a generic dispatcher
([A-007](../../ASSUMPTIONS.md)). The **primary trust boundary of the product**.

**Envelope** — The JSON wrapper on every bridge call.
Request: `{"v":1,"requestId":"<uuid>","payload":{…}}`.
Response: `{"v":1,"requestId":"…","ok":true,"data":{…}}` or
`{"v":1,"requestId":"…","ok":false,"error":{"code":…,"message":…,"retryable":…,"details":{…}}}`.
Payload cap: **1 MiB**.

**Error code** — A **closed enum**. Nothing else may ever appear: `invalid_request`,
`payload_too_large`, `not_found`, `permission_denied`, `layer_locked`, `conflict`, `unsupported`,
`cancelled`, `provider_error`, `internal`.

**Job** — Any work that could take longer than a frame: indexing, graph layout, key rotation,
import/export, snapshots, AI calls. Runs off the UI thread, has a `jobId`, reports progress through the
`JobBridge` signal, and is cancellable. Blocking the Qt event loop is a bug, not a slow path.

**`strata://`** — The custom URL scheme serving the bundled frontend at `strata://app/index.html`,
straight from `frontend/dist`, in-process. **No HTTP server and no listening port** — a localhost server
would be reachable by any other local process, including a browser tab on any website.
([A-006](../../ASSUMPTIONS.md))

---

## Words we do not use

| Not this | Because |
| --- | --- |
| **"Zero knowledge"** | Strata runs on your machine and holds your keys while unlocked. The phrase would be false. |
| **"Military-grade encryption"** | It is XChaCha20-Poly1305 and Argon2id, used carefully. That sentence is both more honest and more checkable. |
| **"Unbreakable" / "bank-level" / "NSA-proof"** | Unverifiable marketing. |
| **"Securely deleted"** | On an SSD with wear levelling, overwriting blocks is theatre. What remains is **ciphertext**, which is the actual mitigation. |
| **"Vault"** | Implies one container. Strata has layers, and the distinction matters. |
| **"Sync"** (for collaboration before M9) | Nothing syncs yet. Do not imply it does. |
| **"AI writes your notes"** | AI **proposes** an operation plan. A human approves it. The difference is the entire safety model. |
