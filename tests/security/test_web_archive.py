"""The encrypted web archive, end to end against real ciphertext.

Save a page and a video into a private layer, then check what the user was
promised: the disk holds only ciphertext (checked with the same plaintext
scanner CI runs), the saved items read back offline while unlocked, a video
seeks by byte range, and locking cuts off everything — including a video that
was already playing.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import httpx
import pytest
from scripts.scan_plaintext import scan_layer

from app.domain.errors import LayerLockedError, PermissionDeniedError
from app.services.container import Services
from app.services.web_archive_service import (
    VAULT_ORIGIN,
    Cookie,
    PageCapture,
    WebArchiveService,
    classify_media_url,
    cookie_header,
    parse_range,
)

pytestmark = pytest.mark.security

PASSWORD = "correct horse battery staple"
PAGE_URL = "https://news.example/story/northwind?id=7"
VIDEO_URL = "https://media.example/v/bluejay-clip.mp4"
VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"BLUEJAYFRAME" * 90_000  # ~1 MB, recognisable
MARKERS = ["Northwind", "BLUEJAY", "bluejay", "news.example", "media.example"]

MHTML = (
    "From: <Saved by Blink>\r\n"
    "Snapshot-Content-Location: https://news.example/story/northwind?id=7\r\n"
    "Subject: Northwind acquisition\r\n"
    "MIME-Version: 1.0\r\n"
    'Content-Type: multipart/related; type="text/html"; boundary="----B"\r\n'
    "\r\n"
    "------B\r\n"
    "Content-Type: text/html\r\n"
    "Content-ID: <frame-main@mhtml.blink>\r\n"
    "Content-Transfer-Encoding: quoted-printable\r\n"
    "Content-Location: https://news.example/story/northwind?id=7\r\n"
    "\r\n"
    '<html><head><link rel=3D"stylesheet" href=3D"https://news.example/s/site.css">'
    '</head><body><h1>Northwind deal</h1><img src=3D"/img/logo.png">'
    '<iframe src=3D"cid:frame-sub@mhtml.blink"></iframe></body></html>\r\n'
    "------B\r\n"
    "Content-Type: text/css\r\n"
    "Content-Transfer-Encoding: quoted-printable\r\n"
    "Content-Location: https://news.example/s/site.css\r\n"
    "\r\n"
    "body { background: url(../img/bg.png) }\r\n"
    "------B\r\n"
    "Content-Type: image/png\r\n"
    "Content-Transfer-Encoding: base64\r\n"
    "Content-Location: https://news.example/img/bg.png\r\n"
    "\r\n"
    "iVBORw0KGgo=\r\n"
    "------B\r\n"
    "Content-Type: text/html\r\n"
    "Content-ID: <frame-sub@mhtml.blink>\r\n"
    "Content-Transfer-Encoding: quoted-printable\r\n"
    "\r\n"
    "<p>BLUEJAY sidebar</p>\r\n"
    "------B--\r\n"
)


def _public(_host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    return [ipaddress.ip_address("93.184.216.34")]


def _video_server(request: httpx.Request) -> httpx.Response:
    if str(request.url) == VIDEO_URL:
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=VIDEO)
    if request.url.path.endswith(".html"):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
    return httpx.Response(404)


@pytest.fixture()
def archive(services: Services) -> tuple[Services, WebArchiveService, str]:
    services.workspace.open_or_create(services.paths.default_workspace, "Test")
    layer, _recovery = services.workspace.create_layer(
        "Research vault", visibility="private", password=PASSWORD
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _video_server(request)

    service = WebArchiveService(
        services.workspace,
        services.settings,
        services.encryption,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=_public,
    )
    service.requests = seen  # type: ignore[attr-defined]
    # The container's lock hook targets the container's instance; point it here.
    services.encryption.on_lock(service.forget_layer)
    return services, service, layer.id


def _capture(**overrides: object) -> PageCapture:
    values: dict[str, object] = {
        "url": PAGE_URL,
        "title": "Northwind acquisition",
        "mhtml": MHTML,
        "media_urls": (VIDEO_URL, "blob:https://news.example/abc"),
        "streamed_media": 1,
        "user_agent": "StrataTest/1.0",
        "cookies": (
            Cookie("session", "s3cret", ".media.example", "/", True),
            Cookie("tracker", "nope", "news.example"),
        ),
    }
    values.update(overrides)
    return PageCapture(**values)  # type: ignore[arg-type]


def _layer_root(services: Services, layer_id: str) -> Path:
    return services.paths.default_workspace / "layers" / layer_id


def _body(response: object) -> bytes:
    body = response.body  # type: ignore[attr-defined]
    try:
        return body.read_at(0, body.size)
    finally:
        body.close()


def test_saves_page_and_video_encrypted(archive: tuple[Services, WebArchiveService, str]) -> None:
    services, service, layer_id = archive
    result = service.save(_capture())

    assert result.media_saved and result.media_saved[0][1] == len(VIDEO)
    assert result.streamed_skipped == 1
    assert "encrypted" in result.summary()

    root = _layer_root(services, layer_id)
    assert scan_layer(root, MARKERS) == []
    everything = b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())
    assert b"BLUEJAYFRAME" not in everything
    assert not list(root.rglob("*.tmp"))


def test_download_sends_only_matching_cookies(
    archive: tuple[Services, WebArchiveService, str],
) -> None:
    _services, service, _layer = archive
    service.save(_capture())
    request = service.requests[0]  # type: ignore[attr-defined]
    assert request.headers["cookie"] == "session=s3cret"
    assert request.headers["referer"] == PAGE_URL
    assert request.headers["user-agent"] == "StrataTest/1.0"


def test_saved_items_are_listed_and_served(
    archive: tuple[Services, WebArchiveService, str],
) -> None:
    _services, service, _layer = archive
    result = service.save(_capture())
    items = service.list_saved()
    page = next(item for item in items if item["kind"] == "web_page")
    video = next(item for item in items if item["kind"] == "web_media")
    assert page["id"] == result.page_id and page["mediaIds"] == [video["id"]]

    library = service.respond("GET", VAULT_ORIGIN + "/")
    assert library.status == 200
    assert b"Northwind acquisition" in _body(library)
    assert library.headers["Cache-Control"] == "no-store"

    full = service.respond("GET", f"{VAULT_ORIGIN}/media/{video['id']}")
    assert full.status == 200 and _body(full) == VIDEO

    partial = service.respond("GET", f"{VAULT_ORIGIN}/media/{video['id']}", "bytes=100-199")
    assert partial.status == 206
    assert partial.headers["Content-Range"] == f"bytes 100-199/{len(VIDEO)}"
    assert _body(partial) == VIDEO[100:200]


def test_saved_page_is_rewritten_to_read_offline(
    archive: tuple[Services, WebArchiveService, str],
) -> None:
    _services, service, _layer = archive
    page_id = service.save(_capture(media_urls=())).page_id

    entry = service.page_entry_url(page_id)
    assert entry.startswith(f"{VAULT_ORIGIN}/page/{page_id}/r/https/news.example/story/northwind")
    main = service.respond("GET", entry)
    assert main.status == 200
    assert "script-src 'none'" in main.headers["Content-Security-Policy"]
    html = _body(main).decode()
    assert f"/page/{page_id}/r/https/news.example/s/site.css" in html
    assert f'src="/page/{page_id}/r/https/news.example/img/logo.png"' in html
    assert "cid:" not in html
    assert html.count(f"/page/{page_id}/") == html.count(f"/page/{page_id}/r/") + html.count(
        f"/page/{page_id}/cid/"
    ), "a path was rewritten twice"
    assert f"/page/{page_id}/r/https/news.example/page/" not in html

    # The stylesheet's relative url() resolves against the mirrored path.
    css = service.respond("GET", f"{VAULT_ORIGIN}/page/{page_id}/r/https/news.example/s/site.css")
    assert b"../img/bg.png" in _body(css)
    image = service.respond("GET", f"{VAULT_ORIGIN}/page/{page_id}/r/https/news.example/img/bg.png")
    assert image.status == 200 and _body(image).startswith(b"\x89PNG")

    missing = service.respond("GET", f"{VAULT_ORIGIN}/page/{page_id}/r/https/cdn.other/x.js")
    assert missing.status == 404


def test_saved_video_plays_in_place_on_the_saved_page(
    archive: tuple[Services, WebArchiveService, str],
) -> None:
    mhtml = MHTML.replace(
        "<h1>Northwind deal</h1>", f'<h1>Northwind deal</h1><video src=3D"{VIDEO_URL}">'
    )
    _services, service, _layer = archive
    page_id = service.save(_capture(mhtml=mhtml)).page_id
    video = next(i for i in service.list_saved() if i["kind"] == "web_media")
    html = _body(service.respond("GET", service.page_entry_url(page_id))).decode()
    assert f'<video src="/media/{video["id"]}">' in html
    assert VIDEO_URL not in html


def test_nothing_saves_without_an_unlocked_private_layer(services: Services) -> None:
    services.workspace.open_or_create(services.paths.default_workspace, "Test")
    service = WebArchiveService(services.workspace, services.settings, services.encryption)
    with pytest.raises(PermissionDeniedError):
        service.save(_capture())


def test_locking_stops_a_playing_video_and_hides_the_library(
    archive: tuple[Services, WebArchiveService, str],
) -> None:
    services, service, layer_id = archive
    service.save(_capture())
    video = next(i for i in service.list_saved() if i["kind"] == "web_media")
    playing = service.respond("GET", f"{VAULT_ORIGIN}/media/{video['id']}")
    assert playing.body.read_at(0, 10) == VIDEO[:10]

    services.workspace.lock_layer(layer_id)

    with pytest.raises(LayerLockedError):
        playing.body.read_at(10, 10)
    locked = service.respond("GET", VAULT_ORIGIN + "/")
    listing = _body(locked)
    assert b"1 private layer is locked" in listing
    assert b"Northwind" not in listing
    again = service.respond("GET", f"{VAULT_ORIGIN}/media/{video['id']}")
    assert again.status in (403, 404)


def test_locking_mid_download_leaves_no_file(
    archive: tuple[Services, WebArchiveService, str],
) -> None:
    services, service, layer_id = archive
    calls = {"n": 0}

    def lock_after_a_while(_message: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            services.workspace.lock_layer(layer_id)

    with pytest.raises(LayerLockedError):
        service.save(_capture(), progress=lock_after_a_while)
    root = _layer_root(services, layer_id)
    assert not list(root.rglob("*.tmp"))
    assert scan_layer(root, MARKERS) == []


def test_non_media_and_oversized_downloads_are_refused(
    archive: tuple[Services, WebArchiveService, str],
) -> None:
    services, service, _layer = archive
    result = service.save(_capture(media_urls=("https://media.example/player.html",)))
    assert result.media_saved == [] and len(result.media_failed) == 1

    services.settings.update({"web_archive_max_media_mb": 1})
    result = service.save(_capture())
    assert result.media_saved == [] and "size limit" in result.media_failed[0][1]


def test_delete_removes_page_and_its_videos(
    archive: tuple[Services, WebArchiveService, str],
) -> None:
    services, service, layer_id = archive
    page_id = service.save(_capture()).page_id
    objects_before = len(list((_layer_root(services, layer_id) / "objects").rglob("*")))

    confirm = service.respond("GET", f"{VAULT_ORIGIN}/delete/{page_id}")
    assert b'<form method="post"' in _body(confirm)
    done = service.respond("POST", f"{VAULT_ORIGIN}/delete/{page_id}")
    assert done.status == 200
    assert service.list_saved() == []
    objects_after = len(list((_layer_root(services, layer_id) / "objects").rglob("*")))
    assert objects_after == objects_before - 2


def test_key_rotation_re_encrypts_saved_items(
    archive: tuple[Services, WebArchiveService, str],
) -> None:
    services, service, layer_id = archive
    service.save(_capture())
    video = next(i for i in service.list_saved() if i["kind"] == "web_media")
    path = next(p for p in (_layer_root(services, layer_id) / "objects").rglob(video["id"]))
    before = path.read_bytes()

    services.workspace.rotate_layer_key(layer_id, PASSWORD)

    assert path.read_bytes() != before
    served = service.respond("GET", f"{VAULT_ORIGIN}/media/{video['id']}")
    assert _body(served) == VIDEO


def test_survives_a_restart(archive: tuple[Services, WebArchiveService, str]) -> None:
    services, service, layer_id = archive
    service.save(_capture())
    services.workspace.close()

    fresh = Services(services.paths, environment="test")
    fresh.workspace.open(services.paths.default_workspace)
    fresh.workspace.unlock_layer(layer_id, PASSWORD)
    items = fresh.web_archive.list_saved()
    video = next(i for i in items if i["kind"] == "web_media")
    served = fresh.web_archive.respond("GET", f"{VAULT_ORIGIN}/media/{video['id']}")
    assert _body(served) == VIDEO


def test_only_the_vault_host_is_answered(archive: tuple[Services, WebArchiveService, str]) -> None:
    _services, service, _layer = archive
    assert service.respond("GET", "https://evil.example/").status == 404
    assert service.respond("GET", f"{VAULT_ORIGIN}/media/../../etc").status == 404
    assert service.respond("PUT", f"{VAULT_ORIGIN}/").status == 405


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bytes=0-99", (0, 100)),
        ("bytes=100-", (100, 1000)),
        ("bytes=-100", (900, 1000)),
        ("bytes=990-2000", (990, 1000)),
        ("bytes=1000-", None),
        ("bytes=-0", None),
        ("bytes=5-1", None),
        ("items=0-1", None),
    ],
)
def test_parse_range(header: str, expected: tuple[int, int] | None) -> None:
    assert parse_range(header, 1000) == expected


def _resolving(table: dict[str, str]):  # type: ignore[no-untyped-def]
    return lambda host: [ipaddress.ip_address(table.get(host, "93.184.216.34"))]


def test_a_video_on_a_private_address_is_refused(services: Services) -> None:
    services.workspace.open_or_create(services.paths.default_workspace, "Test")
    services.workspace.create_layer("Vault", visibility="private", password=PASSWORD)
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=VIDEO)

    service = WebArchiveService(
        services.workspace,
        services.settings,
        services.encryption,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=_resolving({"router.lan": "192.168.1.1", "loop.example": "127.0.0.1"}),
    )
    for url in ("http://router.lan/v.mp4", "http://loop.example/v.mp4", "http://[::1]/v.mp4"):
        result = service.save(_capture(media_urls=(url,)))
        assert result.media_saved == [] and "private network" in result.media_failed[0][1]
    assert hits == []  # refused before any request left

    services.settings.update({"web_archive_allow_private_addresses": True})
    assert service.save(_capture(media_urls=("http://router.lan/v.mp4",))).media_saved


def test_a_redirect_into_the_network_is_refused_at_the_hop(services: Services) -> None:
    services.workspace.open_or_create(services.paths.default_workspace, "Test")
    services.workspace.create_layer("Vault", visibility="private", password=PASSWORD)
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        if request.url.host == "media.example":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/admin"})
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=VIDEO)

    service = WebArchiveService(
        services.workspace,
        services.settings,
        services.encryption,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ),
        resolver=_public,
    )
    result = service.save(_capture())
    assert result.media_saved == []
    assert hits == [VIDEO_URL]  # the loopback hop never happened


def test_media_classification() -> None:
    assert classify_media_url("https://a.example/v.mp4") == "file"
    assert classify_media_url("https://a.example/master.m3u8") == "stream"
    assert classify_media_url("blob:https://a.example/123") == "stream"
    assert classify_media_url("data:video/mp4;base64,AA") == "skip"


def test_cookie_matching_is_per_url() -> None:
    cookies = [
        Cookie("a", "1", ".example.com"),
        Cookie("b", "2", "www.example.com", "/app"),
        Cookie("c", "3", "example.com", "/", secure=True),
    ]
    assert cookie_header(cookies, "https://cdn.example.com/x") == "a=1"
    assert cookie_header(cookies, "https://www.example.com/app/v.mp4") == "a=1; b=2"
    assert cookie_header(cookies, "http://example.com/") == "a=1"
    assert cookie_header(cookies, "https://notexample.com/") == ""


def test_permanent_delete_overwrites_and_asks_for_a_rotation(
    archive: tuple[Services, WebArchiveService, str],
) -> None:
    services, service, layer_id = archive
    page_id = service.save(_capture()).page_id
    video = next(i for i in service.list_saved() if i["kind"] == "web_media")
    video_path = next((_layer_root(services, layer_id) / "objects").rglob(video["id"]))

    written: list[bytes] = []
    real_unlink = Path.unlink

    def spy(path: Path, missing_ok: bool = False) -> None:
        if path == video_path:
            written.append(path.read_bytes())  # what is on disk at the moment of removal
        real_unlink(path, missing_ok=missing_ok)

    import unittest.mock

    before = video_path.read_bytes()
    confirm = _body(service.respond("GET", f"{VAULT_ORIGIN}/delete/{page_id}"))
    assert b"Delete permanently" in confirm
    with unittest.mock.patch.object(Path, "unlink", spy):
        done = service.respond("POST", f"{VAULT_ORIGIN}/delete/{page_id}?permanently=1")
    assert done.status == 200
    assert written and written[0] != before and len(written[0]) == len(before)
    assert b"Rotate key" in _body(service.respond("GET", VAULT_ORIGIN + "/"))

    services.workspace.rotate_layer_key(layer_id, PASSWORD)
    assert b"Rotate key" not in _body(service.respond("GET", VAULT_ORIGIN + "/"))
