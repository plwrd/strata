"""The WebView2 objects Strata drives: an environment, a controller, a webview.

Why WebView2 is here at all: the Qt WebEngine that ships in the PySide6 wheel is
built without proprietary codecs — measured against the installed engine, not
assumed, by ``tests/e2e/test_codec_rationale.py`` — so ``video/mp4;
codecs="avc1…"`` and AAC do not play in the research pane. The workaround before
this was to drive the user's *own* Chrome over the DevTools protocol, and that
cannot be excluded from screen capture: it is someone else's browser, and Strata
cannot follow every window it goes on to open.

WebView2 is Edge's Chromium, hosted **inside Strata's own window**. That buys
the codecs and, more importantly, puts the surface back under a window whose
display affinity Strata controls.

What it does not buy is protection for free. It is the same Chromium
compositor, so the overlay flags in :data:`CAPTURE_SAFE_ARGUMENTS` still matter,
and its popups live in a ``msedgewebview2.exe`` of their own — a process Strata
launches and can therefore sweep by PID, which is exactly what the Chrome
backend could not promise.

Everything here is asynchronous because the underlying API is: creation,
navigation and script evaluation each complete on a callback delivered to the
thread's message loop. In the app that loop is Qt's, so the callbacks arrive on
the Qt thread and may touch widgets directly.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any, Protocol

from app.desktop.capture_flags import CAPTURE_SAFE_ARGUMENTS as _SHARED_CAPTURE_ARGUMENTS
from app.desktop.capture_flags import (
    DISABLE_DIRECT_COMPOSITION_ARGUMENT as _DISABLE_DIRECT_COMPOSITION_ARGUMENT,
)
from app.desktop.capture_flags import SOFTWARE_DECODE_ARGUMENT as _SOFTWARE_DECODE_ARGUMENT
from app.desktop.webview2 import _slots as slots
from app.desktop.webview2.com import (
    BOOL,
    E_POINTER,
    HRESULT,
    LPVOID,
    RECT,
    S_OK,
    Callback,
    ComError,
    EventRegistrationToken,
    Interface,
    alloc_string,
    co_initialize,
    take_string,
)
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

LOADER_NAME = "WebView2Loader.dll"

# Registry keys the Evergreen runtime installer writes. Per-machine first, then
# per-user, matching the order the loader itself resolves them.
_RUNTIME_KEYS = (
    (
        0x80000002,  # HKEY_LOCAL_MACHINE
        r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
        r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    ),
    (
        0x80000002,
        r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    ),
    (
        0x80000001,  # HKEY_CURRENT_USER
        r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    ),
)

# Passed to the browser process at environment creation. These are the same
# levers `app/desktop/application.py` pulls for Qt WebEngine, and for the same
# reason: `SetWindowDisplayAffinity` is enforced by DWM, so a video promoted to
# a hardware overlay plane is composed beside DWM's output and escapes the
# exclusion entirely — which also makes the window flicker as the overlay is
# taken and released. Keeping video on the composited path costs a little GPU
# and keeps every frame inside the exclusion.
# The compositor half is shared with every other Chromium Strata runs (see
# `app.desktop.capture_flags` for why it is one list). The rest is WebView2's
# own: Edge's out-of-process UI surfaces would otherwise put PDF and SmartScreen
# chrome in windows of a process Strata does not own — and a window Strata
# cannot account for is a window it cannot exclude.
CAPTURE_SAFE_ARGUMENTS = (
    *_SHARED_CAPTURE_ARGUMENTS,
    "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection",
)
SOFTWARE_DECODE_ARGUMENT = _SOFTWARE_DECODE_ARGUMENT
DISABLE_DIRECT_COMPOSITION_ARGUMENT = _DISABLE_DIRECT_COMPOSITION_ARGUMENT


class WebView2Unavailable(Exception):
    """No usable WebView2 on this machine — the caller should fall back."""


def _is_windows() -> bool:
    """A function, not an inline literal — see ``screen_security`` for why."""
    return sys.platform == "win32"


def runtime_version() -> str:
    """The installed Evergreen runtime version, or "" if there is none.

    Read from the registry rather than by attempting a load, because the answer
    decides whether the pane offers WebView2 at all and that decision is made
    before any window exists.
    """
    if not _is_windows():
        return ""
    import winreg

    for hive, path in _RUNTIME_KEYS:
        try:
            with winreg.OpenKey(hive, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if version and str(version) != "0.0.0.0":  # noqa: S104 - a version, not an address
            return str(version)
    return ""


def loader_path(resource_root: Path | None = None) -> Path | None:
    """Find ``WebView2Loader.dll``: shipped beside the app, or on the DLL path.

    The loader is a ~166 KB shim that finds and starts the installed runtime; it
    is the only WebView2 file Strata ships. A frozen build carries it next to
    the executable, a source checkout keeps it under ``packaging/webview2``.
    """
    if not _is_windows():
        return None
    candidates: list[Path] = []
    if resource_root is not None:
        candidates += [
            resource_root / LOADER_NAME,
            resource_root / "packaging" / "webview2" / LOADER_NAME,
        ]
    candidates.append(Path(sys.executable).parent / LOADER_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


# Handler prototypes. The signature of each `Invoke` comes from WebView2.h; the
# arguments are all pointer-width, so a mismatch here is caught by the argument
# count rather than silently reading the wrong register.
_INVOKE_HRESULT_PTR = ctypes.WINFUNCTYPE(HRESULT, LPVOID, HRESULT, LPVOID)
_INVOKE_HRESULT_STR = ctypes.WINFUNCTYPE(HRESULT, LPVOID, HRESULT, wintypes.LPCWSTR)
_INVOKE_SENDER_ARGS = ctypes.WINFUNCTYPE(HRESULT, LPVOID, LPVOID, LPVOID)
_GET_STRING = ctypes.WINFUNCTYPE(HRESULT, LPVOID, ctypes.POINTER(LPVOID))
_PUT_STRING = ctypes.WINFUNCTYPE(HRESULT, LPVOID, wintypes.LPCWSTR)
_GET_BOOL = ctypes.WINFUNCTYPE(HRESULT, LPVOID, ctypes.POINTER(BOOL))
_PUT_BOOL = ctypes.WINFUNCTYPE(HRESULT, LPVOID, BOOL)
_INVOKE_HRESULT = ctypes.WINFUNCTYPE(HRESULT, LPVOID, HRESULT)

# COREWEBVIEW2_PERMISSION_STATE: DEFAULT = 0, ALLOW = 1, DENY = 2.
COREWEBVIEW2_PERMISSION_STATE_DENY = 2
# COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL: documents, media, images, everything.
WEB_RESOURCE_CONTEXT_ALL = 0
# COREWEBVIEW2_KEY_EVENT_KIND: KEY_DOWN = 0, KEY_UP = 1, SYSTEM_KEY_DOWN = 2, ...
KEY_EVENT_KEY_DOWN = 0
KEY_EVENT_SYSTEM_KEY_DOWN = 2

_S_FALSE = 1
_E_NOTIMPL = 0x80004001 - 0x100000000
_E_FAIL = 0x80004005 - 0x100000000
_STG_E_ACCESSDENIED = 0x80030005 - 0x100000000
_STG_E_INVALIDFUNCTION = 0x80030001 - 0x100000000

IID_ISEQUENTIAL_STREAM = "0c733a30-2a1c-11ce-ade5-00aa0044773d"
IID_ISTREAM = "0000000c-0000-0000-c000-000000000046"
# A marker interface with no methods: "call me from any thread". Without it
# COM may marshal every Read back to the UI thread, so a playing video would
# decrypt on the thread that also draws the editor.
IID_IAGILE_OBJECT = "94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90"


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _STATSTG(ctypes.Structure):
    _fields_ = [
        ("pwcsName", LPVOID),
        ("type", ctypes.c_uint32),
        ("cbSize", ctypes.c_uint64),
        ("mtime", _FILETIME),
        ("ctime", _FILETIME),
        ("atime", _FILETIME),
        ("grfMode", ctypes.c_uint32),
        ("grfLocksSupported", ctypes.c_uint32),
        ("clsid", ctypes.c_ubyte * 16),
        ("grfStateBits", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


_READ = ctypes.WINFUNCTYPE(HRESULT, LPVOID, LPVOID, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
_WRITE = ctypes.WINFUNCTYPE(HRESULT, LPVOID, LPVOID, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
_SEEK = ctypes.WINFUNCTYPE(
    HRESULT, LPVOID, ctypes.c_longlong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulonglong)
)
_SET_SIZE = ctypes.WINFUNCTYPE(HRESULT, LPVOID, ctypes.c_ulonglong)
_COPY_TO = ctypes.WINFUNCTYPE(
    HRESULT,
    LPVOID,
    LPVOID,
    ctypes.c_ulonglong,
    ctypes.POINTER(ctypes.c_ulonglong),
    ctypes.POINTER(ctypes.c_ulonglong),
)
_COMMIT = ctypes.WINFUNCTYPE(HRESULT, LPVOID, ctypes.c_ulong)
_REVERT = ctypes.WINFUNCTYPE(HRESULT, LPVOID)
_LOCK_REGION = ctypes.WINFUNCTYPE(
    HRESULT, LPVOID, ctypes.c_ulonglong, ctypes.c_ulonglong, ctypes.c_ulong
)
_STAT = ctypes.WINFUNCTYPE(HRESULT, LPVOID, ctypes.POINTER(_STATSTG), ctypes.c_ulong)
_CLONE = ctypes.WINFUNCTYPE(HRESULT, LPVOID, ctypes.POINTER(LPVOID))


class StreamBody(Protocol):
    """What a :class:`ReadStream` reads from: a size and random access.

    The web archive's decrypting reader is one of these, so the engine pulls
    plaintext through memory a chunk at a time - there is never a decrypted
    file for it to read instead.
    """

    @property
    def size(self) -> int: ...

    def read_at(self, offset: int, length: int) -> bytes: ...

    def close(self) -> None: ...


# Streams WebView2 still holds, and ones it has let go of. A released stream is
# not freed at once: its Release thunk may still be returning on the engine's
# thread, so it is kept a few seconds more and then dropped (`_prune_streams`).
_LIVE_STREAMS: set[ReadStream] = set()
_DEAD_STREAMS: list[tuple[float, ReadStream]] = []
_STREAMS_LOCK = threading.Lock()
_DEAD_GRACE_SECONDS = 10.0


def _prune_streams() -> None:
    cutoff = time.monotonic() - _DEAD_GRACE_SECONDS
    with _STREAMS_LOCK:
        _DEAD_STREAMS[:] = [(when, stream) for when, stream in _DEAD_STREAMS if when > cutoff]


class ReadStream(Callback):
    """A read-only, seekable ``IStream`` over a :class:`StreamBody`.

    This is how a saved video reaches the player without a plaintext file:
    WebView2 calls ``Read`` and ``Seek`` on it - from its own threads - and
    each ``Read`` decrypts just the bytes asked for. Reference counting is
    real here (unlike the completion handlers, which live as long as their
    owner): when the engine's last reference goes, the body is closed.
    """

    _body: StreamBody
    _position: int
    _lock: threading.Lock
    _closed: bool

    @classmethod
    def over(cls, body: StreamBody) -> ReadStream:
        _prune_streams()
        stream = cls.__new__(cls)
        stream._body = body
        stream._position = 0
        stream._lock = threading.Lock()
        stream._closed = False
        read_write = ((_READ, stream._read), (_WRITE, stream._write))
        full = (
            *read_write,
            (_SEEK, stream._seek),
            (_SET_SIZE, stream._set_size),
            (_COPY_TO, stream._copy_to),
            (_COMMIT, stream._commit),
            (_REVERT, stream._revert),
            (_LOCK_REGION, stream._lock_region),
            (_LOCK_REGION, stream._lock_region),  # UnlockRegion: same shape
            (_STAT, stream._stat),
            (_CLONE, stream._clone),
        )
        stream._build(
            [
                (IID_ISTREAM, full),
                (IID_ISEQUENTIAL_STREAM, read_write),
                (IID_IAGILE_OBJECT, ()),
            ]
        )
        with _STREAMS_LOCK:
            _LIVE_STREAMS.add(stream)
        return stream

    # -- reference counting ---------------------------------------------------

    def _add_ref(self, _this: int) -> int:
        with self._lock:
            self._refs += 1
            return self._refs

    def _release(self, _this: int) -> int:
        with self._lock:
            self._refs = max(0, self._refs - 1)
            remaining = self._refs
        if remaining == 0:
            self._finish()
        return remaining

    def release_own(self) -> None:
        """Drop the reference Python took at creation."""
        self._release(0)

    def _finish(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._body.close()
        except Exception:  # pragma: no cover - closing must not fault the engine
            logger.exception("webview2.stream_close_failed")
        with _STREAMS_LOCK:
            _LIVE_STREAMS.discard(self)
            _DEAD_STREAMS.append((time.monotonic(), self))

    # -- IStream --------------------------------------------------------------

    def _read(self, _this: int, buffer: int, wanted: int, read: Any) -> int:
        try:
            with self._lock:
                start = self._position
            data = self._body.read_at(start, int(wanted))
            if data:
                ctypes.memmove(buffer, data, len(data))
            with self._lock:
                self._position = start + len(data)
            if read:
                read[0] = len(data)
            return S_OK if len(data) == wanted else _S_FALSE
        except Exception:
            # Locked mid-playback, or the file went away: an error, never
            # stale or partial plaintext presented as the real thing.
            if read:
                read[0] = 0
            return _E_FAIL

    def _write(self, _this: int, _buffer: int, _size: int, written: Any) -> int:
        if written:
            written[0] = 0
        return _STG_E_ACCESSDENIED

    def _seek(self, _this: int, move: int, origin: int, new_position: Any) -> int:
        with self._lock:
            base = {0: 0, 1: self._position, 2: self._body.size}.get(int(origin))
            if base is None:
                return _STG_E_INVALIDFUNCTION
            target = base + int(move)
            if target < 0:
                return _STG_E_INVALIDFUNCTION
            self._position = target
        if new_position:
            new_position[0] = target
        return S_OK

    def _set_size(self, _this: int, _size: int) -> int:
        return _STG_E_ACCESSDENIED

    def _copy_to(self, _this: int, _target: int, _size: int, _read: Any, _written: Any) -> int:
        return _E_NOTIMPL

    def _commit(self, _this: int, _flags: int) -> int:
        return S_OK

    def _revert(self, _this: int) -> int:
        return S_OK

    def _lock_region(self, _this: int, _offset: int, _size: int, _kind: int) -> int:
        return _STG_E_INVALIDFUNCTION

    def _stat(self, _this: int, stat: Any, _flag: int) -> int:
        if not stat:
            return E_POINTER
        ctypes.memset(stat, 0, ctypes.sizeof(_STATSTG))
        stat[0].type = 2  # STGTY_STREAM
        stat[0].cbSize = int(self._body.size)
        return S_OK

    def _clone(self, _this: int, out: Any) -> int:
        if out:
            out[0] = None
        return _E_NOTIMPL


class VaultReply:
    """What the pane hands back for an intercepted request."""

    def __init__(self, status: int, reason: str, headers: str, body: StreamBody) -> None:
        self.status = status
        self.reason = reason
        self.headers = headers
        self.body = body


class _EnvironmentOptions:
    """``ICoreWebView2EnvironmentOptions``, implemented here.

    The SDK's version of this is a C++ helper class in a header, not something
    the loader can hand us, so a caller from another language has to provide it.
    It exists solely to carry :data:`CAPTURE_SAFE_ARGUMENTS` into the browser
    process — which is why it is not optional despite looking like boilerplate.
    """

    def __init__(self, arguments: str, *, extensions_enabled: bool = False) -> None:
        self._arguments = arguments
        self._extensions_enabled = extensions_enabled
        self._language = ""
        # Must be a parseable version, not "". WebView2 compares the installed
        # runtime against it and rejects the whole environment with
        # E_INVALIDARG otherwise — the SDK's C++ helper seeds the same value.
        self._target_version = slots.TARGET_COMPATIBLE_BROWSER_VERSION
        self._single_sign_on = False
        # Two interfaces on one object. Extensions are opted into through
        # `…Options6`, which the runtime reaches by QueryInterface on the very
        # object we pass to creation — there is no other way in, and it must be
        # decided here because the browser process is configured once.
        self._callback = Callback.implementing(
            (
                slots.IID_ENVIRONMENT_OPTIONS,
                (
                    (_GET_STRING, self._get_arguments),
                    (_PUT_STRING, self._put_arguments),
                    (_GET_STRING, self._get_language),
                    (_PUT_STRING, self._put_language),
                    (_GET_STRING, self._get_target_version),
                    (_PUT_STRING, self._put_target_version),
                    (_GET_BOOL, self._get_sso),
                    (_PUT_BOOL, self._put_sso),
                ),
            ),
            (
                slots.IID_ENVIRONMENT_OPTIONS6,
                (
                    (_GET_BOOL, self._get_extensions_enabled),
                    (_PUT_BOOL, self._put_extensions_enabled),
                ),
            ),
        )

    @property
    def pointer(self) -> int:
        return self._callback.pointer

    def _get_arguments(self, _this: int, out: Any) -> int:
        out[0] = alloc_string(self._arguments)
        return 0

    def _put_arguments(self, _this: int, value: str | None) -> int:
        self._arguments = value or ""
        return 0

    def _get_language(self, _this: int, out: Any) -> int:
        out[0] = alloc_string(self._language)
        return 0

    def _put_language(self, _this: int, value: str | None) -> int:
        self._language = value or ""
        return 0

    def _get_target_version(self, _this: int, out: Any) -> int:
        out[0] = alloc_string(self._target_version)
        return 0

    def _put_target_version(self, _this: int, value: str | None) -> int:
        self._target_version = value or ""
        return 0

    def _get_sso(self, _this: int, out: Any) -> int:
        out[0] = 1 if self._single_sign_on else 0
        return 0

    def _put_sso(self, _this: int, value: int) -> int:
        self._single_sign_on = bool(value)
        return 0

    def _get_extensions_enabled(self, _this: int, out: Any) -> int:
        out[0] = 1 if self._extensions_enabled else 0
        return 0

    def _put_extensions_enabled(self, _this: int, value: int) -> int:
        self._extensions_enabled = bool(value)
        return 0


class BrowserExtension:
    """One loaded extension, as the runtime reports it back."""

    def __init__(self, interface: Interface) -> None:
        self._it = interface
        self._handlers: list[Any] = []
        self.id = interface.get_string(slots.BROWSER_EXTENSION_GET_ID, "get_Id")
        self.name = interface.get_string(slots.BROWSER_EXTENSION_GET_NAME, "get_Name")
        self.enabled = interface.get_bool(slots.BROWSER_EXTENSION_GET_ISENABLED, "get_IsEnabled")

    def set_enabled(self, enabled: bool, on_done: Callable[[str], None]) -> None:
        handler: Callback

        def invoke(_this: int, hr: int) -> int:
            self._handlers.remove(handler)
            on_done("" if hr >= 0 else f"0x{hr & 0xFFFFFFFF:08X}")
            return 0

        handler = Callback(
            slots.IID_BROWSER_EXTENSION_ENABLE_COMPLETED_HANDLER, (_INVOKE_HRESULT, invoke)
        )
        self._handlers.append(handler)
        self._it.call(
            slots.BROWSER_EXTENSION_ENABLE,
            (BOOL, LPVOID),
            BOOL(1 if enabled else 0),
            handler.pointer,
            what="Enable",
        )

    def remove(self, on_done: Callable[[str], None]) -> None:
        handler: Callback

        def invoke(_this: int, hr: int) -> int:
            self._handlers.remove(handler)
            on_done("" if hr >= 0 else f"0x{hr & 0xFFFFFFFF:08X}")
            return 0

        handler = Callback(
            slots.IID_BROWSER_EXTENSION_REMOVE_COMPLETED_HANDLER, (_INVOKE_HRESULT, invoke)
        )
        self._handlers.append(handler)
        self._it.call(slots.BROWSER_EXTENSION_REMOVE, (LPVOID,), handler.pointer, what="Remove")

    def release(self) -> None:
        self._it.release()


class Profile:
    """``ICoreWebView2Profile7`` — where extensions live.

    Extensions are per *profile*, not per view, and the profile is reached from
    the view. They are also **unpacked folders**, not ``.crx`` files: there is
    no store install path here, which is why Strata asks for a directory.
    """

    def __init__(self, interface: Interface) -> None:
        self._it = interface
        self._handlers: list[Any] = []

    @property
    def supports_extensions(self) -> bool:
        return bool(self._it)

    def set_autofill(self, *, enabled: bool) -> None:
        """Form autofill and the "save password?" prompt, together.

        Both surface as bubbles owned by the browser process — windows Strata
        cannot exclude from capture — and both would keep what a researched
        page was typed into. Off in the research pane.
        """
        self._it.put_bool(
            slots.PROFILE7_PUT_ISGENERALAUTOFILLENABLED, enabled, "put_IsGeneralAutofillEnabled"
        )
        self._it.put_bool(
            slots.PROFILE7_PUT_ISPASSWORDAUTOSAVEENABLED, enabled, "put_IsPasswordAutosaveEnabled"
        )

    def add_extension(self, folder: Path, on_done: Callable[[str, str], None]) -> None:
        """Load an unpacked extension. ``on_done(name, error)``."""
        handler: Callback

        def invoke(_this: int, hr: int, extension: int) -> int:
            self._handlers.remove(handler)
            if hr < 0 or not extension:
                on_done("", f"0x{hr & 0xFFFFFFFF:08X}")
                return 0
            loaded = Interface(extension)
            name = loaded.get_string(slots.BROWSER_EXTENSION_GET_NAME, "get_Name")
            on_done(name, "")
            return 0

        handler = Callback(
            slots.IID_PROFILE_ADD_BROWSER_EXTENSION_COMPLETED_HANDLER, (_INVOKE_HRESULT_PTR, invoke)
        )
        self._handlers.append(handler)
        self._it.call(
            slots.PROFILE7_ADDBROWSEREXTENSION,
            (wintypes.LPCWSTR, LPVOID),
            str(folder),
            handler.pointer,
            what="AddBrowserExtension",
        )

    def list_extensions(self, on_done: Callable[[list[BrowserExtension]], None]) -> None:
        handler: Callback

        def invoke(_this: int, hr: int, listing: int) -> int:
            self._handlers.remove(handler)
            if hr < 0 or not listing:
                on_done([])
                return 0
            items = Interface(listing)
            count = items.get_uint32(slots.BROWSER_EXTENSION_LIST_GET_COUNT, "get_Count")
            found: list[BrowserExtension] = []
            for index in range(count):
                out = LPVOID()
                items.call(
                    slots.BROWSER_EXTENSION_LIST_GETVALUEATINDEX,
                    (ctypes.c_uint32, ctypes.POINTER(LPVOID)),
                    ctypes.c_uint32(index),
                    ctypes.byref(out),
                    what="GetValueAtIndex",
                )
                if out.value:
                    found.append(BrowserExtension(Interface(out.value)))
            on_done(found)
            return 0

        handler = Callback(
            slots.IID_PROFILE_GET_BROWSER_EXTENSIONS_COMPLETED_HANDLER,
            (_INVOKE_HRESULT_PTR, invoke),
        )
        self._handlers.append(handler)
        self._it.call(
            slots.PROFILE7_GETBROWSEREXTENSIONS,
            (LPVOID,),
            handler.pointer,
            what="GetBrowserExtensions",
        )

    def release(self) -> None:
        self._it.release()


def _header(headers: Interface, name: str) -> str:
    """One request header, or "" when absent (``GetHeader`` errors then)."""
    present = BOOL()
    headers.call(
        slots.HTTP_REQUEST_HEADERS_CONTAINS,
        (wintypes.LPCWSTR, ctypes.POINTER(BOOL)),
        name,
        ctypes.byref(present),
        what="Contains",
    )
    if not present.value:
        return ""
    out = LPVOID()
    headers.call(
        slots.HTTP_REQUEST_HEADERS_GETHEADER,
        (wintypes.LPCWSTR, ctypes.POINTER(LPVOID)),
        name,
        ctypes.byref(out),
        what="GetHeader",
    )
    return take_string(out)


class WebView:
    """``ICoreWebView2`` — the page itself."""

    def __init__(self, interface: Interface) -> None:
        self._it = interface
        # Every handler handed to WebView2 must outlive the registration.
        self._handlers: list[Any] = []
        self._settings = interface.get_interface(slots.WEBVIEW_GET_SETTINGS, "get_Settings")

    # -- state ---------------------------------------------------------------

    @property
    def source(self) -> str:
        return self._it.get_string(slots.WEBVIEW_GET_SOURCE, "get_Source")

    @property
    def title(self) -> str:
        return self._it.get_string(slots.WEBVIEW_GET_DOCUMENTTITLE, "get_DocumentTitle")

    @property
    def browser_process_id(self) -> int:
        """The ``msedgewebview2.exe`` that renders this view.

        Handed to ``screen_security`` so the capture sweep reaches the popups
        and menus that live in the browser process rather than in ours.
        """
        return self._it.get_uint32(slots.WEBVIEW_GET_BROWSERPROCESSID, "get_BrowserProcessId")

    # -- driving -------------------------------------------------------------

    def navigate(self, url: str) -> None:
        self._it.put_string(slots.WEBVIEW_NAVIGATE, url, "Navigate")

    def reload(self) -> None:
        self._it.call(slots.WEBVIEW_RELOAD, what="Reload")

    def stop(self) -> None:
        self._it.call(slots.WEBVIEW_STOP, what="Stop")

    def go_back(self) -> None:
        self._it.call(slots.WEBVIEW_GOBACK, what="GoBack")

    def go_forward(self) -> None:
        self._it.call(slots.WEBVIEW_GOFORWARD, what="GoForward")

    def execute_script(self, source: str, on_result: Callable[[str], None]) -> None:
        """Run ``source`` in the page; ``on_result`` gets the JSON result.

        WebView2 answers with the value already JSON-encoded (``"null"`` when
        the script returned nothing), which is the same shape the Qt pane's
        extraction path already decodes.
        """
        handler: Callback

        def invoke(_this: int, hr: int, result: str | None) -> int:
            self._handlers.remove(handler)
            on_result("null" if hr < 0 or result is None else result)
            return 0

        handler = Callback(
            slots.IID_EXECUTE_SCRIPT_COMPLETED_HANDLER, (_INVOKE_HRESULT_STR, invoke)
        )
        self._handlers.append(handler)
        self._it.call(
            slots.WEBVIEW_EXECUTESCRIPT,
            (wintypes.LPCWSTR, LPVOID),
            source,
            handler.pointer,
            what="ExecuteScript",
        )

    def call_devtools(
        self, method: str, params: str, on_result: Callable[[str | None], None]
    ) -> None:
        """One DevTools-protocol call; ``on_result`` gets its JSON, or None.

        Works with the DevTools *window* switched off (``apply_settings``):
        that setting governs the UI, not the protocol, and the protocol is how
        the web archive takes a page snapshot and reads the cookies a video
        download needs.
        """
        handler: Callback

        def invoke(_this: int, hr: int, result: str | None) -> int:
            self._handlers.remove(handler)
            on_result(None if hr < 0 else result)
            return 0

        handler = Callback(
            slots.IID_CALL_DEV_TOOLS_PROTOCOL_METHOD_COMPLETED_HANDLER,
            (_INVOKE_HRESULT_STR, invoke),
        )
        self._handlers.append(handler)
        self._it.call(
            slots.WEBVIEW_CALLDEVTOOLSPROTOCOLMETHOD,
            (wintypes.LPCWSTR, wintypes.LPCWSTR, LPVOID),
            method,
            params,
            handler.pointer,
            what="CallDevToolsProtocolMethod",
        )

    def serve(
        self,
        environment: Environment,
        uri_filter: str,
        responder: Callable[[str, str, str], VaultReply | None],
    ) -> None:
        """Answer every request matching ``uri_filter`` from Python.

        ``responder(method, uri, range_header)`` returns the reply, or None to
        let the request continue (for the vault's reserved host, that means it
        fails: the name can never resolve). The body is handed to the engine as
        a :class:`ReadStream`, so it is read - and decrypted - on demand.
        """
        self._it.call(
            slots.WEBVIEW_ADDWEBRESOURCEREQUESTEDFILTER,
            (wintypes.LPCWSTR, ctypes.c_int),
            uri_filter,
            WEB_RESOURCE_CONTEXT_ALL,
            what="AddWebResourceRequestedFilter",
        )

        def invoke(_this: int, _sender: int, args: int) -> int:
            try:
                self._answer(environment, Interface(args), responder)
            except Exception:
                # A fault here must not unwind into the engine.
                logger.exception("webview2.serve_failed")
            return 0

        self._add_event(
            slots.WEBVIEW_ADD_WEBRESOURCEREQUESTED,
            slots.IID_WEB_RESOURCE_REQUESTED_EVENT_HANDLER,
            invoke,
        )

    @staticmethod
    def _answer(
        environment: Environment,
        event: Interface,
        responder: Callable[[str, str, str], VaultReply | None],
    ) -> None:
        request = event.get_interface(
            slots.WEB_RESOURCE_REQUESTED_EVENT_ARGS_GET_REQUEST, "get_Request"
        )
        try:
            uri = request.get_string(slots.WEB_RESOURCE_REQUEST_GET_URI, "get_Uri")
            method = request.get_string(slots.WEB_RESOURCE_REQUEST_GET_METHOD, "get_Method")
            headers = request.get_interface(slots.WEB_RESOURCE_REQUEST_GET_HEADERS, "get_Headers")
            try:
                range_header = _header(headers, "Range")
            finally:
                headers.release()
        finally:
            request.release()

        reply = responder(method, uri, range_header)
        if reply is None:
            return
        stream = ReadStream.over(reply.body)
        try:
            response = environment.create_response(
                stream.pointer, reply.status, reply.reason, reply.headers
            )
            try:
                event.call(
                    slots.WEB_RESOURCE_REQUESTED_EVENT_ARGS_PUT_RESPONSE,
                    (LPVOID,),
                    response.pointer,
                    what="put_Response",
                )
            finally:
                response.release()
        finally:
            # The response holds its own reference now; ours goes, so the
            # engine's last Release is what closes the body.
            stream.release_own()

    def add_script_on_document_created(self, source: str) -> None:
        """Inject ``source`` into every future document, before its own scripts."""
        handler: Callback

        def invoke(_this: int, hr: int, _id: str | None) -> int:
            self._handlers.remove(handler)
            if hr < 0:
                logger.warning("webview2.document_script_failed", hr=hr & 0xFFFFFFFF)
            return 0

        handler = Callback(
            slots.IID_ADD_SCRIPT_TO_EXECUTE_ON_DOCUMENT_CREATED_COMPLETED_HANDLER,
            (_INVOKE_HRESULT_STR, invoke),
        )
        self._handlers.append(handler)
        self._it.call(
            slots.WEBVIEW_ADDSCRIPTTOEXECUTEONDOCUMENTCREATED,
            (wintypes.LPCWSTR, LPVOID),
            source,
            handler.pointer,
            what="AddScriptToExecuteOnDocumentCreated",
        )

    # -- settings ------------------------------------------------------------

    def apply_settings(
        self,
        *,
        dev_tools: bool,
        context_menus: bool,
        status_bar: bool,
        script_dialogs: bool = False,
        zoom_control: bool = False,
        browser_accelerator_keys: bool = False,
    ) -> None:
        """Switch off the browser UI that would open a window of the engine's own.

        Every one of these — devtools, the context menu, ``alert()``, the zoom
        bubble, and the accelerators for print, find and "view source" — puts a
        top-level window in ``msedgewebview2.exe``. That window is not ours,
        Windows refuses to let us exclude it from capture, and so it is in the
        recording of a pane that is otherwise hidden. Off by default here;
        the ``CaptureGuard`` closes whatever still gets through.
        """
        if not self._settings:
            return
        self._settings.put_bool(slots.SETTINGS_PUT_AREDEVTOOLSENABLED, dev_tools, "AreDevTools")
        self._settings.put_bool(
            slots.SETTINGS_PUT_AREDEFAULTCONTEXTMENUSENABLED, context_menus, "ContextMenus"
        )
        self._settings.put_bool(slots.SETTINGS_PUT_ISSTATUSBARENABLED, status_bar, "StatusBar")
        self._settings.put_bool(
            slots.SETTINGS_PUT_AREDEFAULTSCRIPTDIALOGSENABLED, script_dialogs, "ScriptDialogs"
        )
        self._settings.put_bool(
            slots.SETTINGS_PUT_ISZOOMCONTROLENABLED, zoom_control, "ZoomControl"
        )
        settings3 = self._settings.query_interface(slots.IID_SETTINGS3)
        if settings3:
            try:
                settings3.put_bool(
                    slots.SETTINGS3_PUT_AREBROWSERACCELERATORKEYSENABLED,
                    browser_accelerator_keys,
                    "BrowserAcceleratorKeys",
                )
            finally:
                settings3.release()
        # A researched page must not be able to reach the host: Strata installs
        # no host object and no web-message channel on this view, and turning
        # the transport off says so to the engine as well as to the reader.
        self._settings.put_bool(slots.SETTINGS_PUT_ISWEBMESSAGEENABLED, False, "WebMessage")
        self._settings.put_bool(slots.SETTINGS_PUT_AREHOSTOBJECTSALLOWED, False, "HostObjects")

    def set_user_agent(self, user_agent: str) -> bool:
        """Override the UA (mobile mode). False when the runtime is too old."""
        settings2 = self._settings.query_interface(slots.IID_SETTINGS2)
        if not settings2:
            return False
        try:
            settings2.put_string(slots.SETTINGS2_PUT_USERAGENT, user_agent, "put_UserAgent")
            return True
        finally:
            settings2.release()

    # -- extensions ----------------------------------------------------------

    def profile(self) -> Profile | None:
        """The profile this view runs under, if the runtime is new enough.

        Two versioned hops: ``ICoreWebView2_13`` to reach the profile at all,
        then ``ICoreWebView2Profile7`` for the extension methods. Either being
        absent means an older runtime, which is a "no extensions" answer rather
        than an error.
        """
        versioned = self._it.query_interface(slots.IID_WEBVIEW_13)
        if not versioned:
            return None
        try:
            base = versioned.get_interface(slots.WEBVIEW_13_GET_PROFILE, "get_Profile")
        finally:
            versioned.release()
        if not base:
            return None
        try:
            seven = base.query_interface(slots.IID_PROFILE7)
        finally:
            base.release()
        return Profile(seven) if seven else None

    # -- events --------------------------------------------------------------

    def on_navigation_starting(self, callback: Callable[[str], bool]) -> None:
        """``callback(url) -> allow``. Returning False cancels the navigation."""

        def invoke(_this: int, _sender: int, args: int) -> int:
            event = Interface(args)
            url = event.get_string(slots.NAVIGATION_STARTING_EVENT_ARGS_GET_URI, "get_Uri")
            if not callback(url):
                event.put_bool(slots.NAVIGATION_STARTING_EVENT_ARGS_PUT_CANCEL, True, "put_Cancel")
            return 0

        self._add_event(
            slots.WEBVIEW_ADD_NAVIGATIONSTARTING,
            slots.IID_NAVIGATION_STARTING_EVENT_HANDLER,
            invoke,
        )

    def on_new_window_requested(self, callback: Callable[[str], None]) -> None:
        """Every ``window.open`` and ``target=_blank``, handled here instead.

        Marking the event handled without supplying a window is what stops
        WebView2 opening a top-level browser window Strata does not own — and
        therefore cannot exclude from capture.
        """

        def invoke(_this: int, _sender: int, args: int) -> int:
            event = Interface(args)
            url = event.get_string(slots.NEW_WINDOW_REQUESTED_EVENT_ARGS_GET_URI, "get_Uri")
            event.put_bool(slots.NEW_WINDOW_REQUESTED_EVENT_ARGS_PUT_HANDLED, True, "put_Handled")
            callback(url)
            return 0

        self._add_event(
            slots.WEBVIEW_ADD_NEWWINDOWREQUESTED,
            slots.IID_NEW_WINDOW_REQUESTED_EVENT_HANDLER,
            invoke,
        )

    def deny_permission_requests(self) -> None:
        """Answer every permission prompt with "deny" before it is shown.

        Camera, microphone, location, notifications, clipboard: each request
        would otherwise raise a bubble that belongs to the browser process and
        is therefore in any recording of the pane. A research pane has no use
        for any of them, and a silent refusal is the answer a page gets.
        """

        def invoke(_this: int, _sender: int, args: int) -> int:
            Interface(args).call(
                slots.PERMISSION_REQUESTED_EVENT_ARGS_PUT_STATE,
                (ctypes.c_int,),
                COREWEBVIEW2_PERMISSION_STATE_DENY,
                what="put_State",
            )
            return 0

        self._add_event(
            slots.WEBVIEW_ADD_PERMISSIONREQUESTED,
            slots.IID_PERMISSION_REQUESTED_EVENT_HANDLER,
            invoke,
        )

    def cancel_downloads(self) -> bool:
        """Refuse every download. False when the runtime is too old to ask.

        A download opens the engine's own dialog — a window of the browser
        process, in every recording — and writes to the user's disk from a
        pane whose whole boundary is that it does not. Cancelled and marked
        handled, so neither the dialog nor the file appears.
        """
        versioned = self._it.query_interface(slots.IID_WEBVIEW_13)
        if not versioned:
            return False

        def invoke(_this: int, _sender: int, args: int) -> int:
            event = Interface(args)
            event.put_bool(slots.DOWNLOAD_STARTING_EVENT_ARGS_PUT_CANCEL, True, "put_Cancel")
            event.put_bool(slots.DOWNLOAD_STARTING_EVENT_ARGS_PUT_HANDLED, True, "put_Handled")
            return 0

        try:
            self._add_event(
                slots.WEBVIEW_13_ADD_DOWNLOADSTARTING,
                slots.IID_DOWNLOAD_STARTING_EVENT_HANDLER,
                invoke,
                on=versioned,
            )
        finally:
            # The registration lives on the object, not on this QI'd pointer.
            versioned.release()
        return True

    def on_source_changed(self, callback: Callable[[], None]) -> None:
        def invoke(_this: int, _sender: int, _args: int) -> int:
            callback()
            return 0

        self._add_event(
            slots.WEBVIEW_ADD_SOURCECHANGED, slots.IID_SOURCE_CHANGED_EVENT_HANDLER, invoke
        )

    def on_navigation_completed(self, callback: Callable[[bool], None]) -> None:
        def invoke(_this: int, _sender: int, args: int) -> int:
            event = Interface(args)
            ok = event.get_bool(
                slots.NAVIGATION_COMPLETED_EVENT_ARGS_GET_ISSUCCESS, "get_IsSuccess"
            )
            callback(ok)
            return 0

        self._add_event(
            slots.WEBVIEW_ADD_NAVIGATIONCOMPLETED,
            slots.IID_NAVIGATION_COMPLETED_EVENT_HANDLER,
            invoke,
        )

    def on_title_changed(self, callback: Callable[[], None]) -> None:
        def invoke(_this: int, _sender: int, _args: int) -> int:
            callback()
            return 0

        self._add_event(
            slots.WEBVIEW_ADD_DOCUMENTTITLECHANGED,
            slots.IID_DOCUMENT_TITLE_CHANGED_EVENT_HANDLER,
            invoke,
        )

    def _add_event(
        self,
        slot: int,
        iid: str,
        invoke: Callable[..., int],
        *,
        on: Interface | None = None,
    ) -> None:
        handler = Callback(iid, (_INVOKE_SENDER_ARGS, invoke))
        self._handlers.append(handler)  # never removed: registered for our lifetime
        token = EventRegistrationToken()
        (on if on is not None else self._it).call(
            slot,
            (LPVOID, ctypes.POINTER(EventRegistrationToken)),
            handler.pointer,
            ctypes.byref(token),
            what=f"add_ (slot {slot})",
        )

    def release(self) -> None:
        self._settings.release()
        self._it.release()


class Controller:
    """``ICoreWebView2Controller`` — where the page sits in our window."""

    def __init__(self, interface: Interface) -> None:
        self._it = interface
        self._handlers: list[Any] = []
        self.webview = WebView(
            interface.get_interface(slots.CONTROLLER_GET_COREWEBVIEW2, "get_CoreWebView2")
        )

    def set_bounds(self, left: int, top: int, right: int, bottom: int) -> None:
        self._it.call(
            slots.CONTROLLER_PUT_BOUNDS, (RECT,), RECT(left, top, right, bottom), what="put_Bounds"
        )

    def set_visible(self, visible: bool) -> None:
        self._it.put_bool(slots.CONTROLLER_PUT_ISVISIBLE, visible, "put_IsVisible")

    def on_accelerator_key(self, callback: Callable[[int], bool]) -> None:
        """``callback(virtual_key) -> handled`` for every key-down with a modifier.

        The only way to see a shortcut while the page has focus: that focus
        belongs to an Edge window, and Qt never receives the keys. Returning
        True marks the key handled so the page does not also act on it.
        """

        def invoke(_this: int, _sender: int, args: int) -> int:
            event = Interface(args)
            kind = ctypes.c_int()
            event.call(
                slots.ACCELERATOR_KEY_PRESSED_EVENT_ARGS_GET_KEYEVENTKIND,
                (ctypes.POINTER(ctypes.c_int),),
                ctypes.byref(kind),
                what="get_KeyEventKind",
            )
            if kind.value not in (KEY_EVENT_KEY_DOWN, KEY_EVENT_SYSTEM_KEY_DOWN):
                return 0
            key = event.get_uint32(
                slots.ACCELERATOR_KEY_PRESSED_EVENT_ARGS_GET_VIRTUALKEY, "get_VirtualKey"
            )
            if callback(key):
                event.put_bool(
                    slots.ACCELERATOR_KEY_PRESSED_EVENT_ARGS_PUT_HANDLED, True, "put_Handled"
                )
            return 0

        handler = Callback(
            slots.IID_ACCELERATOR_KEY_PRESSED_EVENT_HANDLER, (_INVOKE_SENDER_ARGS, invoke)
        )
        self._handlers.append(handler)  # registered for the controller's lifetime
        token = EventRegistrationToken()
        self._it.call(
            slots.CONTROLLER_ADD_ACCELERATORKEYPRESSED,
            (LPVOID, ctypes.POINTER(EventRegistrationToken)),
            handler.pointer,
            ctypes.byref(token),
            what="add_AcceleratorKeyPressed",
        )

    def notify_moved(self) -> None:
        """Tell WebView2 the host window moved, so popups land in the right place."""
        self._it.call(
            slots.CONTROLLER_NOTIFYPARENTWINDOWPOSITIONCHANGED,
            what="NotifyParentWindowPositionChanged",
        )

    def close(self) -> None:
        self.webview.release()
        try:
            self._it.call(slots.CONTROLLER_CLOSE, what="Close")
        except ComError:
            pass  # already torn down with the window
        self._it.release()


class Environment:
    """``ICoreWebView2Environment`` — one per user-data folder, per process."""

    def __init__(self, interface: Interface, options: _EnvironmentOptions) -> None:
        self._it = interface
        self._options = options  # held: WebView2 keeps reading it
        self._pending: list[Any] = []

    @property
    def browser_version(self) -> str:
        return self._it.get_string(
            slots.ENVIRONMENT_GET_BROWSERVERSIONSTRING, "get_BrowserVersionString"
        )

    def create_response(self, stream: int, status: int, reason: str, headers: str) -> Interface:
        """``CreateWebResourceResponse`` over one of our streams."""
        out = LPVOID()
        self._it.call(
            slots.ENVIRONMENT_CREATEWEBRESOURCERESPONSE,
            (LPVOID, ctypes.c_int, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LPVOID)),
            stream,
            status,
            reason,
            headers,
            ctypes.byref(out),
            what="CreateWebResourceResponse",
        )
        return Interface(out.value)

    def create_controller(
        self, hwnd: int, on_ready: Callable[[Controller | None, str], None]
    ) -> None:
        """Put a webview in ``hwnd``. ``on_ready`` fires on the message loop."""
        handler: Callback

        def invoke(_this: int, hr: int, controller: int) -> int:
            self._pending.remove(handler)
            if hr < 0 or not controller:
                on_ready(None, f"CreateCoreWebView2Controller failed: 0x{hr & 0xFFFFFFFF:08X}")
                return 0
            # AddRef: the pointer handed to a completion handler is borrowed,
            # and this one outlives the call by the life of the pane.
            interface = Interface(controller)
            interface.call(1, restype=ctypes.c_ulong, what="AddRef")
            on_ready(Controller(interface), "")
            return 0

        handler = Callback(
            slots.IID_CREATE_CORE_WEB_VIEW2_CONTROLLER_COMPLETED_HANDLER,
            (_INVOKE_HRESULT_PTR, invoke),
        )
        self._pending.append(handler)
        self._it.call(
            slots.ENVIRONMENT_CREATECOREWEBVIEW2CONTROLLER,
            (wintypes.HWND, LPVOID),
            wintypes.HWND(hwnd),
            handler.pointer,
            what="CreateCoreWebView2Controller",
        )

    def release(self) -> None:
        self._it.release()


# Held for the life of the process: a completion handler that is collected
# before WebView2 invokes it is a jump into freed memory, and environment
# creation is the one call whose handler has no object to live on yet.
_PENDING_ENVIRONMENTS: list[Any] = []


def create_environment(
    *,
    user_data_folder: Path,
    loader: Path,
    extra_arguments: tuple[str, ...] = (),
    extensions_enabled: bool = False,
    on_ready: Callable[[Environment | None, str], None],
) -> None:
    """Start a WebView2 environment. ``on_ready`` fires on the message loop.

    Raises :class:`WebView2Unavailable` synchronously when the machine cannot
    host one at all; anything that goes wrong afterwards arrives as an error
    string on ``on_ready``, because by then it is the runtime's answer and not a
    precondition.
    """
    if not _is_windows():
        raise WebView2Unavailable("WebView2 is a Windows component.")
    if not runtime_version():
        raise WebView2Unavailable(
            "The Microsoft Edge WebView2 Runtime is not installed on this machine."
        )
    if not loader.is_file():
        raise WebView2Unavailable(f"{LOADER_NAME} was not found beside the application.")

    co_initialize()
    user_data_folder.mkdir(parents=True, exist_ok=True)

    try:
        library = ctypes.WinDLL(str(loader))
        create = library.CreateCoreWebView2EnvironmentWithOptions
    except (OSError, AttributeError) as exc:
        raise WebView2Unavailable(f"{LOADER_NAME} could not be loaded: {exc}") from exc
    create.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, LPVOID, LPVOID]
    create.restype = HRESULT

    # STRATA_WEBVIEW2_FLAGS is the WebView2 counterpart of Qt's
    # QTWEBENGINE_CHROMIUM_FLAGS: extra Chromium switches appended for A/B
    # testing a compositor problem against a real recorder without a rebuild
    # (e.g. --disable-direct-composition). Appended, never replacing, so the
    # capture-safe set is always present.
    import os

    ad_hoc = tuple(os.environ.get("STRATA_WEBVIEW2_FLAGS", "").split())
    if ad_hoc:
        logger.info("webview2.ad_hoc_flags", flags=ad_hoc)
    options = _EnvironmentOptions(
        " ".join((*CAPTURE_SAFE_ARGUMENTS, *extra_arguments, *ad_hoc)),
        extensions_enabled=extensions_enabled,
    )
    handler: Callback

    def invoke(_this: int, hr: int, environment: int) -> int:
        _PENDING_ENVIRONMENTS.remove(handler)
        if hr < 0 or not environment:
            on_ready(None, f"WebView2 environment creation failed: 0x{hr & 0xFFFFFFFF:08X}")
            return 0
        interface = Interface(environment)
        interface.call(1, restype=ctypes.c_ulong, what="AddRef")
        on_ready(Environment(interface, options), "")
        return 0

    handler = Callback(
        slots.IID_CREATE_CORE_WEB_VIEW2_ENVIRONMENT_COMPLETED_HANDLER, (_INVOKE_HRESULT_PTR, invoke)
    )
    _PENDING_ENVIRONMENTS.append(handler)

    hr = int(create(None, str(user_data_folder), options.pointer, handler.pointer))
    if hr < 0:
        _PENDING_ENVIRONMENTS.remove(handler)
        raise WebView2Unavailable(
            f"CreateCoreWebView2EnvironmentWithOptions: 0x{hr & 0xFFFFFFFF:08X}"
        )
    logger.info("webview2.environment_requested", runtime=runtime_version())
