"""Filesystem watching.

The Markdown file is the source of truth, which is only true if editing it in
another editor actually changes what Strata shows. This service watches the
workspace and emits a coalesced ``changed`` signal.

Coalescing matters: a single save from an editor can produce three or four
inotify/ReadDirectoryChanges events (write, rename of a temp file, attribute
change), and re-reading the whole tree four times per keystroke is how a file
watcher becomes a performance bug.

Privacy: the event payload carries the *origin* ("strata" or "external"), never
a path — a path would leak a private layer's structure into the frontend, the log
and any crash report that captured it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

COALESCE_MS = 250

IGNORED_PARTS = frozenset({".strata", ".git", "node_modules", "__pycache__"})
IGNORED_SUFFIXES = (".tmp", ".swp", "~", ".json.tmp")


class _Handler(FileSystemEventHandler):
    def __init__(self, service: WatchService) -> None:
        self._service = service

    def on_any_event(self, event: FileSystemEvent) -> None:
        path = Path(str(event.src_path))
        if any(part in IGNORED_PARTS for part in path.parts):
            return
        if str(path).endswith(IGNORED_SUFFIXES):
            return
        self._service.notify_external()


class WatchService(QObject):
    """Emits ``changed(origin)`` when the workspace changes on disk."""

    changed = Signal(str)

    # Internal: emitted from whatever thread noticed the change. Emitting a signal
    # across threads is safe; touching the QTimer directly is not, so every path
    # into `_schedule` goes through this queued hop.
    _bump = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._observer: BaseObserver | None = None
        self._pending: str | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COALESCE_MS)
        self._timer.timeout.connect(self._flush)

        self._bump.connect(self._schedule, Qt.ConnectionType.QueuedConnection)

    def start(self, root: Path) -> None:
        self.stop()
        if not root.is_dir():
            return
        observer = Observer()
        observer.schedule(_Handler(self), str(root), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        logger.info("watch.started")

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer = None
            logger.info("watch.stopped")

    def announce(self, origin: str = "strata") -> None:
        """Announce a change Strata itself made."""
        self._bump.emit(origin)

    def notify_external(self) -> None:
        """Called from the watchdog thread; the flush happens on the Qt thread."""
        self._bump.emit("external")

    @Slot(str)  # type: ignore[arg-type]
    def _schedule(self, origin: str) -> None:
        # An external edit is the more interesting of the two, so it wins a tie.
        if self._pending != "external":
            self._pending = origin
        if not self._timer.isActive():
            self._timer.start()

    def _flush(self) -> None:
        origin = self._pending or "strata"
        self._pending = None
        self.changed.emit(origin)
