"""User settings.

Stored as JSON in the OS config directory. Secrets never live here: API keys go
to the OS keychain (Milestone 7), passwords go nowhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

Appearance = Literal["cyberpunk-dark", "cyberpunk-dim", "high-contrast"]
MotionPreference = Literal["full", "reduced", "system"]
GraphQuality = Literal["high", "balanced", "low-gpu"]


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
        temporary.replace(self._path)
