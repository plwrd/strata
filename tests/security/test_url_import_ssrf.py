"""Security: URL import must never become a proxy into the local network.

The guard runs before any request is made, so every case here fails without a
single byte leaving the process — there is no transport mock because nothing
must reach the transport.
"""

from __future__ import annotations

import pytest

from app.domain.errors import InvalidRequestError, PermissionDeniedError
from app.services.container import Services

pytestmark = pytest.mark.security

FORBIDDEN_URLS = [
    "http://127.0.0.1/admin",
    "http://localhost:8787/relay",
    "https://[::1]/",
    "http://10.0.0.5/internal",
    "http://172.16.4.1/router",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
    "http://0.0.0.0/",
]


@pytest.mark.parametrize("url", FORBIDDEN_URLS)
def test_private_and_reserved_addresses_are_refused(workspace: Services, url: str) -> None:
    with pytest.raises(PermissionDeniedError):
        workspace.capture.import_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.org/file",
        "gopher://example.org/",
        "javascript:alert(1)",
    ],
)
def test_non_http_schemes_are_refused(workspace: Services, url: str) -> None:
    with pytest.raises(PermissionDeniedError):
        workspace.capture.import_url(url)


def test_embedded_credentials_are_refused(workspace: Services) -> None:
    with pytest.raises(PermissionDeniedError):
        workspace.capture.import_url("http://user:secret@93.184.216.34/")


def test_the_refusal_is_generic(workspace: Services) -> None:
    """The error must not confirm what exists at the refused address."""
    with pytest.raises(PermissionDeniedError) as denied:
        workspace.capture.import_url("http://169.254.169.254/latest/meta-data/")
    assert "169.254" not in denied.value.message


def test_garbage_urls_fail_closed(workspace: Services) -> None:
    with pytest.raises((InvalidRequestError, PermissionDeniedError)):
        workspace.capture.import_url("http:///nothing")
