"""Models for the controlled browser.

Two ways to put a page in front of you, one set of models:

* **embedded** — a browser pane inside the Strata window (Qt WebEngine, its own
  persistent profile). The default. No loopback port, no second process, and
  sign-ins survive restarts. It cannot load Chrome extensions: Qt ships
  Chromium without the extensions subsystem, and no setting changes that. It
  also cannot play H.264 or AAC — that Qt build carries no proprietary codecs.
* **webview2** — the same pane, in the same window, rendered by the Microsoft
  Edge WebView2 runtime (Windows only). Chosen when video matters: Edge ships
  the codecs Qt's build does not. It can also load unpacked browser extensions,
  which the Qt pane cannot. Being inside Strata's own window, it stays under
  the window's screen-capture exclusion — see ADR-0012.
* **chrome** — the Chrome you already have, driven over the DevTools protocol
  on a loopback port against a Strata-owned profile. For the sign-in flows that
  refuse embedded browsers, and for extensions installed from the store rather
  than unpacked. It is a browser Strata does not own, so it is the one backend
  that cannot be kept out of a screen recording.

Both produce the same :class:`ScrapedPage` from the same extraction, so the
research pipeline downstream cannot tell them apart — and neither can the parts
of the security story that matter: a page is untrusted text either way.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Search stays inside the browser: Strata builds a query URL and navigates the
# window to it. No search API, no API key, no outbound call from Strata itself —
# the engine sees a normal browser request from a normal browser session.
SearchEngine = Literal["duckduckgo", "google", "bing", "brave", "kagi", "startpage"]

BrowserBackend = Literal["embedded", "webview2", "chrome"]

# The two that render inside the Strata window. They differ by engine, not by
# how the app drives them — the pane contract and the capture story are shared.
IN_WINDOW_BACKENDS: frozenset[str] = frozenset({"embedded", "webview2"})

SEARCH_URLS: dict[str, str] = {
    "duckduckgo": "https://duckduckgo.com/?q={query}",
    "google": "https://www.google.com/search?q={query}",
    "bing": "https://www.bing.com/search?q={query}",
    "brave": "https://search.brave.com/search?q={query}",
    "kagi": "https://kagi.com/search?q={query}",
    "startpage": "https://www.startpage.com/sp/search?query={query}",
}


class BrowserTab(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = ""
    title: str = ""
    url: str = ""
    # Chrome reports no "active tab" over the protocol, so the most recently
    # focused page target leads the list and is what "the active tab" means.
    active: bool = False


class BrowserStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    backend: BrowserBackend = "embedded"
    running: bool = False
    # True only for the Chrome backend. The UI states this rather than letting
    # a user wonder why their extensions did not load in the embedded pane.
    supports_extensions: bool = False
    port: int = 0
    browser_version: str = ""
    executable: str = ""
    profile_path: str = ""
    tab_count: int = 0
    # Media blur (embedded pane only). `blur_supported` is false for the Chrome
    # backend, which Strata does not reach into to restyle.
    blur_enabled: bool = False
    blur_amount: int = 12
    blur_supported: bool = False
    # Mobile layout (embedded pane only): the pane serves a mobile user-agent.
    mobile_mode: bool = False
    # One plain sentence for the UI — why it is not running, or what it is.
    detail: str = ""


class ScrapedPage(BaseModel):
    """One page, as text. Never rendered, never executed, never an instruction."""

    model_config = ConfigDict(extra="forbid")

    url: str = ""
    title: str = ""
    text: str = ""
    char_count: int = 0
    truncated: bool = False
    target_id: str = ""
    # Set once the page has been filed as a capture note.
    note_id: str = ""


class ScrapeRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(default="", max_length=256)
