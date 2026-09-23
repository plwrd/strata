"""The encrypted *stream* container: large objects, encrypted as they are written.

:mod:`container` seals one object in one call, which means the whole plaintext
is in memory at once and capped at 256 MiB. A saved video is neither small nor
available all at once — it arrives from the network a buffer at a time — so it
needs a format that can be written incrementally and read back from the middle.

The rule this module exists to keep: **plaintext never touches the disk.** Each
chunk is encrypted in memory and only its ciphertext is written. There is no
"download, then encrypt" step, no plaintext temporary file, and no decrypted
cache on disk when reading — a reader decrypts only the chunks a caller asks
for, into memory. A crash mid-write leaves a ``.tmp`` holding ciphertext only.

Layout (docs/security/encryption-format.md, "Stream objects")::

    offset  size  field
    0       7     magic          b"STRATAS"
    7       1     format_version u8
    8       1     algorithm      u8   (1 = XChaCha20-Poly1305)
    9       1     object_type    u8
    10      1     flags          u8   (bit 0: final chunk padded)
    11      16    layer_binding  BLAKE2b-128(layer_id)
    27      16    object_id      raw 16 bytes (hex form is the filename)
    43      4     chunk_size     u32 big-endian
    47      16    nonce_prefix   random per object
    ---- 63-byte header ----
    then N chunks, each ``ciphertext + 16-byte tag``

This is the STREAM construction (Hoang, Reyhanitabar, Rogaway, Vizár):

* **Chunk nonce** = ``nonce_prefix || u64(index)``. The prefix is random per
  object and the index never repeats within one, so no nonce is reused under a
  key — and there is no counter state to persist, which is the property the
  one-shot container gets from random nonces.
* **Chunk AAD** = ``header || u64(index) || u8(is_final)``. The header binds
  layer, object id, type and chunk size, as in the one-shot container. The index
  stops chunks being reordered or swapped between positions; ``is_final`` stops
  a truncated file passing as a shorter complete one — cutting off the tail
  leaves a last chunk that was sealed as "not final", and it fails.
* **Every chunk except the last holds exactly ``chunk_size`` bytes.** The last
  holds ``data || zero padding || u32(len(data))``. With padding on (the layer
  default) the final chunk is always full size, so the file size reveals the
  length only to ``chunk_size`` granularity — the same idea as the one-shot
  container's buckets.

What still leaks: the size to chunk granularity, the mtime, and that the object
exists. See THREAT_MODEL.md.
"""

from __future__ import annotations

import os
import struct
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

from app.infrastructure.encryption.container import TYPE_WEB_MEDIA, TYPE_WEB_PAGE, layer_binding
from app.infrastructure.encryption.primitives import (
    ALG_XCHACHA20_POLY1305,
    TAG_BYTES,
    DecryptionError,
    decrypt,
    encrypt,
)
from app.infrastructure.storage.paths import replace_atomic

MAGIC: Final = b"STRATAS"
FORMAT_VERSION: Final = 1
HEADER_SIZE: Final = 63
FLAG_PADDED: Final = 0b0000_0001

# 256 KiB: large enough that per-chunk overhead (16 bytes and one libsodium
# call) is noise on a video, small enough that a seek decrypts little it does
# not need and a padded page wastes little disk.
DEFAULT_CHUNK_SIZE: Final = 256 * 1024
_MIN_CHUNK: Final = 4 * 1024
_MAX_CHUNK: Final = 8 * 1024 * 1024
_LENGTH_TRAILER: Final = 4

# Stream object types share the numbering space of the one-shot container's
# (they are defined there), but the formats are disjoint: a stream is never
# accepted by `open_sealed`, and a sealed object is never accepted here.
STREAM_TYPES: Final = frozenset({TYPE_WEB_PAGE, TYPE_WEB_MEDIA})

_HEADER_STRUCT: Final = struct.Struct(">7sBBBB16s16sI16s")
_INDEX: Final = struct.Struct(">Q")


@dataclass(frozen=True)
class StreamHeader:
    format_version: int
    algorithm: int
    object_type: int
    flags: int
    layer_binding: bytes
    object_id: bytes
    chunk_size: int
    nonce_prefix: bytes

    def pack(self) -> bytes:
        return _HEADER_STRUCT.pack(
            MAGIC,
            self.format_version,
            self.algorithm,
            self.object_type,
            self.flags,
            self.layer_binding,
            self.object_id,
            self.chunk_size,
            self.nonce_prefix,
        )

    @classmethod
    def unpack(cls, raw: bytes) -> StreamHeader:
        if len(raw) < HEADER_SIZE:
            raise DecryptionError("The object is truncated.")
        (
            magic,
            format_version,
            algorithm,
            object_type,
            flags,
            binding,
            object_id,
            chunk_size,
            nonce_prefix,
        ) = _HEADER_STRUCT.unpack(raw[:HEADER_SIZE])
        if magic != MAGIC:
            raise DecryptionError("Not a Strata stream object.")
        if format_version > FORMAT_VERSION:
            raise DecryptionError("This object was written by a newer version of Strata.")
        if algorithm != ALG_XCHACHA20_POLY1305:
            raise DecryptionError("Unsupported encryption algorithm.")
        if object_type not in STREAM_TYPES:
            raise DecryptionError("Unknown object type.")
        if not _MIN_CHUNK <= chunk_size <= _MAX_CHUNK:
            raise DecryptionError("The object claims an implausible chunk size.")
        return cls(
            format_version=format_version,
            algorithm=algorithm,
            object_type=object_type,
            flags=flags,
            layer_binding=binding,
            object_id=object_id,
            chunk_size=chunk_size,
            nonce_prefix=nonce_prefix,
        )


def _nonce(header: StreamHeader, index: int) -> bytes:
    return header.nonce_prefix + _INDEX.pack(index)


def _aad(packed_header: bytes, index: int, final: bool) -> bytes:
    return packed_header + _INDEX.pack(index) + (b"\x01" if final else b"\x00")


class StreamWriter:
    """Encrypts as it is fed; only ciphertext is ever written.

    Writes go to ``<path>.tmp`` and are moved into place by :meth:`finish`, so
    a reader never sees a half-written object under the real name. :meth:`abort`
    (or leaving a ``with`` block by exception) deletes the temporary file —
    which, like everything else this writes, is ciphertext.

    At most one chunk of plaintext is held, in memory.
    """

    def __init__(
        self,
        path: Path,
        *,
        key: bytes,
        layer_id: str,
        object_id: bytes,
        object_type: int,
        pad: bool = True,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        if object_type not in STREAM_TYPES:
            raise DecryptionError("Unknown object type.")
        if len(object_id) != 16:
            raise DecryptionError("Object ids are 16 bytes.")
        if not _MIN_CHUNK <= chunk_size <= _MAX_CHUNK:
            raise ValueError("chunk_size out of range")
        self._key = key
        self._path = path
        self._temporary = path.with_name(path.name + ".tmp")
        self._header = StreamHeader(
            format_version=FORMAT_VERSION,
            algorithm=ALG_XCHACHA20_POLY1305,
            object_type=object_type,
            flags=FLAG_PADDED if pad else 0,
            layer_binding=layer_binding(layer_id),
            object_id=object_id,
            chunk_size=chunk_size,
            nonce_prefix=os.urandom(16),
        )
        self._packed = self._header.pack()
        self._buffer = bytearray()
        self._index = 0
        self._written = 0
        self._closed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: BinaryIO = open(self._temporary, "wb")
        self._file.write(self._packed)

    @property
    def bytes_written(self) -> int:
        """Plaintext bytes accepted so far."""
        return self._written

    def write(self, data: bytes) -> None:
        if self._closed:
            raise ValueError("write to a finished stream")
        self._buffer += data
        self._written += len(data)
        size = self._header.chunk_size
        # Strictly greater: a buffer of exactly one chunk may turn out to be the
        # last, and the last chunk is sealed differently.
        while len(self._buffer) > size:
            self._emit(bytes(self._buffer[:size]), final=False)
            del self._buffer[:size]

    def _emit(self, plaintext: bytes, *, final: bool) -> None:
        aad = _aad(self._packed, self._index, final)
        _nonce_used, ciphertext = encrypt(
            self._key, plaintext, aad, nonce=_nonce(self._header, self._index)
        )
        self._file.write(ciphertext)
        self._index += 1

    def finish(self) -> int:
        """Seal the final chunk and move the object into place. Returns its size."""
        if self._closed:
            raise ValueError("stream already finished")
        tail = bytes(self._buffer)
        self._buffer.clear()
        pad = b""
        if self._header.flags & FLAG_PADDED:
            pad = b"\x00" * (self._header.chunk_size - len(tail))
        self._emit(tail + pad + struct.pack(">I", len(tail)), final=True)
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        self._closed = True
        replace_atomic(self._temporary, self._path)
        return self._written

    def abort(self) -> None:
        """Drop everything. Safe to call more than once."""
        self._buffer.clear()
        if self._closed:
            return
        self._closed = True
        try:
            self._file.close()
        finally:
            self._temporary.unlink(missing_ok=True)

    def __enter__(self) -> StreamWriter:
        return self

    def __exit__(self, kind: object, _value: object, _trace: object) -> None:
        if kind is not None:
            self.abort()
        elif not self._closed:
            self.finish()


class StreamReader:
    """Random-access decryption of a stream object.

    Opening authenticates the header and the final chunk, so the length it
    reports is one the key vouches for — not a number read off the file size.
    After that, :meth:`read_at` decrypts only the chunks that overlap the
    request. Thread-safe: the browser engine reads a playing video from threads
    of its own.
    """

    def __init__(
        self,
        path: Path,
        *,
        key: bytes,
        layer_id: str,
        object_id: bytes,
        expected_type: int,
    ) -> None:
        self._key = key
        self._lock = threading.Lock()
        self._file: BinaryIO = open(path, "rb")
        try:
            self._header = StreamHeader.unpack(self._file.read(HEADER_SIZE))
            if self._header.layer_binding != layer_binding(layer_id):
                raise DecryptionError("This object does not belong to this layer.")
            if self._header.object_id != object_id:
                raise DecryptionError("This object is not the one that was requested.")
            if self._header.object_type != expected_type:
                raise DecryptionError("This object is not of the expected type.")
            self._packed = self._header.pack()
            self._sealed = self._header.chunk_size + TAG_BYTES
            body = os.fstat(self._file.fileno()).st_size - HEADER_SIZE
            smallest_final = TAG_BYTES + _LENGTH_TRAILER
            if body < smallest_final:
                raise DecryptionError("The object is truncated.")
            # Every chunk but the last is exactly `_sealed` bytes; the last is
            # between `smallest_final` and `_sealed + 4` (its length trailer), so
            # this floor counts the full ones exactly.
            self._chunks = (body - smallest_final) // self._sealed + 1
            last = self._decrypt_chunk(self._chunks - 1)
            tail = int(struct.unpack(">I", last[-_LENGTH_TRAILER:])[0])
            if tail > self._header.chunk_size or tail > len(last) - _LENGTH_TRAILER:
                raise DecryptionError("The object's declared length is inconsistent.")
            self._last_index = self._chunks - 1
            self._last = last[:tail]
            self._size = (self._chunks - 1) * self._header.chunk_size + tail
            self._cached_index = -1
            self._cached = b""
        except BaseException:
            self._file.close()
            raise

    @property
    def size(self) -> int:
        return self._size

    def _decrypt_chunk(self, index: int) -> bytes:
        final = index == self._chunks - 1
        self._file.seek(HEADER_SIZE + index * self._sealed)
        wanted = self._sealed
        if final:
            # The final chunk may be shorter (unpadded), so read to the end.
            wanted = -1
        ciphertext = self._file.read(wanted)
        if len(ciphertext) < TAG_BYTES or (not final and len(ciphertext) != self._sealed):
            raise DecryptionError("The object is truncated.")
        plaintext = decrypt(
            self._key,
            _nonce(self._header, index),
            ciphertext,
            _aad(self._packed, index, final),
        )
        if not final and len(plaintext) != self._header.chunk_size:
            raise DecryptionError()
        return plaintext

    def _chunk(self, index: int) -> bytes:
        if index == self._last_index:
            return self._last
        if index != self._cached_index:
            self._cached = self._decrypt_chunk(index)
            self._cached_index = index
        return self._cached

    def read_at(self, offset: int, length: int) -> bytes:
        """Up to ``length`` plaintext bytes from ``offset``; ``b""`` past the end."""
        if offset < 0 or length < 0:
            raise ValueError("negative offset or length")
        end = min(self._size, offset + length)
        if offset >= end:
            return b""
        size = self._header.chunk_size
        out = bytearray()
        with self._lock:
            position = offset
            while position < end:
                index, within = divmod(position, size)
                piece = self._chunk(index)[within : within + (end - position)]
                if not piece:
                    raise DecryptionError("The object is truncated.")
                out += piece
                position += len(piece)
        return bytes(out)

    def iter_chunks(self) -> Iterator[bytes]:
        """The whole plaintext, a chunk at a time (rotation, export)."""
        size = self._header.chunk_size
        offset = 0
        while offset < self._size:
            piece = self.read_at(offset, size)
            yield piece
            offset += len(piece)

    def read_all(self) -> bytes:
        return self.read_at(0, self._size)

    def close(self) -> None:
        with self._lock:
            self._cached = b""
            self._last = b""
            self._file.close()

    def __enter__(self) -> StreamReader:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
