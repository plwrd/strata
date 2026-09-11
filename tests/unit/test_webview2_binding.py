"""The hand-written COM binding for WebView2.

These are the parts where a mistake is not an exception but a corrupted
process, so they are pinned directly: GUID parsing, the QueryInterface contract
of a callback we hand to the browser, the two halves of COM string ownership,
and the environment options that carry the capture-safety flags.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pytest

from app.desktop.webview2 import com, sdk

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="WebView2 is Windows-only")


# -- GUIDs -------------------------------------------------------------------


def test_guid_parses_the_sdk_form() -> None:
    parsed = com.guid("2fde08a8-1e9a-4766-8c05-95a9ceb9d1c5")
    assert parsed.Data1 == 0x2FDE08A8
    assert parsed.Data2 == 0x1E9A
    assert parsed.Data3 == 0x4766
    assert bytes(parsed.Data4) == bytes.fromhex("8c0595a9ceb9d1c5")


def test_guid_accepts_braces_and_case() -> None:
    assert bytes(com.guid("{2FDE08A8-1E9A-4766-8C05-95A9CEB9D1C5}")) == bytes(
        com.guid("2fde08a8-1e9a-4766-8c05-95a9ceb9d1c5")
    )


def test_guid_rejects_a_non_guid() -> None:
    with pytest.raises(ValueError):
        com.guid("not-a-guid")


# -- the IUnknown contract ---------------------------------------------------


def _new_callback() -> com.Callback:
    invoke = ctypes.WINFUNCTYPE(com.HRESULT, com.LPVOID, com.HRESULT, com.LPVOID)
    return com.Callback(sdk.slots.IID_EXECUTE_SCRIPT_COMPLETED_HANDLER, (invoke, lambda *a: 0))


@windows_only
def test_callback_answers_query_interface_for_itself_and_iunknown() -> None:
    callback = _new_callback()
    handle = com.Interface(callback.pointer)

    assert handle.query_interface(sdk.slots.IID_EXECUTE_SCRIPT_COMPLETED_HANDLER)
    assert handle.query_interface(com.IID_IUNKNOWN)


@windows_only
def test_callback_refuses_an_interface_it_does_not_implement() -> None:
    """Saying yes here is how a binding hands the browser the wrong vtable."""
    callback = _new_callback()

    assert not com.Interface(callback.pointer).query_interface(sdk.slots.IID_SETTINGS2)


@windows_only
def test_callback_pointer_is_stable() -> None:
    """WebView2 keeps the pointer; it must not move between reads."""
    callback = _new_callback()

    assert callback.pointer == callback.pointer


# -- string ownership --------------------------------------------------------


@windows_only
def test_allocated_string_round_trips_and_is_freed() -> None:
    """The two halves of the COM convention: we allocate, the caller frees."""
    buffer = com.alloc_string("héllo — wörld")

    assert com.take_string(com.LPVOID(buffer)) == "héllo — wörld"


@windows_only
def test_allocated_string_survives_a_64_bit_address() -> None:
    """Regression: an undeclared restype truncates the pointer to 32 bits.

    The failure mode is an access violation on the first write, and it only
    reproduces once the allocator hands back an address above 4 GiB — so the
    assertion is on the pointer itself, not on a round-trip that might get lucky.
    """
    buffer = com.alloc_string("x" * 64)

    assert buffer > 0
    assert buffer == buffer & 0xFFFFFFFFFFFFFFFF
    com.take_string(com.LPVOID(buffer))


def test_empty_string_needs_no_free() -> None:
    assert com.take_string(com.LPVOID(0)) == ""


# -- calling into nothing ----------------------------------------------------


def test_a_null_interface_is_falsy() -> None:
    assert not com.Interface(0)
    assert not com.Interface(None)


def test_calling_a_null_interface_raises_rather_than_faults() -> None:
    with pytest.raises(com.ComError):
        com.Interface(0).call(3, what="Navigate")


def test_query_interface_on_null_returns_null() -> None:
    assert not com.Interface(0).query_interface(com.IID_IUNKNOWN)


# -- environment options -----------------------------------------------------


def test_capture_safe_arguments_keep_video_off_an_overlay_plane() -> None:
    """The whole reason the pane can be hidden from a recording at all.

    Display affinity is enforced by the compositor, so a video handed to a
    hardware overlay plane is composed beside it and escapes the exclusion.
    """
    assert "--disable-direct-composition-video-overlays" in sdk.CAPTURE_SAFE_ARGUMENTS


@windows_only
def test_options_report_the_browser_arguments_through_com() -> None:
    """Read back the way WebView2 reads them — through the vtable, not the object."""
    options = sdk._EnvironmentOptions("--flag-one --flag-two")

    handle = com.Interface(options.pointer)
    reported = handle.get_string(
        sdk.slots.ENVIRONMENT_OPTIONS_GET_ADDITIONALBROWSERARGUMENTS, "args"
    )

    assert reported == "--flag-one --flag-two"


@windows_only
def test_options_report_a_parseable_compatible_version() -> None:
    """Regression: an empty version fails the whole environment with E_INVALIDARG.

    WebView2 compares the installed runtime against this string, and "" is not
    a version. The symptom is creation failing before any window exists, which
    looks like "WebView2 is not installed" and is not.
    """
    options = sdk._EnvironmentOptions("")

    reported = com.Interface(options.pointer).get_string(
        sdk.slots.ENVIRONMENT_OPTIONS_GET_TARGETCOMPATIBLEBROWSERVERSION, "version"
    )

    assert reported.count(".") == 3
    assert all(part.isdigit() for part in reported.split("."))


def test_generated_target_version_is_present() -> None:
    assert sdk.slots.TARGET_COMPATIBLE_BROWSER_VERSION


# -- availability ------------------------------------------------------------


def test_everything_is_unavailable_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sdk.sys, "platform", "linux")

    assert sdk.runtime_version() == ""
    assert sdk.loader_path(Path("/nowhere")) is None
    with pytest.raises(sdk.WebView2Unavailable):
        sdk.create_environment(
            user_data_folder=Path("/nowhere"),
            loader=Path("/nowhere/WebView2Loader.dll"),
            on_ready=lambda env, err: None,
        )


@windows_only
def test_loader_is_found_in_the_source_tree() -> None:
    """A checkout keeps the redistributable under ``packaging/webview2``."""
    root = Path(__file__).resolve().parents[2]

    found = sdk.loader_path(root)

    assert found is not None
    assert found.name == sdk.LOADER_NAME


@windows_only
def test_a_missing_loader_is_reported_not_guessed(tmp_path: pytest.TempPathFactory) -> None:
    with pytest.raises(sdk.WebView2Unavailable, match=sdk.LOADER_NAME):
        sdk.create_environment(
            user_data_folder=Path(str(tmp_path)) / "data",
            loader=Path(str(tmp_path)) / "absent" / sdk.LOADER_NAME,
            on_ready=lambda env, err: None,
        )
