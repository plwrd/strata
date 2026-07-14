"""Extract ``qwebchannel.js`` from Qt's resources into the frontend.

Qt ships the WebChannel client library inside the QtWebChannel module's compiled
resources (``:/qtwebchannel/qwebchannel.js``). The obvious way to load it is
``<script src="qrc:///qtwebchannel/qwebchannel.js">``, but that needs a CSP that
allows the ``qrc:`` scheme, which widens the policy for one file.

Copying it into ``frontend/public/`` instead means it is served from our own
origin, so ``script-src 'self'`` covers it, and the Vite dev server serves the
identical file. The copy is generated, not vendored: it always matches the Qt
version actually installed.

Usage::

    python scripts/sync_qwebchannel.py
"""

from __future__ import annotations

import sys
from pathlib import Path

RESOURCE = ":/qtwebchannel/qwebchannel.js"
DESTINATION = Path(__file__).resolve().parent.parent / "frontend" / "public" / "qwebchannel.js"


def main() -> int:
    from PySide6 import QtWebChannel  # noqa: F401  (registers the Qt resource)
    from PySide6.QtCore import QFile, QIODevice

    handle = QFile(RESOURCE)
    if not handle.exists():
        print(f"error: {RESOURCE} is not present in this Qt installation", file=sys.stderr)
        return 1
    if not handle.open(QIODevice.OpenModeFlag.ReadOnly):
        print(f"error: could not open {RESOURCE}", file=sys.stderr)
        return 1

    data = bytes(handle.readAll().data())
    handle.close()

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_bytes(data)

    cwd = Path.cwd()
    shown = DESTINATION.relative_to(cwd) if DESTINATION.is_relative_to(cwd) else DESTINATION
    print(f"wrote {shown} ({len(data):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
