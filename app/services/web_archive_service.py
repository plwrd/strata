"""The encrypted web archive: save what the browser pane shows, read it offline.

Press **Ctrl+Alt+F** in the WebView2 pane and the page — plus any video it
plays from a plain file — goes into a private layer. Three rules shape
everything here:

* **Encrypted as it is written, never after.** A page snapshot and a video are
  both *stream objects* (``encryption.stream``): each chunk is encrypted in
  memory the moment it arrives and only ciphertext is written. There is no
  plaintext download folder, no plaintext temporary file, nothing to clean up.
* **Decrypted as it is read, into memory only.** Saved items are served back to
  the pane from a virtual origin (:data:`VAULT_ORIGIN`) by answering the
  engine's resource requests directly — a video seek decrypts the chunks it
  covers and nothing else, and every response says ``no-store`` so the engine
  does not put decrypted bytes into its own disk cache.
* **Only while unlocked.** Everything goes through ``private_access``, which is
  the lock check. Locking the layer closes every reader this service opened, so
  a video that was playing stops rather than playing on from a key that should
  be gone. A save in progress aborts and removes its (ciphertext) partial file.

What is saved: the page as MHTML (Chromium's single-file snapshot: HTML, CSS,
images, fonts), each ``<video>``/``<audio>`` whose source is an ordinary
http(s) file, and — through :mod:`stream_extractor` (yt-dlp + ffmpeg) — video a
page's own player assembles from pieces, as on YouTube and X. That last kind is
joined by ffmpeg into a pipe and encrypted from the pipe, so it too never
exists as a plaintext file. Copy-protected (DRM) media is refused by design.

This module is Qt-free; the pane (``app.desktop.webview2.pane``) gathers the
capture and hands resource requests in.
"""

from __future__ import annotations

import email
import email.policy
import html
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.domain.errors import (
    InvalidRequestError,
    LayerLockedError,
    NotFoundError,
    PermissionDeniedError,
    StrataError,
)
from app.infrastructure.encryption.stream import StreamReader
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.net_guard import Resolver, guard_public_url, resolve
from app.infrastructure.storage.paths import safe_filename
from app.services.encryption_service import EncryptionService
from app.services.private_layer_access import PrivateLayerAccess
from app.services.settings_service import SettingsService
from app.services.stream_extractor import StreamCookie, StreamExtractor
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)

# A reserved TLD (RFC 2606): it can never resolve, so if interception ever
# failed the request would go nowhere rather than to somebody's server.
VAULT_HOST = "saved.strata.invalid"
VAULT_ORIGIN = f"https://{VAULT_HOST}"

_DOWNLOAD_PIECE = 256 * 1024
_MEDIA_TYPES = ("video/", "audio/")
_BINARY_TYPES = frozenset(
    {"application/octet-stream", "binary/octet-stream", "application/mp4", "application/ogg"}
)
_STREAMING_SUFFIXES = (".m3u8", ".mpd")
_PAGE_CACHE_SIZE = 3

# Saved pages are shown with scripts, network and framing by others all off.
# A snapshot is a document to read, not an app to run — and with nothing able
# to reach the network, viewing a saved page tells no one it was viewed.
_PAGE_CSP = (
    "default-src 'self' data: blob:; script-src 'none'; object-src 'none'; "
    "style-src 'self' 'unsafe-inline' data:; connect-src 'none'; "
    "frame-ancestors 'self'; form-action 'none'; base-uri 'none'"
)
# The library and watch pages post one form (delete) back to the vault.
_APP_CSP = (
    "default-src 'self'; script-src 'none'; object-src 'none'; style-src 'unsafe-inline'; "
    "img-src 'self' data:; media-src 'self'; connect-src 'none'; "
    "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


# -- what the pane hands in ----------------------------------------------------


@dataclass(frozen=True)
class Cookie:
    """One cookie as the DevTools protocol reports it — only what matching needs."""

    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False

    def matches(self, url: str) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        domain = self.domain.lower()
        if domain.startswith("."):
            if not (host == domain[1:] or host.endswith(domain)):
                return False
        elif host != domain:
            return False
        if self.secure and parts.scheme != "https":
            return False
        path = parts.path or "/"
        return path == self.path or path.startswith(self.path.rstrip("/") + "/") or self.path == "/"


def cookie_header(cookies: Iterable[Cookie], url: str) -> str:
    """The ``Cookie`` header the browser itself would send to ``url``.

    Matched per URL, never pooled: cookies for a page's own site must not be
    handed to a third-party video host just because both were on one page.
    """
    return "; ".join(f"{c.name}={c.value}" for c in cookies if c.matches(url))


@dataclass(frozen=True)
class PageCapture:
    url: str
    title: str
    mhtml: str
    media_urls: tuple[str, ...] = ()
    # Media the page plays from `blob:` or a streaming manifest: not saved yet,
    # but reported so a user is never left thinking the video came along.
    streamed_media: int = 0
    user_agent: str = ""
    cookies: tuple[Cookie, ...] = ()
    # Pages whose video is player-assembled (a YouTube watch page, an X post):
    # handed to yt-dlp, which finds the real streams behind them.
    stream_pages: tuple[str, ...] = ()


@dataclass
class SaveResult:
    layer_name: str
    page_id: str
    media_saved: list[tuple[str, int]] = field(default_factory=list)
    media_failed: list[tuple[str, str]] = field(default_factory=list)
    streamed_skipped: int = 0

    def summary(self) -> str:
        parts = ["Page saved"]
        if self.media_saved:
            total = sum(size for _title, size in self.media_saved)
            count = len(self.media_saved)
            parts.append(f"{count} video{'s' if count != 1 else ''} ({_human(total)})")
        text = " + ".join(parts) + f", encrypted in “{self.layer_name}”."
        if self.media_failed:
            count = len(self.media_failed)
            text += f" {count} video{'s' if count != 1 else ''} could not be saved"
            # The first reason is usually the one that matters ("sign in",
            # "ffmpeg not found"), and a count alone cannot be acted on.
            text += f": {self.media_failed[0][1]}"
            text = text if text.endswith(".") else text + "."
        if self.streamed_skipped:
            text += (
                f" {self.streamed_skipped} streamed video(s) skipped"
                " (player-assembled streams are not supported yet)."
            )
        return text


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover - loop always returns


def classify_media_url(url: str) -> str:
    """``"file"`` (saveable), ``"stream"`` (player-assembled) or ``"skip"``."""
    parts = urlsplit(url)
    if parts.scheme in ("http", "https"):
        if parts.path.lower().endswith(_STREAMING_SUFFIXES):
            return "stream"
        return "file"
    if parts.scheme == "blob":
        return "stream"
    return "skip"


# -- what the pane gets back ---------------------------------------------------


class BodySource(Protocol):
    @property
    def size(self) -> int: ...

    def read_at(self, offset: int, length: int) -> bytes: ...

    def close(self) -> None: ...


class BytesSource:
    """A response body already in memory (pages, the library)."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    @property
    def size(self) -> int:
        return len(self._data)

    def read_at(self, offset: int, length: int) -> bytes:
        return self._data[offset : offset + length]

    def close(self) -> None:
        self._data = b""


class RangeSource:
    """A window onto a :class:`StreamReader` — the body of a (206) media reply.

    Decrypts on each read. Closing it closes the reader, and so does locking
    the layer (the service closes every reader it handed out), after which a
    read raises instead of returning plaintext from a key that is gone.
    """

    def __init__(
        self,
        reader: StreamReader,
        start: int,
        end: int,
        on_close: Callable[[], None],
        *,
        object_id: str = "",
    ) -> None:
        self.object_id = object_id
        self._reader = reader
        self._start = start
        self._size = max(0, end - start)
        self._on_close = on_close
        self._closed = False

    @property
    def size(self) -> int:
        return self._size

    def read_at(self, offset: int, length: int) -> bytes:
        if self._closed:
            raise LayerLockedError("This layer is locked.")
        length = max(0, min(length, self._size - offset))
        return self._reader.read_at(self._start + offset, length) if length else b""

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._on_close()


@dataclass
class VaultResponse:
    status: int
    reason: str
    headers: dict[str, str]
    body: BodySource

    def header_block(self) -> str:
        """``Name: value`` lines, CRLF-separated — WebView2's header format."""
        return "\r\n".join(f"{name}: {value}" for name, value in self.headers.items())


def _response(
    status: int,
    reason: str,
    body: bytes | BodySource,
    content_type: str,
    *,
    csp: str | None = _APP_CSP,
    extra: dict[str, str] | None = None,
) -> VaultResponse:
    source: BodySource = BytesSource(body) if isinstance(body, bytes) else body
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(source.size),
        # Decrypted bytes must not be written back to disk by the engine's own
        # HTTP cache — that would undo the whole point.
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    if csp:
        headers["Content-Security-Policy"] = csp
    headers.update(extra or {})
    return VaultResponse(status, reason, headers, source)


def _html_response(status: int, reason: str, markup: str) -> VaultResponse:
    return _response(status, reason, markup.encode("utf-8"), "text/html; charset=utf-8")


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """``Range: bytes=a-b`` → ``(start, end_exclusive)``; None when unsatisfiable.

    One range only, which is all a media element asks for.
    """
    match = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", header or "")
    if match is None:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:
        suffix = int(last)
        if suffix == 0:
            return None
        return max(0, size - suffix), size
    start = int(first)
    end = size if not last else min(size, int(last) + 1)
    if start >= size or end <= start:
        return None
    return start, end


# -- MHTML → something the pane can show offline -------------------------------


@dataclass
class _Part:
    content_type: str
    location: str
    content_id: str
    payload: bytes
    charset: str


class ArchivedPage:
    """A parsed MHTML snapshot, served back part by part.

    Chromium will not render MHTML from an https origin, so the snapshot is
    unpacked here and each part served at a *mirrored* path:
    ``https://cdn.example/css/a.css`` becomes
    ``/page/<id>/r/https/cdn.example/css/a.css``. Mirroring the original
    structure means relative URLs inside a stylesheet still resolve to the
    right part without anyone rewriting them.
    """

    def __init__(self, page_id: str, raw: bytes, media: dict[str, str] | None = None) -> None:
        self.page_id = page_id
        # Original media URL -> the vault path of its saved copy.
        self._media = dict(media or {})
        message = email.message_from_bytes(raw, policy=email.policy.compat32)
        self._parts: list[_Part] = []
        for part in message.walk():
            if part.is_multipart():
                continue
            self._parts.append(self._read_part(part))
        if not self._parts:
            raise InvalidRequestError("The saved page is empty.")
        self._by_location = {p.location: p for p in self._parts if p.location}
        self._by_cid = {p.content_id: p for p in self._parts if p.content_id}
        self.main = next((p for p in self._parts if p.content_type == "text/html"), self._parts[0])
        self._rewritten: dict[int, bytes] = {}

    @staticmethod
    def _read_part(part: Message) -> _Part:
        payload = part.get_payload(decode=True)
        content_id = str(part.get("Content-ID", "")).strip().strip("<>")
        return _Part(
            content_type=part.get_content_type(),
            location=str(part.get("Content-Location", "")).strip(),
            content_id=content_id,
            payload=payload if isinstance(payload, bytes) else b"",
            charset=part.get_content_charset() or "utf-8",
        )

    # -- paths ----------------------------------------------------------------

    def mirror(self, location: str) -> str:
        parts = urlsplit(location)
        if parts.scheme in ("http", "https") and parts.netloc:
            path = parts.path or "/"
            query = f"?{parts.query}" if parts.query else ""
            return f"/page/{self.page_id}/r/{parts.scheme}/{parts.netloc}{path}{query}"
        return f"/page/{self.page_id}/cid/{quote(location, safe='')}"

    def _path_for(self, part: _Part) -> str:
        if part.location and urlsplit(part.location).scheme in ("http", "https"):
            return self.mirror(part.location)
        return f"/page/{self.page_id}/cid/{quote(part.content_id, safe='')}"

    @property
    def entry_path(self) -> str:
        return self._path_for(self.main)

    def lookup(self, rest: str, query: str) -> _Part | None:
        """``rest`` is the request path after ``/page/<id>/``."""
        if rest.startswith("r/"):
            scheme, _, remainder = rest[2:].partition("/")
            netloc, slash, path = remainder.partition("/")
            location = f"{scheme}://{netloc}{slash}{path}" + (f"?{query}" if query else "")
            for candidate in (location, unquote(location)):
                if candidate in self._by_location:
                    return self._by_location[candidate]
            # A page's bare origin is stored with or without its slash.
            return self._by_location.get(location.rstrip("/"))
        if rest.startswith("cid/"):
            key = unquote(rest[4:])
            return self._by_cid.get(key) or self._by_location.get(key)
        return None

    # -- bodies ---------------------------------------------------------------

    def body(self, part: _Part) -> bytes:
        if part.content_type not in ("text/html", "text/css"):
            return part.payload
        cached = self._rewritten.get(id(part))
        if cached is None:
            cached = self._rewrite(part).encode("utf-8")
            self._rewritten[id(part)] = cached
        return cached

    def _rewrite(self, part: _Part) -> str:
        text = part.payload.decode(part.charset, errors="replace")
        origin = urlsplit(part.location)
        if origin.scheme in ("http", "https") and origin.netloc:
            # Root-relative references (`/img/x.png`) would otherwise resolve
            # against the vault's own root. Point them at the mirrored origin.
            # First, so the absolute rewrite below cannot be rewritten twice.
            root = f"/page/{self.page_id}/r/{origin.scheme}/{origin.netloc}/"
            if part.content_type == "text/html":
                text = re.sub(
                    r"""((?:src|href|poster)\s*=\s*["'])/(?!/)""",
                    lambda m: m.group(1) + root,
                    text,
                    flags=re.I,
                )
            text = re.sub(
                r"""(url\(\s*["']?)/(?!/)""", lambda m: m.group(1) + root, text, flags=re.I
            )
        mapping: dict[str, str] = {}
        for other in self._parts:
            target = self._path_for(other)
            if other.location:
                mapping[other.location] = target
                mapping[html.escape(other.location, quote=True)] = target
            if other.content_id:
                mapping[f"cid:{other.content_id}"] = target
        # A video saved alongside the page plays from the vault in place.
        for url, target in self._media.items():
            mapping[url] = target
            mapping[html.escape(url, quote=True)] = target
        if mapping:
            # Longest first, so `.../a.css` is never cut short by a match on `.../a`.
            pattern = re.compile(
                "|".join(re.escape(key) for key in sorted(mapping, key=len, reverse=True))
            )
            text = pattern.sub(lambda match: mapping[match.group(0)], text)
        return text


# -- the service ---------------------------------------------------------------


class WebArchiveService:
    def __init__(
        self,
        workspace: WorkspaceService,
        settings: SettingsService,
        encryption: EncryptionService,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
        extractor: StreamExtractor | None = None,
        resolver: Resolver = resolve,
    ) -> None:
        self._resolver = resolver
        self._workspace = workspace
        self._settings = settings
        self._encryption = encryption
        self._client_factory = client_factory or self._default_client
        self._extractor = extractor or StreamExtractor(
            ffmpeg_path=lambda: settings.settings.web_archive_ffmpeg_path,
            max_height=lambda: settings.settings.web_archive_max_height,
        )
        self._lock = threading.Lock()
        self._pages: dict[tuple[str, str], ArchivedPage] = {}
        self._readers: dict[str, set[RangeSource]] = {}
        # Layers where something was deleted permanently: layer id -> the key
        # generation at the time. Until the key is rotated past it, an old
        # disk copy could still be decrypted, and the library says so.
        self._rotation_due: dict[str, int] = {}

    @staticmethod
    def _default_client() -> httpx.Client:
        return httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=120.0),
        )

    def _guard(self, url: str) -> None:
        """A page chose this URL: refuse it if it points into the user's network.

        Opt out with ``web_archive_allow_private_addresses`` (a NAS, a home
        media server) — a deliberate choice, off by default.
        """
        if self._settings.settings.web_archive_allow_private_addresses:
            return
        guard_public_url(
            url,
            message="This video is on a private network address, which the archive "
            "does not fetch from (see Settings to allow it).",
            resolver=self._resolver,
        )

    def _guarded_client(self) -> httpx.Client:
        client = self._client_factory()

        def check(request: httpx.Request) -> None:
            # Every hop, redirects included: a public URL that bounces to
            # 127.0.0.1 is refused at the bounce.
            self._guard(str(request.url))

        hooks = dict(client.event_hooks)
        hooks["request"] = [*hooks.get("request", []), check]
        client.event_hooks = hooks
        return client

    # -- layers ---------------------------------------------------------------

    def _private_layers(self) -> list[Any]:
        if not self._workspace.is_open:
            return []
        return [
            layer
            for layer in self._workspace.readable_layers()
            if layer.visibility == "private" and layer.storage == "encrypted-objects"
        ]

    def target_layer(self) -> tuple[str, str]:
        """``(layer_id, display_name)`` of the layer a save goes into.

        Never a public layer, and never a fallback to plain storage: if there
        is no unlocked private layer, the answer is "unlock one", not "save it
        somewhere else".
        """
        if not self._workspace.is_open:
            raise InvalidRequestError("Open a workspace first.")
        layers = self._private_layers()
        preferred = self._settings.settings.web_archive_layer_id
        if preferred:
            for layer in layers:
                if layer.id == preferred:
                    return layer.id, layer.display_name
            raise LayerLockedError(
                "Unlock the private layer chosen for saved pages first.",
                details={"layerId": preferred},
            )
        if not layers:
            raise PermissionDeniedError(
                "Unlock a private layer to save pages — saved pages are only ever stored encrypted."
            )
        return layers[0].id, layers[0].display_name

    def _locked_private_layers(self) -> int:
        if not self._workspace.is_open:
            return 0
        return sum(1 for layer in self._workspace.locked_layers() if layer.visibility == "private")

    def _access(self, layer_id: str) -> PrivateLayerAccess:
        return self._workspace.private_access(layer_id)

    def _still_unlocked(self, layer_id: str) -> None:
        if not self._encryption.is_unlocked(layer_id):
            raise LayerLockedError("The layer was locked while saving.")

    # -- saving ---------------------------------------------------------------

    def save(
        self,
        capture: PageCapture,
        *,
        progress: Callable[[str], None] = lambda _message: None,
    ) -> SaveResult:
        """Save a page and its plain-file media. Blocking: call off the UI thread."""
        if urlsplit(capture.url).scheme not in ("http", "https"):
            raise InvalidRequestError("Only web pages can be saved.")
        layer_id, layer_name = self.target_layer()
        access = self._access(layer_id)

        progress("Encrypting page…")
        page_id = self._save_page(access, layer_id, capture)
        result = SaveResult(
            layer_name=layer_name,
            page_id=page_id,
            # Streamed media is only "skipped" when there was no page to hand
            # to the extractor; an attempt that fails is a failure, with a reason.
            streamed_skipped=0 if capture.stream_pages else capture.streamed_media,
        )

        media_ids: list[str] = []
        seen: set[str] = set()
        for url in capture.media_urls:
            if url in seen or classify_media_url(url) != "file":
                continue
            seen.add(url)
            try:
                media_id, title, size = self._save_media(
                    access, layer_id, capture, url, page_id, progress
                )
            except LayerLockedError:
                raise
            except (httpx.HTTPError, StrataError, OSError) as exc:
                reason = str(exc) or type(exc).__name__
                result.media_failed.append((url, reason))
                logger.warning("web_archive.media_failed", error=type(exc).__name__)
                continue
            media_ids.append(media_id)
            result.media_saved.append((title, size))

        for stream_page in dict.fromkeys(capture.stream_pages):
            try:
                media_id, title, size = self._save_stream(
                    access, layer_id, capture, stream_page, page_id, progress
                )
            except LayerLockedError:
                raise
            except (StrataError, OSError) as exc:
                result.media_failed.append((stream_page, str(exc) or type(exc).__name__))
                logger.warning("web_archive.stream_failed", error=type(exc).__name__)
                continue
            media_ids.append(media_id)
            result.media_saved.append((title, size))

        if media_ids:
            self._still_unlocked(layer_id)
            access.update_stream_properties(page_id, {"media_ids": media_ids})
        logger.info(
            "web_archive.saved",
            media=len(media_ids),
            failed=len(result.media_failed),
            streamed=capture.streamed_media,
        )
        return result

    def _save_page(self, access: PrivateLayerAccess, layer_id: str, capture: PageCapture) -> str:
        raw = capture.mhtml.encode("utf-8")
        object_id, writer = access.begin_stream("web_page")
        with writer:
            for start in range(0, len(raw), _DOWNLOAD_PIECE):
                self._still_unlocked(layer_id)
                writer.write(raw[start : start + _DOWNLOAD_PIECE])
        del raw
        self._still_unlocked(layer_id)
        title = (capture.title or urlsplit(capture.url).netloc or "Saved page").strip()[:300]
        access.commit_stream(
            object_id,
            kind="web_page",
            title=title,
            filename=f"{safe_filename(title)[:120] or 'page'}.mhtml",
            size_bytes=writer.bytes_written,
            properties={"url": capture.url[:4000], "saved_at": _now(), "media_ids": []},
        )
        return object_id

    def _save_media(
        self,
        access: PrivateLayerAccess,
        layer_id: str,
        capture: PageCapture,
        url: str,
        page_id: str,
        progress: Callable[[str], None],
    ) -> tuple[str, str, int]:
        cap = self._settings.settings.web_archive_max_media_mb * 1024 * 1024
        headers = {"Accept": "*/*", "Referer": capture.url}
        if capture.user_agent:
            headers["User-Agent"] = capture.user_agent
        cookie = cookie_header(capture.cookies, url)
        if cookie:
            headers["Cookie"] = cookie

        name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "video"
        with self._guarded_client() as client, client.stream("GET", url, headers=headers) as reply:
            reply.raise_for_status()
            final_scheme = reply.url.scheme
            if final_scheme not in ("http", "https"):
                raise InvalidRequestError("The video redirected somewhere that is not the web.")
            mime = reply.headers.get("content-type", "").split(";")[0].strip().lower()
            if not (mime.startswith(_MEDIA_TYPES) or mime in _BINARY_TYPES):
                raise InvalidRequestError(f"Not a media file ({mime or 'unknown type'}).")
            declared = int(reply.headers.get("content-length") or 0)
            if declared > cap:
                raise InvalidRequestError("The video is larger than the size limit.")

            object_id, writer = access.begin_stream("web_media")
            with writer:
                for piece in reply.iter_bytes(_DOWNLOAD_PIECE):
                    self._still_unlocked(layer_id)
                    writer.write(piece)
                    if writer.bytes_written > cap:
                        raise InvalidRequestError("The video is larger than the size limit.")
                    if declared:
                        percent = writer.bytes_written * 100 // declared
                        progress(f"Encrypting video {name[:40]}… {percent}%")
                    else:
                        progress(f"Encrypting video {name[:40]}… {_human(writer.bytes_written)}")
            size = writer.bytes_written

        self._still_unlocked(layer_id)
        title = safe_filename(name)[:200] or "video"
        access.commit_stream(
            object_id,
            kind="web_media",
            title=title,
            filename=title,
            size_bytes=size,
            properties={
                "url": url[:4000],
                "page_id": page_id,
                "mime": mime if mime.startswith(_MEDIA_TYPES) else "video/mp4",
                "saved_at": _now(),
            },
        )
        return object_id, title, size

    def _save_stream(
        self,
        access: PrivateLayerAccess,
        layer_id: str,
        capture: PageCapture,
        stream_page: str,
        page_id: str,
        progress: Callable[[str], None],
    ) -> tuple[str, str, int]:
        """A player-assembled video: yt-dlp finds it, ffmpeg pipes it, we encrypt."""
        reason = self._extractor.unavailable_reason()
        if reason:
            raise InvalidRequestError(f"Streamed video needs {reason}.")
        progress("Finding the video stream…")
        cookies = [
            StreamCookie(c.name, c.value, c.domain, c.path, c.secure) for c in capture.cookies
        ]
        found = self._extractor.extract(stream_page, cookies, capture.user_agent)
        for track_url, _headers in found.tracks:
            self._guard(track_url)
        cap = self._settings.settings.web_archive_max_media_mb * 1024 * 1024
        label = found.title[:40]

        object_id, writer = access.begin_stream("web_media")
        with writer:

            def take(piece: bytes) -> None:
                self._still_unlocked(layer_id)
                writer.write(piece)
                if writer.bytes_written > cap:
                    raise InvalidRequestError("The video is larger than the size limit.")

            def report(seconds: float) -> None:
                if found.duration:
                    percent = min(99, int(seconds * 100 / found.duration))
                    progress(f"Encrypting video {label}… {percent}%")
                else:
                    progress(f"Encrypting video {label}… {_human(writer.bytes_written)}")

            self._extractor.run(found, take, report)
            size = writer.bytes_written
            if size == 0:
                raise InvalidRequestError("The video stream was empty.")

        self._still_unlocked(layer_id)
        title = safe_filename(found.title)[:200] or "video"
        access.commit_stream(
            object_id,
            kind="web_media",
            title=title,
            filename=f"{title}.mp4",
            size_bytes=size,
            properties={
                "url": stream_page[:4000],
                "page_id": page_id,
                "mime": "video/mp4",
                "saved_at": _now(),
                "streamed": True,
                "height": found.height,
            },
        )
        return object_id, title, size

    # -- managing -------------------------------------------------------------

    def list_saved(self) -> list[dict[str, Any]]:
        """Every saved item in every unlocked private layer, newest first."""
        items: list[dict[str, Any]] = []
        for layer in self._private_layers():
            for entry in self._access(layer.id).list_streams():
                items.append(
                    {
                        "id": entry.object_id,
                        "layerId": layer.id,
                        "layerName": layer.display_name,
                        "kind": entry.kind,
                        "title": entry.title,
                        "url": str(entry.properties.get("url", "")),
                        "savedAt": entry.created_at,
                        "sizeBytes": entry.size_bytes,
                        "mediaIds": list(entry.properties.get("media_ids", [])),
                        "pageId": entry.properties.get("page_id"),
                        "mime": entry.properties.get("mime", ""),
                    }
                )
        items.sort(key=lambda item: item["savedAt"], reverse=True)
        return items

    def _key_generation(self, layer_id: str) -> int:
        from app.infrastructure.encryption.layer_header import LayerHeader

        return LayerHeader.load(self._workspace.layer_root(layer_id)).key_generation

    def rotation_reminders(self) -> list[str]:
        """Display names of layers that still need a key rotation."""
        names: list[str] = []
        for layer in self._private_layers():
            generation = self._rotation_due.get(layer.id)
            if generation is None:
                continue
            if self._key_generation(layer.id) > generation:
                del self._rotation_due[layer.id]  # rotated since: done
            else:
                names.append(layer.display_name)
        return names

    def delete(self, object_id: str, *, permanently: bool = False) -> int:
        """Delete a saved item; deleting a page deletes its videos too.

        ``permanently`` overwrites the ciphertext before removing it and
        reminds the user to rotate the layer key, which is what actually
        makes a leftover copy (a backup, an SSD's spare blocks) unreadable.
        """
        layer_id, access = self._find(object_id)
        entry = access.stream_entry(object_id)
        doomed = [object_id]
        if entry.kind == "web_page":
            doomed += [str(i) for i in entry.properties.get("media_ids", [])]
        # A video still playing holds its file open, and Windows will not
        # delete an open file. Stop it first.
        with self._lock:
            playing = [
                source
                for source in self._readers.get(layer_id, set())
                if source.object_id in doomed
            ]
        for source in playing:
            source.close()
        removed = 0
        for item in doomed:
            try:
                access.delete_stream(item, overwrite=permanently)
                removed += 1
            except NotFoundError:
                continue
        with self._lock:
            self._pages.pop((layer_id, object_id), None)
        if permanently and removed:
            self._rotation_due.setdefault(layer_id, self._key_generation(layer_id))
        logger.info("web_archive.deleted", objects=removed, permanently=permanently)
        return removed

    def _find(self, object_id: str) -> tuple[str, PrivateLayerAccess]:
        if not re.fullmatch(r"[0-9a-f]{32}", object_id):
            raise NotFoundError("Saved item not found.")
        for layer in self._private_layers():
            access = self._access(layer.id)
            try:
                access.stream_entry(object_id)
            except NotFoundError:
                continue
            return layer.id, access
        raise NotFoundError("Saved item not found.")

    def forget_layer(self, layer_id: str) -> None:
        """The layer locked: drop parsed pages and cut off every open reader."""
        with self._lock:
            for key in [k for k in self._pages if k[0] == layer_id]:
                del self._pages[key]
            sources = self._readers.pop(layer_id, set())
        for source in sources:
            source.close()

    # -- serving --------------------------------------------------------------

    def respond(self, method: str, url: str, range_header: str = "") -> VaultResponse:
        """Answer one request for :data:`VAULT_ORIGIN`. Never raises."""
        try:
            return self._route(method.upper(), url, range_header)
        except LayerLockedError:
            return _html_response(403, "Locked", _locked_page())
        except NotFoundError:
            return _html_response(
                404, "Not Found", _message_page("Not found", "That saved item is not here.")
            )
        except Exception:
            logger.exception("web_archive.respond_failed")
            return _html_response(
                500, "Error", _message_page("Could not open", "That saved item could not be read.")
            )

    def _route(self, method: str, url: str, range_header: str) -> VaultResponse:
        parts = urlsplit(url)
        if parts.hostname != VAULT_HOST:
            raise NotFoundError("Not a vault address.")
        path = parts.path or "/"
        if method not in ("GET", "HEAD", "POST"):
            return _html_response(405, "Method Not Allowed", _message_page("Not allowed", ""))

        if path == "/":
            return _html_response(200, "OK", self._library_page())
        if match := re.fullmatch(r"/delete/([0-9a-f]{32})", path):
            if method == "POST":
                permanently = parts.query == "permanently=1"
                self.delete(match.group(1), permanently=permanently)
                return _html_response(
                    200,
                    "OK",
                    self._library_page(
                        notice="Deleted permanently." if permanently else "Deleted."
                    ),
                )
            return _html_response(200, "OK", self._confirm_delete_page(match.group(1)))
        if match := re.fullmatch(r"/watch/([0-9a-f]{32})", path):
            return _html_response(200, "OK", self._watch_page(match.group(1)))
        if match := re.fullmatch(r"/media/([0-9a-f]{32})", path):
            return self._media(match.group(1), range_header, head=method == "HEAD")
        if match := re.fullmatch(r"/page/([0-9a-f]{32})/(.*)", path, flags=re.S):
            return self._page_part(match.group(1), match.group(2), parts.query)
        raise NotFoundError("No such vault page.")

    def _media(self, object_id: str, range_header: str, *, head: bool) -> VaultResponse:
        layer_id, access = self._find(object_id)
        entry = access.stream_entry(object_id)
        mime = str(entry.properties.get("mime") or "application/octet-stream")
        reader = access.open_stream(object_id)
        size = reader.size
        if range_header:
            window = parse_range(range_header, size)
            if window is None:
                reader.close()
                return _response(
                    416,
                    "Range Not Satisfiable",
                    b"",
                    mime,
                    csp=None,
                    extra={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
                )
            start, end = window
            status, reason = 206, "Partial Content"
            extra = {"Content-Range": f"bytes {start}-{end - 1}/{size}", "Accept-Ranges": "bytes"}
        else:
            start, end = 0, size
            status, reason = 200, "OK"
            extra = {"Accept-Ranges": "bytes"}
        if head:
            reader.close()
            response = _response(status, reason, b"", mime, csp=None, extra=extra)
            response.headers["Content-Length"] = str(end - start)
            return response

        source: RangeSource

        def release() -> None:
            reader.close()
            with self._lock:
                self._readers.get(layer_id, set()).discard(source)

        source = RangeSource(reader, start, end, release, object_id=object_id)
        with self._lock:
            self._readers.setdefault(layer_id, set()).add(source)
        return _response(status, reason, source, mime, csp=None, extra=extra)

    def _page(self, object_id: str) -> ArchivedPage:
        layer_id, access = self._find(object_id)
        with self._lock:
            cached = self._pages.get((layer_id, object_id))
        if cached is not None:
            return cached
        entry = access.stream_entry(object_id)
        media: dict[str, str] = {}
        for media_id in entry.properties.get("media_ids", []):
            try:
                saved = access.stream_entry(str(media_id))
            except NotFoundError:
                continue
            url = str(saved.properties.get("url", ""))
            if url:
                media[url] = f"/media/{media_id}"
        with access.open_stream(object_id) as reader:
            page = ArchivedPage(object_id, reader.read_all(), media)
        with self._lock:
            self._pages[(layer_id, object_id)] = page
            while len(self._pages) > _PAGE_CACHE_SIZE:
                self._pages.pop(next(iter(self._pages)))
        return page

    def page_entry_url(self, object_id: str) -> str:
        return VAULT_ORIGIN + self._page(object_id).entry_path

    def _page_part(self, object_id: str, rest: str, query: str) -> VaultResponse:
        page = self._page(object_id)
        if rest in ("", "/"):
            part = page.main
        else:
            found = page.lookup(rest, query)
            if found is None:
                # Offline, a resource the snapshot did not include is simply
                # absent — say so quietly rather than reach for the network.
                return _response(404, "Not Found", b"", "text/plain", csp=_PAGE_CSP)
            part = found
        content_type = part.content_type
        if content_type in ("text/html", "text/css"):
            content_type += "; charset=utf-8"
        return _response(200, "OK", page.body(part), content_type, csp=_PAGE_CSP)

    # -- markup ---------------------------------------------------------------

    def _library_page(self, notice: str = "") -> str:
        try:
            items = self.list_saved()
        except LayerLockedError:
            return _locked_page()
        pages = [item for item in items if item["kind"] == "web_page"]
        media = {item["id"]: item for item in items if item["kind"] == "web_media"}
        rows: list[str] = []
        for page in pages:
            videos = "".join(
                f'<li><a href="/watch/{m}">▶ {html.escape(media[m]["title"])}</a>'
                f' <span class="meta">{_human(media[m]["sizeBytes"])}</span></li>'
                for m in page["mediaIds"]
                if m in media
            )
            rows.append(
                "<article>"
                f'<h2><a href="/page/{page["id"]}/">{html.escape(page["title"])}</a></h2>'
                f'<p class="meta">{html.escape(page["url"])}<br>'
                f"Saved {html.escape(page['savedAt'][:16].replace('T', ' '))} · "
                f"{html.escape(page['layerName'])} · {_human(page['sizeBytes'])}"
                f' · <a class="danger" href="/delete/{page["id"]}">Delete</a></p>'
                + (f"<ul>{videos}</ul>" if videos else "")
                + "</article>"
            )
        body = "".join(rows) or (
            '<p class="empty">Nothing saved yet. Press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+'
            "<kbd>F</kbd> on any page in the browser pane to save it here, encrypted.</p>"
        )
        banner = f'<p class="notice">{html.escape(notice)}</p>' if notice else ""
        for name in self.rotation_reminders():
            banner += (
                f'<p class="notice">Items were deleted permanently from “{html.escape(name)}”. '
                "Finish it: in Strata, open the layer's <b>Key management</b> and choose "
                "<b>Rotate key</b>. Until then, an old copy of the disk could still be read "
                "with the current key.</p>"
            )
        locked = self._locked_private_layers()
        if locked:
            # Say that more exists, not what: a locked layer's contents stay unknown.
            banner += (
                f'<p class="notice">{locked} private layer{"s are" if locked != 1 else " is"}'
                " locked. Unlock it in Strata to see the pages saved there.</p>"
            )
        return _shell(
            "Saved pages",
            f"<h1>Saved pages</h1><p class='meta'>Encrypted in your private layers. "
            f"Readable offline while unlocked.</p>{banner}{body}",
        )

    def _watch_page(self, object_id: str) -> str:
        _layer_id, access = self._find(object_id)
        entry = access.stream_entry(object_id)
        if entry.kind != "web_media":
            raise NotFoundError("Saved item not found.")
        page_id = entry.properties.get("page_id")
        back = f'<a href="/page/{page_id}/">Open the saved page</a> · ' if page_id else ""
        return _shell(
            entry.title,
            f'<p><a href="/">← All saved pages</a></p><h1>{html.escape(entry.title)}</h1>'
            f'<video controls autoplay src="/media/{object_id}"></video>'
            f'<p class="meta">{back}{html.escape(str(entry.properties.get("url", "")))}</p>',
        )

    def _confirm_delete_page(self, object_id: str) -> str:
        _layer_id, access = self._find(object_id)
        entry = access.stream_entry(object_id)
        extra = len(entry.properties.get("media_ids", [])) if entry.kind == "web_page" else 0
        also = f" and its {extra} saved video(s)" if extra else ""
        return _shell(
            "Delete",
            f"<h1>Delete “{html.escape(entry.title)}”{also}?</h1>"
            "<p>The encrypted files are removed from this layer. This cannot be undone.</p>"
            f'<form method="post" action="/delete/{object_id}">'
            '<button class="danger" type="submit">Delete</button> '
            '<a href="/">Cancel</a></form>'
            f'<form method="post" action="/delete/{object_id}?permanently=1">'
            '<p class="meta">Delete permanently: the encrypted file is overwritten before it '
            "is removed, and you will be reminded to rotate this layer's key — the step "
            "that makes any leftover copy (a backup, an SSD's spare blocks) unreadable.</p>"
            '<button class="danger" type="submit">Delete permanently</button></form>',
        )


def _shell(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>"
        ":root{color-scheme:light dark;--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;"
        "--line:#e7e5e4;--accent:#2563eb;--danger:#b91c1c}"
        "@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;"
        "--muted:#a8a29e;--line:#44403c;--accent:#60a5fa;--danger:#f87171}}"
        "body{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);"
        "max-width:860px;margin:0 auto;padding:24px 16px}"
        "h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0}"
        "a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}"
        "article{border-top:1px solid var(--line);padding:14px 0}"
        ".meta{color:var(--muted);font-size:13px;margin:4px 0;word-break:break-all}"
        ".danger{color:var(--danger)}button.danger{background:var(--danger);color:#fff;"
        "border:0;border-radius:6px;padding:8px 14px;font:inherit;cursor:pointer}"
        "ul{margin:6px 0 0;padding-left:18px}video{width:100%;max-height:70vh;"
        "background:#000;border-radius:8px}.notice{padding:8px 12px;border:1px solid "
        "var(--line);border-radius:6px}.empty{color:var(--muted);padding:32px 0}"
        "kbd{border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:13px}"
        f"</style></head><body>{body}</body></html>"
    )


def _message_page(heading: str, text: str) -> str:
    return _shell(
        heading,
        f'<p><a href="/">← All saved pages</a></p><h1>{html.escape(heading)}</h1>'
        f"<p>{html.escape(text)}</p>",
    )


def _locked_page() -> str:
    return _shell(
        "Locked",
        "<h1>Locked</h1><p>Saved pages live in a private layer. Unlock it in Strata "
        "to read them.</p>",
    )
