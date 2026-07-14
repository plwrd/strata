"""The unlocked-key holder.

"Locked" is not a UI state. It is the absence of a key in this object. Everything
that can read a private layer must ask here, and locking removes the key — so a
component that forgot to check the lock state simply cannot get a key.

Zeroisation is best-effort and is documented as such (THREAT_MODEL.md, T-06). The
key is held in a `bytearray` so it *can* be overwritten, but CPython may already
have copied it, and the OS may have paged it out. This narrows the window; it does
not eliminate it. Claiming otherwise would be the kind of promise this project
refuses to make.
"""

from __future__ import annotations

from app.domain.errors import LayerLockedError
from app.infrastructure.encryption.primitives import zeroize
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


class KeyHolder:
    """Holds the data keys of currently-unlocked layers."""

    def __init__(self) -> None:
        self._keys: dict[str, bytearray] = {}

    def unlock(self, layer_id: str, key: bytes) -> None:
        self.lock(layer_id)  # never leave an old key behind
        self._keys[layer_id] = bytearray(key)
        logger.info("layer.unlocked", layer_id=layer_id)

    def key_for(self, layer_id: str) -> bytes:
        key = self._keys.get(layer_id)
        if key is None:
            # Generic on purpose: it does not say whether the layer exists.
            raise LayerLockedError("This layer is locked.", details={"layerId": layer_id})
        return bytes(key)

    def is_unlocked(self, layer_id: str) -> bool:
        return layer_id in self._keys

    def unlocked_layers(self) -> list[str]:
        return list(self._keys)

    def lock(self, layer_id: str) -> bool:
        key = self._keys.pop(layer_id, None)
        if key is None:
            return False
        zeroize(key)
        logger.info("layer.locked", layer_id=layer_id)
        return True

    def lock_all(self) -> int:
        count = 0
        for layer_id in list(self._keys):
            if self.lock(layer_id):
                count += 1
        return count
