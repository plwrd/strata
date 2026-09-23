"""Streamed video (YouTube, X): what goes to yt-dlp and ffmpeg, and what never does.

yt-dlp is replaced with a fake here — the real one talks to the network — so
these pin Strata's side of the contract: cookies stay in memory and off
ffmpeg's command line, DRM and live streams are refused, ffmpeg writes to a
pipe and never to a file, and a streamed save is encrypted like any other.
"""

from __future__ import annotations

import ipaddress
import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest
from scripts.scan_plaintext import scan_layer

from app.domain.errors import InvalidRequestError, LayerLockedError, ProviderError
from app.services.container import Services
from app.services.stream_extractor import ExtractedStream, StreamCookie, StreamExtractor
from app.services.web_archive_service import Cookie, PageCapture, WebArchiveService

pytestmark = pytest.mark.security

PASSWORD = "correct horse battery staple"
FRAGMENT = b"\x00\x00\x00\x18ftypisom" + b"BLUEJAYSTREAM" * 50_000


class _FakeYDL:
    """Stands in for ``yt_dlp.YoutubeDL``; records what Strata handed it."""

    last: ClassVar[_FakeYDL | None] = None
    info: ClassVar[dict[str, Any]] = {}
    error: ClassVar[str] = ""

    def __init__(self, options: dict[str, Any]) -> None:
        import http.cookiejar

        self.options = options
        self.cookiejar = http.cookiejar.CookieJar()
        self.urls: list[str] = []
        _FakeYDL.last = self

    def __enter__(self) -> _FakeYDL:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        assert download is False
        self.urls.append(url)
        if _FakeYDL.error:
            raise sys.modules["yt_dlp"].utils.DownloadError(_FakeYDL.error)
        return _FakeYDL.info


@pytest.fixture()
def fake_ytdlp(monkeypatch: pytest.MonkeyPatch) -> type[_FakeYDL]:
    class DownloadError(Exception):
        pass

    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = _FakeYDL  # type: ignore[attr-defined]
    module.utils = types.SimpleNamespace(DownloadError=DownloadError)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    _FakeYDL.error = ""
    _FakeYDL.info = {
        "title": "Northwind briefing",
        "duration": 12.0,
        "height": 720,
        "requested_formats": [
            {
                "url": "https://rr1.googlevideo.example/videoplayback?itag=136",
                "protocol": "https",
                "http_headers": {"User-Agent": "UA", "Cookie": "SID=secret"},
            },
            {
                "url": "https://rr1.googlevideo.example/videoplayback?itag=140",
                "protocol": "https",
                "http_headers": {"User-Agent": "UA"},
            },
        ],
    }
    return _FakeYDL


def test_cookies_go_to_ytdlp_in_memory_and_not_to_ffmpeg(fake_ytdlp: type[_FakeYDL]) -> None:
    extractor = StreamExtractor(ffmpeg_path=lambda: sys.executable)
    found = extractor.extract(
        "https://www.youtube.com/watch?v=abc",
        [StreamCookie("SID", "secret", ".youtube.com", "/", True)],
        "UA",
    )
    ydl = fake_ytdlp.last
    assert ydl is not None
    assert [c.name for c in ydl.cookiejar] == ["SID"]
    assert "cookiefile" not in ydl.options and ydl.options["cachedir"] is False
    assert ydl.options["noplaylist"] is True

    assert len(found.tracks) == 2
    command = " ".join(extractor.command(found))
    assert "secret" not in command and "Cookie" not in command
    assert command.endswith("pipe:1")  # the only output is the pipe
    # Every input is limited to web protocols: a playlist cannot name file:.
    argv = extractor.command(found)
    for index, arg in enumerate(argv):
        if arg == "-i":
            assert argv[index - 2 : index] == [
                "-protocol_whitelist",
                "https,http,tls,tcp,crypto,hls",
            ]
    assert "frag_keyframe+empty_moov" in command


@pytest.mark.parametrize(
    ("patch", "error"),
    [
        ({"requested_formats": [{"url": "https://a/x", "has_drm": True}]}, "DRM"),
        ({"is_live": True}, "Live"),
        ({"requested_formats": [{"url": "file:///etc/passwd"}]}, "not on the web"),
        ({"requested_formats": [{"url": "https://a/x", "protocol": "rtmp"}]}, "Unsupported"),
    ],
)
def test_refuses_drm_live_and_non_web_streams(
    fake_ytdlp: type[_FakeYDL], patch: dict[str, Any], error: str
) -> None:
    fake_ytdlp.info = {**fake_ytdlp.info, **patch}
    with pytest.raises(InvalidRequestError, match=error):
        StreamExtractor().extract("https://x.com/a/status/1", [])


def test_bot_check_becomes_an_actionable_message(fake_ytdlp: type[_FakeYDL]) -> None:
    fake_ytdlp.error = "ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies"
    with pytest.raises(ProviderError, match="Sign in to YouTube in the browser pane"):
        StreamExtractor().extract("https://www.youtube.com/watch?v=abc", [])


def test_missing_ffmpeg_is_reported(fake_ytdlp: type[_FakeYDL]) -> None:
    extractor = StreamExtractor(ffmpeg_path=lambda: "definitely-not-ffmpeg-here")
    assert "ffmpeg" in extractor.unavailable_reason()


# -- through the archive -------------------------------------------------------


class _FakeExtractor(StreamExtractor):
    def __init__(self, *, fail_after: int = 0) -> None:
        super().__init__()
        self.pages: list[str] = []
        self.cookies: list[StreamCookie] = []
        self.fail_after = fail_after

    def unavailable_reason(self) -> str:
        return ""

    def extract(self, page_url: str, cookies: Any, user_agent: str = "") -> ExtractedStream:
        self.pages.append(page_url)
        self.cookies = list(cookies)
        return ExtractedStream("Northwind briefing", page_url, 12.0, [("https://v/1", {})], 720)

    def run(self, stream: ExtractedStream, on_chunk: Any, on_progress: Any = None) -> None:
        for index, start in enumerate(range(0, len(FRAGMENT), 100_000)):
            if self.fail_after and index >= self.fail_after:
                raise ProviderError("The video stream could not be fetched.")
            on_chunk(FRAGMENT[start : start + 100_000])
            if on_progress:
                on_progress(index)


def _archive(services: Services, extractor: StreamExtractor) -> tuple[WebArchiveService, str]:
    services.workspace.open_or_create(services.paths.default_workspace, "Test")
    layer, _ = services.workspace.create_layer("Vault", visibility="private", password=PASSWORD)
    service = WebArchiveService(
        services.workspace,
        services.settings,
        services.encryption,
        extractor=extractor,
        resolver=lambda _host: [ipaddress.ip_address("93.184.216.34")],
    )
    services.encryption.on_lock(service.forget_layer)
    return service, layer.id


def _capture() -> PageCapture:
    return PageCapture(
        url="https://x.com/home",
        title="Home / X",
        mhtml="MIME-Version: 1.0\r\nContent-Type: text/html\r\n\r\n<p>feed</p>",
        streamed_media=1,
        cookies=(Cookie("auth_token", "tok", ".x.com", "/", True),),
        stream_pages=("https://x.com/nasa/status/1",),
    )


def test_a_streamed_video_is_saved_encrypted(services: Services) -> None:
    extractor = _FakeExtractor()
    service, layer_id = _archive(services, extractor)
    result = service.save(_capture())

    assert result.media_saved == [("Northwind briefing", len(FRAGMENT))]
    assert result.streamed_skipped == 0
    assert extractor.pages == ["https://x.com/nasa/status/1"]
    assert [c.name for c in extractor.cookies] == ["auth_token"]
    video = next(i for i in service.list_saved() if i["kind"] == "web_media")
    assert video["mime"] == "video/mp4" and video["url"] == "https://x.com/nasa/status/1"

    root = services.paths.default_workspace / "layers" / layer_id
    assert scan_layer(root, ["BLUEJAYSTREAM", "Northwind", "nasa"]) == []


def test_a_failed_stream_leaves_nothing_and_says_why(services: Services) -> None:
    service, layer_id = _archive(services, _FakeExtractor(fail_after=2))
    result = service.save(_capture())
    assert result.media_saved == []
    assert "could not be fetched" in result.summary()
    root = services.paths.default_workspace / "layers" / layer_id
    assert not list(root.rglob("*.tmp"))
    assert [i["kind"] for i in service.list_saved()] == ["web_page"]


def test_locking_during_a_stream_stops_it(services: Services) -> None:
    extractor = _FakeExtractor()
    service, layer_id = _archive(services, extractor)
    ticks = {"n": 0}

    def lock_midway(message: str) -> None:
        if message.startswith("Encrypting video"):
            ticks["n"] += 1
            if ticks["n"] == 2:
                services.workspace.lock_layer(layer_id)

    with pytest.raises(LayerLockedError):
        service.save(_capture(), progress=lock_midway)
    root = services.paths.default_workspace / "layers" / layer_id
    assert not list(root.rglob("*.tmp"))


def test_without_the_tools_the_reason_is_reported(services: Services) -> None:
    class Missing(_FakeExtractor):
        def unavailable_reason(self) -> str:
            return "ffmpeg was not found"

    service, _layer = _archive(services, Missing())
    result = service.save(_capture())
    assert "ffmpeg was not found" in result.summary()


def test_real_ffmpeg_pipe_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ffmpeg's only output is stdout: run it in an empty directory and check."""
    extractor = StreamExtractor()
    if not extractor.ffmpeg():
        pytest.skip("ffmpeg not installed")
    monkeypatch.chdir(tmp_path)
    stream = ExtractedStream("t", "https://x", 1.0, [("https://x", {})])
    command = extractor.command(stream)
    # Replace the network input with a generated one; everything else as shipped.
    at = command.index("-i")
    command[at : at + 2] = ["-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5"]
    command[command.index("-c") : command.index("-c") + 2] = ["-c:v", "libx264"]
    import subprocess

    out = subprocess.run(command, capture_output=True, check=True).stdout  # noqa: S603
    assert out[4:8] == b"ftyp" and b"moof" in out
    assert list(tmp_path.iterdir()) == []
