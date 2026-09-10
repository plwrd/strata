"""The browser pane's media-blur injection.

The pane blurs media by setting an inline ``filter`` with ``!important`` through
the CSSOM, not by injecting a ``<style>``. That choice is the fix for two real
failures on live sites (x.com): a site's own ``!important`` rule out-specifies a
bare ``img`` stylesheet rule, and a strict ``style-src`` CSP blocks an injected
``<style>`` outright — neither defeats an inline programmatic write.

These check the generated script without opening a window (the helpers are
pure): the radius is clamped, it cannot break out of the script string, the
selector reaches the elements sites actually use for avatars and media, and the
script carries the specificity/CSP/flicker mitigations.
"""

from __future__ import annotations

import pytest

# The pane module imports PySide6 at load; skip cleanly where Qt is absent.
pytest.importorskip("PySide6.QtWebEngineCore")

from app.desktop.browser_pane import _BLUR_SELECTOR, _blur_source


def test_off_disables_the_injection() -> None:
    source = _blur_source(False, 16)
    assert "const ON = false" in source


def test_on_sets_an_inline_important_filter() -> None:
    source = _blur_source(True, 16)
    assert "const ON = true" in source
    # Inline !important via the CSSOM — beats a site's own !important rule, and is
    # not subject to the page's style-src CSP the way an injected <style> is.
    assert 'setProperty("filter", value, "important")' in source
    assert "RADIUS = 16" in source


def test_the_selector_covers_what_sites_really_use() -> None:
    # Avatars and thumbnails are often background-image <div>s, not <img>; photos
    # and players are <picture>/<iframe>. The old img-only rule missed them.
    for needed in ("img", "video", "canvas", "picture", "iframe", "background-image"):
        assert needed in _BLUR_SELECTOR
    # Bare <svg> stays out, so the page's icons are not all fuzzed.
    assert "svg" not in _BLUR_SELECTOR


def test_video_is_promoted_to_its_own_layer() -> None:
    # A blurred <video> flickers against the GPU overlay; translateZ(0) settles it.
    source = _blur_source(True, 16)
    assert "translateZ(0)" in source
    assert 'el.tagName === "VIDEO"' in source


def test_dynamic_media_is_caught_after_load() -> None:
    # Single-page apps add media nodes after first paint; an observer re-applies.
    assert "MutationObserver" in _blur_source(True, 16)


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(-5, 1), (0, 1), (1, 1), (16, 16), (100, 100), (9999, 100)],
)
def test_the_radius_is_clamped(amount: int, expected: int) -> None:
    assert f"RADIUS = {expected}" in _blur_source(True, amount)


def test_the_amount_cannot_break_out_of_the_injected_script() -> None:
    """A hostile radius is coerced through int(); it cannot inject CSS or JS."""
    with pytest.raises((ValueError, TypeError)):
        _blur_source(True, "16px; } evil()")  # type: ignore[arg-type]


def test_the_selector_is_embedded_as_a_json_string() -> None:
    source = _blur_source(True, 16)
    # The selector is a JSON string literal, not spliced in raw.
    assert "\"img,video,canvas,picture,iframe,[style*='background-image']\"" in source
