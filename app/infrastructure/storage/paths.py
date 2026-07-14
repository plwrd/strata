"""Path safety.

Every path that originates outside the process (a note id, a layer name, an
import target) is resolved through here. Path traversal is a security rule, not
a nicety: ``..`` and absolute paths are rejected, and the resolved path must
still be inside the root.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from app.domain.errors import InvalidRequestError

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
MAX_NAME_LENGTH = 120


def safe_filename(name: str) -> str:
    """Turn a user-supplied title into a filename that is safe on all targets."""
    name = unicodedata.normalize("NFC", name).strip()
    name = _UNSAFE.sub("-", name)
    name = name.strip(". ")
    if not name:
        raise InvalidRequestError("Name is empty after sanitisation.")
    if name.upper().split(".")[0] in _RESERVED_WINDOWS:
        name = f"_{name}"
    return name[:MAX_NAME_LENGTH]


def resolve_within(root: Path, *parts: str) -> Path:
    """Resolve ``parts`` under ``root``, refusing anything that escapes it."""
    for part in parts:
        if not part:
            continue
        if Path(part).is_absolute() or ".." in Path(part).parts:
            raise InvalidRequestError("Path is not permitted.")
    candidate = root.joinpath(*[p for p in parts if p])
    root_resolved = root.resolve()
    try:
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover - platform dependent
        raise InvalidRequestError("Path could not be resolved.") from exc
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise InvalidRequestError("Path escapes the workspace root.")
    return resolved
