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
    # The app connected its own channel; tests reuse it (see the health test).
    assert _run_js(qtbot, window, "!!window.__strataBridge") is True


def test_a_health_call_round_trips_from_javascript_into_python(qtbot: Any, shell: Any) -> None:
    """The Milestone 1 React-to-Python health check, executed in the real page."""
    _app, window, _services = shell
    _wait_for_load(qtbot, window)

    # Deliberately reuse the page's own channel rather than constructing a second
    # QWebChannel over the same transport. Two channels sharing one transport both
    # receive every reply and route it through their own callback table, so the
    # second one throws "execCallbacks[message.id] is not a function" and corrupts
    # the first one's in-flight calls. `runJavaScript` also does not await
    # promises, so the reply is latched on `window` and polled for.
    window._page.runJavaScript(
        """
        window.__health = null;
        window.__strataBridge.workspace.health()
          .then((data) => { window.__health = JSON.stringify(data); })
          .catch((e) => { window.__health = JSON.stringify({ error: e.message }); });
        """,
        0,
        lambda _result: None,
    )

    qtbot.waitUntil(
        lambda: bool(_run_js(qtbot, window, "window.__health || ''")),
        timeout=20_000,
    )
    response = json.loads(_run_js(qtbot, window, "window.__health"))

    assert response.get("ok") is True
    assert response["app"] == "strata"
    assert response["protocol_version"] == 1
    assert response["python_version"].startswith("3.")


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


def test_the_editor_loads_a_real_note_and_saves_it_back_to_disk(
    qtbot: Any, shell: Any
) -> None:
    """Milestone 2, end to end: CodeMirror mounts, and a save reaches the file.

    This is the test that would have caught a CodeMirror that fails to construct
    under the CSP, or an autosave that never reaches Python.
    """
    _app, window, services = shell
    _wait_for_load(qtbot, window)

    qtbot.waitUntil(
        lambda: _run_js(qtbot, window, "document.querySelectorAll('[role=treeitem]').length") > 0,
        timeout=20_000,
    )

    # Open the first note through the app's own store, exactly as a click would.
    note_id = next(
        note.metadata.id
        for note in services.notes.list_notes()
        if note.metadata.title == "Encryption Architecture"
    )
    window._page.runJavaScript(
        f"""
        window.__saved = null;
        const store = window.__strataStore;
        store.getState().openNoteById({note_id!r}).then(() => {{
          store.getState().setMode('focus');
          return store.getState().saveNote({note_id!r}, '# Rewritten by the e2e test\\n');
        }}).then(() => {{ window.__saved = 'ok'; }})
          .catch((e) => {{ window.__saved = 'error: ' + e.message; }});
        """,
        0,
        lambda _result: None,
    )

    qtbot.waitUntil(
        lambda: bool(_run_js(qtbot, window, "window.__saved || ''")),
        timeout=20_000,
    )
    assert _run_js(qtbot, window, "window.__saved") == "ok"

    # The claim under test: the bytes are on disk.
    reread = services.notes.get_note(note_id)
    assert reread.content.strip() == "# Rewritten by the e2e test"

    # And CodeMirror actually mounted — the editor is not a textarea stub.
    qtbot.waitUntil(
        lambda: _run_js(qtbot, window, "!!document.querySelector('.cm-editor')") is True,
        timeout=20_000,
    )


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
