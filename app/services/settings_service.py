"""User settings.

Stored as JSON in the OS config directory. Secrets never live here: API keys go
to the OS keychain (Milestone 7), passwords go nowhere.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.browser import SEARCH_URLS
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.storage.paths import replace_atomic

# A research pane is not a browser install. More than a handful of extensions
# is a sign the setting is being used as one, and each is third-party code with
# sight of every page the pane visits.
MAX_BROWSER_EXTENSIONS = 10
MAX_BROWSER_USER_SCRIPTS = 20
# Generous, because a blocklist is the one of these three that people paste in
# bulk — but still a list a human curated, not a subscribed filter feed.
MAX_BROWSER_BLOCKED_HOSTS = 2000


def _clean_paths(value: Any, field: str, limit: int) -> list[str]:
    """Strip, drop blanks and duplicates, cap. Order and case preserved.

    Existence is deliberately not checked: a settings file has to load on a
    machine where a path has since moved, and the pane reports what it could
    not find when it tries. Refusing to start the app over it is the wrong trade.
    """
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise ValueError(f"{field} must be a list")
    cleaned: list[str] = []
    for entry in value:
        text = str(entry).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    if len(cleaned) > limit:
        raise ValueError(f"{field} holds at most {limit} entries")
    return cleaned


logger = get_logger(__name__)

Appearance = Literal[
    "cyberpunk-dark",
    "cyberpunk-dim",
    "high-contrast",
    "ember",
    "forest",
    "slate",
]
MotionPreference = Literal["full", "reduced", "system"]
GraphQuality = Literal["high", "balanced", "low-gpu"]
FontBody = Literal["inter", "system", "chakra"]
FontDisplay = Literal["chakra", "inter", "system"]
FontMono = Literal["jetbrains", "consolas", "system"]

# Whitelisted theme colour keys (snake_case → --kebab-case on the frontend).
THEME_COLOR_KEYS: frozenset[str] = frozenset(
    {
        "surface_void",
        "surface_base",
        "surface_raised",
        "surface_overlay",
        "text_primary",
        "text_secondary",
        "text_tertiary",
        "accent_primary",
        "accent_ai",
        "accent_collaboration",
        "status_success",
        "status_warning",
        "status_danger",
        "graph_background",
        "graph_node_default",
        "graph_node_selected",
        "graph_glow_selected",
        "graph_edge_default",
        "graph_edge_selected",
        "border_accent",
    }
)

_HEX6 = re.compile(r"^#[0-9A-Fa-f]{6}$")
_UI_SCALE_MIN = 0.85
_UI_SCALE_MAX = 1.35


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format_version: int = 1
    appearance: Appearance = "cyberpunk-dark"
    motion: MotionPreference = "system"
    graph_quality: GraphQuality = "balanced"
    particles_enabled: bool = True
    bloom_enabled: bool = True
    battery_saver: bool = False
    telemetry_enabled: bool = False  # opt-in, and there is nothing to opt into yet
    default_lens_id: str = "lens_all"
    last_workspace_path: str = ""
    developer_tools: bool = False

    # -- Theme customization -------------------------------------------------
    #
    # Template lives in `appearance`. These fields layer CSS variable overrides
    # on top: fonts, UI rem scale, and a whitelist of hex colours.
    font_body: FontBody = "inter"
    font_display: FontDisplay = "chakra"
    font_mono: FontMono = "jetbrains"
    ui_scale: float = 1.0
    theme_colors: dict[str, str] = Field(default_factory=dict)

    # -- Collaboration -------------------------------------------------------
    #
    # When set to a relay URL (e.g. https://relay.example/), collaboration syncs
    # over the network instead of the on-disk directory relay. The relay only ever
    # sees ciphertext (ADR-0006); it is not a trusted party. Empty = local only.
    relay_url: str = ""

    # -- AI ------------------------------------------------------------------
    #
    # Note what is NOT here: no API keys. Those live in the OS keychain, never in a
    # settings file that gets copied into a bug report or synced to a backup.
    default_provider: str = "ollama"
    # Qwythos-9B via Ollama (`qwythos`). Empty was historically allowed; the
    # AI service resolves blank/"default" to this id as well.
    default_model: str = "qwythos"
    embedding_model: str = ""
    claude_cli_path: str = ""
    provider_base_urls: dict[str, str] = Field(default_factory=dict)
    prefer_local_ai: bool = True
    # Resource controls for local models.
    local_context_tokens: int = 8192
    local_max_output_tokens: int = 8192
    ai_request_timeout: int = 120

    # -- Capture -------------------------------------------------------------
    #
    # URL import is the only outbound fetch besides AI providers and the relay.
    # It ships SSRF-guarded (scheme allowlist, private-range block, no
    # redirects) and can be switched off entirely here.
    url_import_enabled: bool = True

    # -- Browser research ----------------------------------------------------
    #
    # Strata can drive a real Chrome over a loopback DevTools port so research
    # reaches logged-in and JavaScript-rendered pages (and so the user's own
    # extensions apply). That is a large surface — a browser Strata can read is
    # a browser Strata can read *everything* in — so it ships off, and turning
    # it on is a deliberate act. Blank executable and profile paths mean "find
    # Chrome yourself" and "use the profile Strata owns"; pointing
    # `browser_profile_path` at an everyday profile hands Strata that whole
    # session, which is the user's call to make, not the default.
    browser_control_enabled: bool = False
    # "embedded" is the browser pane inside the Strata window: no second
    # process, no loopback port, sign-ins kept in a profile of its own. It
    # cannot load Chrome extensions — Qt ships Chromium without the extensions
    # subsystem — so "webview2" (Edge's engine, in the same window) and "chrome"
    # stay available for the pages that need them.
    browser_backend: str = "embedded"
    browser_executable_path: str = ""
    browser_profile_path: str = ""
    browser_debug_port: int = 9333
    browser_search_engine: str = "duckduckgo"
    # Blur images, video and canvas in the browser pane, so a shoulder-surfer or
    # a screen share sees text but not media. `browser_blur_media` is only the
    # starting state — the pane is toggled live with a hotkey; the radius is the
    # adjustable part. Embedded pane only.
    browser_blur_media: bool = False
    browser_blur_amount: int = 12
    # Unpacked Chrome/Edge extension folders to load into the WebView2 pane.
    # Folders, not `.crx` files: WebView2 has no store-install path, so this is
    # a directory containing a manifest. Empty by default and deliberately
    # opt-in per extension — an extension reads every page the pane visits, so
    # this is the user adding third-party code to their own research session.
    browser_extensions: list[str] = Field(default_factory=list)
    # The Qt pane's two stand-ins for extensions, which it cannot load at all
    # (Chromium's extensions subsystem is not compiled into Qt WebEngine, and
    # no flag adds it). Between them they cover what people install extensions
    # *for*: `browser_user_scripts` are Tampermonkey-style `.js` files injected
    # at document-creation, and `browser_blocked_hosts` are domains whose
    # requests the pane refuses — a hosts-file ad blocker, in effect.
    browser_user_scripts: list[str] = Field(default_factory=list)
    browser_blocked_hosts: list[str] = Field(default_factory=list)
    # Mobile mode: the browser pane serves a mobile user-agent so sites render
    # their touch/mobile layout. Synthetic touch events are advertised to pages
    # from the next launch (a process-global Chromium flag; see application.py).
    browser_mobile_mode: bool = False

    # -- Onboarding ----------------------------------------------------------
    #
    # False until the first-run tutorial is skipped or finished. Replay from
    # More → Tutorial does not clear this; Skip/Finish set it true again.
    onboarding_tour_completed: bool = False

    # -- Screen security -----------------------------------------------------
    #
    # Signal-style "Hidden for sharing" (on by default): when True, the OS
    # excludes the entire Strata window from screenshots and screen shares
    # (Windows: WDA_EXCLUDEFROMCAPTURE, with WDA_MONITOR fallback). The window
    # stays visible on your display.
    hide_for_sharing: bool = True

    # -- System tray ---------------------------------------------------------
    #
    # When on, closing or minimizing the window *hides* it: it leaves the
    # taskbar but Strata keeps running behind a tray icon, and quitting is a
    # deliberate act from the tray menu. `start_in_tray` starts hidden, for a
    # launch that does not announce itself.
    #
    # This hides the window, never the process. Strata stays fully visible to
    # Task Manager and every other process tool by design — see app/desktop/tray.py.
    minimize_to_tray: bool = False
    start_in_tray: bool = False
    # Drop the taskbar button entirely (Windows), even while the window is open.
    # Pairs with the tray, which is the way back to a window that has no taskbar
    # button — so turning this on keeps the tray icon up and sends minimize to it.
    hide_from_taskbar: bool = False

    @field_validator("browser_backend", mode="before")
    @classmethod
    def _check_backend(cls, value: Any) -> str:
        backend = str(value).strip().lower()
        if backend not in ("embedded", "webview2", "chrome"):
            raise ValueError("browser_backend must be 'embedded', 'webview2' or 'chrome'")
        return backend

    @field_validator("browser_debug_port", mode="before")
    @classmethod
    def _check_debug_port(cls, value: Any) -> int:
        """A user-space port, or the default. Never a privileged one, and never
        a number the frontend picked out of range."""
        try:
            port = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("browser_debug_port must be a number") from exc
        if not 1024 <= port <= 65535:
            raise ValueError("browser_debug_port must be between 1024 and 65535")
        return port

    @field_validator("browser_extensions", mode="before")
    @classmethod
    def _clean_extensions(cls, value: Any) -> list[str]:
        """Whitespace and duplicates out; order and case kept.

        Existence is *not* checked here. A settings file must load on a machine
        where an extension folder has been moved or deleted, and the pane says
        which one is missing when it tries — refusing to start the app over it
        would be the wrong trade.
        """
        return _clean_paths(value, "browser_extensions", MAX_BROWSER_EXTENSIONS)

    @field_validator("browser_user_scripts", mode="before")
    @classmethod
    def _clean_user_scripts(cls, value: Any) -> list[str]:
        return _clean_paths(value, "browser_user_scripts", MAX_BROWSER_USER_SCRIPTS)

    @field_validator("browser_blocked_hosts", mode="before")
    @classmethod
    def _clean_blocked_hosts(cls, value: Any) -> list[str]:
        """Hostnames, lower-cased, without scheme or path.

        People paste `https://ads.example.com/tag.js` into a blocklist box, and
        a list that silently keeps that entry blocks nothing while looking as
        though it works. Reduce each entry to its host and drop what has none.
        """
        raw = _clean_paths(value, "browser_blocked_hosts", MAX_BROWSER_BLOCKED_HOSTS)
        hosts: list[str] = []
        for entry in raw:
            host = entry.lower()
            if "//" in host:
                host = host.split("//", 1)[1]
            host = host.split("/", 1)[0].split("@")[-1].split(":")[0].strip(".")
            if host and " " not in host and host not in hosts:
                hosts.append(host)
        return hosts

    @field_validator("browser_blur_amount", mode="before")
    @classmethod
    def _clamp_blur_amount(cls, value: Any) -> int:
        try:
            amount = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("browser_blur_amount must be a number") from exc
        # Below 1 is not a blur; above 100 is a solid smear with no gain.
        return max(1, min(100, amount))

    @field_validator("browser_search_engine", mode="before")
    @classmethod
    def _check_search_engine(cls, value: Any) -> str:
        engine = str(value).strip().lower()
        if engine not in SEARCH_URLS:
            raise ValueError("browser_search_engine must be an engine Strata knows")
        return engine

    @field_validator("ui_scale", mode="before")
    @classmethod
    def _clamp_ui_scale(cls, value: Any) -> float:
        try:
            scale = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("ui_scale must be a number") from exc
        return max(_UI_SCALE_MIN, min(_UI_SCALE_MAX, scale))

    @field_validator("theme_colors", mode="before")
    @classmethod
    def _sanitize_theme_colors(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("theme_colors must be an object")
        cleaned: dict[str, str] = {}
        for raw_key, raw_hex in value.items():
            key = str(raw_key)
            if key not in THEME_COLOR_KEYS:
                continue
            hex_value = str(raw_hex).strip()
            if not _HEX6.match(hex_value):
                raise ValueError(f"theme_colors.{key} must be a #RRGGBB hex colour")
            cleaned[key] = hex_value.lower()
        return cleaned


class SettingsService:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._settings = self._load()

    def _load(self) -> AppSettings:
        if not self._path.is_file():
            return AppSettings()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return AppSettings.model_validate(raw)
        except (json.JSONDecodeError, OSError, ValidationError):
            # Corrupt settings must never stop the app from starting.
            logger.warning("settings.unreadable_using_defaults")
            return AppSettings()

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def update(self, values: dict[str, object]) -> AppSettings:
        merged = self._settings.model_dump()
        merged.update(values)
        self._settings = AppSettings.model_validate(merged)
        self.save()
        return self._settings

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(self._settings.model_dump_json(indent=2), encoding="utf-8")
        replace_atomic(temporary, self._path)
