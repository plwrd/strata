"""The relay: a dumb forwarder of sealed blobs.

The relay is untrusted by construction (ADR-0006, THREAT_MODEL). It stores and
forwards opaque, already-sealed blobs and awareness messages. It never holds a
key and never sees plaintext; all it can observe is blob sizes, timing, and
pseudonymous channel/peer ids. That is the whole reason the CRDT lives *behind*
the encryption boundary.

Three implementations ship:

- :class:`LocalRelay` — in-process, for tests and for a single machine driving
  more than one document.
- :class:`DirectoryRelay` — a shared directory of sealed blobs, usable over any
  filesystem two peers already share (a synced folder, a LAN mount). Each blob is
  one atomically-written file; peers fetch everything past their cursor.
- :class:`HttpRelay` — talks to a self-hostable network relay
  (:mod:`app.infrastructure.crdt.relay_server`) over HTTP, so two peers converge
  across a LAN or the internet. It sends only ciphertext; the server never holds
  a key. Put TLS in front in production to also hide blob sizes/timing.
"""

from __future__ import annotations

import base64
import re
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from app.infrastructure.storage.paths import resolve_within

# A channel id is a pseudonymous, opaque handle for one shared document. It must
# be filesystem-safe because DirectoryRelay uses it as a directory name.
_CHANNEL_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _check_channel(channel: str) -> None:
    if not _CHANNEL_RE.match(channel):
        raise ValueError("Invalid relay channel id.")


class Relay(ABC):
    """Store-and-forward of sealed blobs for one or more channels."""

    @abstractmethod
    def publish(self, channel: str, blob: bytes) -> int:
        """Append a sealed blob to a channel. Returns its monotonic sequence."""

    @abstractmethod
    def fetch(self, channel: str, after_seq: int) -> list[tuple[int, bytes]]:
        """Every ``(seq, blob)`` with ``seq > after_seq``, in order."""

    @abstractmethod
    def head(self, channel: str) -> int:
        """The highest sequence published to a channel (0 if empty)."""

    # Presence is ephemeral and best-effort. The blob is opaque to the relay just
    # like an update; a peer that goes away simply stops refreshing it.
    @abstractmethod
    def announce(self, channel: str, peer_id: str, blob: bytes) -> None:
        """Publish this peer's latest awareness blob, replacing any previous one."""

    @abstractmethod
    def presence(self, channel: str) -> dict[str, bytes]:
        """The latest awareness blob per peer currently announced."""


class LocalRelay(Relay):
    """In-memory relay. Loses everything on process exit; ideal for tests."""

    def __init__(self) -> None:
        self._log: dict[str, list[bytes]] = {}
        self._presence: dict[str, dict[str, bytes]] = {}

    def publish(self, channel: str, blob: bytes) -> int:
        _check_channel(channel)
        log = self._log.setdefault(channel, [])
        log.append(bytes(blob))
        return len(log)

    def fetch(self, channel: str, after_seq: int) -> list[tuple[int, bytes]]:
        _check_channel(channel)
        log = self._log.get(channel, [])
        start = max(after_seq, 0)
        return [(i + 1, log[i]) for i in range(start, len(log))]

    def head(self, channel: str) -> int:
        return len(self._log.get(channel, []))

    def announce(self, channel: str, peer_id: str, blob: bytes) -> None:
        _check_channel(channel)
        self._presence.setdefault(channel, {})[peer_id] = bytes(blob)

    def presence(self, channel: str) -> dict[str, bytes]:
        return dict(self._presence.get(channel, {}))


class DirectoryRelay(Relay):
    """A relay backed by a shared directory. Only ciphertext ever touches disk."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _channel_dir(self, channel: str) -> Path:
        _check_channel(channel)
        # resolve_within refuses traversal even though the regex already did; two
        # guards on a path that names a directory from an id is cheap insurance.
        path = resolve_within(self._root, channel)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def publish(self, channel: str, blob: bytes) -> int:
        directory = self._channel_dir(channel)
        seq = self.head(channel) + 1
        target = directory / f"{seq:016d}.blob"
        tmp = directory / f".{seq:016d}.tmp"
        tmp.write_bytes(bytes(blob))
        tmp.replace(target)
        return seq

    def fetch(self, channel: str, after_seq: int) -> list[tuple[int, bytes]]:
        directory = self._channel_dir(channel)
        out: list[tuple[int, bytes]] = []
        for path in sorted(directory.glob("*.blob")):
            seq = int(path.stem)
            if seq > after_seq:
                out.append((seq, path.read_bytes()))
        return out

    def head(self, channel: str) -> int:
        directory = self._channel_dir(channel)
        seqs = [int(p.stem) for p in directory.glob("*.blob")]
        return max(seqs, default=0)

    def announce(self, channel: str, peer_id: str, blob: bytes) -> None:
        directory = self._channel_dir(channel)
        presence = directory / "presence"
        presence.mkdir(exist_ok=True)
        safe_peer = re.sub(r"[^a-zA-Z0-9_-]", "-", peer_id)[:64] or "peer"
        target = presence / f"{safe_peer}.awareness"
        tmp = presence / f".{safe_peer}.tmp"
        tmp.write_bytes(bytes(blob))
        tmp.replace(target)

    def presence(self, channel: str) -> dict[str, bytes]:
        directory = self._channel_dir(channel)
        presence = directory / "presence"
        if not presence.is_dir():
            return {}
        return {p.stem: p.read_bytes() for p in presence.glob("*.awareness")}


class HttpRelay(Relay):
    """A relay backed by a self-hostable HTTP relay server. Ciphertext only."""

    def __init__(
        self, base_url: str, *, client: httpx.Client | None = None, timeout: float = 10.0
    ) -> None:
        self._base = base_url.rstrip("/")
        # A caller-supplied client is how tests inject an in-process WSGI transport;
        # in production we own a plain client with no redirects (a relay never
        # redirects, and following one blindly would be a request-forgery foothold).
        self._client = client or httpx.Client(follow_redirects=False, timeout=timeout)

    def publish(self, channel: str, blob: bytes) -> int:
        _check_channel(channel)
        response = self._client.post(
            f"{self._base}/channels/{channel}/blobs",
            content=bytes(blob),
            headers={"Content-Type": "application/octet-stream"},
        )
        response.raise_for_status()
        return int(response.json()["seq"])

    def fetch(self, channel: str, after_seq: int) -> list[tuple[int, bytes]]:
        _check_channel(channel)
        response = self._client.get(
            f"{self._base}/channels/{channel}/blobs", params={"after": after_seq}
        )
        response.raise_for_status()
        return [(int(seq), base64.b64decode(b64)) for seq, b64 in response.json()["items"]]

    def head(self, channel: str) -> int:
        _check_channel(channel)
        response = self._client.get(f"{self._base}/channels/{channel}/head")
        response.raise_for_status()
        return int(response.json()["head"])

    def announce(self, channel: str, peer_id: str, blob: bytes) -> None:
        _check_channel(channel)
        safe_peer = re.sub(r"[^a-zA-Z0-9_-]", "-", peer_id)[:64] or "peer"
        response = self._client.put(
            f"{self._base}/channels/{channel}/presence/{safe_peer}",
            content=bytes(blob),
            headers={"Content-Type": "application/octet-stream"},
        )
        response.raise_for_status()

    def presence(self, channel: str) -> dict[str, bytes]:
        _check_channel(channel)
        response = self._client.get(f"{self._base}/channels/{channel}/presence")
        response.raise_for_status()
        return {peer: base64.b64decode(b64) for peer, b64 in response.json()["peers"].items()}

    def close(self) -> None:
        self._client.close()
