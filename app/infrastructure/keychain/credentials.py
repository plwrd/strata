"""API credentials, in the OS keychain.

Never in a Markdown file, a workspace file, SQLite, a log, the frontend store, or
an export package. The keychain is the one place on a desktop that is designed for
this, and using anything else would be choosing convenience over the user's key.

If the platform has no usable keychain (a bare Linux container, a locked-down
kiosk), the store fails **closed**: it reports that it cannot save, and Strata
treats the provider as unconfigured. It does not quietly fall back to a file — a
silent downgrade from "encrypted by the OS" to "plaintext on disk" is exactly the
kind of helpfulness that gets people's keys stolen.
"""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

SERVICE = "strata.ai-provider"


class CredentialStore:
    def __init__(self, service: str = SERVICE) -> None:
        self._service = service
        self._available: bool | None = None

    def is_available(self) -> bool:
        """Whether this machine has a keychain we can actually use."""
        if self._available is not None:
            return self._available
        try:
            backend = keyring.get_keyring()
            # keyring's "fail" and "null" backends are its way of saying "there is
            # no real keychain here". Their class is named `Keyring`, so the module
            # is the reliable tell. Treat either as unavailable rather than
            # pretending a store exists.
            module = type(backend).__module__.lower()
            self._available = "fail" not in module and "null" not in module
        except KeyringError:
            self._available = False

        if not self._available:
            logger.warning("keychain.unavailable")
        return self._available

    def get(self, provider_id: str) -> str | None:
        if not self.is_available():
            return None
        try:
            return keyring.get_password(self._service, provider_id)
        except KeyringError:
            logger.warning("keychain.read_failed", provider_id=provider_id)
            return None

    def set(self, provider_id: str, secret: str) -> bool:
        """Store a credential. Returns False rather than falling back to a file."""
        if not self.is_available():
            return False
        try:
            keyring.set_password(self._service, provider_id, secret)
        except KeyringError:
            logger.warning("keychain.write_failed", provider_id=provider_id)
            return False
        # The secret itself is never logged, only the fact that one was stored.
        logger.info("keychain.stored", provider_id=provider_id)
        return True

    def delete(self, provider_id: str) -> bool:
        if not self.is_available():
            return False
        try:
            keyring.delete_password(self._service, provider_id)
        except KeyringError:
            return False
        logger.info("keychain.deleted", provider_id=provider_id)
        return True

    def has(self, provider_id: str) -> bool:
        return bool(self.get(provider_id))
