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
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.domain.errors import InvalidRequestError, ProviderError
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

_PIPE_READ = 256 * 1024
_ALLOWED_PROTOCOLS = frozenset({"https", "http", "m3u8", "m3u8_native", "http_dash_segments"})
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
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._max_height = max_height

    # -- availability ---------------------------------------------------------

    def ffmpeg(self) -> str:
        configured = self._ffmpeg_path().strip()
        if configured:
            return configured if shutil.which(configured) else ""
        return shutil.which("ffmpeg") or ""

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
        self, page_url: str, cookies: Iterable[StreamCookie], user_agent: str = ""
    ) -> ExtractedStream:
        """Ask yt-dlp what video is behind ``page_url``. Blocking; network."""
        import yt_dlp

        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            # No cache directory: yt-dlp would otherwise keep player code and
            # tokens on disk, outside any layer.
            "cachedir": False,
            "format": _FORMAT.format(h=max(144, int(self._max_height()))),
            # YouTube's signature challenge needs a JavaScript runtime.
            "js_runtimes": {"deno": {}, "node": {}},
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
            command += ["-i", url]
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
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            self.command(stream),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=flags,
        )
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
        if code != 0:
            tail = b"".join(errors).decode("utf-8", "replace")
            lines = [line for line in tail.splitlines() if line and not line.startswith("frame=")]
            logger.warning("web_archive.ffmpeg_failed", code=code)
            raise ProviderError(
                "The video stream could not be fetched"
                + (f" ({lines[-1][:160]})." if lines else ".")
            )


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
