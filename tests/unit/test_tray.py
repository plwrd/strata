"""System tray behaviour.

Two layers. The *policy* — does a close hide the window or really quit? — is a
pure function and is tested exhaustively without a display. The *widget* needs a
QApplication, so those tests are marked ``gui`` and skipped where Qt cannot open
one; they check the guarantees that matter: an unavailable tray never claims to
be enabled, disabling it never strands the window, and Quit really quits.
"""

from __future__ import annotations

import pytest

from app.desktop.tray import should_hide_to_tray
from app.services.settings_service import AppSettings


def test_tray_settings_default_off() -> None:
    settings = AppSettings()
    assert settings.minimize_to_tray is False
    assert settings.start_in_tray is False


@pytest.mark.parametrize(
    ("tray_enabled", "is_quitting", "expected"),
    [
        (True, False, True),  # the normal case: close hides to the tray
        (True, True, False),  # Quit from the tray menu really quits
        (False, False, False),  # no tray: a close must close, never vanish
        (False, True, False),
    ],
)
def test_close_hides_only_with_a_tray_and_no_quit(
    tray_enabled: bool, is_quitting: bool, expected: bool
) -> None:
    assert should_hide_to_tray(tray_enabled=tray_enabled, is_quitting=is_quitting) is expected


# -- the widget ----------------------------------------------------------------

pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture()
def qt_app():  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _controller(qt_app, window):  # type: ignore[no-untyped-def]
    from PySide6.QtGui import QIcon

    from app.desktop.tray import TrayController

    calls = {"show": 0, "quit": 0}
    controller = TrayController(
        icon=QIcon(),
        window=window,
        on_show=lambda: calls.__setitem__("show", calls["show"] + 1),
        on_quit=lambda: calls.__setitem__("quit", calls["quit"] + 1),
        parent=qt_app,
    )
    return controller, calls


@pytest.mark.gui
def test_an_unavailable_tray_reports_disabled(qt_app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QSystemTrayIcon, QWidget

    window = QWidget()
    controller, _calls = _controller(qt_app, window)
    # Force "no system tray on this box" regardless of the CI environment.
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))

    controller.set_enabled(True)

    assert controller.enabled is False
    controller.dispose()


@pytest.mark.gui
def test_disabling_the_tray_brings_the_window_back(qt_app) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QSystemTrayIcon, QWidget

    if not QSystemTrayIcon.isSystemTrayAvailable():
        pytest.skip("no system tray in this environment")

    window = QWidget()
    controller, calls = _controller(qt_app, window)
    controller.set_enabled(True)

    controller.hide_to_tray()  # window now off the taskbar
    controller.set_enabled(False)  # turning it off must un-strand the window

    assert calls["show"] >= 1
    controller.dispose()


@pytest.mark.gui
def test_quit_calls_back_to_the_window(qt_app) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QSystemTrayIcon, QWidget

    if not QSystemTrayIcon.isSystemTrayAvailable():
        pytest.skip("no system tray in this environment")

    window = QWidget()
    controller, calls = _controller(qt_app, window)
    controller.set_enabled(True)

    controller._quit()

    assert calls["quit"] == 1
    controller.dispose()
