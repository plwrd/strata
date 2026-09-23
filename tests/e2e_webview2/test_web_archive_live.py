"""The encrypted web archive against the real WebView2 engine.

Unit tests pin the binding by vtable slot; only the engine itself can say the
slots are the *right* ones. This drives a real ``WebView2Pane`` through the
whole path — page + video saved over the DevTools snapshot and the worker
thread, the disk checked for plaintext, the network switched off, the saved
page and video read back through request interception and the decrypting
``IStream`` (including a seek, i.e. a range request), a web page refused the
vault, the lock — plus the trace clear that must keep sign-in cookies.

Opt-in (``-m webview2``): it opens a window. CI runs it on Windows.
"""

from __future__ import annotations

import functools
import http.server
import json
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.webview2,
    pytest.mark.skipif(sys.platform != "win32", reason="WebView2 is Windows-only"),
]

SITE = Path(__file__).with_name("site")
PAGE_HTML = (
    "<!doctype html><html><head><title>Northwind test page</title>"
    '<link rel="stylesheet" href="/style.css"></head>'
    '<body><h1>BLUEJAY research page</h1><video controls src="/clip.mp4"></video></body></html>'
)


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = PAGE_HTML.encode()
            self.send_response(200)
            # A sign-in cookie: the trace clear must leave it alone.
            self.send_header("Set-Cookie", "session=keep-me; Path=/; Max-Age=3600")
        elif self.path == "/style.css":
            body = b"h1{color:rgb(170,0,0)}"
            self.send_response(200)
        else:
            super().do_GET()
            return
        self.send_header("Content-Type", "text/html" if body.startswith(b"<") else "text/css")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    from app.bootstrap import resource_root
    from app.desktop.webview2 import sdk
    from app.desktop.webview2.pane import WebView2Pane
    from app.services.container import Paths, Services

    if not sdk.runtime_version():
        pytest.skip("the WebView2 runtime is not installed")
    loader = sdk.loader_path(resource_root())
    if loader is None:
        pytest.skip("WebView2Loader.dll is not available")

    app = QApplication.instance() or QApplication([])
    root = tmp_path_factory.mktemp("archive")
    paths = Paths(
        config_dir=root / "config",
        data_dir=root / "data",
        log_dir=root / "data" / "logs",
        default_workspace=root / "workspace",
    )
    services = Services(paths, environment="test")
    services.workspace.open_or_create(paths.default_workspace, "E2E")
    layer, _ = services.workspace.create_layer(
        "Vault", visibility="private", password="pw pw pw pw"
    )
    # The test site is on loopback, which the archive refuses by default (SSRF).
    services.settings.update({"web_archive_allow_private_addresses": True})

    handler = functools.partial(_Handler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    host = QWidget()
    host.resize(1000, 750)
    pane = WebView2Pane(
        user_data_dir=root / "wv2",
        loader=loader,
        hide_for_sharing=False,
        web_archive=services.web_archive,
        parent=host,
    )
    QVBoxLayout(host).addWidget(pane)
    host.show()

    def wait(condition: Callable[[], bool], timeout: float = 20.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if condition():
                return True
            time.sleep(0.02)
        return False

    def js(source: str, timeout: float = 10.0) -> Any:
        box: list[str] = []
        pane._controller.webview.execute_script(source, box.append)
        if not wait(lambda: bool(box), timeout):
            return None
        try:
            return json.loads(box[0])
        except ValueError:
            return box[0]

    if not wait(lambda: pane.ready, 60):
        pytest.skip(f"WebView2 did not start: {pane.failure_reason}")
    yield {
        "services": services,
        "layer": layer,
        "layer_dir": paths.default_workspace / "layers" / layer.id,
        "pane": pane,
        "server": server,
        "base": f"http://127.0.0.1:{server.server_address[1]}",
        "wait": wait,
        "js": js,
    }
    pane.shutdown()
    host.close()
    server.shutdown()


def test_save_read_offline_play_seek_and_lock(rig: dict[str, Any]) -> None:
    from app.services.web_archive_service import VAULT_ORIGIN

    pane, services, wait, js = rig["pane"], rig["services"], rig["wait"], rig["js"]
    pane.load_url(rig["base"] + "/index.html")
    assert wait(lambda: js("document.readyState") == "complete" and "Northwind" in pane._title)
    assert wait(lambda: (js("document.querySelector('video').readyState") or 0) >= 1, 15)

    pane.save_page()
    assert wait(lambda: not pane._saving, 90)
    assert "1 video" in pane._archive_status.text(), pane._archive_status.text()

    items = services.web_archive.list_saved()
    page = next(i for i in items if i["kind"] == "web_page")
    video = next(i for i in items if i["kind"] == "web_media")
    clip = (SITE / "clip.mp4").read_bytes()
    assert video["sizeBytes"] == len(clip)

    disk = b"".join(p.read_bytes() for p in rig["layer_dir"].rglob("*") if p.is_file())
    for marker in (b"BLUEJAY", b"Northwind", b"ftyp", clip[500:564]):
        assert marker not in disk
    assert not list(rig["layer_dir"].rglob("*.tmp"))

    rig["server"].shutdown()  # offline from here on

    pane.open_library()
    assert wait(lambda: "Northwind test page" in str(js("document.body.innerText") or ""), 15)

    pane._vault_requested = True
    pane.load_url(services.web_archive.page_entry_url(page["id"]))
    assert wait(lambda: "BLUEJAY" in str(js("document.body.innerText") or ""), 15)
    assert js("getComputedStyle(document.querySelector('h1')).color") == "rgb(170, 0, 0)"

    pane._vault_requested = True
    pane.load_url(f"{VAULT_ORIGIN}/watch/{video['id']}")
    assert wait(lambda: js("!!document.querySelector('video')") is True, 15)
    js("const v=document.querySelector('video'); v.muted=true; v.play(); 1")
    assert wait(lambda: (js("document.querySelector('video').currentTime") or 0) > 0.5, 20)
    js("document.querySelector('video').currentTime = 3; 1")
    assert wait(lambda: (js("document.querySelector('video').currentTime") or 0) >= 3, 15)

    # A document the pane did not open is refused the vault.
    pane._url, pane._vault_requested = "https://example.invalid/", False
    assert pane._serve_vault("GET", f"{VAULT_ORIGIN}/", "").status == 403

    services.workspace.lock_layer(rig["layer"].id)
    locked = services.web_archive.respond("GET", f"{VAULT_ORIGIN}/media/{video['id']}")
    assert locked.status in (403, 404)


def test_clearing_traces_keeps_sign_in_cookies(rig: dict[str, Any]) -> None:
    pane, wait = rig["pane"], rig["wait"]
    cleared: list[str] = []
    profile = pane._profile_interface()
    assert profile is not None
    cookies: list[str | None] = []
    pane._controller.webview.call_devtools("Network.getAllCookies", "{}", cookies.append)
    assert wait(lambda: bool(cookies), 10)
    assert "keep-me" in str(cookies[0])

    from app.desktop.webview2 import sdk

    profile.clear_browsing_data(sdk.BROWSING_TRACES, cleared.append)
    assert wait(lambda: bool(cleared), 20)
    assert cleared == [""]

    after: list[str | None] = []
    pane._controller.webview.call_devtools("Network.getAllCookies", "{}", after.append)
    assert wait(lambda: bool(after), 10)
    assert "keep-me" in str(after[0]), "the sign-in cookie must survive the clear"
