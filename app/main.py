"""Strata entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from app.desktop.application import create_application

    app, window = create_application()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
