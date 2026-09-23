"""The read-only ``IStream`` the web archive hands WebView2.

Driven here through its real vtable, the way the engine calls it: by slot
number, through a raw pointer. A wrong slot order would not raise — the
engine would call ``Seek`` expecting ``Read`` — so the order is pinned.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

import pytest

from app.desktop.webview2 import com, sdk

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="WebView2 is Windows-only")

# IStream vtable order (objidl.h): IUnknown 0-2, then these.
READ, WRITE, SEEK, SET_SIZE, COPY_TO, COMMIT, REVERT, LOCK, UNLOCK, STAT, CLONE = range(3, 14)
DATA = bytes(range(256)) * 40  # 10 KB


class _Body:
    def __init__(self, data: bytes = DATA) -> None:
        self.data = data
        self.closed = False
        self.fail = False

    @property
    def size(self) -> int:
        return len(self.data)

    def read_at(self, offset: int, length: int) -> bytes:
        if self.fail:
            raise RuntimeError("layer locked")
        return self.data[offset : offset + length]

    def close(self) -> None:
        self.closed = True


def _read(stream: com.Interface, size: int) -> tuple[int, bytes]:
    """Read through the vtable. Failing HRESULTs raise (``Interface.call``)."""
    buffer = ctypes.create_string_buffer(size)
    got = ctypes.c_ulong()
    hr = stream.call(
        READ,
        (ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)),
        buffer,
        size,
        ctypes.byref(got),
    )
    return int(hr), buffer.raw[: got.value]


def _seek(stream: com.Interface, move: int, origin: int) -> int:
    position = ctypes.c_ulonglong()
    stream.call(
        SEEK,
        (ctypes.c_longlong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulonglong)),
        move,
        origin,
        ctypes.byref(position),
    )
    return int(position.value)


def _release(stream: com.Interface) -> int:
    return int(
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(stream._vtable()[2])(stream.pointer)
    )


def test_answers_to_istream_sequential_stream_and_agile() -> None:
    stream = sdk.ReadStream.over(_Body())
    handle = com.Interface(stream.pointer)
    for iid in (sdk.IID_ISTREAM, sdk.IID_ISEQUENTIAL_STREAM, sdk.IID_IAGILE_OBJECT):
        assert handle.query_interface(iid)
    assert not handle.query_interface(sdk.slots.IID_SETTINGS2)


def test_reads_sequentially_and_reports_the_end() -> None:
    stream = com.Interface(sdk.ReadStream.over(_Body()).pointer)
    hr, first = _read(stream, 4000)
    assert hr == 0 and first == DATA[:4000]
    hr, second = _read(stream, 8000)
    assert hr == 1  # S_FALSE: fewer bytes than asked, the end of the stream
    assert second == DATA[4000:]
    hr, third = _read(stream, 10)
    assert hr == 1 and third == b""


def test_seeks_from_every_origin() -> None:
    stream = com.Interface(sdk.ReadStream.over(_Body()).pointer)
    assert _seek(stream, 100, 0) == 100
    assert _read(stream, 5)[1] == DATA[100:105]
    assert _seek(stream, 10, 1) == 115
    assert _seek(stream, -6, 2) == len(DATA) - 6
    assert _read(stream, 100)[1] == DATA[-6:]


def test_stat_reports_the_size() -> None:
    stream = com.Interface(sdk.ReadStream.over(_Body()).pointer)
    stat = sdk._STATSTG()
    stream.call(STAT, (ctypes.POINTER(sdk._STATSTG), ctypes.c_ulong), ctypes.byref(stat), 1)
    assert stat.cbSize == len(DATA) and stat.type == 2 and not stat.pwcsName


def test_is_read_only() -> None:
    stream = com.Interface(sdk.ReadStream.over(_Body()).pointer)
    data = ctypes.create_string_buffer(b"overwrite")
    with pytest.raises(com.ComError) as refused:
        stream.call(
            WRITE,
            (ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)),
            data,
            9,
            None,
        )
    assert refused.value.hr == 0x80030005  # STG_E_ACCESSDENIED


def test_a_failing_body_is_an_error_not_short_data() -> None:
    body = _Body()
    stream = com.Interface(sdk.ReadStream.over(body).pointer)
    body.fail = True
    with pytest.raises(com.ComError) as failed:
        _read(stream, 100)
    assert failed.value.hr == 0x80004005  # E_FAIL, not S_FALSE with short data


def test_last_release_closes_the_body() -> None:
    body = _Body()
    stream = sdk.ReadStream.over(body)
    handle = com.Interface(stream.pointer)
    handle.call(1, restype=ctypes.c_ulong)  # the engine's AddRef
    stream.release_own()
    assert not body.closed
    assert _release(handle) == 0
    assert body.closed
    assert stream not in sdk._LIVE_STREAMS


def test_vault_reply_carries_what_the_response_needs() -> None:
    reply: Any = sdk.VaultReply(206, "Partial Content", "Content-Range: bytes 0-1/2", _Body())
    assert reply.status == 206 and "Content-Range" in reply.headers
