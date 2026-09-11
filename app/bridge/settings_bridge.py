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
    # What the OS actually granted for "Hidden for sharing", which is not the
    # same thing as the setting. The setting is a request; this is the answer,
    # and the dialog has to show the answer — a privacy control that claims a
    # protection the platform refused is worse than no control at all.
    #
    # "unknown" is used before the window exists (a headless bridge in tests,
    # or a call that arrives before the first show).
    capture_protection: str = "unknown"


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)


class SettingsBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    def _capture_protection(self) -> str:
        """Ask the window what protection it actually has. Never guess."""
        state = getattr(self.parent(), "capture_state", None)
        if not callable(state):
            return "unknown"
        try:
            return str(state().value)
        except Exception:  # pragma: no cover - a status must not break settings
            return "unknown"

    def _response(self, settings: AppSettings) -> SettingsResponse:
        return SettingsResponse(settings=settings, capture_protection=self._capture_protection())

    @Slot(str, result=str)
    @bridge_method(EmptyRequest)
    def get_settings(self, _request: EmptyRequest) -> SettingsResponse:
        return self._response(self._services.settings.settings)

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
        # Read *after* applying, so a toggle reports the state it just produced.
        return self._response(settings)

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
        return self._response(self._services.settings.update({"browser_user_scripts": current}))

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
        return self._response(self._services.settings.update({"browser_extensions": current}))
