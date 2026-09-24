"""The strata:// scheme's security headers reach the engine intact.

`setAdditionalResponseHeaders` takes a QMultiMap. Handing it a bare QByteArray
per header made the binding iterate the value byte by byte: every character
went out as a header of its own, the CSP was unparseable, and Chromium logged
over a thousand "unrecognized directive" warnings while enforcing nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("PySide6.QtWebEngineCore")

from app.desktop import webengine

pytestmark = pytest.mark.security


class _Job:
    def __init__(self) -> None:
        self.headers: dict[Any, Any] = {}

    def setAdditionalResponseHeaders(self, headers: dict[Any, Any]) -> None:
        self.headers = headers


def test_each_header_is_one_whole_value() -> None:
    job = _Job()
    webengine._set_security_headers(job)  # type: ignore[arg-type]
    sent = {bytes(name): [bytes(v) for v in values] for name, values in job.headers.items()}
    assert sent[b"Content-Security-Policy"] == [webengine.CONTENT_SECURITY_POLICY.encode()]
    assert sent[b"X-Content-Type-Options"] == [b"nosniff"]


def test_the_policy_admits_the_apps_own_scheme_and_nothing_remote() -> None:
    policy = webengine.CONTENT_SECURITY_POLICY
    directives = dict(part.strip().split(" ", 1) for part in policy.split(";"))
    for name in ("script-src", "style-src", "connect-src"):
        sources = directives[name].split()
        assert "strata:" in sources
        assert not any(s.startswith(("http:", "https:", "*")) for s in sources)
    assert directives["default-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"


def test_the_meta_copy_matches_the_header() -> None:
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text("utf-8")
    assert f'content="{webengine.CONTENT_SECURITY_POLICY}"' in html
