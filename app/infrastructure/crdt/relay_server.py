"""A self-hostable network relay for collaboration.

This is the "dumb forwarder" of ADR-0006, as a tiny WSGI app: it stores and
forwards opaque, already-sealed blobs, and it never holds a key or sees
plaintext. Two Strata peers that can both reach this server converge — over a
LAN or the open internet — without any change to the trust model.

It is deliberately a stdlib WSGI app (no framework, no new dependency): that
makes it testable in-process with httpx's WSGI transport and runnable standalone
with ``wsgiref``. A production deployment would put a real WSGI server (gunicorn,
uvicorn+asgi bridge, waitress) and TLS in front — the relay handles ciphertext,
but TLS still hides blob sizes/timing from the network path.

Run it::

    python -m app.infrastructure.crdt.relay_server --host 0.0.0.0 --port 8787

The wire protocol (all bodies are raw bytes unless noted):

    POST /channels/<id>/blobs            body=blob     -> {"seq": n}
    GET  /channels/<id>/blobs?after=<n>                -> {"items": [[seq, b64], ...]}
    GET  /channels/<id>/head                           -> {"head": n}
    PUT  /channels/<id>/presence/<peer>  body=blob     -> 204
    GET  /channels/<id>/presence                       -> {"peers": {peer: b64, ...}}
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Iterable
from threading import Lock
from typing import Any
from urllib.parse import parse_qs

_CHANNEL = r"[a-zA-Z0-9_-]{1,128}"
_PEER = r"[a-zA-Z0-9_-]{1,64}"
_RE_BLOBS = re.compile(rf"^/channels/({_CHANNEL})/blobs$")
_RE_HEAD = re.compile(rf"^/channels/({_CHANNEL})/head$")
_RE_PRESENCE = re.compile(rf"^/channels/({_CHANNEL})/presence$")
_RE_PRESENCE_PUT = re.compile(rf"^/channels/({_CHANNEL})/presence/({_PEER})$")

# A single sealed batch is capped so one client cannot exhaust the relay's memory.
MAX_BLOB_BYTES = 8 * 1024 * 1024

WSGIEnviron = dict[str, Any]
StartResponse = Callable[[str, list[tuple[str, str]]], Any]


class _Store:
    """In-memory blob log and presence, guarded by a lock (WSGI may be threaded)."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._log: dict[str, list[bytes]] = {}
        self._presence: dict[str, dict[str, bytes]] = {}

    def publish(self, channel: str, blob: bytes) -> int:
        with self._lock:
            log = self._log.setdefault(channel, [])
            log.append(blob)
            return len(log)

    def fetch(self, channel: str, after: int) -> list[tuple[int, bytes]]:
        with self._lock:
            log = self._log.get(channel, [])
            start = max(after, 0)
            return [(i + 1, log[i]) for i in range(start, len(log))]

    def head(self, channel: str) -> int:
        with self._lock:
            return len(self._log.get(channel, []))

    def announce(self, channel: str, peer: str, blob: bytes) -> None:
        with self._lock:
            self._presence.setdefault(channel, {})[peer] = blob

    def presence(self, channel: str) -> dict[str, bytes]:
        with self._lock:
            return dict(self._presence.get(channel, {}))


def _json(start: StartResponse, status: str, payload: dict[str, Any]) -> list[bytes]:
    body = json.dumps(payload).encode("utf-8")
    start(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]


def _read_body(environ: WSGIEnviron) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    if length < 0 or length > MAX_BLOB_BYTES:
        return b""
    stream = environ.get("wsgi.input")
    return stream.read(length) if stream is not None else b""


def make_relay_app(
    store: _Store | None = None,
) -> Callable[[WSGIEnviron, StartResponse], Iterable[bytes]]:
    """Build the WSGI application. Pass a shared ``_Store`` to inspect it in tests."""
    state = store or _Store()

    def app(environ: WSGIEnviron, start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "")

        if (match := _RE_BLOBS.match(path)) and method == "POST":
            blob = _read_body(environ)
            if not blob:
                return _json(start_response, "400 Bad Request", {"error": "empty or oversize blob"})
            seq = state.publish(match.group(1), blob)
            return _json(start_response, "200 OK", {"seq": seq})

        if (match := _RE_BLOBS.match(path)) and method == "GET":
            after = _after(environ)
            items = [
                [seq, base64.b64encode(b).decode("ascii")]
                for seq, b in state.fetch(match.group(1), after)
            ]
            return _json(start_response, "200 OK", {"items": items})

        if (match := _RE_HEAD.match(path)) and method == "GET":
            return _json(start_response, "200 OK", {"head": state.head(match.group(1))})

        if (match := _RE_PRESENCE_PUT.match(path)) and method == "PUT":
            state.announce(match.group(1), match.group(2), _read_body(environ))
            start_response("204 No Content", [("Content-Length", "0")])
            return [b""]

        if (match := _RE_PRESENCE.match(path)) and method == "GET":
            peers = {
                p: base64.b64encode(b).decode("ascii")
                for p, b in state.presence(match.group(1)).items()
            }
            return _json(start_response, "200 OK", {"peers": peers})

        return _json(start_response, "404 Not Found", {"error": "no such route"})

    return app


def _after(environ: WSGIEnviron) -> int:
    query = parse_qs(environ.get("QUERY_STRING", ""))
    try:
        return int(query.get("after", ["0"])[0])
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    from wsgiref.simple_server import make_server

    parser = argparse.ArgumentParser(
        description="Strata collaboration relay (forwards ciphertext only)."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)

    app = make_relay_app()
    with make_server(args.host, args.port, app) as server:
        print(f"Strata relay listening on http://{args.host}:{args.port} (ciphertext only)")
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
