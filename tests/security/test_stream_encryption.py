"""The encrypted stream container (saved pages and videos).

The property under test is the one the user asked for in so many words: the
file is encrypted *as it is written* and stays encrypted; reading decrypts in
memory. So besides round-trips, these check that no plaintext ever reaches the
disk — including mid-write and after an abort — and that every way of editing
the ciphertext (flip, truncate, reorder, swap, extend) is refused.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from app.infrastructure.encryption.container import (
    TYPE_ATTACHMENT,
    TYPE_WEB_MEDIA,
    TYPE_WEB_PAGE,
    open_sealed,
)
from app.infrastructure.encryption.primitives import DecryptionError, random_key
from app.infrastructure.encryption.stream import (
    HEADER_SIZE,
    StreamReader,
    StreamWriter,
)

pytestmark = pytest.mark.security

CHUNK = 4096
SECRET = b"BLUEJAY-video-frame-"
LAYER = "layer_stream"
OBJECT_ID = bytes(range(16))


def _payload(size: int) -> bytes:
    # Recognisable and non-repeating, so a leak of any slice is findable.
    out = bytearray()
    counter = 0
    while len(out) < size:
        out += SECRET + counter.to_bytes(4, "big")
        counter += 1
    return bytes(out[:size])


def _write(path: Path, key: bytes, data: bytes, *, pad: bool = True, piece: int = 1000) -> None:
    with StreamWriter(
        path,
        key=key,
        layer_id=LAYER,
        object_id=OBJECT_ID,
        object_type=TYPE_WEB_MEDIA,
        pad=pad,
        chunk_size=CHUNK,
    ) as writer:
        for start in range(0, len(data), piece):
            writer.write(data[start : start + piece])


def _reader(path: Path, key: bytes, **overrides: object) -> StreamReader:
    options: dict[str, object] = {
        "key": key,
        "layer_id": LAYER,
        "object_id": OBJECT_ID,
        "expected_type": TYPE_WEB_MEDIA,
    }
    options.update(overrides)
    return StreamReader(path, **options)  # type: ignore[arg-type]


@pytest.mark.parametrize("size", [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, 3 * CHUNK, 5 * CHUNK + 17])
@pytest.mark.parametrize("pad", [True, False])
def test_round_trips_at_every_boundary(tmp_path: Path, size: int, pad: bool) -> None:
    key = random_key()
    data = _payload(size)
    path = tmp_path / "obj"
    _write(path, key, data, pad=pad)

    with _reader(path, key) as reader:
        assert reader.size == size
        assert reader.read_all() == data


def test_random_access_reads_match_the_plaintext(tmp_path: Path) -> None:
    key = random_key()
    data = _payload(7 * CHUNK + 123)
    path = tmp_path / "obj"
    _write(path, key, data)

    rng = secrets.SystemRandom()
    with _reader(path, key) as reader:
        for _ in range(200):
            offset = rng.randrange(0, len(data) + 10)
            length = rng.randrange(0, 3 * CHUNK)
            assert reader.read_at(offset, length) == data[offset : offset + length]


def test_no_plaintext_is_ever_on_disk(tmp_path: Path) -> None:
    key = random_key()
    data = _payload(6 * CHUNK)
    path = tmp_path / "obj"
    writer = StreamWriter(
        path,
        key=key,
        layer_id=LAYER,
        object_id=OBJECT_ID,
        object_type=TYPE_WEB_MEDIA,
        chunk_size=CHUNK,
    )
    for start in range(0, len(data), 1500):
        writer.write(data[start : start + 1500])
        # Mid-write: whatever is on disk right now is ciphertext.
        for file in tmp_path.iterdir():
            assert SECRET not in file.read_bytes()
    writer.finish()

    on_disk = path.read_bytes()
    assert SECRET not in on_disk
    assert list(tmp_path.iterdir()) == [path]  # no temporary left behind


def test_an_aborted_write_leaves_nothing(tmp_path: Path) -> None:
    key = random_key()
    path = tmp_path / "obj"
    with pytest.raises(RuntimeError):
        with StreamWriter(
            path,
            key=key,
            layer_id=LAYER,
            object_id=OBJECT_ID,
            object_type=TYPE_WEB_MEDIA,
            chunk_size=CHUNK,
        ) as writer:
            writer.write(_payload(3 * CHUNK))
            raise RuntimeError("network dropped")
    assert list(tmp_path.iterdir()) == []


def test_padding_hides_the_length_to_chunk_granularity(tmp_path: Path) -> None:
    key = random_key()
    sizes = set()
    for length in (1, 100, CHUNK - 10):
        path = tmp_path / f"obj{length}"
        _write(path, key, _payload(length))
        sizes.add(path.stat().st_size)
    assert len(sizes) == 1


def test_wrong_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "obj"
    _write(path, random_key(), _payload(2 * CHUNK))
    with pytest.raises(DecryptionError):
        _reader(path, random_key())


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("layer_id", "another_layer"),
        ("object_id", bytes(16)),
        ("expected_type", TYPE_WEB_PAGE),
    ],
)
def test_binding_to_layer_id_and_type(tmp_path: Path, override: str, value: object) -> None:
    key = random_key()
    path = tmp_path / "obj"
    _write(path, key, _payload(CHUNK * 2))
    with pytest.raises(DecryptionError):
        _reader(path, key, **{override: value})


def test_a_flipped_bit_anywhere_is_refused(tmp_path: Path) -> None:
    key = random_key()
    data = _payload(3 * CHUNK)
    path = tmp_path / "obj"
    _write(path, key, data)
    original = path.read_bytes()

    for position in (
        0,
        10,
        HEADER_SIZE - 1,
        HEADER_SIZE + 5,
        len(original) // 2,
        len(original) - 1,
    ):
        tampered = bytearray(original)
        tampered[position] ^= 0x01
        path.write_bytes(bytes(tampered))
        with pytest.raises(DecryptionError):
            with _reader(path, key) as reader:
                reader.read_all()


def test_truncation_at_a_chunk_boundary_is_refused(tmp_path: Path) -> None:
    key = random_key()
    path = tmp_path / "obj"
    _write(path, key, _payload(4 * CHUNK + 10))
    sealed = CHUNK + 16
    original = path.read_bytes()
    # Drop the final chunk exactly: what is left is a well-formed prefix whose
    # new last chunk was sealed as "not final".
    path.write_bytes(original[: HEADER_SIZE + 4 * sealed])
    with pytest.raises(DecryptionError):
        _reader(path, key)


def test_reordered_chunks_are_refused(tmp_path: Path) -> None:
    key = random_key()
    path = tmp_path / "obj"
    _write(path, key, _payload(4 * CHUNK + 10))
    sealed = CHUNK + 16
    raw = path.read_bytes()
    first = raw[HEADER_SIZE : HEADER_SIZE + sealed]
    second = raw[HEADER_SIZE + sealed : HEADER_SIZE + 2 * sealed]
    path.write_bytes(raw[:HEADER_SIZE] + second + first + raw[HEADER_SIZE + 2 * sealed :])
    with _reader(path, key) as reader, pytest.raises(DecryptionError):
        reader.read_at(0, 10)


def test_chunks_cannot_be_moved_between_objects(tmp_path: Path) -> None:
    key = random_key()
    one, two = tmp_path / "one", tmp_path / "two"
    _write(one, key, _payload(3 * CHUNK))
    _write(two, key, _payload(3 * CHUNK)[::-1])
    sealed = CHUNK + 16
    a, b = one.read_bytes(), two.read_bytes()
    # Same key, same layer, same object id, same position — only the random
    # nonce prefix differs, and it is in the AAD.
    spliced = a[:HEADER_SIZE] + b[HEADER_SIZE : HEADER_SIZE + sealed] + a[HEADER_SIZE + sealed :]
    one.write_bytes(spliced)
    with _reader(one, key) as reader, pytest.raises(DecryptionError):
        reader.read_at(0, 10)


def test_streams_and_sealed_objects_are_not_interchangeable(tmp_path: Path) -> None:
    key = random_key()
    path = tmp_path / "obj"
    _write(path, key, _payload(100))
    with pytest.raises(DecryptionError):
        open_sealed(
            key=key,
            layer_id=LAYER,
            object_id=OBJECT_ID,
            expected_type=TYPE_ATTACHMENT,
            blob=path.read_bytes(),
        )


def test_writer_refuses_a_non_stream_type(tmp_path: Path) -> None:
    with pytest.raises(DecryptionError):
        StreamWriter(
            tmp_path / "obj",
            key=random_key(),
            layer_id=LAYER,
            object_id=OBJECT_ID,
            object_type=TYPE_ATTACHMENT,
        )
    assert not any(tmp_path.iterdir())


def test_nonce_prefixes_differ_between_objects(tmp_path: Path) -> None:
    key = random_key()
    prefixes = set()
    for index in range(16):
        path = tmp_path / f"obj{index}"
        _write(path, key, b"x")
        prefixes.add(path.read_bytes()[47:63])
    assert len(prefixes) == 16


def test_reader_is_safe_under_concurrent_reads(tmp_path: Path) -> None:
    import threading

    key = random_key()
    data = _payload(8 * CHUNK)
    path = tmp_path / "obj"
    _write(path, key, data)
    errors: list[BaseException] = []

    with _reader(path, key) as reader:

        def hammer(seed: int) -> None:
            rng = secrets.SystemRandom()
            try:
                for _ in range(100):
                    offset = rng.randrange(0, len(data))
                    assert reader.read_at(offset, 999) == data[offset : offset + 999]
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert not errors


def test_temporary_file_holds_only_ciphertext_after_a_crash(tmp_path: Path) -> None:
    """A process killed mid-download leaves a `.tmp`; it must be ciphertext."""
    key = random_key()
    path = tmp_path / "obj"
    writer = StreamWriter(
        path,
        key=key,
        layer_id=LAYER,
        object_id=OBJECT_ID,
        object_type=TYPE_WEB_MEDIA,
        chunk_size=CHUNK,
    )
    writer.write(_payload(5 * CHUNK))
    writer._file.flush()  # what the OS has, at the moment of the "crash"
    os.fsync(writer._file.fileno())
    leftover = path.with_name(path.name + ".tmp").read_bytes()
    assert len(leftover) > HEADER_SIZE
    assert SECRET not in leftover
    writer.abort()
