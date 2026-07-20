"""e2e fixtures. The ``shell`` fixture here backs the interaction tests; the
older desktop-shell test defines its own identically-shaped one, which takes
precedence within that module (pytest fixture override), so this does not change
its behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e._shell_support import FRONTEND_DIST, build_shell

pytest.importorskip("PySide6.QtWebEngineWidgets")

if not (FRONTEND_DIST / "index.html").is_file():  # pragma: no cover - build gate
    pytest.skip(
        "frontend/dist is missing — run `npm --prefix frontend run build` first",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def shell(tmp_path_factory: pytest.TempPathFactory) -> Any:
    root = tmp_path_factory.mktemp("e2e")
    app, window, services = build_shell(root)
    yield app, window, services
    window.close()
