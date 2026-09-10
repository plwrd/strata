"""The browser pane's media-blur stylesheet.

The pane injects CSS into arbitrary web pages, so two things have to hold no
matter what: the radius is clamped to something sane, and it cannot break out of
the JavaScript string it is embedded in — a page must never be able to smuggle
CSS or script through the blur amount. Both are checked here without opening a
window (the helpers are pure); the wiring that calls them is covered by the
service tests.
"""

from __future__ import annotations

import pytest

# The pane module imports PySide6 at load; skip cleanly where Qt is absent.
pytest.importorskip("PySide6.QtWebEngineCore")

from app.desktop.browser_pane import _BLUR_SELECTOR, _blur_css, _blur_source


def test_off_produces_no_rules() -> None:
    assert _blur_css(False, 16) == ""


def test_on_blurs_only_media() -> None:
    css = _blur_css(True, 16)
    assert css == f"{_BLUR_SELECTOR} {{ filter: blur(16px) !important; }}"
    # Text elements are deliberately absent — the point is to keep reading.
    for selector in ("p", "div", "body", "span"):
        assert f"{selector} " not in css


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(-5, 1), (0, 1), (1, 1), (16, 16), (100, 100), (9999, 100)],
)
def test_the_radius_is_clamped(amount: int, expected: int) -> None:
    assert f"blur({expected}px)" in _blur_css(True, amount)


def test_the_amount_cannot_break_out_of_the_injected_string() -> None:
    """A hostile radius is coerced through int(); it cannot inject CSS or JS."""
    with pytest.raises((ValueError, TypeError)):
        _blur_source(True, "16px; } body { display: none }")  # type: ignore[arg-type]


def test_the_source_json_escapes_the_stylesheet() -> None:
    source = _blur_source(True, 16)
    # The CSS is embedded as a JSON string literal, not spliced in raw.
    assert '"img, video, canvas { filter: blur(16px) !important; }"' in source
    assert "img, video, canvas" in source
