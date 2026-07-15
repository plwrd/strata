"""The credential store.

The one rule that must never break: when the keychain is unavailable, the store
fails **closed**. It never writes a key to a file. A silent downgrade from
"encrypted by the OS" to "plaintext on disk" is precisely how credentials leak.
"""

from __future__ import annotations

import keyring
import pytest
from keyring.backends.fail import Keyring as FailKeyring
from keyring.errors import KeyringError

from app.infrastructure.keychain.credentials import CredentialStore


class MemoryKeyring(keyring.backend.KeyringBackend):
    """An in-memory keychain, so the test does not touch the real one."""

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


@pytest.fixture()
def memory_keyring() -> MemoryKeyring:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


def test_a_credential_round_trips(memory_keyring: MemoryKeyring) -> None:
    store = CredentialStore()

    assert store.set("openai", "sk-test-key") is True
    assert store.get("openai") == "sk-test-key"
    assert store.has("openai") is True


def test_deleting_a_credential(memory_keyring: MemoryKeyring) -> None:
    store = CredentialStore()
    store.set("openai", "sk-test-key")

    assert store.delete("openai") is True
    assert store.get("openai") is None
    assert store.has("openai") is False


def test_the_secret_is_not_returned_for_another_provider(memory_keyring: MemoryKeyring) -> None:
    store = CredentialStore()
    store.set("openai", "sk-openai")

    assert store.get("anthropic") is None


@pytest.mark.security()
def test_it_fails_closed_when_the_keychain_is_unavailable() -> None:
    """No keychain means no storage — never a fallback to a file."""
    previous = keyring.get_keyring()
    keyring.set_keyring(FailKeyring())
    try:
        store = CredentialStore()

        assert store.is_available() is False
        assert store.set("openai", "sk-secret") is False
        assert store.get("openai") is None
    finally:
        keyring.set_keyring(previous)


@pytest.mark.security()
def test_a_keychain_error_on_write_is_reported_not_swallowed(
    memory_keyring: MemoryKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CredentialStore()
    store.is_available()  # cache the (available) result

    def explode(*_args: object, **_kwargs: object) -> None:
        raise KeyringError("locked")

    monkeypatch.setattr(keyring, "set_password", explode)

    # A write that fails returns False. It does not pretend to have stored the key,
    # which would leave the user believing a request will authenticate when it will not.
    assert store.set("openai", "sk-secret") is False
