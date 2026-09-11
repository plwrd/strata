"""Why the browser pane needs WebView2 at all, measured rather than asserted.

The claim underneath ``app/desktop/webview2/`` is that the Qt WebEngine in the
PySide6 wheel cannot play H.264 or AAC. That is a property of *someone else's
build*, so it is checked against the engine that is actually installed instead
of being written down once and trusted.

This is a canary in both directions. If Qt ever ships proprietary codecs, this
fails — and that failure is the signal to reconsider carrying a second browser
engine, not something to paper over.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets")

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QApplication

from tests.e2e._shell_support import FRONTEND_DIST  # noqa: F401  (sets the Qt platform)

PROBE = """
(() => {
  const v = document.createElement('video');
  const can = (type) => v.canPlayType(type) || 'no';
  return JSON.stringify({
    h264: can('video/mp4; codecs="avc1.42E01E"'),
    aac: can('audio/mp4; codecs="mp4a.40.2"'),
    vp9: can('video/webm; codecs="vp9"'),
  });
})()
"""

TIMEOUT_MS = 20_000


@pytest.fixture(scope="module")
def codecs() -> dict[str, str]:
    """What the installed Qt WebEngine says it can decode."""
    app = QApplication.instance() or QApplication([])
    page = QWebEnginePage()
    answer: dict[str, str] = {}

    def on_loaded(_ok: bool) -> None:
        page.runJavaScript(PROBE, lambda raw: (answer.update(json.loads(raw)), app.quit()))

    page.loadFinished.connect(on_loaded)
    # A real origin, not about:blank — codec support is not origin-dependent, but
    # an opaque origin has bitten enough media checks to be worth avoiding.
    page.setHtml("<html><body>probe</body></html>", QUrl("https://strata.invalid/"))
    QTimer.singleShot(TIMEOUT_MS, app.quit)
    app.exec()

    if not answer:
        pytest.skip("the engine did not answer the codec probe in time")
    return answer


def test_qt_webengine_cannot_play_h264(codecs: dict[str, str]) -> None:
    """The reason the Chrome backend, and now WebView2, exist at all."""
    assert codecs["h264"] == "no", (
        "Qt WebEngine now decodes H.264 — if that holds, the WebView2 pane may no "
        "longer be worth its dependency. Re-read docs/adr/0012-webview2-browser-pane.md "
        "before changing this test."
    )


def test_qt_webengine_cannot_play_aac(codecs: dict[str, str]) -> None:
    assert codecs["aac"] == "no"


def test_qt_webengine_does_play_the_royalty_free_codecs(codecs: dict[str, str]) -> None:
    """Establishes that the probe works, so the two "no"s above mean something."""
    assert codecs["vp9"] == "probably"
