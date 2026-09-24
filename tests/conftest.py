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


@pytest.fixture(autouse=True, scope="session")
def cheap_kdf() -> Iterator[None]:
    """Derive layer keys with a cheap Argon2 profile, for tests only.

    Production derives at 256 MiB per hash. A suite that creates dozens of
    private layers asks for that allocation dozens of times, and on a busy
    machine argon2 starts answering ``HashingError: Memory allocation error`` —
    failures that move between unrelated tests every run and pass in isolation.

    Only *new* layers are affected: the parameters are written into each layer
    header and read back from it, so unlock still exercises the real code path
    with whatever the header says. The production constants are untouched, and
    ``test_encryption.py`` asserts them directly so a real weakening cannot hide
    behind this fixture.
    """
    from app.infrastructure.encryption import primitives

    original = primitives.KdfParams.new

    @classmethod  # type: ignore[misc]
    def cheap(cls: type[primitives.KdfParams]) -> primitives.KdfParams:
        import secrets

        return cls(
            time_cost=1,
            memory_kib=8_192,  # 8 MiB
            parallelism=1,
            salt=secrets.token_bytes(primitives.SALT_BYTES),
        )

    primitives.KdfParams.new = cheap  # type: ignore[method-assign]
    try:
        yield
    finally:
        primitives.KdfParams.new = original  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def isolated_keychain() -> Iterator[None]:
    """Never touch the real OS keychain in tests.

    Without this, a test that stores an API key would write it to the developer's
    actual keychain and pollute every later test that asks whether a provider is
    configured. An in-memory backend keeps each run hermetic.
    """
    import keyring

    class _MemoryKeyring(keyring.backend.KeyringBackend):
        priority = 1  # type: ignore[assignment]

        def __init__(self) -> None:
            super().__init__()
            self._store: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self._store.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self._store[(service, username)] = password

        def delete_password(self, service: str, username: str) -> None:
            self._store.pop((service, username), None)

    previous = keyring.get_keyring()
    keyring.set_keyring(_MemoryKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)


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
