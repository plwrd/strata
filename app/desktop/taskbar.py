"""Keep the main window off the taskbar (no taskbar button, no Alt-Tab entry).

On Windows the taskbar button is governed by two extended window styles:
``WS_EX_APPWINDOW`` forces one, ``WS_EX_TOOLWINDOW`` suppresses it. Setting the
tool-window bit (and clearing the app-window bit) drops the button while leaving
the ordinary window frame and controls intact — unlike Qt's ``Qt.Tool`` flag,
which also strips the title-bar buttons.

This is meant to pair with the system tray: a window with no taskbar button
needs some other way back, which the tray icon provides. It is off by default,
Windows-only, and a graceful no-op elsewhere — the same shape as
``screen_security``.

The one subtlety is that removing the button from an *already shown* window
needs a hide/show cycle for the shell to notice. So the call is idempotent: it
reads the current style and only touches the window when the bit actually has to
change. Re-applying it (from ``showEvent``, say) when it is already correct does
nothing, which is what keeps that re-apply from looping through the hide/show.
"""

from __future__ import annotations

import sys
from typing import Any, Protocol, cast

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SW_HIDE = 0
SW_SHOW = 5


class _Window(Protocol):
    def winId(self) -> object: ...
    def isVisible(self) -> bool: ...


def _is_windows() -> bool:
    return sys.platform == "win32"


def set_window_in_taskbar(window: _Window, *, shown: bool) -> bool:
    """Show or hide ``window``'s taskbar button.

    Returns True on success or a deliberate no-op (non-Windows). Idempotent:
    when the window is already in the requested state it touches nothing, so the
    hide/show cycle only runs on an actual change.
    """
    if not _is_windows():
        # macOS keeps every window in the Dock via the app, not per-window; a
        # Linux WM decides from its own hints. Neither is a per-window ex-style,
        # so this stays a Windows feature rather than a half-working one.
        logger.info("taskbar.unsupported_platform", platform=sys.platform, shown=shown)
        return True
    return _windows_set_in_taskbar(window, shown=shown)


def _windows_set_in_taskbar(window: _Window, *, shown: bool) -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    # LONG_PTR-wide getters/setters, so the 64-bit ex-style is not truncated.
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    get_long.restype = ctypes.c_ssize_t
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_long.restype = ctypes.c_ssize_t
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL

    hwnd = int(cast(Any, window.winId()))
    current = int(get_long(hwnd, GWL_EXSTYLE))
    if shown:
        # Only *undo* a hide. An ordinary top-level window already has a taskbar
        # button without `WS_EX_APPWINDOW`, so forcing that bit on changed the
        # ex-style of every default window — and the change costs a hide/show
        # cycle, which is a whole-window flash at every launch for a user who
        # never touched this setting. `WS_EX_APPWINDOW` is only needed to
        # *restore* a button that `WS_EX_TOOLWINDOW` took away.
        if not current & WS_EX_TOOLWINDOW:
            return True
        desired = (current & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
    else:
        desired = (current | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW

    if desired == current:
        return True  # already right — do nothing, so a re-apply cannot flicker or loop

    visible = window.isVisible()
    # The shell only re-reads these styles when the window is shown, so an
    # already-visible window has to be hidden and shown again for the change to
    # land. A window that is not yet visible just adopts the style on first show.
    if visible:
        user32.ShowWindow(hwnd, SW_HIDE)
    set_long(hwnd, GWL_EXSTYLE, ctypes.c_ssize_t(desired))
    if visible:
        user32.ShowWindow(hwnd, SW_SHOW)
    logger.info("taskbar.applied", shown=shown)
    return True
