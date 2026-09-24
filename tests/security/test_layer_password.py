"""Remember-on-this-device uses the OS keychain, not workspace files."""

from __future__ import annotations

from app.infrastructure.keychain.credentials import LAYER_UNLOCK_SERVICE, CredentialStore
from app.services.container import Services

PASSWORD = "correct horse battery staple"


def test_a_remembered_password_unlocks_on_reopen(services: Services) -> None:
    services.workspace.open_or_create(services.paths.default_workspace, "Test")
    layer, _recovery = services.workspace.create_layer(
        "Deals", visibility="private", password=PASSWORD
    )
    services.workspace.lock_layer(layer.id)
    services.workspace.unlock_layer(layer.id, PASSWORD, remember=True)
    services.workspace.lock_layer(layer.id)
    assert services.workspace.require_layer(layer.id).state == "locked"

    services.workspace.close()
    services.workspace.open(services.paths.default_workspace)

    reopened = services.workspace.require_layer(layer.id)
    assert reopened.state == "unlocked"
    assert services.workspace.layer_for_client(layer.id).password_remembered is True


def test_unlocking_without_remember_forgets_a_saved_password(services: Services) -> None:
    services.workspace.open_or_create(services.paths.default_workspace, "Test")
    layer, _recovery = services.workspace.create_layer(
        "Deals", visibility="private", password=PASSWORD
    )
    services.workspace.unlock_layer(layer.id, PASSWORD, remember=True)
    services.workspace.lock_layer(layer.id)
    services.workspace.unlock_layer(layer.id, PASSWORD, remember=False)
    store = CredentialStore(LAYER_UNLOCK_SERVICE)
    assert store.has(layer.id) is False


def test_a_remembered_password_never_lands_in_the_workspace(services: Services) -> None:
    services.workspace.open_or_create(services.paths.default_workspace, "Test")
    layer, _recovery = services.workspace.create_layer(
        "Deals", visibility="private", password=PASSWORD
    )
    services.workspace.unlock_layer(layer.id, PASSWORD, remember=True)
    dumped = (services.paths.default_workspace / "workspace.json").read_text(encoding="utf-8")
    assert PASSWORD not in dumped
