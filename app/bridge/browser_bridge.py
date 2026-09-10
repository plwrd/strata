"""Browser research: drive the browser, read a page, keep what it said.

The widening this represents is deliberate (THREAT_MODEL §6,
docs/security-and-privacy.md §4). Everything here is refused outright unless
``settings.browser_control_enabled`` is on, and every page that comes back is
stored as an ordinary untrusted capture — the same shape URL import produces,
reaching a model only through the usual policy gate.

Two shapes of method, for two reasons:

* **Navigation is synchronous.** Opening a page is a widget call or one loopback
  request; it returns before the page has loaded, which is what a browser does.
* **Reading is not.** Extraction runs *in* the page, and a page can be slow,
  hostile, or gone. So ``scrape_tab``/``capture_tab`` return a request id and
  deliver on ``pageEvent`` — the read happens on a worker thread, and the editor
  stays responsive while it does.

Reading is also split from capturing on purpose: ``scrape_tab`` shows you the
text, ``capture_tab`` is what writes a note. A user who reads the wrong page
sees it before anything lands in their workspace. The write itself is marshalled
back onto the Qt thread, so no worker thread ever touches the note store.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Qt, Signal, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.browser import SEARCH_URLS, BrowserStatus, BrowserTab, ScrapedPage
from app.domain.digest import DigestMode
from app.domain.errors import StrataError
from app.domain.ids import new_request_id
from app.infrastructure.logging.logger import get_logger
from app.services.container import Services

logger = get_logger(__name__)

MAX_CAPTURE_TITLE = 200


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    engine: str = Field(default="", max_length=32)


class OpenUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


class BlurRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(default="", max_length=256)
    # Everything below is capture-only; `scrape_tab` never writes and ignores it.
    layer_id: str = Field(default="", max_length=128)
    capture_reason: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=20)
    # "full" keeps the page verbatim; "brief"/"outline" run a model first and
    # keep only the digest — the page itself is never saved.
    mode: DigestMode = "full"
    instruction: str = Field(default="", max_length=500)
    provider_id: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=128)
    confirmed_remote: bool = False


class StatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: BrowserStatus
    engines: list[str] = Field(default_factory=list)


class TabResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tab: BrowserTab


class TabListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tabs: list[BrowserTab] = Field(default_factory=list)


class ReadStartedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str


@dataclass
class _PendingRead:
    """One in-flight read. Written by its worker, read on the Qt thread."""

    target_id: str
    capture: bool
    layer_id: str = ""
    capture_reason: str = ""
    tags: list[str] = field(default_factory=list)
    mode: DigestMode = "full"
    instruction: str = ""
    provider_id: str = ""
    model: str = ""
    confirmed_remote: bool = False
    page: ScrapedPage | None = None
    # A digest replaces the page text before it is saved; these carry it from the
    # worker thread (where the model runs) to the Qt thread (where the note is
    # written). None means "save the page as-is".
    content_override: str | None = None
    extra_properties: dict[str, str] = field(default_factory=dict)
    error: str = ""


class BrowserBridge(QObject):
    """The controlled browser, exposed to the frontend.

    ``pageEvent`` is the only push channel: it carries a page's text — untrusted
    data the frontend renders as text — and never anything from a layer.
    """

    pageEvent = Signal(str)
    # Pushed whenever blur changes by *any* path — including the application
    # hotkey, which the panel never sees — so a "Blur media" toggle can stay in
    # step with the real state. Carries no page content, only on/off + radius.
    blurEvent = Signal(str)
    # Internal hop from the reading thread back to the Qt thread, so the capture
    # (a filesystem write) happens where every other write happens.
    _readFinished = Signal(str)

    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._pending: dict[str, _PendingRead] = {}
        self._readFinished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._services.browser.on_blur_changed = self._emit_blur

    # -- state ---------------------------------------------------------------

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def get_status(self, _request: EmptyRequest) -> StatusResponse:
        """Answers even when the feature is off — the UI needs to say *why*."""
        return StatusResponse(
            status=self._services.browser.status(),
            engines=sorted(SEARCH_URLS),
        )

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def launch(self, _request: EmptyRequest) -> StatusResponse:
        """Open the pane, or start Chrome — whichever backend is configured."""
        return StatusResponse(
            status=self._services.browser.launch(),
            engines=sorted(SEARCH_URLS),
        )

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def close_browser(self, _request: EmptyRequest) -> StatusResponse:
        """Close the pane, or stop the Chrome Strata started."""
        self._services.browser.close()
        return StatusResponse(
            status=self._services.browser.status(),
            engines=sorted(SEARCH_URLS),
        )

    @Slot(str, result=str)
    @bridge_method(BlurRequest)
    def set_blur(self, request: BlurRequest) -> StatusResponse:
        """Blur (or unblur) images, video and canvas in the pane."""
        self._services.browser.set_blur(request.enabled)
        return StatusResponse(
            status=self._services.browser.status(),
            engines=sorted(SEARCH_URLS),
        )

    def _emit_blur(self) -> None:
        enabled, amount = self._services.browser.blur_state()
        self.blurEvent.emit(
            json.dumps(
                {
                    "enabled": enabled,
                    "amount": amount,
                    "supported": self._services.browser.blur_supported,
                }
            )
        )

    # -- navigation ----------------------------------------------------------

    @Slot(str, result=str)
    @bridge_method(SearchRequest)
    def search(self, request: SearchRequest) -> TabResponse:
        """Search is navigation: the query becomes a URL the browser goes to.

        Strata makes no request of its own, so no search API is involved — the
        engine sees an ordinary browser session."""
        return TabResponse(tab=self._services.browser.search(request.query, request.engine))

    @Slot(str, result=str)
    @bridge_method(OpenUrlRequest)
    def open_url(self, request: OpenUrlRequest) -> TabResponse:
        return TabResponse(tab=self._services.browser.open_url(request.url))

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def list_tabs(self, _request: EmptyRequest) -> TabListResponse:
        return TabListResponse(tabs=self._services.browser.tabs())

    # -- reading -------------------------------------------------------------

    @Slot(str, result=str)
    @bridge_method(ReadRequest)
    def scrape_tab(self, request: ReadRequest) -> ReadStartedResponse:
        """Read a page without writing anything. Look before you file."""
        return self._start(request, capture=False)

    @Slot(str, result=str)
    @bridge_method(ReadRequest)
    def capture_tab(self, request: ReadRequest) -> ReadStartedResponse:
        """Read a page and file it as a raw capture in the chosen layer."""
        return self._start(request, capture=True)

    def _start(self, request: ReadRequest, *, capture: bool) -> ReadStartedResponse:
        # The gate is enforced here, on the calling thread, so a disabled feature
        # is a plain refusal rather than an event nobody is listening for yet.
        self._services.browser.require_enabled()
        request_id = new_request_id()
        self._pending[request_id] = _PendingRead(
            target_id=request.target_id,
            capture=capture,
            layer_id=request.layer_id,
            capture_reason=request.capture_reason,
            tags=request.tags,
            mode=request.mode if capture else "full",
            instruction=request.instruction,
            provider_id=request.provider_id,
            model=request.model,
            confirmed_remote=request.confirmed_remote,
        )
        thread = threading.Thread(target=self._read, args=(request_id,), daemon=True)
        thread.start()
        return ReadStartedResponse(request_id=request_id)

    def _read(self, request_id: str) -> None:
        """On a worker thread: extraction takes as long as the page takes."""
        pending = self._pending.get(request_id)
        if pending is None:
            return
        try:
            pending.page = self._services.browser.read_page(pending.target_id)
            if pending.capture and pending.mode != "full":
                self._digest(pending)
        except StrataError as exc:
            pending.error = exc.message
        except Exception:  # a bug here must not strand the caller in silence
            logger.info("browser.read_failed")
            pending.error = "The page could not be read."
        self._readFinished.emit(request_id)

    def _digest(self, pending: _PendingRead) -> None:
        """Condense the page into a brief. Worker thread only — it calls a model."""
        page = pending.page
        if page is None:
            return
        digest, execution_id = self._services.digest.digest_sync(
            text=page.text,
            url=page.url,
            title=page.title,
            mode=pending.mode,
            instruction=pending.instruction,
            provider_id=pending.provider_id or "ollama",
            model=pending.model or "default",
            confirmed_remote=pending.confirmed_remote,
        )
        content = self._services.digest.render(
            digest, mode=pending.mode, title=page.title, url=page.url
        )
        pending.content_override = content
        pending.extra_properties = {
            "review_status": "ai-inferred",
            "generated_by": execution_id,
            "digest_mode": pending.mode,
            "processing_status": "processed",
        }
        pending.tags = list(
            dict.fromkeys([*pending.tags, *self._services.digest.clean_tags(digest)])
        )
        # Show the user what was actually kept, not the discarded page.
        pending.page = page.model_copy(
            update={"text": content, "char_count": len(content), "truncated": False}
        )

    @Slot(str)
    def _deliver(self, request_id: str) -> None:
        """Back on the Qt thread: capture if asked, then tell the frontend."""
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        if pending.error or pending.page is None:
            self._emit(request_id, {"kind": "error", "error": pending.error or "The read failed."})
            return

        page = pending.page
        if not pending.capture:
            self._emit(request_id, {"kind": "page", "page": page.model_dump()})
            return

        try:
            note = self._services.capture.capture(
                content=pending.content_override or page.text,
                title=page.title[:MAX_CAPTURE_TITLE],
                layer_id=pending.layer_id,
                source_url=page.url,
                capture_reason=pending.capture_reason,
                tags=pending.tags or None,
                extra_properties=pending.extra_properties or None,
            )
        except StrataError as exc:
            self._emit(request_id, {"kind": "error", "error": exc.message})
            return

        self._services.watcher.announce("strata")
        self._emit(
            request_id,
            {
                "kind": "page",
                "page": page.model_copy(update={"note_id": note.metadata.id}).model_dump(),
                "note": note.model_dump(),
            },
        )

    def _emit(self, request_id: str, payload: dict[str, object]) -> None:
        self.pageEvent.emit(json.dumps({"requestId": request_id, **payload}))
