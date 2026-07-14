"""Opaque identifiers.

Every identifier that crosses the bridge is opaque: it carries no filesystem
path, no title, and no information about the object it names. Private-layer
object ids are additionally random (never derived from content or names) so that
the on-disk filename leaks nothing — see ADR-0004.
"""

from __future__ import annotations

import secrets
from typing import Final

_OBJECT_ID_BYTES: Final = 16
_SHORT_ID_BYTES: Final = 8


def new_object_id() -> str:
    """Return a random 128-bit opaque object id as 32 lowercase hex characters."""
    return secrets.token_hex(_OBJECT_ID_BYTES)


def new_layer_id() -> str:
    return f"layer_{secrets.token_hex(_SHORT_ID_BYTES)}"


def new_workspace_id() -> str:
    return f"ws_{secrets.token_hex(_SHORT_ID_BYTES)}"


def new_job_id() -> str:
    return f"job_{secrets.token_hex(_SHORT_ID_BYTES)}"


def new_request_id() -> str:
    return f"req_{secrets.token_hex(_SHORT_ID_BYTES)}"


def new_export_id() -> str:
    return f"exp_{secrets.token_hex(_SHORT_ID_BYTES)}"


def shard_for(object_id: str) -> str:
    """Return the two-character storage shard for an object id.

    Private layers store objects at ``objects/<shard>/<object_id>``.
    """
    if len(object_id) < 2:
        raise ValueError("object id too short to shard")
    return object_id[:2]
