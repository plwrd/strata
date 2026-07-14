# ADR-0005: Encryption primitives (XChaCha20-Poly1305 + Argon2id)

**Status:** Accepted, 2026-07-14

## Context

Private layers must be confidential and tamper-evident against an attacker who holds the encrypted
workspace but not the password (ADR-0004 defines what the on-disk layout does and does not hide; this
ADR defines what protects the bytes). The threat model assumes:

- The attacker has the full directory, possibly many versions of it over time.
- The attacker can mount an offline dictionary/brute-force attack against the password at whatever rate
  their hardware allows (GPU/ASIC).
- The attacker can *modify* the encrypted files and hand them back to the user (an evil-maid or
  malicious-sync-server scenario). Confidentiality alone is insufficient; we need integrity, and we
  need it bound to *where* the ciphertext sits, not just to its bytes — otherwise an attacker can swap
  object A's ciphertext for object B's, or replay an old version of a note, without breaking any MAC.
- We will need per-user identity keys for M9 collaboration (peer authentication, signed updates).

Constraints: Python host (ADR-0001); no custom cryptography under any circumstances; parameters must be
**versioned**, because Argon2 costs that are right in 2026 will be wrong in 2031 and we must be able to
raise them without breaking existing layers.

## Decision

### AEAD: XChaCha20-Poly1305 (libsodium via PyNaCl)

Every object in a private layer (ADR-0004) — notes, attachments, index shards, the manifest — is sealed
with **XChaCha20-Poly1305** using libsodium's `crypto_aead_xchacha20poly1305_ietf` construction, exposed
through **PyNaCl**.

- **Key:** the 256-bit layer data key (LDK), below.
- **Nonce: 24 bytes, drawn fresh at random from the OS CSPRNG for every single encryption.** With a
  192-bit nonce, random generation is safe: the birthday bound puts collision probability below 2^-32
  even after 2^80 encryptions. This is the entire reason for choosing X-ChaCha over vanilla
  ChaCha20-Poly1305 or AES-GCM — **we never have to maintain a counter, and we can therefore never
  reuse a nonce by getting counter state wrong across processes, crashes, restores-from-backup, or two
  devices in M9.** Nonce management is the single most common way real systems break AEAD, and we
  design it out rather than manage it.
- **Nonce storage:** prepended to the ciphertext in the object file. Not secret.
- **Associated data (AAD) — bound on every object:**

  ```
  AAD = canonical_cbor({
    "fmt":  "strata.object.v1",   # format version
    "lid":  <layer_id>,           # which layer this object belongs to
    "oid":  <object_id>,          # the 32-hex random id = the file's own name
    "type": "note"|"attachment"|"manifest"|"index"|"embedding"|"snapshot"
  })
  ```

  This is what makes the ciphertext **positionally bound**. An attacker cannot move object `3f9c…`'s
  bytes into file `a0d4…` (the `oid` won't match, so the Poly1305 tag fails), cannot move an object
  between layers (`lid`), cannot pass off an attachment as a manifest (`type`), and cannot roll the
  format back (`fmt`). Decryption is done with the AAD reconstructed **from the file's location and the
  header**, never from anything inside the file — so a lie inside the file cannot make the AAD match.

  Note what this does *not* stop: **replay of an older version of the same object id.** An attacker with
  an old backup can restore an old object with the same id and it will decrypt cleanly. Defending
  against that requires a monotonic version/counter in the manifest (which is itself an object, so it
  must be protected by an out-of-band anchor) — see "Negative" and the M11 hardening item.

### Password KDF: Argon2id (argon2-cffi), versioned

The password never touches an object. It derives a **key-encryption key (KEK)**:

```
KEK = Argon2id(password, salt, t=3, m=262144 KiB (256 MiB), p=4, out=32 bytes)   # kdf_version 1
salt = 16 random bytes, stored in layer.header
```

- **Argon2id**, not Argon2i or Argon2d: id is the hybrid recommended by RFC 9106 and by the Argon2
  authors for password hashing where side-channel and GPU resistance both matter.
- **Parameters are stored per-layer in `layer.header`** (`{alg, v, t, m_kib, p, salt}`) and are read
  from the header at unlock, never hardcoded at the call site. A `kdf_version` registry in code maps
  version → defaults. **Raising the defaults does not break old layers**: an old layer unlocks with its
  own recorded params, and the UI can offer "strengthen this layer" (re-derive the KEK at the new
  params and re-wrap the LDK — cheap, because it only rewrites the header, not the data).
- The 256 MiB / t=3 / p=4 default targets roughly **0.5–1.5 s** on the reference machine. It runs on a
  worker (ADR-0001, ADR-0003) and reports progress as a job. On memory-constrained machines the app may
  select a lower-memory profile at *layer creation*, and it says so explicitly in the UI — it does not
  silently weaken a layer.

### Key hierarchy

```
password ──Argon2id(salt, params)──▶ KEK (256-bit, memory only, zeroized on lock)
                                      │
                                      └─ XChaCha20-Poly1305 ─▶ wrapped_ldk  (in layer.header)
                                                                    │
recovery key (32 random bytes, ──XChaCha20-Poly1305──▶ recovery_wrapped_ldk (in layer.header, optional)
 shown once, base32 to the user)                                    │
                                                                    ▼
                                                    LDK  (256-bit random, memory only)
                                                                    │
                                              ┌─────────────────────┴──────────────────────┐
                                              ▼                                            ▼
                                   object encryption (AEAD + AAD)              (M9) per-doc CRDT
                                                                                 update encryption
```

- The **LDK is 256 random bits** from the OS CSPRNG, generated at layer creation. It is *never* derived
  from the password. This is what makes password change a header rewrite rather than a full re-encrypt
  of every object, and it is what allows two independent unwrap paths (password, recovery key) to the
  same data.
- The **recovery key** is optional, generated at random, wrapped over the *same* LDK, displayed to the
  user exactly once, and never stored by the app. If the user loses both the password and the recovery
  key, **the data is unrecoverable**, and the product must say this in plain language at layer creation.
  There is no backdoor, no escrow, and no reset. That is the point.
- Both the KEK and the LDK live only in Python process memory. They are **never** sent over the bridge
  (ADR-0003), never written to disk unwrapped, never logged. On lock, on workspace close, and on
  application exit they are overwritten and dropped. (We note honestly that Python offers no reliable
  memory zeroization — `bytes` are immutable and may be copied by the allocator or the GC. We use
  `bytearray` + explicit overwrite for key material, and `sodium_memzero` where PyNaCl exposes it, and
  we accept that a memory-dump attacker against a *running, unlocked* process wins. That is stated in
  THREAT_MODEL, not hidden.)

### Identity keys (M9)

Peer identity for collaboration uses **X25519** (key agreement) and **Ed25519** (signatures). The
`cryptography` package is retained as a dependency for this work — its X.509/PKCS#8 serialisation and
its key-handling API are better suited to an identity subsystem than PyNaCl's — though PyNaCl's
`crypto_box`/`crypto_sign` are functionally equivalent and either may be used. The identity private key
is itself sealed with the same AEAD under a key derived from the user's workspace passphrase.
**The concrete session-key-agreement protocol for M9 is deliberately not specified here** — it is a
separate design with its own ADR, due in M9. What is fixed now is the primitive family, so that M3's
dependency set does not have to change.

### Explicit prohibitions

These are non-negotiable and reviewers should treat a violation as a release blocker:

- **No custom cryptography.** No hand-rolled constructions, no "we just XOR a hash here", no novel
  combinations of primitives. If a design needs a primitive we do not have, we adopt a standard one or
  we change the design.
- **No AES-GCM with counter-based nonces.** (See alternatives.)
- **No password-as-key.** The password derives a KEK and nothing else, ever.
- **No ECB, no unauthenticated modes, no encrypt-then-hope.** Every ciphertext is AEAD-authenticated.
- **No secret-dependent branching or comparison** in our code: constant-time compare
  (`hmac.compare_digest` / `sodium_memcmp`) for anything key-adjacent.
- **No key material across the bridge.** Ever.

## Consequences

### Positive

- **Nonce misuse is structurally impossible.** Random 192-bit nonces remove the single most common
  catastrophic AEAD failure mode, and they remove it in exactly the scenarios (multi-device, crash,
  restore-from-backup) where counter schemes fail hardest — which is precisely the scenario M9
  introduces.
- **Positional binding via AAD** turns whole classes of attack (object swap, cross-layer transplant,
  type confusion, format rollback) into decryption failures rather than exploits.
- **The LDK indirection** makes password change and recovery-key support O(header) instead of
  O(workspace), and it makes multi-recipient wrapping (a future "share this layer with a teammate's
  public key") a natural extension rather than a rewrite.
- **Versioned KDF params** mean we can raise the cost of the offline attack in 2029 without a migration
  that touches user data.
- libsodium and Argon2 are among the most scrutinised implementations in existence, and PyNaCl /
  argon2-cffi are thin, well-maintained bindings to them. We are not the weakest link in the
  cryptography; we are the weakest link in the *plumbing*, which is where our review effort should go.
- ChaCha20 is constant-time in software on every platform we ship, with no dependence on hardware AES —
  relevant because we cannot assume AES-NI on every user's machine, and a table-driven AES fallback
  would be both slow and cache-timing-vulnerable.

### Negative

- **Unlock is deliberately slow (0.5–1.5 s) and costs 256 MiB of RAM.** Users will feel it. On a machine
  with 4 GB of RAM, opening three private layers at once is a real memory event. This is the intended
  trade — it is what makes offline brute force expensive — but it must be surfaced honestly (progress
  UI, and a warning if the machine cannot support the default profile) rather than tuned down quietly.
- **A weak password remains a weak password.** Argon2id at 256 MiB raises the cost per guess by orders
  of magnitude; it does not save `password123`. Password strength UX (zxcvbn-style estimation, a hard
  floor, and honest messaging about what the estimate means) is a required part of the feature, not a
  nicety.
- **Replay of an old object version is not prevented** by AEAD+AAD alone. An attacker with an old copy
  of the workspace can revert individual objects, or the whole manifest, and everything will verify. The
  mitigation is a monotonic manifest version plus an integrity anchor the attacker cannot roll back
  (e.g. a signed head stored in the OS keychain alongside the credential, checked at unlock, warning
  loudly on regression). **This is deferred to M11 (production hardening) and is called out as a known
  gap in THREAT_MODEL until then.** We are not pretending it is solved.
- **No memory-safe key handling in Python.** A `bytes` key can be copied by the interpreter; we cannot
  guarantee erasure. An attacker with a memory dump of an unlocked process, or with the ability to
  attach a debugger, gets the LDK. Locking the layer helps only if we have actually dropped every
  reference — which requires discipline in every code path that touches a key.
- We carry two crypto dependencies (PyNaCl **and** `cryptography`), which means two native build
  toolchains in CI and two supply chains to watch. Justified by M9, but it is a real cost paid from M0.

### Neutral

- Ciphertext expansion is 24 (nonce) + 16 (Poly1305 tag) = **40 bytes per object**, plus padding to the
  bucket boundary (ADR-0004). Negligible against the padding.
- XChaCha20-Poly1305 is not a NIST/FIPS-approved construction. If a future customer requires FIPS 140
  validation, this decision blocks it and would need revisiting — with AES-256-GCM-SIV as the likely
  replacement, not plain GCM. Recording this so nobody is surprised.
- Argon2id at these parameters is *not* suitable for a per-object or per-request operation. It runs
  exactly once per unlock. Any proposal to call it in a loop is a design error.
- The AEAD is not key-committing. Poly1305 tags do not commit to the key, so a maliciously crafted
  ciphertext can in principle decrypt under two different keys. This matters for multi-recipient /
  partitioning-oracle scenarios, not for our single-LDK-per-layer design today, but it becomes relevant
  if layer sharing with multiple wrapped keys ships — at which point a key-commitment step (e.g. a
  committing-AEAD transform, or a KDF-derived commitment stored in the header) must be added.

## Alternatives considered

### AES-256-GCM

The default choice almost everywhere, hardware-accelerated (AES-NI), FIPS-approved.

**Why rejected:** the 96-bit nonce. With random 96-bit nonces, the birthday bound means collision
probability becomes non-negligible around 2^32 encryptions under one key — and a single nonce collision
in GCM is **catastrophic**: it leaks the XOR of two plaintexts *and* enables forgery of the
authentication key. A notes app that encrypts every object on every save, across multiple devices, over
years, is exactly the workload that walks into that bound. The alternative — a counter-based nonce —
requires durable, monotonic, per-key counter state that survives crashes, backups, restores, and (in
M9) two devices using the same key concurrently. That state is a bug waiting to happen, and the failure
is silent and total. XChaCha20's 192-bit nonce makes the problem vanish. We give up AES-NI throughput;
ChaCha20 in software is fast enough (GB/s) that this is not a real cost for our object sizes.

### AES-256-GCM-SIV

Nonce-misuse-resistant AES. Would genuinely fix the nonce problem.

**Why rejected (for now):** it is not exposed by libsodium, so it would mean going through
`cryptography`/OpenSSL for the hot path, and its misuse-resistance comes at the price of two passes over
the plaintext. XChaCha20-Poly1305 solves the same problem more simply with a primitive libsodium
exposes directly. GCM-SIV is, however, the correct answer if a FIPS requirement ever forces AES.

### scrypt

Memory-hard, widely deployed, well understood.

**Why rejected:** Argon2id won the Password Hashing Competition and is the current recommendation
(RFC 9106, OWASP). scrypt is not broken; Argon2id simply has better resistance to the specific
GPU/ASIC tradeoffs that matter for offline attack, and a cleaner parameterisation
(time/memory/parallelism as independent knobs). Choosing the current recommendation over the previous
one needs no stronger justification than that.

### PBKDF2-HMAC-SHA256

Ubiquitous, in every stdlib, FIPS-approved.

**Why rejected:** it is not memory-hard. Its only cost knob is iterations, which GPUs and ASICs
parallelise nearly for free. Against a determined offline attacker with a stolen laptop — our exact
threat model — PBKDF2 buys perhaps 2–3 orders of magnitude where Argon2id at 256 MiB buys many more,
because the attacker must now provision 256 MiB of fast memory *per parallel guess*. Using PBKDF2 here
would be choosing the weakest acceptable option for the one thing that stands between a stolen disk and
the user's notes.

### SQLCipher / whole-database encryption

Put everything in one SQLite database and encrypt the pages.

**Why rejected:** three reasons. (1) It leaks structure — the page layout, table sizes, and index shapes
are visible in the encrypted file's growth and access pattern, and SQLCipher does not pad. (2) It does
not cover attachments; a 40 MB PDF either goes in a BLOB (making the database a monolith that resyncs
whole, and making page-level encryption a poor fit) or sits outside the database unencrypted, which is
absurd. (3) It welds our storage format to a specific database and a specific fork of it, permanently,
for a product whose data-longevity promise is the reason people trust it with their notes. It also
conflicts directly with ADR-0004's object store, which was chosen precisely so that ordinary backup and
sync tools work.

### Encrypt with a key derived directly from the password (no LDK)

Simpler: one fewer key, no wrapping.

**Why rejected:** password change would require re-encrypting every object in the layer, a recovery key
would be impossible (there is only one path to the key), and sharing a layer with another identity would
be impossible. The LDK indirection costs one AEAD operation at unlock and buys all three.

### Roll our own AEAD / "just use a hash and XOR"

**Why rejected:** no. There is no version of this that ends well, and the fact that it is tempting when
a primitive doesn't quite fit is exactly why the prohibition is written down.

## Revisit when

- Hardware or attack developments make the Argon2id defaults (t=3, m=256 MiB, p=4) inadequate — reviewed
  at least **annually**, and the `kdf_version` registry exists precisely so this is a config change.
- **Layer sharing with multiple wrapped keys ships** — at that point the non-key-committing AEAD becomes
  a real issue and a key-commitment mechanism must be designed in.
- A **FIPS 140 requirement** appears from a customer or a market we want; the migration target is
  AES-256-GCM-SIV, and it is a format-version bump.
- **M11**: the object-replay/rollback gap is closed with a monotonic manifest version and a keychain-anchored
  integrity head. Until then it is an accepted, documented gap.
- A practical attack on ChaCha20, Poly1305, or Argon2id is published. (In which case, so is everyone
  else's.)
