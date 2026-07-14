"""Private-layer lifecycle: create, unlock, lock, change password, rotate.

This service owns the only path from a password to a key. Everything else asks the
:class:`KeyHolder`, and the KeyHolder only has a key if this service put one there.

The teardown on lock is the part that is easy to get wrong. Removing the key stops
*future* reads, but the decrypted content already handed to the search index, the
graph labels, the editor buffer and the AI context does not vanish on its own. So
lock() runs a registered set of teardown hooks, and anything that caches decrypted
private content is required to register one. See ``on_lock``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from app.domain.errors import ConflictError, InvalidRequestError, NotFoundError
from app.infrastructure.encryption.keyholder import KeyHolder
from app.infrastructure.encryption.layer_header import (
    LayerHeader,
    generate_recovery_key,
)
from app.infrastructure.encryption.primitives import DecryptionError, random_key
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.storage.encrypted_store import EncryptedLayerStore

logger = get_logger(__name__)

MIN_PASSWORD_LENGTH = 8


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class EncryptionService:
    def __init__(self, keys: KeyHolder | None = None) -> None:
        self.keys = keys or KeyHolder()
        self._teardown: list[Callable[[str], None]] = []

    def on_lock(self, hook: Callable[[str], None]) -> None:
        """Register a teardown hook, called with the layer id when it locks.

        Anything that holds decrypted private content must register one: search
        indexes, embedding matrices, graph labels, editor buffers, previews, AI
        context. A component that caches and does not register is a leak that
        survives the lock.
        """
        self._teardown.append(hook)

    # -- creation ------------------------------------------------------------

    def create_layer(
        self,
        *,
        layer_id: str,
        root: Path,
        password: str,
        with_recovery_key: bool = True,
        padding: bool = True,
    ) -> tuple[LayerHeader, str | None]:
        """Create a private layer. Returns its header and the recovery key (once)."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidRequestError(
                f"A layer password must be at least {MIN_PASSWORD_LENGTH} characters.",
                details={"minimum": MIN_PASSWORD_LENGTH},
            )
        if (root / "layer.header").exists():
            raise ConflictError("This layer already exists.")

        recovery_key = generate_recovery_key() if with_recovery_key else None
        header, layer_key = LayerHeader.create(
            layer_id=layer_id,
            password=password,
            created_at=_now(),
            recovery_key=recovery_key,
        )
        header.padding_enabled = padding

        store = EncryptedLayerStore(layer_id, root, padding=padding)
        store.ensure()
        header.manifest_object_id = store.create_manifest(layer_key)
        header.save(root)

        self.keys.unlock(layer_id, layer_key)
        logger.info("layer.created_private", layer_id=layer_id, recovery=bool(recovery_key))

        # Shown once, never stored. If the user loses it along with the password,
        # the layer is gone — and we say so rather than keeping a copy "to help".
        return header, recovery_key

    # -- unlock / lock -------------------------------------------------------

    def unlock(self, layer_id: str, root: Path, password: str) -> LayerHeader:
        header = self._load_header(root, layer_id)
        try:
            layer_key = header.unlock_with_password(password)
        except DecryptionError:
            # One generic failure. It does not say whether the password was wrong,
            # whether the layer is corrupt, or whether anything is inside.
            logger.warning("layer.unlock_failed", layer_id=layer_id)
            raise
        self.keys.unlock(layer_id, layer_key)
        return header

    def unlock_with_recovery_key(self, layer_id: str, root: Path, recovery_key: str) -> LayerHeader:
        header = self._load_header(root, layer_id)
        layer_key = header.unlock_with_recovery_key(recovery_key)
        self.keys.unlock(layer_id, layer_key)
        logger.info("layer.unlocked_via_recovery", layer_id=layer_id)
        return header

    def lock(self, layer_id: str) -> bool:
        """Drop the key and tear down everything that cached decrypted content."""
        locked = self.keys.lock(layer_id)
        for hook in self._teardown:
            try:
                hook(layer_id)
            except Exception:
                logger.exception("layer.teardown_hook_failed", layer_id=layer_id)
        return locked

    def lock_all(self) -> int:
        count = 0
        for layer_id in self.keys.unlocked_layers():
            if self.lock(layer_id):
                count += 1
        return count

    def is_unlocked(self, layer_id: str) -> bool:
        return self.keys.is_unlocked(layer_id)

    # -- key management ------------------------------------------------------

    def change_password(
        self, layer_id: str, root: Path, old_password: str, new_password: str
    ) -> LayerHeader:
        """Rewrap the layer key. Cheap, and does *not* revoke anyone."""
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise InvalidRequestError(
                f"A layer password must be at least {MIN_PASSWORD_LENGTH} characters.",
                details={"minimum": MIN_PASSWORD_LENGTH},
            )
        header = self._load_header(root, layer_id)
        header.change_password(old_password, new_password)
        header.updated_at = _now()
        header.save(root)
        logger.info("layer.password_changed", layer_id=layer_id)
        return header

    def reissue_recovery_key(self, layer_id: str, root: Path, password: str) -> str:
        header = self._load_header(root, layer_id)
        recovery_key = generate_recovery_key()
        header.set_recovery_key(password, recovery_key)
        header.updated_at = _now()
        header.save(root)
        return recovery_key

    def rotate_key(self, layer_id: str, root: Path, password: str) -> int:
        """Generate a new layer key and re-encrypt every object under it.

        This is the operation that actually revokes a former collaborator — a
        password change does not, because they may already hold the layer key.

        It is not atomic across a crash: if the process dies mid-rotation, some
        objects are under the new key and some under the old. The header is only
        written *after* every object succeeds, so a crashed rotation leaves the old
        header in place, and the old key still opens the objects that were not yet
        rewritten. Recovering the ones that were is Milestone 11's job (a rotation
        journal); until then, back up before rotating, and the UI says so.
        """
        header = self._load_header(root, layer_id)
        old_key = header.unlock_with_password(password)
        new_key = random_key()

        store = EncryptedLayerStore(layer_id, root, padding=header.padding_enabled)
        rewritten = store.rotate(old_key, new_key, header.manifest_object_id)

        header.rewrap_for_rotation(password, new_key)
        header.updated_at = _now()
        header.save(root)

        self.keys.unlock(layer_id, new_key)
        logger.info(
            "layer.key_rotated",
            layer_id=layer_id,
            objects=rewritten,
            generation=header.key_generation,
        )
        return rewritten

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _load_header(root: Path, layer_id: str) -> LayerHeader:
        header = LayerHeader.load(root)
        if header.layer_id and header.layer_id != layer_id:
            raise NotFoundError("Layer not found.")
        return header

    def store_for(self, layer_id: str, root: Path, header: LayerHeader) -> EncryptedLayerStore:
        return EncryptedLayerStore(layer_id, root, padding=header.padding_enabled)
