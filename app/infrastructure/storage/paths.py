"""Path safety.

Every path that originates outside the process (a note id, a layer name, an
import target) is resolved through here. Path traversal is a security rule, not
a nicety: ``..`` and absolute paths are rejected, and the resolved path must
still be inside the root.
"""

from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path, PureWindowsPath

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
    # Collapse dot runs *after* the separators are gone: "../../evil" would
    # otherwise sanitise to "-..-evil", which is harmless as a single component
    # but leaves a traversal token in a name that later code may split again.
    while ".." in name:
        name = name.replace("..", "-")
    # Strip sanitizer tokens *and* any Unicode whitespace (e.g. a non-breaking
    # space) from both ends — \s in a str regex is Unicode-aware — so a name never
    # ends up with an invisible trailing character.
    name = re.sub(r"^[-.\s]+|[-.\s]+$", "", name)
    if not name:
        # A name made only of dots, dashes and spaces is not a name. Refusing is
        # better than inventing one the user did not choose.
        raise InvalidRequestError("Name is empty after sanitisation.")
    if name.upper().split(".")[0] in _RESERVED_WINDOWS:
        name = f"_{name}"
    return name[:MAX_NAME_LENGTH]


def resolve_within(root: Path, *parts: str) -> Path:
    """Resolve ``parts`` under ``root``, refusing anything that escapes it."""
    for part in parts:
        if not part:
            continue
        # Judge every component against *both* path flavours, so hostile input is
        # refused identically on every platform. A POSIX host treats "C:\\Windows"
        # or "a\\..\\b" as an ordinary filename; a Windows host treats them as a
        # drive path and a traversal. Neither is ever a legitimate component here.
        windows = PureWindowsPath(part)
        if (
            Path(part).is_absolute()
            or windows.is_absolute()
            or windows.drive
            or "\\" in part
            or ".." in Path(part).parts
            or ".." in windows.parts
            # A NUL or other control character is never a legitimate component and
            # makes the OS path resolver raise a raw ValueError; refuse it here so
            # the failure is a typed Strata error, not an untyped crash.
            or any(ord(ch) < 0x20 for ch in part)
        ):
            raise InvalidRequestError("Path is not permitted.")
    candidate = root.joinpath(*[p for p in parts if p])
    root_resolved = root.resolve()
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError) as exc:  # pragma: no cover - platform dependent
        raise InvalidRequestError("Path could not be resolved.") from exc
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise InvalidRequestError("Path escapes the workspace root.")
    return resolved


def replace_atomic(source: Path, target: Path, *, attempts: int = 8) -> None:
    """``source.replace(target)`` with a bounded retry for Windows races.

    On Windows, an antivirus scanner or the search indexer can hold a freshly
    written file for a few milliseconds; ``os.replace`` then fails with
    ``PermissionError`` (WinError 5/32) even though nothing is actually wrong.
    Every atomic write in Strata goes through here so that transient hold is a
    short wait, not a crash — while a *persistent* error still raises after the
    final attempt, because masking a real permission problem would be worse.
    """
    delay = 0.01
    for attempt in range(attempts):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.2)
