"""What the Qt pane has instead of extensions.

Qt WebEngine cannot load a Chrome extension at any price — Chromium's
extensions subsystem is not compiled into it and no flag adds it. These cover
the two things it *can* do, which between them are most of what people install
extensions for: userscripts injected into the page, and refusing requests to
hosts the user named.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtWebEngineCore")

from PySide6.QtWebEngineCore import QWebEngineScript, QWebEngineUrlRequestInfo

from app.desktop.browser_pane import HostBlockInterceptor, _run_at
from app.services.settings_service import AppSettings

POINT = QWebEngineScript.InjectionPoint


# -- when a userscript runs ----------------------------------------------------


def test_a_script_without_metadata_runs_once_the_page_is_there() -> None:
    """The ecosystem default is `document-idle`, and scripts are written for it.

    A naive script's first line is usually `document.documentElement…`, which
    is null at document-creation. Defaulting to the early point would break the
    majority to suit the few.
    """
    assert _run_at("console.log('hi')") == POINT.Deferred


def test_a_script_can_ask_to_run_before_the_page_does() -> None:
    """The minority case that document-start exists for: patching globals."""
    source = "// ==UserScript==\n// @run-at  document-start\n// ==/UserScript==\nx()"

    assert _run_at(source) == POINT.DocumentCreation


def test_document_end_maps_to_the_ready_point() -> None:
    assert _run_at("// @run-at document-end\n") == POINT.DocumentReady


def test_an_unknown_run_at_falls_back_to_the_default() -> None:
    assert _run_at("// @run-at whenever\n") == POINT.Deferred


def test_only_the_metadata_block_is_read() -> None:
    """A mention far down the file is a comment about `@run-at`, not a directive."""
    source = "x()\n" + ("// filler\n" * 900) + "// @run-at document-start\n"

    assert len(source) > 4096
    assert _run_at(source) == POINT.Deferred


# -- what the blocklist blocks -------------------------------------------------


class FakeRequest:
    """Stands in for ``QWebEngineUrlRequestInfo``, which Qt will not construct."""

    def __init__(self, url: str, *, main_frame: bool = False) -> None:
        from PySide6.QtCore import QUrl

        self._url = QUrl(url)
        self._main_frame = main_frame
        self.blocked = False

    def requestUrl(self) -> Any:
        return self._url

    def resourceType(self) -> Any:
        kinds = QWebEngineUrlRequestInfo.ResourceType
        return kinds.ResourceTypeMainFrame if self._main_frame else kinds.ResourceTypeImage

    def block(self, value: bool) -> None:
        self.blocked = value


def _intercept(hosts: tuple[str, ...], url: str, *, main_frame: bool = False) -> bool:
    interceptor = HostBlockInterceptor(hosts)
    request = FakeRequest(url, main_frame=main_frame)
    interceptor.interceptRequest(request)  # type: ignore[arg-type]
    return request.blocked


def test_a_listed_host_is_refused() -> None:
    assert _intercept(("ads.example.com",), "https://ads.example.com/tag.js") is True


def test_a_subdomain_of_a_listed_host_is_refused() -> None:
    """`example.com` has to cover `ads.example.com`, or a list is unusable."""
    assert _intercept(("example.com",), "https://ads.example.com/tag.js") is True


def test_matching_stops_at_a_label_boundary() -> None:
    """Regression shape: a suffix test without the dot blocks `notevil.com`."""
    assert _intercept(("evil.com",), "https://notevil.com/app.js") is False


def test_an_unlisted_host_is_left_alone() -> None:
    assert _intercept(("ads.example.com",), "https://cdn.example.com/app.js") is False


def test_an_empty_list_blocks_nothing() -> None:
    assert _intercept((), "https://ads.example.com/tag.js") is False


def test_the_page_you_navigated_to_is_never_blocked() -> None:
    """Blocking a typed navigation gives a blank pane and no explanation.

    That reads as a broken browser rather than a blocklist working; ads and
    trackers are sub-resources anyway.
    """
    assert _intercept(("example.com",), "https://example.com/", main_frame=True) is False


def test_blocked_requests_are_counted() -> None:
    interceptor = HostBlockInterceptor(("ads.example.com",))
    for url in ("https://ads.example.com/a", "https://ads.example.com/b", "https://ok.com/c"):
        interceptor.interceptRequest(FakeRequest(url))  # type: ignore[arg-type]

    assert interceptor.blocked == 2
    assert interceptor.host_count == 1


# -- the settings that feed them -----------------------------------------------


def test_both_lists_start_empty() -> None:
    settings = AppSettings()

    assert settings.browser_user_scripts == []
    assert settings.browser_blocked_hosts == []


def test_a_pasted_url_is_reduced_to_its_host() -> None:
    """People paste what they copied. A kept-as-is entry blocks nothing while
    looking as though it works."""
    settings = AppSettings(
        browser_blocked_hosts=["https://Ads.Example.com/tag.js", "http://x.io:8080/p"]
    )

    assert settings.browser_blocked_hosts == ["ads.example.com", "x.io"]


def test_blocked_hosts_are_deduplicated_after_normalising() -> None:
    settings = AppSettings(browser_blocked_hosts=["ads.example.com", "https://ads.example.com/"])

    assert settings.browser_blocked_hosts == ["ads.example.com"]


def test_user_scripts_keep_their_paths() -> None:
    settings = AppSettings(browser_user_scripts=["  C:/s/a.js  ", "C:/s/a.js", "C:/s/b.js"])

    assert settings.browser_user_scripts == ["C:/s/a.js", "C:/s/b.js"]


def test_a_missing_script_is_kept_so_the_pane_can_report_it(tmp_path: Path) -> None:
    """Validation does not touch the disk: a settings file must load on a
    machine where a script has moved, and be told about it by the pane."""
    settings = AppSettings(browser_user_scripts=[str(tmp_path / "gone.js")])

    assert settings.browser_user_scripts == [str(tmp_path / "gone.js")]


def test_the_lists_are_capped() -> None:
    with pytest.raises(ValueError, match="at most"):
        AppSettings(browser_user_scripts=[f"C:/s/{index}.js" for index in range(100)])
