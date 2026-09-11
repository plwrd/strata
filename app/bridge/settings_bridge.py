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
        # Native window effects (screen-capture exclusion) live outside the
        # renderer — apply them whenever the setting changes.
        parent = self.parent()
        apply_capture = getattr(parent, "apply_hide_for_sharing", None)
        if callable(apply_capture):
            apply_capture(settings.hide_for_sharing)
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
    def choose_browser_extension(self, _request: EmptyRequest) -> SettingsResponse:
        """Let the *user* pick an unpacked extension folder, and remember it.

        A native dialog rather than a text field, for the same reason the
        workspace picker is one: the path has to exist and be a directory, and
        a typo in a settings box is a worse way to find that out.

        The folder is only recorded here — it is loaded when the pane next
        starts, because WebView2 configures extensions at browser-process
        creation and there is no way to add one to a running engine.
        """
        from PySide6.QtWidgets import QFileDialog

        from app.domain.errors import CancelledError

        directory = QFileDialog.getExistingDirectory(
            None,
            "Choose an unpacked extension folder (the one containing manifest.json)",
            "",
        )
        if not directory:
            raise CancelledError("No extension folder was chosen.")

        current = list(self._services.settings.settings.browser_extensions)
        if directory not in current:
            current.append(directory)
        return SettingsResponse(
            settings=self._services.settings.update(
                {
                    "browser_extensions": current,
                }
            )
        )
