"""Lock every private layer without anyone having to remember to.

Locking is what takes the keys out of memory, so it should not depend on the
user pressing a button before walking away. Three triggers:

* **Idle** — no keyboard or mouse input *anywhere on the system* for
  ``auto_lock_minutes`` (``GetLastInputInfo``; 0 turns it off). System-wide,
  not app-wide: someone reading in another app is not idle.
* **The OS session locks or disconnects** — Win+L, a remote session dropping,
  fast user switching (``WTSRegisterSessionNotification``).
* **Sleep / hibernate** — ``PBT_APMSUSPEND``: a sleeping laptop's RAM (and a
  hibernation file) should not hold unlocked keys.

The last two are ``auto_lock_on_system_lock``. Both settings are read each time,
so a change in Settings applies without a restart.

What locking does *not* do: log the user out of websites.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

CHECK_INTERVAL_MS = 30_000

WM_WTSSESSION_CHANGE = 0x02B1
WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
# WTS_CONSOLE_DISCONNECT, WTS_REMOTE_DISCONNECT, WTS_SESSION_LOGOFF, WTS_SESSION_LOCK
LOCKING_SESSION_EVENTS = frozenset({0x2, 0x4, 0x6, 0x7})
_NOTIFY_FOR_THIS_SESSION = 0


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input on this session; 0 if unknown."""
    if sys.platform != "win32":
        return 0.0
    info = _LASTINPUTINFO(cbSize=ctypes.sizeof(_LASTINPUTINFO))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return float((int(now) - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0


def is_locking_message(message: int, wparam: int) -> str:
    """The reason a native window message should lock, or ""."""
    if message == WM_WTSSESSION_CHANGE and wparam in LOCKING_SESSION_EVENTS:
        return "session"
    if message == WM_POWERBROADCAST and wparam == PBT_APMSUSPEND:
        return "suspend"
    return ""


class AutoLock(QObject):
    """Owns the idle timer; the window forwards its native messages here."""

    def __init__(
        self,
        *,
        lock_all: Callable[[], int],
        idle_minutes: Callable[[], int],
        on_system_lock: Callable[[], bool],
        idle: Callable[[], float] = idle_seconds,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._lock_all = lock_all
        self._idle_minutes = idle_minutes
        self._on_system_lock = on_system_lock
        self._idle = idle
        self._registered_hwnd = 0
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check_idle)
        self._timer.start()

    def register(self, hwnd: int) -> None:
        """Ask Windows to tell this window about session lock/disconnect."""
        if sys.platform != "win32" or not hwnd or hwnd == self._registered_hwnd:
            return
        if ctypes.windll.wtsapi32.WTSRegisterSessionNotification(
            wintypes.HWND(hwnd), _NOTIFY_FOR_THIS_SESSION
        ):
            self._registered_hwnd = hwnd
        else:  # pragma: no cover - only fails on a broken session
            logger.warning("auto_lock.session_notification_unavailable")

    def unregister(self) -> None:
        if self._registered_hwnd and sys.platform == "win32":
            ctypes.windll.wtsapi32.WTSUnRegisterSessionNotification(
                wintypes.HWND(self._registered_hwnd)
            )
        self._registered_hwnd = 0
        self._timer.stop()

    def check_idle(self) -> None:
        minutes = int(self._idle_minutes())
        if minutes > 0 and self._idle() >= minutes * 60:
            self._lock("idle")

    def handle_native(self, message: int, wparam: int) -> None:
        reason = is_locking_message(message, wparam)
        if reason and self._on_system_lock():
            self._lock(reason)

    def _lock(self, reason: str) -> None:
        count = self._lock_all()
        if count:
            logger.info("auto_lock.locked", reason=reason, layers=count)
