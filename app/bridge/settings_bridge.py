"""Application settings, including the accessibility and performance switches."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.services.container import Services
from app.services.settings_service import AppSettings


class SettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: AppSettings


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)


class SettingsBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def get_settings(self, _request: EmptyRequest) -> SettingsResponse:
        return SettingsResponse(settings=self._services.settings.settings)

    @Slot(str, result=str)
    @bridge_method(UpdateSettingsRequest)
    def update_settings(self, request: UpdateSettingsRequest) -> SettingsResponse:
        # AppSettings ignores unknown keys, so a rogue key cannot inject state.
        return SettingsResponse(settings=self._services.settings.update(request.values))
