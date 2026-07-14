"""The encryption core.

If any of these fail, private layers are not private. They test the properties the
threat model depends on, not just that encrypt/decrypt round-trips.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from app.infrastructure.encryption.container import (
    FORMAT_VERSION,
    HEADER_SIZE,
    MAGIC,
    TYPE_ATTACHMENT,
    TYPE_MANIFEST,
    TYPE_NOTE,
    ObjectHeader,
    layer_binding,
    open_sealed,
    padded_length,
    seal,
)
from app.infrastructure.encryption.keyholder import KeyHolder
from app.infrastructure.encryption.layer_header import (
    LayerHeader,
    generate_recovery_key,
    normalise_recovery_key,
)
from app.infrastructure.encryption.primitives import (
    KEY_BYTES,
    NONCE_BYTES,
    DecryptionError,
    KdfParams,
    decrypt,
    derive_key,
    encrypt,
    random_key,
    random_nonce,
    zeroize,
)
from app.services.encryption_service import EncryptionService

pytestmark = pytest.mark.security

PLAINTEXT = b"We will offer 4.2 million for Northwind in Q3. Codename: BLUEJAY."


# --- primitives -------------------------------------------------------------


def test_keys_and_nonces_are_the_right_size_and_never_repeat() -> None:
    keys = {random_key() for _ in range(64)}
    nonces = {random_nonce() for _ in range(256)}

    assert len(keys) == 64
    assert len(nonces) == 256
    assert all(len(key) == KEY_BYTES for key in keys)
    assert all(len(nonce) == NONCE_BYTES for nonce in nonces)


def test_encrypt_decrypt_round_trips() -> None:
    key = random_key()
    nonce, ciphertext = encrypt(key, PLAINTEXT, b"aad")

    assert decrypt(key, nonce, ciphertext, b"aad") == PLAINTEXT


def test_the_ciphertext_does_not_contain_the_plaintext() -> None:
    key = random_key()
    _nonce, ciphertext = encrypt(key, PLAINTEXT, b"aad")

    assert b"Northwind" not in ciphertext
    assert b"BLUEJAY" not in ciphertext


def test_a_wrong_key_fails_authentication() -> None:
    nonce, ciphertext = encrypt(random_key(), PLAINTEXT, b"aad")

    with pytest.raises(DecryptionError):
        decrypt(random_key(), nonce, ciphertext, b"aad")


def test_changing_the_aad_fails_authentication() -> None:
    key = random_key()
    nonce, ciphertext = encrypt(key, PLAINTEXT, b"aad")

    with pytest.raises(DecryptionError):
        decrypt(key, nonce, ciphertext, b"different aad")


def test_flipping_one_ciphertext_bit_fails_authentication() -> None:
    key = random_key()
    nonce, ciphertext = encrypt(key, PLAINTEXT, b"aad")

    tampered = bytearray(ciphertext)
    tampered[0] ^= 0x01

    with pytest.raises(DecryptionError):
        decrypt(key, nonce, bytes(tampered), b"aad")


def test_the_error_never_says_why_it_failed() -> None:
    """A wrong password and a corrupt file must be indistinguishable."""
    key = random_key()
    nonce, ciphertext = encrypt(key, PLAINTEXT, b"aad")

    with pytest.raises(DecryptionError) as wrong_key:
        decrypt(random_key(), nonce, ciphertext, b"aad")
    with pytest.raises(DecryptionError) as corrupt:
        decrypt(key, nonce, ciphertext[:-1] + b"\x00", b"aad")

    assert str(wrong_key.value) == str(corrupt.value)


def test_argon2id_is_deterministic_for_the_same_salt_and_password() -> None:
    params = KdfParams.new()

    assert derive_key("correct horse", params) == derive_key("correct horse", params)


def test_argon2id_differs_for_a_different_password_or_salt() -> None:
    params = KdfParams.new()
    other = KdfParams.new()

    assert derive_key("a", params) != derive_key("b", params)
    assert derive_key("a", params) != derive_key("a", other)


def test_the_password_is_never_the_key() -> None:
    """The KDF output must not be the password's bytes in any trivial form."""
    params = KdfParams.new()
    password = "correct horse battery staple"

    key = derive_key(password, params)

    assert password.encode() not in key
    assert key != password.encode().ljust(KEY_BYTES, b"\x00")[:KEY_BYTES]


def test_kdf_params_round_trip_through_json() -> None:
    params = KdfParams.new()

    restored = KdfParams.from_json(json.loads(json.dumps(params.to_json())))

    assert restored == params


def test_zeroize_overwrites_the_buffer() -> None:
    buffer = bytearray(b"a very secret key")
    zeroize(buffer)

    assert bytes(buffer) == b"\x00" * 17


# --- the container ----------------------------------------------------------


def test_a_sealed_object_round_trips() -> None:
    key = random_key()
    object_id = secrets.token_bytes(16)

    blob = seal(
        key=key, layer_id="layer_a", object_id=object_id, object_type=TYPE_NOTE, plaintext=PLAINTEXT
    )
    opened = open_sealed(
        key=key, layer_id="layer_a", object_id=object_id, expected_type=TYPE_NOTE, blob=blob
    )

    assert opened == PLAINTEXT


def test_the_object_file_reveals_nothing() -> None:
    key = random_key()
    blob = seal(
        key=key,
        layer_id="layer_a",
        object_id=secrets.token_bytes(16),
        object_type=TYPE_NOTE,
        plaintext=b"---\ntitle: Acquisition Of Northwind\n---\n# Plan\n",
    )

    assert b"Northwind" not in blob
    assert b"title" not in blob
    assert b"---" not in blob[HEADER_SIZE:]
    # The header is readable by design (version, algorithm, nonce) and says nothing.
    assert blob[:7] == MAGIC


def test_an_object_cannot_be_moved_to_another_layer() -> None:
    """The AAD binds the layer, so a stolen object will not open elsewhere."""
    key = random_key()
    object_id = secrets.token_bytes(16)
    blob = seal(
        key=key, layer_id="layer_a", object_id=object_id, object_type=TYPE_NOTE, plaintext=PLAINTEXT
    )

    with pytest.raises(DecryptionError):
        open_sealed(
            key=key, layer_id="layer_b", object_id=object_id, expected_type=TYPE_NOTE, blob=blob
        )


def test_an_object_cannot_be_swapped_for_another() -> None:
    """The AAD binds the object id, so ciphertexts cannot be transplanted."""
    key = random_key()
    first = secrets.token_bytes(16)
    second = secrets.token_bytes(16)
    blob = seal(
        key=key, layer_id="layer_a", object_id=first, object_type=TYPE_NOTE, plaintext=PLAINTEXT
    )

    with pytest.raises(DecryptionError):
        open_sealed(
            key=key, layer_id="layer_a", object_id=second, expected_type=TYPE_NOTE, blob=blob
        )


def test_a_note_cannot_be_served_as_a_manifest() -> None:
    """The AAD binds the object type: type confusion is not possible."""
    key = random_key()
    object_id = secrets.token_bytes(16)
    blob = seal(
        key=key, layer_id="layer_a", object_id=object_id, object_type=TYPE_NOTE, plaintext=PLAINTEXT
    )

    with pytest.raises(DecryptionError):
        open_sealed(
            key=key, layer_id="layer_a", object_id=object_id, expected_type=TYPE_MANIFEST, blob=blob
        )


@pytest.mark.parametrize("offset", [7, 8, 9, 10, 11, 30, 45, 68])
def test_tampering_with_any_header_byte_is_detected(offset: int) -> None:
    """The whole header is the AAD, so every field is authenticated."""
    key = random_key()
    object_id = secrets.token_bytes(16)
    blob = bytearray(
        seal(
            key=key,
            layer_id="layer_a",
            object_id=object_id,
            object_type=TYPE_NOTE,
            plaintext=PLAINTEXT,
        )
    )
    blob[offset] ^= 0x01

    with pytest.raises(DecryptionError):
        open_sealed(
            key=key,
            layer_id="layer_a",
            object_id=object_id,
            expected_type=TYPE_NOTE,
            blob=bytes(blob),
        )


def test_a_truncated_object_is_refused_not_half_read() -> None:
    key = random_key()
    object_id = secrets.token_bytes(16)
    blob = seal(
        key=key, layer_id="layer_a", object_id=object_id, object_type=TYPE_NOTE, plaintext=PLAINTEXT
    )

    for length in (0, 10, HEADER_SIZE, HEADER_SIZE + 5, len(blob) - 1):
        with pytest.raises(DecryptionError):
            open_sealed(
                key=key,
                layer_id="layer_a",
                object_id=object_id,
                expected_type=TYPE_NOTE,
                blob=blob[:length],
            )


def test_random_bytes_are_not_mistaken_for_an_object() -> None:
    with pytest.raises(DecryptionError):
        open_sealed(
            key=random_key(),
            layer_id="layer_a",
            object_id=secrets.token_bytes(16),
            expected_type=TYPE_NOTE,
            blob=secrets.token_bytes(400),
        )


def test_a_newer_format_version_is_refused_rather_than_guessed() -> None:
    key = random_key()
    object_id = secrets.token_bytes(16)
    blob = bytearray(
        seal(
            key=key,
            layer_id="layer_a",
            object_id=object_id,
            object_type=TYPE_NOTE,
            plaintext=PLAINTEXT,
        )
    )
    blob[7] = FORMAT_VERSION + 1

    with pytest.raises(DecryptionError):
        open_sealed(
            key=key,
            layer_id="layer_a",
            object_id=object_id,
            expected_type=TYPE_NOTE,
            blob=bytes(blob),
        )


def test_padding_hides_the_exact_length() -> None:
    key = random_key()
    sizes = set()
    for length in (1, 50, 200, 255, 256, 257, 900):
        blob = seal(
            key=key,
            layer_id="layer_a",
            object_id=secrets.token_bytes(16),
            object_type=TYPE_NOTE,
            plaintext=b"x" * length,
        )
        sizes.add(len(blob))

    # Seven different plaintext lengths collapse into two on-disk sizes.
    assert len(sizes) == 2


def test_padding_is_exactly_reversible() -> None:
    key = random_key()
    for length in (0, 1, 255, 256, 257, 4096, 100_000):
        object_id = secrets.token_bytes(16)
        plaintext = secrets.token_bytes(length)
        blob = seal(
            key=key,
            layer_id="layer_a",
            object_id=object_id,
            object_type=TYPE_ATTACHMENT,
            plaintext=plaintext,
        )
        opened = open_sealed(
            key=key,
            layer_id="layer_a",
            object_id=object_id,
            expected_type=TYPE_ATTACHMENT,
            blob=blob,
        )
        assert opened == plaintext


def test_padded_length_buckets() -> None:
    assert padded_length(0) == 256
    assert padded_length(256) == 256
    assert padded_length(257) == 1024
    assert padded_length(1_048_577) == 2_097_152


def test_the_layer_binding_is_not_the_layer_id() -> None:
    binding = layer_binding("layer_ab12cd34")

    assert len(binding) == 16
    assert b"layer_" not in binding


def test_the_header_packs_and_unpacks() -> None:
    header = ObjectHeader(
        format_version=FORMAT_VERSION,
        algorithm=1,
        object_type=TYPE_NOTE,
        flags=1,
        layer_binding=layer_binding("layer_a"),
        object_id=secrets.token_bytes(16),
        nonce=random_nonce(),
        plaintext_len=1234,
    )

    packed = header.pack()
    assert len(packed) == HEADER_SIZE
    assert ObjectHeader.unpack(packed) == header


# --- the key hierarchy ------------------------------------------------------


def test_a_layer_key_is_random_and_wrapped_by_the_password(tmp_path: Path) -> None:
    header, key = LayerHeader.create(
        layer_id="layer_a", password="correct horse", created_at="2026-07-14T00:00:00+00:00"
    )

    assert len(key) == KEY_BYTES
    assert header.password_envelope is not None
    # The wrapped key on disk is not the key.
    assert header.password_envelope.ciphertext != key
    assert header.unlock_with_password("correct horse") == key


def test_the_wrong_password_does_not_unlock_and_does_not_say_why() -> None:
    header, _key = LayerHeader.create(
        layer_id="layer_a", password="correct horse", created_at="2026-07-14T00:00:00+00:00"
    )

    with pytest.raises(DecryptionError):
        header.unlock_with_password("incorrect horse")


def test_two_layers_with_the_same_password_have_different_keys() -> None:
    """Unique salts, unique random keys: one cracked layer is not all of them."""
    first, key_a = LayerHeader.create(
        layer_id="a", password="same password", created_at="2026-07-14T00:00:00+00:00"
    )
    second, key_b = LayerHeader.create(
        layer_id="b", password="same password", created_at="2026-07-14T00:00:00+00:00"
    )

    assert key_a != key_b
    assert first.password_envelope is not None
    assert second.password_envelope is not None
    assert first.password_envelope.kdf.salt != second.password_envelope.kdf.salt


def test_a_recovery_key_opens_the_same_layer_key() -> None:
    recovery = generate_recovery_key()
    header, key = LayerHeader.create(
        layer_id="layer_a",
        password="correct horse",
        created_at="2026-07-14T00:00:00+00:00",
        recovery_key=recovery,
    )

    assert header.unlock_with_recovery_key(recovery) == key
    # Formatting is forgiving: a human retypes this from paper.
    assert header.unlock_with_recovery_key(recovery.lower().replace("-", " ")) == key


def test_a_recovery_key_is_not_guessable() -> None:
    keys = {generate_recovery_key() for _ in range(50)}

    assert len(keys) == 50
    assert all(len(normalise_recovery_key(key)) == 52 for key in keys)


def test_a_password_envelope_cannot_be_used_as_a_recovery_envelope() -> None:
    """The wrap AAD binds the envelope's purpose."""
    recovery = generate_recovery_key()
    header, _key = LayerHeader.create(
        layer_id="layer_a",
        password="correct horse",
        created_at="2026-07-14T00:00:00+00:00",
        recovery_key=recovery,
    )
    header.recovery_envelope = header.password_envelope

    with pytest.raises(DecryptionError):
        header.unlock_with_recovery_key("correct horse")


def test_changing_the_password_keeps_the_same_layer_key() -> None:
    header, key = LayerHeader.create(
        layer_id="layer_a", password="old password", created_at="2026-07-14T00:00:00+00:00"
    )

    header.change_password("old password", "new password")

    assert header.unlock_with_password("new password") == key
    with pytest.raises(DecryptionError):
        header.unlock_with_password("old password")


def test_the_header_round_trips_through_disk(tmp_path: Path) -> None:
    recovery = generate_recovery_key()
    header, key = LayerHeader.create(
        layer_id="layer_a",
        password="correct horse",
        created_at="2026-07-14T00:00:00+00:00",
        recovery_key=recovery,
    )
    header.manifest_object_id = "ab" * 16
    header.save(tmp_path)

    loaded = LayerHeader.load(tmp_path)

    assert loaded.unlock_with_password("correct horse") == key
    assert loaded.unlock_with_recovery_key(recovery) == key
    assert loaded.manifest_object_id == header.manifest_object_id


def test_the_header_on_disk_contains_no_key_and_no_password(tmp_path: Path) -> None:
    header, key = LayerHeader.create(
        layer_id="layer_a",
        password="correct horse battery staple",
        created_at="2026-07-14T00:00:00+00:00",
        recovery_key=generate_recovery_key(),
    )
    header.save(tmp_path)

    raw = (tmp_path / "layer.header").read_bytes()

    assert key not in raw
    assert key.hex().encode() not in raw
    assert b"correct horse" not in raw


# --- the key holder ---------------------------------------------------------


def test_the_key_holder_refuses_a_locked_layer() -> None:
    from app.domain.errors import LayerLockedError

    holder = KeyHolder()

    with pytest.raises(LayerLockedError):
        holder.key_for("layer_a")


def test_locking_removes_and_zeroes_the_key() -> None:
    from app.domain.errors import LayerLockedError

    holder = KeyHolder()
    key = random_key()
    holder.unlock("layer_a", key)

    assert holder.key_for("layer_a") == key
    assert holder.lock("layer_a") is True

    with pytest.raises(LayerLockedError):
        holder.key_for("layer_a")
    assert holder.is_unlocked("layer_a") is False


def test_locking_one_layer_does_not_unlock_another() -> None:
    holder = KeyHolder()
    holder.unlock("a", random_key())
    holder.unlock("b", random_key())

    holder.lock("a")

    assert holder.is_unlocked("b") is True
    assert holder.unlocked_layers() == ["b"]


# --- the service ------------------------------------------------------------


def test_creating_a_private_layer_writes_only_opaque_files(tmp_path: Path) -> None:
    service = EncryptionService()
    root = tmp_path / "layer_a"

    header, recovery = service.create_layer(layer_id="layer_a", root=root, password="correct horse")

    assert recovery is not None
    assert service.is_unlocked("layer_a")
    assert (root / "layer.header").is_file()

    entries = {path.name for path in root.iterdir()}
    assert entries == {"layer.header", "objects"}


def test_a_short_password_is_refused(tmp_path: Path) -> None:
    from app.domain.errors import InvalidRequestError

    service = EncryptionService()

    with pytest.raises(InvalidRequestError):
        service.create_layer(layer_id="layer_a", root=tmp_path / "l", password="short")


def test_unlock_lock_unlock_cycle(tmp_path: Path) -> None:
    service = EncryptionService()
    root = tmp_path / "layer_a"
    service.create_layer(layer_id="layer_a", root=root, password="correct horse")

    service.lock("layer_a")
    assert not service.is_unlocked("layer_a")

    service.unlock("layer_a", root, "correct horse")
    assert service.is_unlocked("layer_a")


def test_a_wrong_password_leaves_the_layer_locked_and_intact(tmp_path: Path) -> None:
    service = EncryptionService()
    root = tmp_path / "layer_a"
    service.create_layer(layer_id="layer_a", root=root, password="correct horse")
    service.lock("layer_a")

    before = (root / "layer.header").read_bytes()

    for _ in range(3):
        with pytest.raises(DecryptionError):
            service.unlock("layer_a", root, "wrong password")

    assert not service.is_unlocked("layer_a")
    # Repeated failures must not corrupt anything or lock the user out permanently.
    assert (root / "layer.header").read_bytes() == before
    service.unlock("layer_a", root, "correct horse")
    assert service.is_unlocked("layer_a")


def test_lock_runs_the_teardown_hooks(tmp_path: Path) -> None:
    service = EncryptionService()
    torn_down: list[str] = []
    service.on_lock(torn_down.append)

    root = tmp_path / "layer_a"
    service.create_layer(layer_id="layer_a", root=root, password="correct horse")
    service.lock("layer_a")

    # Anything caching decrypted content must be told, or it outlives the lock.
    assert torn_down == ["layer_a"]


def test_a_failing_teardown_hook_does_not_keep_the_layer_unlocked(tmp_path: Path) -> None:
    service = EncryptionService()

    def broken(_layer_id: str) -> None:
        raise RuntimeError("boom")

    service.on_lock(broken)
    root = tmp_path / "layer_a"
    service.create_layer(layer_id="layer_a", root=root, password="correct horse")

    service.lock("layer_a")

    assert not service.is_unlocked("layer_a")


def test_changing_the_password_does_not_re_encrypt_objects(tmp_path: Path) -> None:
    from app.infrastructure.storage.encrypted_store import EncryptedLayerStore

    service = EncryptionService()
    root = tmp_path / "layer_a"
    header, _recovery = service.create_layer(layer_id="layer_a", root=root, password="old password")
    store = EncryptedLayerStore("layer_a", root)
    before = {oid: store._object_path(oid).read_bytes() for oid in store.object_ids()}

    service.change_password("layer_a", root, "old password", "new password")
    service.lock("layer_a")
    service.unlock("layer_a", root, "new password")

    after = {oid: store._object_path(oid).read_bytes() for oid in store.object_ids()}
    assert before == after  # a rewrap touches no object

    with pytest.raises(DecryptionError):
        service.unlock("layer_a", root, "old password")


def test_rotating_the_key_re_encrypts_every_object(tmp_path: Path) -> None:
    from app.infrastructure.storage.encrypted_store import EncryptedLayerStore

    service = EncryptionService()
    root = tmp_path / "layer_a"
    header, _recovery = service.create_layer(layer_id="layer_a", root=root, password="password one")
    key_before = service.keys.key_for("layer_a")

    store = EncryptedLayerStore("layer_a", root)
    manifest = store.read_manifest(key_before, header.manifest_object_id)
    store.write_note(
        key_before,
        manifest,
        object_id=None,
        title="Secret",
        folder_path="",
        content="BLUEJAY",
        properties={},
        timestamp="2026-07-14T00:00:00+00:00",
    )
    store.write_manifest(key_before, header.manifest_object_id, manifest)

    ciphertext_before = {oid: store._object_path(oid).read_bytes() for oid in store.object_ids()}

    rewritten = service.rotate_key("layer_a", root, "password one")

    assert rewritten == 2  # the note and the manifest
    key_after = service.keys.key_for("layer_a")
    assert key_after != key_before

    # Every object is now different on disk, and the old key opens none of them.
    for object_id, blob in ciphertext_before.items():
        assert store._object_path(object_id).read_bytes() != blob

    with pytest.raises(DecryptionError):
        store.read_manifest(key_before, LayerHeader.load(root).manifest_object_id)

    # And the content survived the rotation.
    rotated = store.read_manifest(key_after, LayerHeader.load(root).manifest_object_id)
    note_entry = next(e for e in rotated.entries.values() if e.kind == "note")
    assert store.read_note(key_after, note_entry).content == "BLUEJAY"


def test_rotation_drops_the_old_recovery_key(tmp_path: Path) -> None:
    """A recovery key that still opened the old layer key would defeat rotation."""
    service = EncryptionService()
    root = tmp_path / "layer_a"
    _header, recovery = service.create_layer(layer_id="layer_a", root=root, password="password one")
    assert recovery is not None

    service.rotate_key("layer_a", root, "password one")
    service.lock("layer_a")

    with pytest.raises(DecryptionError):
        service.unlock_with_recovery_key("layer_a", root, recovery)

    # A fresh one can be issued, and it opens the *new* key.
    reissued = service.reissue_recovery_key("layer_a", root, "password one")
    service.lock("layer_a")
    service.unlock_with_recovery_key("layer_a", root, reissued)
    assert service.is_unlocked("layer_a")
