"""Strata entry point."""

from __future__ import annotations

import sys


def main() -> int:
    # The confined yt-dlp worker (see `app.services.stream_extractor`): a frozen
    # build has no `-m`, so it re-enters here. Checked before Qt is imported -
    # the worker must never build a window or open the workspace.
    from app.services.stream_extractor import WORKER_FLAG

    if WORKER_FLAG in sys.argv[1:]:
        from app.services.stream_extractor import worker_main

        return worker_main()

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
