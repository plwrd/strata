"""The desktop shell, for real: Qt WebEngine loads the bundled frontend over the
``strata://`` scheme and completes a WebChannel round trip into Python.

This is the test that proves the Milestone 1 claim "the packaged frontend loads
correctly". It is skipped (not silently passed) when the bundle has not been
built, because a green test on a missing bundle would be a lie.

Runs offscreen, so it works in CI with no display.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-software-rasterizer --in-process-gpu --no-sandbox",
)

pytestmark = [pytest.mark.gui, pytest.mark.slow]

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

pytest.importorskip("PySide6.QtWebEngineWidgets")

if not (FRONTEND_DIST / "index.html").is_file():  # pragma: no cover - build gate
    pytest.skip(
        "frontend/dist is missing — run `npm --prefix frontend run build` first",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def shell(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A real MainWindow with a real bridge, pointed at a temporary workspace."""
    from PySide6.QtWidgets import QApplication

    from app.desktop.webengine import register_scheme

    register_scheme()

    app = QApplication.instance() or QApplication([])

    root = tmp_path_factory.mktemp("e2e")
    from app.desktop.main_window import MainWindow
    from app.services.container import Paths, Services

    paths = Paths(
        config_dir=root / "config",
        data_dir=root / "data",
        log_dir=root / "data" / "logs",
        default_workspace=root / "workspace",
    )
    services = Services(paths, environment="test")
    window = MainWindow(services, FRONTEND_DIST)
    window.resize(1440, 900)

    # `loadFinished` fires once for the module-scoped window, so the outcome is
    # latched here rather than awaited in each test.
    outcome: list[bool] = []
    window._view.loadFinished.connect(outcome.append)
    window._loaded = outcome  # type: ignore[attr-defined]
    window.show()

    yield app, window, services

    window.close()


def _wait_for_load(qtbot: Any, window: Any) -> None:
    qtbot.waitUntil(lambda: bool(window._loaded), timeout=30_000)
    assert window._loaded[0] is True, "the frontend failed to load over strata://"


def _run_js(qtbot: Any, window: Any, script: str, timeout: int = 15_000) -> Any:
    result: list[Any] = []
    window._page.runJavaScript(script, 0, result.append)
    qtbot.waitUntil(lambda: bool(result), timeout=timeout)
    return result[0]


def test_the_bundled_frontend_loads_over_the_custom_scheme(qtbot: Any, shell: Any) -> None:
    _app, window, _services = shell
    _wait_for_load(qtbot, window)

    assert window._page.url().scheme() == "strata"
    assert _run_js(qtbot, window, "document.title") == "Strata"
    assert _run_js(qtbot, window, "!!document.getElementById('root')") is True


def test_the_webchannel_transport_is_present(qtbot: Any, shell: Any) -> None:
    _app, window, _services = shell
    _wait_for_load(qtbot, window)

    # qwebchannel.js is served from our own origin under `script-src 'self'`.
    assert _run_js(qtbot, window, "typeof window.QWebChannel") == "function"
    assert _run_js(qtbot, window, "!!window.qt && !!window.qt.webChannelTransport") is True


def test_a_health_call_round_trips_from_javascript_into_python(qtbot: Any, shell: Any) -> None:
    """The Milestone 1 React-to-Python health check, executed in the real page."""
    _app, window, _services = shell
    _wait_for_load(qtbot, window)

    # `runJavaScript` does not await promises, so the reply is latched on `window`
    # and polled for.
    window._page.runJavaScript(
        """
        window.__health = null;
        new QWebChannel(qt.webChannelTransport, (channel) => {
          channel.objects.workspace.health(
            JSON.stringify({ v: 1, requestId: 'req_e2e', payload: {} }),
            (reply) => { window.__health = reply; },
          );
        });
        """,
        0,
        lambda _result: None,
    )

    qtbot.waitUntil(
        lambda: bool(_run_js(qtbot, window, "window.__health || ''")),
        timeout=20_000,
    )
    raw = _run_js(qtbot, window, "window.__health")
    response = json.loads(raw)

    assert response["ok"] is True
    assert response["requestId"] == "req_e2e"
    assert response["data"]["app"] == "strata"
    assert response["data"]["protocol_version"] == 1


def test_the_app_renders_the_shell_and_the_selection_surface(qtbot: Any, shell: Any) -> None:
    """React mounted, the store initialised, and the real seeded graph rendered."""
    _app, window, _services = shell
    _wait_for_load(qtbot, window)

    qtbot.waitUntil(
        lambda: _run_js(qtbot, window, "!!document.querySelector('.shell')") is True,
        timeout=20_000,
    )

    assert _run_js(qtbot, window, "!!document.querySelector('.commandbar')") is True
    assert _run_js(qtbot, window, "!!document.querySelector('.statusbar')") is True

    # The accessible graph must exist, and it must be populated from the workspace
    # that Python actually created on disk.
    qtbot.waitUntil(
        lambda: _run_js(qtbot, window, "document.querySelectorAll('[role=treeitem]').length") > 0,
        timeout=20_000,
    )
    labels = _run_js(
        qtbot,
        window,
        "Array.from(document.querySelectorAll('.graph-list__label'))"
        ".map(n => n.textContent).join('|')",
    )
    assert "Encryption Architecture" in labels


@pytest.mark.security()
def test_external_navigation_is_blocked(qtbot: Any, shell: Any) -> None:
    _app, window, _services = shell
    _wait_for_load(qtbot, window)

    page = window._page
    from PySide6.QtCore import QUrl

    allowed = page.acceptNavigationRequest(
        QUrl("strata://app/index.html"),
        page.NavigationType.NavigationTypeTyped,
        True,
    )
    blocked = page.acceptNavigationRequest(
        QUrl("file:///C:/Windows/System32/drivers/etc/hosts"),
        page.NavigationType.NavigationTypeTyped,
        True,
    )

    assert allowed is True
    assert blocked is False


@pytest.mark.security()
def test_the_scheme_handler_refuses_to_escape_the_bundle(qtbot: Any, shell: Any) -> None:
    """`strata://app/../../workspace.json` must not resolve to anything."""
    _app, window, _services = shell
    _wait_for_load(qtbot, window)

    window._page.runJavaScript(
        """
        window.__escape = null;
        fetch('strata://app/../../../pyproject.toml')
          .then((r) => r.text())
          .then((t) => { window.__escape = 'served:' + t.slice(0, 40); })
          .catch(() => { window.__escape = 'blocked'; });
        """,
        0,
        lambda _result: None,
    )

    qtbot.waitUntil(
        lambda: bool(_run_js(qtbot, window, "window.__escape || ''")),
        timeout=20_000,
    )
    result = str(_run_js(qtbot, window, "window.__escape"))

    # Either the CSP blocks the fetch or the handler refuses the path. Both are
    # acceptable; serving pyproject.toml to the page is not.
    assert "[project]" not in result
    assert "strata" not in result.replace("blocked", "")


@pytest.mark.security()
def test_the_page_cannot_reach_the_network(qtbot: Any, shell: Any) -> None:
    """`connect-src 'self'` — the frontend has no route to a remote model.

    Every outbound request must go through Python, where the per-layer AI policy
    is enforced. A page that can `fetch()` an API endpoint routes around that.
    """
    _app, window, _services = shell
    _wait_for_load(qtbot, window)

    window._page.runJavaScript(
        """
        window.__net = null;
        fetch('https://api.openai.com/v1/models')
          .then(() => { window.__net = 'allowed'; })
          .catch(() => { window.__net = 'blocked'; });
        """,
        0,
        lambda _result: None,
    )

    qtbot.waitUntil(lambda: bool(_run_js(qtbot, window, "window.__net || ''")), timeout=20_000)

    assert _run_js(qtbot, window, "window.__net") == "blocked"
