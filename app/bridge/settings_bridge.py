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
        settings = self._services.settings.update(request.values)
        # Native window effects live outside the renderer — apply them whenever
        # the setting changes.
        parent = self.parent()
        apply_tray = getattr(parent, "apply_minimize_to_tray", None)
        if callable(apply_tray):
            apply_tray(settings.minimize_to_tray)
        apply_taskbar = getattr(parent, "apply_hide_from_taskbar", None)
        if callable(apply_taskbar):
            apply_taskbar(settings.hide_from_taskbar)
        # The blur radius is a setting; a live pane must pick up a change to it.
        self._services.browser.set_blur_amount(settings.browser_blur_amount)
        return SettingsResponse(settings=settings)

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def choose_user_script(self, _request: EmptyRequest) -> SettingsResponse:
        """Pick a ``.js`` userscript for the built-in pane, and remember it.

        The Qt pane's stand-in for an extension, so it goes through the same
        deliberate native-dialog choice: a path typed into a settings box is a
        typo waiting to look like a broken feature.
        """
        from PySide6.QtWidgets import QFileDialog

        from app.domain.errors import CancelledError

        chosen, _filter = QFileDialog.getOpenFileName(
            None,
            "Choose a user script",
            "",
            "JavaScript (*.js *.user.js)",
        )
        if not chosen:
            raise CancelledError("No script was chosen.")

        current = list(self._services.settings.settings.browser_user_scripts)
        if chosen not in current:
            current.append(chosen)
        return SettingsResponse(
            settings=self._services.settings.update({"browser_user_scripts": current})
        )
