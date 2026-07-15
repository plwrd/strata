# Security Policy

Strata is **pre-alpha (0.1.0)** and has **not been security-audited**. The encryption layer is
specified but not yet implemented (it lands in Milestone 3). Please read
[THREAT_MODEL.md](THREAT_MODEL.md) before trusting this software with anything that matters.

---

## Reporting a vulnerability

> **TODO — placeholder contact.** `security@strata.example` is **not a real address**. A monitored
> contact address and a PGP key must be published before the first public build (this is a release
> blocker, tracked alongside [A-013](ASSUMPTIONS.md)).

| | |
| --- | --- |
| **Contact** | `security@strata.example` *(TODO: replace with a real, monitored address + PGP key)* |
| **Do not** | Open a public issue for a security bug. Do not post a PoC publicly before a fix ships. |
| **Please include** | Affected version/commit, platform, a reproduction, the impact you believe it has, and whether you have disclosed it elsewhere. |
| **Acknowledgement** | Within **3 business days**. |
| **Triage & severity assessment** | Within **10 business days**. |
| **Fix target** | Critical: 14 days. High: 30 days. Medium/Low: next scheduled release. |
| **Disclosure** | Coordinated. We will agree a date with you; **90 days** is our default cap, and we would rather ship a fix than argue about the calendar. |
| **Credit** | Offered by default; tell us if you would rather not be named. |
| **Bounty** | None. We are pre-alpha and have no funding for one. We will not pretend otherwise. |

**In scope:** the Strata application, the bridge, the encryption format, the storage layout, the build
and release pipeline.

**Out of scope** (see [THREAT_MODEL.md §0](THREAT_MODEL.md)): local malware; a compromised OS while a
layer is unlocked; physical attacks against an unlocked machine; social engineering of the user;
vulnerabilities in third-party AI providers; and anything requiring the attacker to already have code
execution as the user.

---

## Supported versions

| Version | Supported |
| --- | --- |
| `0.1.x` (pre-alpha) | Security fixes only, on the `main` branch. **Not supported for production use.** |
| Anything older | No. |

Because there is no auto-update yet ([A-009](ASSUMPTIONS.md)), a security fix means a new build that
users must install by hand. When auto-update lands (M11), this table gains a real support window.

---

## Security posture

**What Strata is designed to protect:** the contents of your **private layers** against someone who
obtains your disk — a stolen laptop, a mislaid backup drive, a sold hard disk.

**What Strata is designed to give you:** an honest, visible account of every byte that leaves your
machine.

**What Strata does not do:**

| We do not claim | Reality |
| --- | --- |
| "Zero knowledge" | Strata runs on your machine and holds your keys while unlocked. We do not use the phrase. |
| "Military-grade encryption" | It is XChaCha20-Poly1305 and Argon2id, used carefully. That is the honest sentence. |
| "Your data is safe from anything" | See the leakage statement below and in the threat model. |
| Protection from local malware | **Explicitly out of scope.** If an attacker runs code as you while a layer is unlocked, they have your notes. |
| Protection from a weak password | Argon2id makes each guess expensive. It does not make a bad password good. |

### What a private layer still leaks to someone holding your disk

Object count · approximate object sizes (blunted by padding buckets) · object mtimes · total layer
size · access patterns over repeated observation · **the fact that a private layer exists**.

This is documented in full in [THREAT_MODEL.md §5](THREAT_MODEL.md). If any of it is unacceptable for
your situation, Strata is not sufficient for you, and we would rather tell you that now.

### Also: enable full-disk encryption

Strata cannot stop your OS from writing your memory to a swap or hibernation file
([T-07](THREAT_MODEL.md)). BitLocker / FileVault / LUKS is the mitigation for that, and it is the OS's
job, not ours. **Strata is not a substitute for full-disk encryption.**

### Deleting text from a shared (collaborative) layer

A collaborative layer is a CRDT (M9, [ADR-0006](docs/adr/0006-crdt-selection.md)). Deleting text there
does **not** erase it immediately: a CRDT retains deleted content as a tombstone until the document is
**compacted**, so recently-deleted text may persist in the layer's (encrypted) update history for a
while. It is never plaintext on disk — every update is sealed under the layer key — and the relay only
ever sees ciphertext. But if you need a specific passage *gone*, delete it and then run compaction;
until then, treat it as still present in the history. This applies only to shared layers; a personal
private layer has no CRDT and no tombstones.

---

## Non-negotiable security rules

These are invariants. A pull request that violates one does not get merged, regardless of what it
enables. If you believe one must change, that is an ADR and a threat-model review, not a code review.

1. **No cryptography in JavaScript.** No key derivation, no encryption, no decryption, no key material
   of any kind in the renderer. Crypto happens in Python, in `app/crypto/`, and nowhere else.
2. **No arbitrary Python, shell, filesystem, or network access exposed on the bridge.** The bridge
   surface is a closed set of enumerated, schema-validated operations. No `eval`, no `exec`, no
   `subprocess`, no "run this path", no "fetch this URL", no generic `call(method, args)` dispatcher.
3. **Strict Content-Security-Policy**, enforced exactly as:
   ```
   default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'
   ```
   Loosening any directive requires a threat-model review. (`style-src 'unsafe-inline'` is present
   because the CSS-in-JS runtime requires it; it is a known, bounded exception and is the only one.)
4. **External navigation is blocked.** The WebEngine page refuses navigation away from
   `strata://app/`. External links open in the **OS browser**, after a confirmation dialog that shows
   the full destination URL.
5. **DevTools and the remote debugging port exist in development builds only** and are compiled out of
   release builds.
6. **AI never reads a locked layer.** Not for context, not for embeddings, not for search, not for a
   count, not for a filename. A locked layer contributes nothing and is not silently substituted.
7. **Imported and shared notes are untrusted data, never instructions.** Instruction/content
   separation in every prompt; no arbitrary tool execution; structured-output validation against a
   Pydantic schema; operations restricted to the user's explicit selection and to unlocked layers;
   path restriction; **preview-before-apply**.
8. **Every AI mutation is a transactional operation plan**: visual diff → explicit approval →
   all-or-nothing apply → single-unit undo. AI does not write to the workspace directly, ever.
9. **A privacy receipt is written for every remote AI call and every decrypted export.** No exception,
   no "internal" calls that skip it.
10. **Decrypted private-layer content never touches disk.** No temp files, no thumbnail cache, no
    plaintext search index, no staging directory. If an OS API requires a file path, the feature is
    disabled for private layers.
11. **Production bridge responses carry no stack traces and no filesystem paths.** Errors come from
    the closed enum below.
12. **Random 24-byte nonces, never reused, never derived from a counter.** AAD binds layer id, object
    id, object type, and format version to every ciphertext.
13. **No telemetry.** Not off-by-default — absent.

### The bridge contract

Every bridge call takes a JSON request envelope and returns a JSON response envelope.

```jsonc
// request
{ "v": 1, "requestId": "<uuid>", "payload": { /* ... */ } }

// response — success
{ "v": 1, "requestId": "<uuid>", "ok": true, "data": { /* ... */ } }

// response — failure
{ "v": 1, "requestId": "<uuid>", "ok": false,
  "error": { "code": "...", "message": "...", "retryable": false, "details": { /* ... */ } } }
```

**Error codes are a closed enum.** Nothing else may ever appear in `error.code`:

| Code | Meaning | Typically retryable |
| --- | --- | --- |
| `invalid_request` | Failed schema validation, malformed envelope, unknown field | No |
| `payload_too_large` | Request exceeded the **1 MiB** cap | No |
| `not_found` | The referenced workspace/layer/object does not exist | No |
| `permission_denied` | The operation is not permitted (e.g. layer AI policy is `never`) | No |
| `layer_locked` | The target layer is locked; unlock is required | No (until unlocked) |
| `conflict` | Concurrent modification; state has moved underneath the caller | Yes |
| `unsupported` | Not implemented in this milestone/platform | No |
| `cancelled` | The job was cancelled by the user | No |
| `provider_error` | An AI provider failed | Sometimes — see `retryable` |
| `internal` | An unexpected error. **Message is generic; details are logged locally, never returned.** | Sometimes |

Requests are validated with **Pydantic** before any side effect. Progress and events are pushed to the
frontend through a `JobBridge` Qt Signal carrying JSON — never by polling, and never by widening the
request surface.

---

## Cryptography

| | |
| --- | --- |
| Object & key-wrap AEAD | **XChaCha20-Poly1305** (PyNaCl / libsodium), 24-byte random nonce, 16-byte tag |
| Password KDF | **Argon2id**, versioned params in the layer header; defaults t=3, m=256 MiB, p=4, 16-byte random salt |
| Layer Data Key | Random 256-bit, wrapped independently by the password-derived KEK and (optionally) by a recovery key |
| Recovery key | Random 256-bit, Base32-grouped, **shown once**, never stored by Strata |
| Object ids | 32 bytes from the OS CSPRNG; filenames are opaque and random (no deterministic filename encryption) |
| Randomness | OS CSPRNG only (`os.urandom` / libsodium). No user-space PRNG for anything security-relevant. |

Full byte-level specification: [`docs/security/encryption-format.md`](docs/security/encryption-format.md).

**We do not roll our own primitives.** If a PR contains a hand-written cipher, mode, KDF, or MAC, it is
rejected on sight. We compose well-reviewed libraries and we get the *composition* reviewed.

---

## Supply chain

| Control | Status | Notes |
| --- | --- | --- |
| **Exact version pinning** | In place (M0) | Every dependency in `pyproject.toml` is `==`-pinned. Frontend deps are lockfile-pinned. |
| **Hash-pinned installs** | Planned (M11) | `--require-hashes` in CI and release builds. |
| **Lockfiles committed** | In place | Both Python and npm. |
| **SBOM** | Planned (M11) | CycloneDX SBOM published with every release artifact. |
| **Dependency advisory scanning** | Planned (M0→M11) | Automated in CI; a new advisory fails the build, it does not open a ticket to be ignored. |
| **Secret scanning** | Planned (M0) | Pre-commit hook + CI scan. A committed key means a rotated key, always. |
| **New-dependency review** | In place | Every new dependency needs a written justification in the PR. The dependency count is kept deliberately small; a small tree is a security control. |
| **Release signing** | Planned (M11) | Windows Authenticode, macOS notarization, detached signatures + SHA-256 checksums for all artifacts. |
| **Signing key custody** | Planned (M11) | Hardware-backed key. The key never exists on a CI runner in a form the runner can exfiltrate. |
| **Reproducible builds** | Aspirational (M11) | Full reproducibility with PyInstaller is hard; we will publish what we can verify and be honest about what we cannot. |
| **Update channel** | Does not exist (A-009) | No auto-update before M11. When it lands: signed manifest, signature verified before execution, anti-downgrade, offered-not-forced. |

If our **signing key** is ever compromised, signatures and update verification both fail as controls
([T-28](THREAT_MODEL.md)). Key custody is therefore a release-engineering requirement, not an app
feature, and it must be settled before the first signed build.

---

## What we promise, and what we don't

**We promise to:**
- Publish the encryption format and the storage layout so you can write your own reader and verify our
  claims — and so you are never locked in.
- Document what a private layer leaks, rather than claiming it leaks nothing.
- Tell you, in the UI, when a byte is about to leave your machine, and record it in a receipt.
- Fail closed: authentication failures are errors, not warnings; locked means locked; a validation
  failure means the operation does not happen.
- Ship no telemetry.
- Say "not implemented yet" instead of implying a milestone we haven't reached.

**We do not promise:**
- That Strata is secure against a compromised OS, local malware, or an attacker with code execution as
  your user.
- That your data is recoverable if you lose both your password and your recovery key. **It is not.
  There is no backdoor and no reset.** That is the point.
- That prompt injection is solved. It is not, by anyone. We constrain what a model can *do* — it can
  only propose a plan you review — rather than claiming we can stop it from being persuaded.
- That the code is bug-free, or that this design survives contact with a real auditor unchanged.
