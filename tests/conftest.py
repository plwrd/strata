"""Shared fixtures.

Every test gets a real workspace in a temporary directory: real Markdown files,
real parsing, real graph extraction, real export rendering. Nothing here mocks
Strata's own behaviour — the only thing faked is *where* the workspace lives.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.services.container import Paths, Services
from app.services.workspace_service import WorkspaceService


@pytest.fixture()
def paths(tmp_path: Path) -> Paths:
    return Paths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "data" / "logs",
        default_workspace=tmp_path / "workspace",
    )


@pytest.fixture()
def services(paths: Paths) -> Services:
    return Services(paths, environment="test")


@pytest.fixture()
def workspace(services: Services) -> Services:
    """A services container with the seeded demo workspace open."""
    services.workspace.open_or_create(services.paths.default_workspace, "Test Workspace")
    return services


@pytest.fixture()
def empty_workspace(services: Services) -> Services:
    services.workspace.create(services.paths.default_workspace, "Empty", seed_demo=False)
    return services


@pytest.fixture()
def note_ids(workspace: Services) -> list[str]:
    return [note.metadata.id for note in workspace.notes.list_notes()]


@pytest.fixture()
def blank_workspace_service(tmp_path: Path) -> Iterator[WorkspaceService]:
    service = WorkspaceService()
    yield service
    service.close()
