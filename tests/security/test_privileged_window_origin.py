"""What may be loaded into the window that holds the bridge.

The main window's page carries the WebChannel: whatever origin is loaded there
can call every bridge — read notes, reach an unlocked private layer, change
settings, drive the browser. So "which URL does that window load" is an access
control, and it is the one tested here.

Two ways it could go wrong, both closed:

* an **environment variable** pointing the window at another origin. Setting one
  is not authorisation — anyone who can edit a shortcut or ``HKCU\\Environment``
  can set one — so a packaged build ignores it outright, and a source checkout
  accepts only loopback.
* a **prefix match** on the dev-server URL. ``startswith`` on
  ``http://localhost:5173`` also accepts ``http://localhost:5173.evil.example``.
  The comparison is by origin.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app import bootstrap

pytestmark = pytest.mark.security


@pytest.fixture()
def packaged(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(bootstrap, "is_frozen", lambda: True)
    yield


def test_a_packaged_build_ignores_the_dev_server_variable(
    packaged: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(bootstrap.DEV_SERVER_ENV, "http://attacker.example/")

    assert bootstrap.dev_server() is None


def test_a_packaged_build_is_always_production(
    packaged: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Development mode opens the developer tools onto the bridge page."""
    monkeypatch.setenv(bootstrap.ENV_ENV, "development")

    assert bootstrap.environment() == "production"


@pytest.mark.parametrize(
    "url",
    [
        "http://attacker.example/",
        "https://127.0.0.1.attacker.example/",
        "file:///C:/tmp/index.html",
        "strata://app/index.html",
    ],
)
def test_a_source_checkout_only_accepts_a_loopback_dev_server(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bootstrap, "is_frozen", lambda: False)
    monkeypatch.setenv(bootstrap.DEV_SERVER_ENV, url)

    assert bootstrap.dev_server() is None


def test_the_ordinary_vite_url_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap, "is_frozen", lambda: False)
    monkeypatch.setenv(bootstrap.DEV_SERVER_ENV, "http://localhost:5173")

    assert bootstrap.dev_server() == "http://localhost:5173"


def test_the_dev_server_is_matched_by_origin_not_by_prefix() -> None:
    """A look-alike host that merely *starts with* the dev server URL."""
    from app.desktop.webengine import _origin_of

    allowed = _origin_of("http://localhost:5173")

    assert _origin_of("http://localhost:5173/src/main.tsx") == allowed
    assert _origin_of("http://localhost:5173.attacker.example/") != allowed
    assert _origin_of("http://localhost:51730/") != allowed
    assert _origin_of("https://localhost:5173/") != allowed
