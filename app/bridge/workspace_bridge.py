"""Workspace lifecycle over the WebChannel."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.workspace import KnowledgeLens, WorkspaceDescriptor
from app.services.container import Services

APP_PROTOCOL_VERSION = 1


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    app: str = "strata"
    version: str
    protocol_version: int = APP_PROTOCOL_VERSION
    environment: str
    python_version: str
    qt_version: str
    workspace_open: bool = False


class WorkspaceStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_open: bool
    workspace: WorkspaceDescriptor | None = None
    lenses: list[KnowledgeLens] = Field(default_factory=list)


class OpenWorkspaceRequest(BaseModel):
    """Deliberately has no ``path`` field.

    The frontend never names a filesystem location: it can ask for *the default
    workspace* or ask the user to pick one with a native dialog. That removes an
    entire class of "make Python read /etc/shadow" bugs by construction.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Strata Workspace", min_length=1, max_length=200)


class WorkspaceBridge(QObject):
    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def health(self, _request: EmptyRequest) -> HealthResponse:
        """Liveness + protocol handshake. The frontend calls this before anything else."""
        import platform

        from PySide6 import __version__ as pyside_version

        return HealthResponse(
            version=self._services.app_version,
            environment=self._services.environment,
            python_version=platform.python_version(),
            qt_version=pyside_version,
            workspace_open=self._services.workspace.is_open,
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def get_state(self, _request: EmptyRequest) -> WorkspaceStateResponse:
        workspace = self._services.workspace
        if not workspace.is_open:
            return WorkspaceStateResponse(is_open=False)
        return WorkspaceStateResponse(
            is_open=True,
            workspace=workspace.descriptor,
            lenses=workspace.lenses(),
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(OpenWorkspaceRequest)
    def open_default_workspace(self, request: OpenWorkspaceRequest) -> WorkspaceStateResponse:
        """Open (or first-run create) the workspace at the platform default path."""
        workspace = self._services.workspace
        workspace.open_or_create(self._services.paths.default_workspace, request.name)
        return WorkspaceStateResponse(
            is_open=True,
            workspace=workspace.descriptor,
            lenses=workspace.lenses(),
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(OpenWorkspaceRequest)
    def choose_workspace(self, request: OpenWorkspaceRequest) -> WorkspaceStateResponse:
        """Let the *user* pick a folder with a native dialog, then open it."""
        from PySide6.QtWidgets import QFileDialog

        from app.domain.errors import CancelledError

        directory = QFileDialog.getExistingDirectory(
            None,
            "Choose a Strata workspace folder",
            str(self._services.paths.default_workspace.parent),
        )
        if not directory:
            raise CancelledError("No workspace was chosen.")

        workspace = self._services.workspace
        workspace.open_or_create(Path(directory), request.name)
        return WorkspaceStateResponse(
            is_open=True,
            workspace=workspace.descriptor,
            lenses=workspace.lenses(),
        )

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def close_workspace(self, _request: EmptyRequest) -> WorkspaceStateResponse:
        self._services.workspace.close()
        return WorkspaceStateResponse(is_open=False)
