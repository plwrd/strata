"""Shared harness for the desktop-shell e2e tests.

Builds a real ``MainWindow`` with a real bridge over a temporary workspace, and
gives tests two primitives: ``wait_for_load`` and ``run_js`` (synchronous
``runJavaScript`` that returns the value). Interaction tests dispatch real DOM
events through ``run_js`` so the *actual* React handlers run — the layer that
unit tests cannot reach and where the link-click and tab-switch bugs lived.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-software-rasterizer --in-process-gpu --no-sandbox",
)

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def build_shell(root: Path) -> tuple[Any, Any, Any]:
    """Return ``(app, window, services)`` for a fresh shell over ``root``."""
    from PySide6.QtWidgets import QApplication

    from app.desktop.main_window import MainWindow
    from app.desktop.webengine import register_scheme
    from app.services.container import Paths, Services

    register_scheme()
    app = QApplication.instance() or QApplication([])

    paths = Paths(
        config_dir=root / "config",
        data_dir=root / "data",
        log_dir=root / "data" / "logs",
        default_workspace=root / "workspace",
    )
    services = Services(paths, environment="test")
    window = MainWindow(services, FRONTEND_DIST)
    window.resize(1440, 900)

    outcome: list[bool] = []
    window._view.loadFinished.connect(outcome.append)
    window._loaded = outcome  # type: ignore[attr-defined]
    window.show()
    return app, window, services


def wait_for_load(qtbot: Any, window: Any) -> None:
    qtbot.waitUntil(lambda: bool(window._loaded), timeout=30_000)
    assert window._loaded[0] is True, "the frontend failed to load over strata://"


def run_js(qtbot: Any, window: Any, script: str, timeout: int = 15_000) -> Any:
    result: list[Any] = []
    window._page.runJavaScript(script, 0, result.append)
    qtbot.waitUntil(lambda: bool(result), timeout=timeout)
    return result[0]


def run_async_js(qtbot: Any, window: Any, body: str, timeout: int = 20_000) -> Any:
    """Run JS that resolves a Promise, latch the result on window, return it.

    ``runJavaScript`` does not await promises, so the pattern everywhere is to
    stash the outcome on ``window.__async`` and poll for it. ``body`` must be an
    expression evaluating to a Promise; its resolved value is JSON-encoded.
    """
    window._page.runJavaScript(
        f"""
        window.__async = null;
        Promise.resolve()
          .then(() => {body})
          .then((v) => {{ window.__async = JSON.stringify({{ ok: v ?? true }}); }})
          .catch((e) => {{
            window.__async = JSON.stringify({{ error: String((e && e.message) || e) }});
          }});
        """,
        0,
        lambda _r: None,
    )
    qtbot.waitUntil(lambda: bool(run_js(qtbot, window, "window.__async || ''")), timeout=timeout)
    import json

    return json.loads(run_js(qtbot, window, "window.__async"))


def wait_for_tree(qtbot: Any, window: Any) -> None:
    qtbot.waitUntil(
        lambda: run_js(qtbot, window, "document.querySelectorAll('[role=treeitem]').length") > 0,
        timeout=20_000,
    )
