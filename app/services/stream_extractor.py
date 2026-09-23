"""Streamed video (YouTube, X, and other player-assembled media) for the archive.

A page like YouTube or X never hands its ``<video>`` a file: the player fetches
pieces (HLS/DASH, or YouTube's own protocol) and feeds them in through a
``blob:`` URL, so there is nothing plain for the archive to download. Saving
those needs two tools the ordinary path does not:

* **yt-dlp** finds the real streams behind the page — the same job it does for
  ~1,800 sites — using the *pane's own cookies*, loaded into its in-memory
  cookie jar (never a cookie file on disk). YouTube refuses anonymous requests
  with a bot check; the signed-in (or merely visited) pane session is what
  gets past it.
* **ffmpeg** fetches the chosen video and audio tracks and joins them into one
  *fragmented* MP4 on its standard output. Fragmented, because a normal MP4
  writes its index at the end and needs to seek back to the start — which
  would mean a file on disk. Here ffmpeg never writes a file at all: its output
  is a pipe, read by :class:`StreamExtractor` a chunk at a time and handed to
  the archive's encrypting writer. Plaintext exists only in memory.

What is refused: DRM-protected formats (``has_drm``). What is not attempted:
live streams. Both are reported, never silently skipped.

yt-dlp is imported lazily, so a build without it still saves pages and plain
videos and says why streamed ones were skipped.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.domain.errors import InvalidRequestError, ProviderError
from app.infrastructure import sandbox
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

_PIPE_READ = 256 * 1024
_ALLOWED_PROTOCOLS = frozenset({"https", "http", "m3u8", "m3u8_native", "http_dash_segments"})
_PROTOCOL_WHITELIST = "https,http,tls,tcp,crypto,hls"
_FFMPEG_TIME = re.compile(rb"time=(\d+):(\d+):(\d+(?:\.\d+)?)")

# H.264 + AAC first, because that is what plays everywhere (and what the
# WebView2 pane, with Edge's codecs, plays with hardware help). Then anything
# up to the height cap, then anything at all.
_FORMAT = (
    "bv*[vcodec^=avc1][height<={h}]+ba[acodec^=mp4a]"
    "/b[vcodec^=avc1][height<={h}]"
    "/bv*[height<={h}]+ba"
    "/b[height<={h}]"
    "/bv*+ba/b"
)


@dataclass(frozen=True)
class StreamCookie:
    """A cookie from the pane, as DevTools reports it."""

    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False


@dataclass
class ExtractedStream:
    """What yt-dlp found behind a page: one video, as one or two tracks."""

    title: str
    page_url: str
    duration: float
    # (url, headers) per track, in input order for ffmpeg.
    tracks: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    height: int = 0


def bundled_tool(name: str) -> str:
    """A helper the Windows installer ships (packaging/tools/<name>/<name>.exe), or ""."""
    from app.bootstrap import resource_root

    candidate = resource_root() / "packaging" / "tools" / name / f"{name}.exe"
    return str(candidate) if candidate.is_file() else ""


def _cookie_jar(cookies: Iterable[StreamCookie]) -> http.cookiejar.CookieJar:
    jar = http.cookiejar.CookieJar()
    for c in cookies:
        domain = c.domain
        jar.set_cookie(
            http.cookiejar.Cookie(
                version=0,
                name=c.name,
                value=c.value,
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=True,
                domain_initial_dot=domain.startswith("."),
                path=c.path or "/",
                path_specified=True,
                secure=c.secure,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
            )
        )
    return jar


class StreamExtractor:
    """yt-dlp to find the streams, ffmpeg to join them into a pipe."""

    def __init__(
        self,
        *,
        ffmpeg_path: Callable[[], str] = lambda: "",
        max_height: Callable[[], int] = lambda: 1080,
        worker_command: list[str] | None = None,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._max_height = max_height
        self._worker_command = worker_command

    # -- availability ---------------------------------------------------------

    def ffmpeg(self) -> str:
        """The configured ffmpeg, else the one the installer bundles, else PATH."""
        configured = self._ffmpeg_path().strip()
        if configured:
            return configured if shutil.which(configured) else ""
        return bundled_tool("ffmpeg") or shutil.which("ffmpeg") or ""

    def unavailable_reason(self) -> str:
        """Why streamed video cannot be saved on this machine, or ""."""
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return "the yt-dlp component is not installed"
        if not self.ffmpeg():
            return "ffmpeg was not found (install it, or set its path in Settings)"
        return ""

    # -- finding the streams --------------------------------------------------

    def extract(
        self,
        page_url: str,
        cookies: Iterable[StreamCookie],
        user_agent: str = "",
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> ExtractedStream:
        """Find the video behind ``page_url`` — in a confined worker process.

        yt-dlp parses whatever the site sends, so it does not run in the
        process that holds the layer keys. The request (cookies included) goes
        to the worker on stdin, never on its command line; the answer comes back
        as JSON on stdout and is validated again here, since the worker is the
        less trusted side.
        """
        request = json.dumps(
            {
                "deno_path": bundled_tool("deno"),
                "page_url": page_url,
                "cookies": [c.__dict__ for c in cookies],
                "user_agent": user_agent,
                "max_height": int(self._max_height()),
            }
        ).encode("utf-8")
        confined = sandbox.spawn(
            self._worker_argv(),
            # yt-dlp may start a JavaScript runtime (Node/Deno) for YouTube.
            sandbox.Limits(memory_mb=1536, max_processes=6),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        process = confined.process
        chunks: list[bytes] = []

        def pump() -> None:
            assert process.stdin is not None and process.stdout is not None
            try:
                process.stdin.write(request)
                process.stdin.close()
            except OSError:
                pass  # the worker died; its (empty) reply says so
            chunks.append(process.stdout.read(_MAX_REPLY + 1))

        # Pipes on a thread, so this loop can watch for a cancel and a timeout
        # without the reply ever filling a pipe buffer nobody is reading.
        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        deadline = time.monotonic() + _WORKER_TIMEOUT
        try:
            while reader.is_alive():
                reader.join(timeout=0.2)
                if cancelled():
                    raise ProviderError("Cancelled.")
                if time.monotonic() > deadline:
                    raise ProviderError("Finding the video took too long.")
        finally:
            confined.close()  # kills the worker (and any JS runtime) if still running
            process.wait(timeout=10)
        return _parse_worker_reply(b"".join(chunks), page_url)

    def _worker_argv(self) -> list[str]:
        if self._worker_command is not None:
            return list(self._worker_command)
        if getattr(sys, "frozen", False):
            return [sys.executable, WORKER_FLAG]
        return [sys.executable, "-m", "app.services.stream_extractor"]

    # -- fetching and joining -------------------------------------------------

    def command(self, stream: ExtractedStream) -> list[str]:
        ffmpeg = self.ffmpeg()
        if not ffmpeg:
            raise InvalidRequestError("ffmpeg was not found.")
        command = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-stats"]
        for url, headers in stream.tracks:
            if headers:
                block = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
                command += ["-headers", block]
            # Web protocols only. A playlist is site-controlled and could
            # otherwise name `file:` paths, `concat:`, or other local inputs.
            command += ["-protocol_whitelist", _PROTOCOL_WHITELIST, "-i", url]
        for index in range(len(stream.tracks)):
            command += ["-map", f"{index}:v?", "-map", f"{index}:a?"]
        command += [
            "-c",
            "copy",
            # HLS (X) carries AAC in ADTS framing, which MP4 cannot hold as is.
            # A no-op for audio that is already MP4-framed (YouTube's m4a).
            "-bsf:a",
            "aac_adtstoasc",
            "-f",
            "mp4",
            # Fragmented: written front to back with no seek, so the output can
            # be a pipe. `default_base_moof` keeps it playable in Chromium.
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "pipe:1",
        ]
        return command

    def run(
        self,
        stream: ExtractedStream,
        on_chunk: Callable[[bytes], None],
        on_progress: Callable[[float], None] = lambda _seconds: None,
    ) -> None:
        """Run ffmpeg and hand its output over a chunk at a time.

        ``on_chunk`` raising (the layer locked, the size cap) stops ffmpeg.
        Nothing is ever written to a file: ffmpeg's only output is the pipe.
        """
        # Confined: one process (ffmpeg starts nothing), bounded memory, and
        # killed with Strata. Remuxing with `-c copy` needs little memory.
        confined = sandbox.spawn(
            self.command(stream),
            sandbox.Limits(memory_mb=768, max_processes=1),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        process = confined.process
        errors: list[bytes] = []

        def watch_stderr() -> None:
            assert process.stderr is not None
            buffer = b""
            while True:
                piece = process.stderr.read(512)
                if not piece:
                    break
                buffer = (buffer + piece)[-4096:]
                matches = list(_FFMPEG_TIME.finditer(buffer))
                if matches:
                    match = matches[-1]
                    hours, minutes, seconds = match.groups()
                    on_progress(int(hours) * 3600 + int(minutes) * 60 + float(seconds))
            errors.append(buffer)

        reader = threading.Thread(target=watch_stderr, daemon=True)
        reader.start()
        try:
            assert process.stdout is not None
            while True:
                piece = process.stdout.read(_PIPE_READ)
                if not piece:
                    break
                on_chunk(piece)
        except BaseException:
            process.kill()
            raise
        finally:
            code = process.wait()
            reader.join(timeout=5)
            confined.close()
        if code != 0:
            tail = b"".join(errors).decode("utf-8", "replace")
            lines = [line for line in tail.splitlines() if line and not line.startswith("frame=")]
            logger.warning("web_archive.ffmpeg_failed", code=code)
            raise ProviderError(
                "The video stream could not be fetched"
                + (f" ({lines[-1][:160]})." if lines else ".")
            )


def extract_in_process(
    page_url: str,
    cookies: Iterable[StreamCookie],
    user_agent: str = "",
    max_height: int = 1080,
    *,
    deno_path: str = "",
) -> ExtractedStream:
    """Ask yt-dlp what video is behind ``page_url``. Blocking; network.

    Runs in the worker process (:func:`worker_main`), never in Strata's own.
    """
    import yt_dlp

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        # No cache directory: yt-dlp would otherwise keep player code and
        # tokens on disk, outside any layer.
        "cachedir": False,
        "format": _FORMAT.format(h=max(144, int(max_height))),
        # YouTube's signature challenge needs a JavaScript runtime.
        # The bundled Deno when there is one; else whatever is installed.
        "js_runtimes": {"deno": {"path": deno_path}} if deno_path else {"deno": {}, "node": {}},
        "logger": _QuietLogger(),
    }
    if user_agent:
        options["http_headers"] = {"User-Agent": user_agent}
    with yt_dlp.YoutubeDL(options) as ydl:
        # The pane's session, in memory only: never a cookies.txt.
        for cookie in _cookie_jar(cookies):
            ydl.cookiejar.set_cookie(cookie)
        try:
            info = ydl.extract_info(page_url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise ProviderError(_short_error(str(exc))) from exc

    if info is None:
        raise ProviderError("No video was found on this page.")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise ProviderError("No video was found on this page.")
        info = entries[0]
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise InvalidRequestError("Live streams cannot be saved.")

    formats = info.get("requested_formats") or [info]
    tracks: list[tuple[str, dict[str, str]]] = []
    for fmt in formats:
        if fmt.get("has_drm"):
            raise InvalidRequestError("This video is copy-protected (DRM) and cannot be saved.")
        protocol = str(fmt.get("protocol") or "https").split("+")[0]
        if protocol not in _ALLOWED_PROTOCOLS:
            raise InvalidRequestError(f"Unsupported stream type ({protocol}).")
        url = str(fmt.get("url") or "")
        if urlsplit(url).scheme not in ("http", "https"):
            raise InvalidRequestError("The stream is not on the web.")
        headers = {
            k: str(v)
            for k, v in (fmt.get("http_headers") or {}).items()
            # Cookies stay out of ffmpeg's command line, where any process
            # of this user could read them. The stream URLs yt-dlp returns
            # for these sites are signed and need none.
            if k.lower() != "cookie"
        }
        tracks.append((url, headers))
    if not tracks:
        raise ProviderError("No downloadable video was found on this page.")
    return ExtractedStream(
        title=str(info.get("title") or "video")[:200],
        page_url=page_url,
        duration=float(info.get("duration") or 0),
        tracks=tracks,
        height=int(info.get("height") or 0),
    )


# -- the worker process ----------------------------------------------------------

WORKER_FLAG = "--ytdlp-worker"
_WORKER_TIMEOUT = 180
_MAX_REPLY = 4 * 1024 * 1024


def _parse_worker_reply(raw: bytes, page_url: str) -> ExtractedStream:
    if len(raw) > _MAX_REPLY:
        raise ProviderError("The video finder sent back too much.")
    try:
        reply = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProviderError("The video finder stopped unexpectedly.") from None
    if not isinstance(reply, dict):
        raise ProviderError("The video finder stopped unexpectedly.")
    error = reply.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "No video was found.")[:300]
        if error.get("kind") == "invalid":
            raise InvalidRequestError(message)
        raise ProviderError(message)
    tracks: list[tuple[str, dict[str, str]]] = []
    for item in reply.get("tracks") or []:
        url = str(item[0]) if isinstance(item, list) and item else ""
        if urlsplit(url).scheme not in ("http", "https"):
            raise InvalidRequestError("The stream is not on the web.")
        headers = item[1] if len(item) > 1 and isinstance(item[1], dict) else {}
        tracks.append(
            (url, {str(k): str(v) for k, v in headers.items() if str(k).lower() != "cookie"})
        )
    if not tracks:
        raise ProviderError("No downloadable video was found on this page.")
    return ExtractedStream(
        title=str(reply.get("title") or "video")[:200],
        page_url=page_url,
        duration=float(reply.get("duration") or 0),
        tracks=tracks,
        height=int(reply.get("height") or 0),
    )


def worker_main(stdin: Any = None, stdout: Any = None) -> int:
    """The worker: one request on stdin, one JSON reply on stdout."""
    source = stdin or sys.stdin.buffer
    sink = stdout or sys.stdout.buffer
    try:
        request = json.loads(source.read().decode("utf-8"))
        found = extract_in_process(
            str(request["page_url"]),
            [StreamCookie(**c) for c in request.get("cookies") or []],
            str(request.get("user_agent") or ""),
            int(request.get("max_height") or 1080),
            deno_path=str(request.get("deno_path") or ""),
        )
        reply: dict[str, Any] = {
            "title": found.title,
            "duration": found.duration,
            "height": found.height,
            "tracks": found.tracks,
        }
    except InvalidRequestError as exc:
        reply = {"error": {"kind": "invalid", "message": str(exc)}}
    except Exception as exc:
        reply = {"error": {"kind": "provider", "message": str(exc) or type(exc).__name__}}
    sink.write(json.dumps(reply).encode("utf-8"))
    sink.flush()
    return 0


class _QuietLogger:
    """yt-dlp talks a lot; only its errors matter, and those arrive as exceptions."""

    def debug(self, _message: str) -> None:
        pass

    def info(self, _message: str) -> None:
        pass

    def warning(self, _message: str) -> None:
        pass

    def error(self, _message: str) -> None:
        pass


def _short_error(message: str) -> str:
    message = re.sub(r"^ERROR:\s*", "", message.strip())
    message = re.sub(r"\[[^\]]+\]\s*[\w-]+:\s*", "", message, count=1)
    if "not a bot" in message or "Sign in" in message:
        return (
            "YouTube asked to confirm this is not a bot. Sign in to YouTube in the "
            "browser pane (or play the video once), then save again."
        )
    return message.split(" Use --")[0][:240]


if __name__ == "__main__":
    sys.exit(worker_main())
