"""A very small Chrome DevTools Protocol client.

Two transports, because Chrome uses two:

* an HTTP endpoint on ``127.0.0.1:<port>`` for discovery (``/json/version``,
  ``/json/list``, ``/json/new``, ``/json/activate``);
* a WebSocket per page target for everything else — reading a page's text needs
  ``Runtime.evaluate``, and there is no HTTP route for that.

The WebSocket client here is deliberately minimal rather than a dependency: it
speaks exactly the subset RFC 6455 requires of a client talking to one known
peer on loopback — a masked text frame out, text/continuation frames in, ping
answered, close honoured. It is not a general-purpose WebSocket library and
should not become one.

Everything is pinned to loopback. The port belongs to a browser Strata itself
launched with ``--remote-debugging-address=127.0.0.1``; a non-loopback address
here means the URL did not come from that browser, so it is refused rather
than dialled.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.domain.errors import InvalidRequestError, ProviderError
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

HTTP_TIMEOUT_SECONDS = 5.0
WS_TIMEOUT_SECONDS = 20.0
# A page's extracted text is capped inside the browser before it is ever framed,
# so a hostile page cannot make Strata buffer an unbounded message.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024

_OPCODE_CONTINUATION = 0x0
_OPCODE_TEXT = 0x1
_OPCODE_BINARY = 0x2
_OPCODE_CLOSE = 0x8
_OPCODE_PING = 0x9
_OPCODE_PONG = 0xA

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class BrowserUnavailableError(ProviderError):
    """The debugging endpoint did not answer. Almost always: it is not running."""


def _endpoint(port: int, path: str) -> str:
    return f"http://127.0.0.1:{port}{path}"


class CDPClient:
    """Discovery over HTTP, evaluation over one short-lived WebSocket."""

    def __init__(self, port: int) -> None:
        self._port = port

    # -- HTTP discovery ------------------------------------------------------

    def version(self) -> dict[str, Any]:
        payload = self._get_json("/json/version")
        return payload if isinstance(payload, dict) else {}

    def targets(self) -> list[dict[str, Any]]:
        payload = self._get_json("/json/list")
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def pages(self) -> list[dict[str, Any]]:
        """Page targets only — not workers, not extension background pages.

        Extension pages are filtered out on purpose: the user installed those to
        browse with, not to hand to a model, and one of them is never what "the
        tab I am looking at" means.
        """
        return [
            target
            for target in self.targets()
            if target.get("type") == "page"
            and not str(target.get("url", "")).startswith(("devtools://", "chrome-extension://"))
        ]

    def open_tab(self, url: str) -> dict[str, Any]:
        """Open a new tab. Chrome 111+ requires PUT here; older builds accept both."""
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
                response = client.put(_endpoint(self._port, "/json/new"), params={"url": url})
                if response.status_code == 405:  # pre-111 Chrome, and some forks
                    response = client.get(_endpoint(self._port, "/json/new"), params={"url": url})
        except httpx.HTTPError as exc:
            raise BrowserUnavailableError("The browser is not reachable.") from exc
        if response.status_code >= 400:
            raise ProviderError("The browser refused to open the page.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("The browser sent an answer Strata could not read.") from exc
        return payload if isinstance(payload, dict) else {}

    def activate(self, target_id: str) -> None:
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
                client.get(_endpoint(self._port, f"/json/activate/{target_id}"))
        except httpx.HTTPError:  # raising focus is a convenience, never a failure
            logger.info("cdp.activate_failed")

    def _get_json(self, path: str) -> Any:
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
                response = client.get(_endpoint(self._port, path))
        except httpx.HTTPError as exc:
            raise BrowserUnavailableError("The browser is not reachable.") from exc
        if response.status_code != 200:
            raise BrowserUnavailableError("The browser is not reachable.")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("The browser sent an answer Strata could not read.") from exc

    # -- evaluation over WebSocket -------------------------------------------

    def evaluate(self, websocket_url: str, expression: str) -> Any:
        """Run one expression in a page and return its value.

        The expression is Strata's own JavaScript. Its *result* is whatever the
        page contained, which is untrusted data — every caller treats it so.
        """
        message = json.dumps(
            {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True,
                    "userGesture": False,
                },
            }
        )
        raw = _round_trip(websocket_url, message)
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise ProviderError("The browser sent an answer Strata could not read.") from exc

        if payload.get("error"):
            raise ProviderError("The browser refused to read that tab.")
        result = payload.get("result", {})
        if not isinstance(result, dict):
            raise ProviderError("The browser sent an answer Strata could not read.")
        if result.get("exceptionDetails"):
            raise ProviderError("The page could not be read.")
        inner = result.get("result", {})
        return inner.get("value") if isinstance(inner, dict) else None


# -- the minimal WebSocket client -------------------------------------------


def _round_trip(websocket_url: str, message: str) -> str:
    """Connect, send one message, return the first reply carrying its id."""
    host, port, path = _split(websocket_url)
    sock = socket.create_connection((host, port), timeout=WS_TIMEOUT_SECONDS)
    buffer = _Buffer(sock)
    try:
        sock.settimeout(WS_TIMEOUT_SECONDS)
        _handshake(buffer, host, port, path)
        _send_text(sock, message)
        while True:
            reply = _read_message(buffer)
            # Chrome interleaves unsolicited events with command replies; ours
            # is the one carrying our id.
            try:
                if json.loads(reply).get("id") == 1:
                    return reply
            except ValueError:
                continue
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _split(websocket_url: str) -> tuple[str, int, str]:
    parts = urlsplit(websocket_url)
    if parts.scheme != "ws":
        raise InvalidRequestError("The browser gave an unexpected debugging address.")
    host = parts.hostname or ""
    if host not in _LOOPBACK:
        raise InvalidRequestError("The browser debugging address is not local.")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return host, parts.port or 80, path


class _Buffer:
    """A socket plus the bytes already read past the last frame boundary."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.pending = b""

    def recv_exact(self, count: int) -> bytes:
        while len(self.pending) < count:
            chunk = self.sock.recv(max(4096, count - len(self.pending)))
            if not chunk:
                raise BrowserUnavailableError("The browser closed the debugging connection.")
            self.pending += chunk
        taken, self.pending = self.pending[:count], self.pending[count:]
        return taken

    def recv_until_headers(self) -> bytes:
        while b"\r\n\r\n" not in self.pending:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise BrowserUnavailableError("The browser closed the debugging connection.")
            self.pending += chunk
            if len(self.pending) > 64 * 1024:
                raise ProviderError("The browser sent an oversized handshake.")
        header, self.pending = self.pending.split(b"\r\n\r\n", 1)
        return header


def _handshake(buffer: _Buffer, host: str, port: int, path: str) -> None:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    buffer.sock.sendall(request.encode("ascii"))
    status = buffer.recv_until_headers().split(b"\r\n", 1)[0]
    if b"101" not in status:
        raise BrowserUnavailableError("The browser refused the debugging connection.")


def _mask(payload: bytes) -> tuple[bytes, bytes]:
    mask = os.urandom(4)
    return mask, bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))


def _send_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    mask, masked = _mask(payload)

    header = bytearray([0x80 | _OPCODE_TEXT])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    sock.sendall(bytes(header) + mask + masked)


def _read_message(buffer: _Buffer) -> str:
    """One complete text message: fragments reassembled, pings answered."""
    chunks: list[bytes] = []
    total = 0
    while True:
        first, second = buffer.recv_exact(2)
        final = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", buffer.recv_exact(2))
        elif length == 127:
            (length,) = struct.unpack(">Q", buffer.recv_exact(8))

        total += length
        if total > MAX_MESSAGE_BYTES:
            raise ProviderError("The browser sent more data than Strata will read.")

        mask = buffer.recv_exact(4) if masked else b""
        payload = buffer.recv_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

        if opcode == _OPCODE_CLOSE:
            raise BrowserUnavailableError("The browser closed the debugging connection.")
        if opcode == _OPCODE_PING:
            _send_pong(buffer.sock, payload)
            continue
        if opcode == _OPCODE_PONG:
            continue
        if opcode == _OPCODE_BINARY:
            raise ProviderError("The browser sent an unexpected binary frame.")
        if opcode in (_OPCODE_TEXT, _OPCODE_CONTINUATION):
            chunks.append(payload)
            if final:
                return b"".join(chunks).decode("utf-8", errors="replace")
            continue
        raise ProviderError("The browser sent an unexpected frame.")


def _send_pong(sock: socket.socket, payload: bytes) -> None:
    mask, masked = _mask(payload[:125])
    sock.sendall(bytes([0x80 | _OPCODE_PONG, 0x80 | len(masked)]) + mask + masked)
