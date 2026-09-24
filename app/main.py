"""Strata entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from app.desktop.application import create_application

    app, window = create_application()
    # start_in_tray launches without a window: the tray icon is the only thing
    # on screen until the user opens it. The window is built either way, so the
    # workspace is open and ready behind the icon.
    if not window.start_hidden():
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
