"""Auto-lock: idle, session lock and suspend each lock every private layer."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6.QtCore")

from app.desktop.auto_lock import (
    PBT_APMSUSPEND,
    WM_POWERBROADCAST,
    WM_WTSSESSION_CHANGE,
    AutoLock,
    idle_seconds,
    is_locking_message,
)
from app.services.container import Services


def _auto(idle: float, minutes: int = 15, system: bool = True) -> tuple[AutoLock, list[int]]:
    calls: list[int] = []

    def lock_all() -> int:
        calls.append(1)
        return 1

    lock = AutoLock(
        lock_all=lock_all,
        idle_minutes=lambda: minutes,
        on_system_lock=lambda: system,
        idle=lambda: idle,
    )
    return lock, calls


def test_locks_after_the_idle_limit_and_not_before() -> None:
    lock, calls = _auto(idle=14 * 60)
    lock.check_idle()
    assert calls == []
    lock, calls = _auto(idle=15 * 60)
    lock.check_idle()
    assert calls == [1]


def test_zero_minutes_turns_idle_locking_off() -> None:
    lock, calls = _auto(idle=10**6, minutes=0)
    lock.check_idle()
    assert calls == []


@pytest.mark.parametrize(
    ("message", "wparam", "reason"),
    [
        (WM_WTSSESSION_CHANGE, 0x7, "session"),  # Win+L
        (WM_WTSSESSION_CHANGE, 0x2, "session"),  # console disconnect
        (WM_WTSSESSION_CHANGE, 0x4, "session"),  # remote disconnect
        (WM_WTSSESSION_CHANGE, 0x8, ""),  # unlock: nothing to do
        (WM_POWERBROADCAST, PBT_APMSUSPEND, "suspend"),
        (WM_POWERBROADCAST, 0x12, ""),  # resume
        (0x0100, 0, ""),  # WM_KEYDOWN
    ],
)
def test_which_system_events_lock(message: int, wparam: int, reason: str) -> None:
    assert is_locking_message(message, wparam) == reason
    lock, calls = _auto(idle=0)
    lock.handle_native(message, wparam)
    assert calls == ([1] if reason else [])


def test_system_locking_can_be_turned_off() -> None:
    lock, calls = _auto(idle=0, system=False)
    lock.handle_native(WM_WTSSESSION_CHANGE, 0x7)
    assert calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="GetLastInputInfo is Windows-only")
def test_idle_seconds_reads_the_system() -> None:
    assert 0 <= idle_seconds() < 10**7


def test_lock_all_really_drops_the_keys(services: Services) -> None:
    services.workspace.open_or_create(services.paths.default_workspace, "T")
    layer, _ = services.workspace.create_layer("P", visibility="private", password="pw pw pw pw")
    assert services.encryption.is_unlocked(layer.id)
    assert services.workspace.lock_all_layers() == 1
    assert not services.encryption.is_unlocked(layer.id)
