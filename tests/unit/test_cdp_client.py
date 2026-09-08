"""The minimal DevTools WebSocket client.

Hand-rolled framing is exactly the kind of code that works on the happy path and
falls over on the real one, so this drives it against a socket server that
answers the way Chrome actually does: an unsolicited event before the reply, a
ping mid-stream, a fragmented message, and a payload past the 16-bit length
boundary. The loopback rule is checked too — a debugging address that is not
local is refused, never dialled.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
from typing import Any

import pytest

from app.domain.errors import InvalidRequestError, ProviderError
from app.infrastructure.browser.cdp import CDPClient


def _frame(payload: bytes, *, opcode: int = 0x1, final: bool = True) -> bytes:
    """A server frame: never masked, which is what the client must expect."""
    header = bytearray([(0x80 if final else 0x00) | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    return bytes(header) + payload


def _text(message: dict[str, Any]) -> bytes:
    return json.dumps(message).encode("utf-8")


class FakeChrome:
    """A loopback socket that completes the handshake, then sends `script`."""

    def __init__(self, script: bytes) -> None:
        self.script = script
        self.received = b""
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        connection, _ = self._server.accept()
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                request += chunk
            connection.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n\r\n"
            )
            self.received = connection.recv(65536)
            connection.sendall(self.script)
            # Hold the connection open until the client hangs up.
            connection.recv(65536)
        except OSError:
            pass
        finally:
            connection.close()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/devtools/page/tab_1"

    def close(self) -> None:
        self._server.close()


def _evaluate(script: bytes) -> Any:
    chrome = FakeChrome(script)
    try:
        return CDPClient(chrome.port).evaluate(chrome.url, "1 + 1")
    finally:
        chrome.close()


def test_the_reply_is_read_past_unsolicited_events() -> None:
    script = (
        # Chrome emits lifecycle events whenever it likes; ours is the one with
        # our id, and everything before it must be skipped, not mistaken for it.
        _frame(_text({"method": "Page.frameNavigated", "params": {}}))
        + _frame(_text({"id": 1, "result": {"result": {"value": "the page text"}}}))
    )

    assert _evaluate(script) == "the page text"


def test_a_ping_is_answered_and_does_not_end_the_message() -> None:
    script = _frame(b"keepalive", opcode=0x9) + _frame(
        _text({"id": 1, "result": {"result": {"value": "still here"}}})
    )

    assert _evaluate(script) == "still here"


def test_a_fragmented_message_is_reassembled() -> None:
    payload = _text({"id": 1, "result": {"result": {"value": "split across frames"}}})
    half = len(payload) // 2
    script = _frame(payload[:half], final=False) + _frame(payload[half:], opcode=0x0, final=True)

    assert _evaluate(script) == "split across frames"


def test_a_payload_past_the_16_bit_boundary_is_read_whole() -> None:
    big = "x" * 70_000
    script = _frame(_text({"id": 1, "result": {"result": {"value": big}}}))

    assert _evaluate(script) == big


def test_an_exception_in_the_page_is_an_error_not_a_value() -> None:
    script = _frame(
        _text({"id": 1, "result": {"exceptionDetails": {"text": "boom"}, "result": {}}})
    )

    with pytest.raises(ProviderError):
        _evaluate(script)


def test_a_protocol_error_is_an_error() -> None:
    script = _frame(_text({"id": 1, "error": {"code": -32000, "message": "no target"}}))

    with pytest.raises(ProviderError):
        _evaluate(script)


def test_a_close_frame_instead_of_a_reply_fails_closed() -> None:
    script = _frame(b"", opcode=0x8)

    with pytest.raises(ProviderError):
        _evaluate(script)


@pytest.mark.parametrize(
    "url",
    [
        "ws://10.0.0.5:9333/devtools/page/x",
        "ws://evil.example.com:9333/devtools/page/x",
        "wss://127.0.0.1:9333/devtools/page/x",
        "http://127.0.0.1:9333/devtools/page/x",
    ],
)
def test_a_debugging_address_that_is_not_local_is_refused(url: str) -> None:
    """The port belongs to a browser Strata launched on loopback. Anything else
    did not come from that browser, so it is not dialled at all."""
    with pytest.raises(InvalidRequestError):
        CDPClient(9333).evaluate(url, "1 + 1")
