"""Universal capture — the fast path into the Inbox, and the guarded URL import.

Network fetches are mocked at the transport (respx); the SSRF guard is tested
against address literals so no test ever resolves or touches a real host.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.errors import InvalidRequestError, PermissionDeniedError
from app.services.capture_service import _html_title, _html_to_text
from app.services.container import Services

# A public, non-private address literal: passes the guard, intercepted by respx.
PUBLIC_URL = "http://93.184.216.34/article"


def test_capture_lands_raw_in_the_inbox(workspace: Services) -> None:
    note = workspace.capture.capture(
        content="An idea worth keeping.",
        title="An idea",
        capture_reason="might matter for the launch",
        tags=["ideas"],
    )

    assert note.metadata.folder_path == "Inbox"
    assert note.metadata.properties["type"] == "capture"
    assert note.metadata.properties["processing_status"] == "raw"
    assert note.metadata.properties["capture_reason"] == "might matter for the launch"
    assert "captured_at" in note.metadata.properties
    assert "ideas" in note.metadata.tags


def test_capture_derives_a_title_from_the_first_line(workspace: Services) -> None:
    note = workspace.capture.capture(content="# Meeting with the platform team\n\nNotes…")

    assert note.metadata.title == "Meeting with the platform team"


def test_empty_capture_is_refused(workspace: Services) -> None:
    with pytest.raises(InvalidRequestError):
        workspace.capture.capture(content="   ")


def test_new_workspaces_are_seeded_with_the_knowledge_areas(empty_workspace: Services) -> None:
    folders = {folder.name for folder in empty_workspace.notes.list_folders()}
    assert {"Inbox", "Knowledge", "Reports", "Templates"} <= folders


# -- URL import -----------------------------------------------------------------


@respx.mock
def test_url_import_creates_a_capture_with_source_metadata(workspace: Services) -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><head><title>How memory works</title>"
                "<script>alert('never stored')</script></head>"
                "<body><p>Neurons keep the state.</p></body></html>"
            ),
        )
    )

    note = workspace.capture.import_url(PUBLIC_URL, capture_reason="research")

    assert note.metadata.title == "How memory works"
    assert note.metadata.properties["source_url"] == PUBLIC_URL
    assert note.metadata.properties["processing_status"] == "raw"
    assert "Neurons keep the state." in note.content
    assert "alert(" not in note.content  # scripts are stripped, never stored


@respx.mock
def test_redirects_are_refused_not_followed(workspace: Services) -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
    )

    with pytest.raises(InvalidRequestError, match="redirects"):
        workspace.capture.import_url(PUBLIC_URL)


@respx.mock
def test_non_text_content_is_refused(workspace: Services) -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b"\x00\x01"
        )
    )

    with pytest.raises(InvalidRequestError, match="text pages"):
        workspace.capture.import_url(PUBLIC_URL)


def test_url_import_can_be_disabled(workspace: Services) -> None:
    workspace.settings.update({"url_import_enabled": False})

    with pytest.raises(PermissionDeniedError, match="disabled"):
        workspace.capture.import_url(PUBLIC_URL)


# -- HTML helpers ---------------------------------------------------------------


def test_html_to_text_survives_hostile_markup() -> None:
    hostile = "<p>ok</p><div" + "<" * 500
    assert "ok" in _html_to_text(hostile)


def test_html_title_is_bounded() -> None:
    assert _html_title("<title>" + "x" * 500 + "</title>") == "x" * 120
