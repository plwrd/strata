"""Universal capture: get material into the workspace fast, organise later.

A capture is an ordinary note in the ``Inbox/`` folder with the ``capture``
schema — `processing_status: raw` until the user processes it. Nothing here is
a new storage model; a capture is legible Markdown like everything else.

URL import is the one place this service touches the network, and it treats
that as the security event it is (docs/security-and-privacy.md §3):

- ``https``/``http`` only — no ``file://``, no custom schemes.
- Every resolved address is checked against private/reserved ranges *before*
  the request. DNS rebinding after the check remains a documented residual
  risk; redirects are refused outright so a public page cannot bounce the
  fetch somewhere the check never saw.
- Size and time are capped; content is stored as untrusted text, never
  rendered as instructions, never executed.
- The whole feature sits behind ``settings.url_import_enabled``.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from app.domain.errors import InvalidRequestError, PermissionDeniedError
from app.domain.note import Note
from app.domain.schema import INBOX_FOLDER
from app.infrastructure.logging.logger import get_logger
from app.services.note_service import NoteService
from app.services.settings_service import SettingsService
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)

MAX_CAPTURE_CHARS = 512_000
MAX_URL_BYTES = 2 * 1024 * 1024
URL_TIMEOUT_SECONDS = 20.0
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class CaptureService:
    def __init__(
        self,
        workspace: WorkspaceService,
        notes: NoteService,
        settings: SettingsService,
    ) -> None:
        self._workspace = workspace
        self._notes = notes
        self._settings = settings

    # -- quick capture -------------------------------------------------------

    def capture(
        self,
        *,
        content: str,
        title: str = "",
        layer_id: str = "",
        source_url: str = "",
        source_author: str = "",
        capture_reason: str = "",
        tags: list[str] | None = None,
    ) -> Note:
        """Create a raw capture in the layer's Inbox. Fast path, no questions."""
        text = content.strip()
        if not text and not title.strip():
            raise InvalidRequestError("There is nothing to capture.")
        if len(text) > MAX_CAPTURE_CHARS:
            raise InvalidRequestError("This capture is too large. Import it as a file instead.")

        target_layer = self._resolve_layer(layer_id)
        properties: dict[str, object] = {
            "type": "capture",
            "processing_status": "raw",
            "captured_at": _now(),
        }
        if source_url:
            properties["source_url"] = source_url
        if source_author:
            properties["source_author"] = source_author
        if capture_reason:
            properties["capture_reason"] = capture_reason
        if tags:
            properties["tags"] = [tag.strip() for tag in tags if tag.strip()][:20]

        note = self._notes.create_note(
            layer_id=target_layer,
            folder_path=INBOX_FOLDER,
            title=self._title_for(title, text),
            content=text,
            properties=properties,
        )
        logger.info("capture.created", layer_id=target_layer)
        return note

    def _resolve_layer(self, layer_id: str) -> str:
        if layer_id:
            self._workspace.require_readable_layer(layer_id)
            return layer_id
        first_public = next(
            (
                layer.id
                for layer in self._workspace.readable_layers()
                if layer.storage == "markdown"
            ),
            None,
        )
        if first_public is None:
            raise InvalidRequestError("No writable public layer is available for capture.")
        return first_public

    def _title_for(self, title: str, text: str) -> str:
        cleaned = title.strip()
        if cleaned:
            return cleaned[:120]
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        first_line = first_line.lstrip("#").strip()
        if len(first_line) >= 3:
            return first_line[:120]
        return f"Capture {_now().replace(':', '-')}"

    # -- URL import ----------------------------------------------------------

    def import_url(
        self,
        url: str,
        *,
        layer_id: str = "",
        capture_reason: str = "",
    ) -> Note:
        """Fetch a page and store it as a raw capture. The page is data — its
        text is kept, its markup and scripts are not, and nothing in it is ever
        treated as an instruction."""
        if not self._settings.settings.url_import_enabled:
            raise PermissionDeniedError("URL import is disabled in this workspace's settings.")
        cleaned = url.strip()
        self._guard_url(cleaned)

        try:
            with httpx.Client(
                timeout=httpx.Timeout(URL_TIMEOUT_SECONDS, connect=5.0),
                follow_redirects=False,  # a redirect is a fetch the guard never saw
                headers={"User-Agent": "Strata/URL-import"},
            ) as client:
                response = client.get(cleaned)
        except httpx.HTTPError:
            raise InvalidRequestError("The page could not be fetched.") from None

        if response.status_code in (301, 302, 303, 307, 308):
            raise InvalidRequestError(
                "The page redirects elsewhere. Follow the redirect yourself and "
                "import the final URL."
            )
        if response.status_code != 200:
            raise InvalidRequestError(f"The server answered {response.status_code}.")

        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and not (
            content_type.startswith("text/") or content_type == "application/xhtml+xml"
        ):
            raise InvalidRequestError("Only text pages can be imported as captures.")
        if len(response.content) > MAX_URL_BYTES:
            raise InvalidRequestError("The page is too large to import.")

        body = response.text
        text = _html_to_text(body) if "html" in content_type or "<html" in body[:2048] else body
        title = _html_title(body) or urlsplit(cleaned).netloc

        return self.capture(
            content=text[:MAX_CAPTURE_CHARS],
            title=title,
            layer_id=layer_id,
            source_url=cleaned,
            capture_reason=capture_reason,
        )

    def _guard_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme.lower() not in _ALLOWED_SCHEMES:
            raise PermissionDeniedError("Only http and https URLs can be imported.")
        host = parts.hostname or ""
        if not host:
            raise InvalidRequestError("That is not a valid URL.")
        if parts.username or parts.password:
            raise PermissionDeniedError("URLs with embedded credentials are not imported.")

        for address in self._resolve(host):
            if self._is_forbidden(address):
                # One generic message: the guard does not confirm what exists
                # on the network it just refused to touch.
                raise PermissionDeniedError("This address is not reachable from URL import.")

    @staticmethod
    def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            literal = ipaddress.ip_address(host)
            return [literal]
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except OSError:
            raise InvalidRequestError("The host could not be resolved.") from None
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(str(info[4][0])))
            except ValueError:
                continue
        if not addresses:
            raise InvalidRequestError("The host could not be resolved.")
        return addresses

    @staticmethod
    def _is_forbidden(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )


class _TextExtractor(HTMLParser):
    """Markup → text. No rendering, no scripts, no external fetches."""

    _SKIP = frozenset({"script", "style", "noscript", "template", "head", "svg"})
    _BREAK = frozenset({"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:  # hostile markup must degrade, not crash
        return re.sub(r"<[^>]+>", " ", html)
    text = "".join(extractor.parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _html_title(html: str) -> str:
    match = _TITLE.search(html[:8192])
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:120]
