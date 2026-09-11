"""Just enough COM to drive WebView2 from Python, with no .NET in the process.

Why by hand rather than through a binding library:

* **pythonnet** works, and is what most projects reach for, but it puts the CLR
  in the process and makes the PyInstaller/Inno build carry — or prompt for —
  the .NET Desktop Runtime. For an app whose pitch is "a directory on your
  disk", a second runtime is a real cost.
* **comtypes** generates from a *type library*, and WebView2 ships none. It is
  a C header and a loader DLL.

So this module does the three things a COM caller actually needs, and nothing
else:

* **call a method on an interface pointer** — :meth:`Interface.call`, by vtable
  slot;
* **give COM an object of ours** — :class:`Callback`, a vtable backed by Python
  functions. Not optional: the whole WebView2 API is asynchronous, so every
  result comes back through a callback we have to implement;
* **free what COM allocated** — :func:`take_string`, for the ``LPWSTR*`` out
  parameters, which are the caller's to release.

Every vtable slot number and IID this package uses is *generated from the SDK's
own ``WebView2.h``* by ``scripts/webview2_slots.py``, never transcribed by hand:
a wrong slot index is not a type error and not an exception. It calls whichever
function happens to sit at that offset, and the failure mode is a corrupted
process.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

S_OK = 0
E_NOINTERFACE = 0x80004002 - 0x100000000  # an HRESULT is signed
E_POINTER = 0x80004003 - 0x100000000
RPC_E_CHANGED_MODE = 0x80010106

IID_IUNKNOWN = "00000000-0000-0000-C000-000000000046"

HRESULT = ctypes.c_long
LPVOID = ctypes.c_void_p
# BOOL in COM is a 4-byte int, not ctypes.c_bool (one byte). Passing the latter
# by value would leave three bytes of the argument slot undefined.
BOOL = ctypes.c_int


class ComError(Exception):
    """A COM call came back with a failing HRESULT."""

    def __init__(self, what: str, hr: int) -> None:
        self.hr = hr & 0xFFFFFFFF
        super().__init__(f"{what} failed: 0x{self.hr:08X}")


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class EventRegistrationToken(ctypes.Structure):
    """What every ``add_*`` hands back so the event can be removed again."""

    _fields_ = [("value", ctypes.c_int64)]


def guid(text: str) -> GUID:
    """Parse ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`` into a GUID.

    ``CLSIDFromString`` would do this, but it wants COM already initialised and
    this runs at import time to build the IID table.
    """
    parts = text.strip().strip("{}").split("-")
    if len(parts) != 5:
        raise ValueError(f"not a GUID: {text!r}")
    tail = bytes.fromhex(parts[3] + parts[4])
    return GUID(
        int(parts[0], 16),
        int(parts[1], 16),
        int(parts[2], 16),
        (ctypes.c_ubyte * 8)(*tail),
    )


def check(hr: int, what: str) -> None:
    if hr < 0:
        raise ComError(what, hr)


def co_initialize() -> None:
    """Put this thread in an apartment, unless something already has.

    WebView2 answers ``CO_E_NOTINITIALIZED`` from environment creation
    otherwise. Qt initialises COM on the GUI thread itself, so inside the
    running app this normally returns ``S_FALSE`` and does nothing — but the
    binding must not *depend* on Qt having got there first, because a test or a
    spike has no Qt.
    """
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [LPVOID, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = HRESULT
    hr = int(ole32.CoInitializeEx(None, 0x2))  # COINIT_APARTMENTTHREADED
    # S_FALSE (1) is "already initialised on this thread". RPC_E_CHANGED_MODE is
    # "already initialised, in the other apartment model" — Qt's choice, and we
    # are a guest on its thread, so neither is ours to complain about.
    if hr < 0 and (hr & 0xFFFFFFFF) != RPC_E_CHANGED_MODE:
        raise ComError("CoInitializeEx", hr)


def _ole32() -> Any:
    """``ole32`` with its prototypes declared.

    Not optional decoration. ``CoTaskMemAlloc`` returns a pointer, and a
    ``ctypes`` function with no ``restype`` is assumed to return ``c_int`` — so
    on 64-bit the top half of every allocation address is silently discarded
    and the first write to it faults. Declaring the prototypes once, here, is
    what stops that being rediscovered per call site.
    """
    ole32 = ctypes.windll.ole32
    ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
    ole32.CoTaskMemAlloc.restype = LPVOID
    ole32.CoTaskMemFree.argtypes = [LPVOID]
    ole32.CoTaskMemFree.restype = None
    return ole32


def alloc_string(value: str) -> int:
    """Copy ``value`` into memory the *caller* will free with CoTaskMemFree.

    The other half of :func:`take_string`: when WebView2 calls one of *our*
    ``get_`` methods, we are the callee and must allocate. Handing back a
    pointer into a Python string would give the browser memory the interpreter
    can move or reclaim underneath it.
    """
    encoded = value.encode("utf-16-le") + b"\x00\x00"
    buffer = _ole32().CoTaskMemAlloc(len(encoded))
    if not buffer:
        raise MemoryError("CoTaskMemAlloc failed")
    ctypes.memmove(buffer, encoded, len(encoded))
    return int(buffer)


def take_string(buffer: LPVOID) -> str:
    """Read an ``LPWSTR`` out-parameter and free it, as COM requires.

    ``get_Uri``/``get_Source``/``get_DocumentTitle`` each hand back memory the
    *caller* owns. Reading without freeing leaks on every navigation.
    """
    if not buffer:
        return ""
    try:
        return str(ctypes.wstring_at(buffer))
    finally:
        _ole32().CoTaskMemFree(buffer)


class Interface:
    """A raw COM interface pointer, called by vtable slot.

    Deliberately *not* reference-counted per Python copy. Every pointer this
    package holds is owned for the lifetime of the pane and released once, by
    hand, in :meth:`release`. The alternative — a ``__del__``-driven refcount —
    runs during interpreter shutdown in a process that also contains Chromium,
    which is not a place to discover an ordering bug.
    """

    __slots__ = ("_ptr",)

    def __init__(self, pointer: int | None) -> None:
        self._ptr = int(pointer or 0)

    def __bool__(self) -> bool:
        return self._ptr != 0

    @property
    def pointer(self) -> int:
        return self._ptr

    def _vtable(self) -> Any:
        return ctypes.cast(self._ptr, ctypes.POINTER(ctypes.POINTER(LPVOID)))[0]

    def call(
        self,
        slot: int,
        argtypes: tuple[Any, ...] = (),
        *args: Any,
        restype: Any = HRESULT,
        what: str = "",
    ) -> Any:
        """Invoke vtable entry ``slot``. Raises :class:`ComError` on failure."""
        if not self._ptr:
            raise ComError(what or f"slot {slot}", E_POINTER)
        prototype = ctypes.WINFUNCTYPE(restype, LPVOID, *argtypes)
        result = prototype(self._vtable()[slot])(self._ptr, *args)
        if restype is HRESULT:
            check(int(result), what or f"slot {slot}")
        return result

    def get_string(self, slot: int, what: str) -> str:
        out = LPVOID()
        self.call(slot, (ctypes.POINTER(LPVOID),), ctypes.byref(out), what=what)
        return take_string(out)

    def get_interface(self, slot: int, what: str) -> Interface:
        out = LPVOID()
        self.call(slot, (ctypes.POINTER(LPVOID),), ctypes.byref(out), what=what)
        return Interface(out.value)

    def get_bool(self, slot: int, what: str) -> bool:
        out = BOOL()
        self.call(slot, (ctypes.POINTER(BOOL),), ctypes.byref(out), what=what)
        return bool(out.value)

    def get_uint32(self, slot: int, what: str) -> int:
        out = ctypes.c_uint32()
        self.call(slot, (ctypes.POINTER(ctypes.c_uint32),), ctypes.byref(out), what=what)
        return int(out.value)

    def put_bool(self, slot: int, value: bool, what: str) -> None:
        self.call(slot, (BOOL,), BOOL(1 if value else 0), what=what)

    def put_string(self, slot: int, value: str, what: str) -> None:
        self.call(slot, (wintypes.LPCWSTR,), value, what=what)

    def query_interface(self, iid: str) -> Interface:
        """QI, returning a *null* :class:`Interface` when unsupported.

        Not an error case. The WebView2 API is versioned by interface, so
        ``ICoreWebView2Settings2`` simply being absent is how an older runtime
        says "no user-agent override"; the caller degrades instead of failing.
        """
        if not self._ptr:
            return Interface(0)
        out = LPVOID()
        target = guid(iid)
        prototype = ctypes.WINFUNCTYPE(
            HRESULT, LPVOID, ctypes.POINTER(GUID), ctypes.POINTER(LPVOID)
        )
        hr = int(prototype(self._vtable()[0])(self._ptr, ctypes.byref(target), ctypes.byref(out)))
        return Interface(out.value) if hr >= 0 else Interface(0)

    def release(self) -> None:
        if not self._ptr:
            return
        ctypes.WINFUNCTYPE(ctypes.c_ulong, LPVOID)(self._vtable()[2])(self._ptr)
        self._ptr = 0


_QUERY_INTERFACE = ctypes.WINFUNCTYPE(HRESULT, LPVOID, ctypes.POINTER(GUID), ctypes.POINTER(LPVOID))
_ADD_REF = ctypes.WINFUNCTYPE(ctypes.c_ulong, LPVOID)
_RELEASE = ctypes.WINFUNCTYPE(ctypes.c_ulong, LPVOID)


class Callback:
    """A COM object implemented in Python: an IUnknown, plus its own methods.

    Lifetime is the entire point of this class. The vtable, the ``ctypes``
    thunks and the object header must each stay referenced from Python for as
    long as WebView2 holds the pointer; if any one of them is collected, the
    next call from the browser process jumps into freed memory. Holding the
    :class:`Callback` holds all three, so the rule for callers is just: keep the
    Callback alive as long as whatever you handed it to.
    """

    def __init__(self, iid: str, *methods: tuple[Any, Callable[..., Any]]) -> None:
        self._iid_bytes = bytes(guid(iid))
        self._unknown_bytes = bytes(guid(IID_IUNKNOWN))

        fields: list[tuple[str, Any]] = [
            ("QueryInterface", _QUERY_INTERFACE),
            ("AddRef", _ADD_REF),
            ("Release", _RELEASE),
        ]
        for index, (functype, _) in enumerate(methods):
            fields.append((f"method{index}", functype))
        vtable_type = type("Vtbl", (ctypes.Structure,), {"_fields_": fields})
        object_type = type(
            "ComObject",
            (ctypes.Structure,),
            {"_fields_": [("lpVtbl", ctypes.POINTER(vtable_type))]},
        )

        # Held on self, so none of them outlives its references.
        self._thunks: list[Any] = [
            _QUERY_INTERFACE(self._query_interface),
            _ADD_REF(self._add_ref),
            _RELEASE(self._release),
        ]
        self._thunks.extend(functype(handler) for functype, handler in methods)

        self._vtable = vtable_type(*self._thunks)
        self._object = object_type(ctypes.pointer(self._vtable))
        self._refs = 1

    @property
    def pointer(self) -> int:
        """The ``IUnknown*`` to hand to WebView2."""
        return ctypes.addressof(self._object)

    def _query_interface(self, _this: int, riid: Any, out: Any) -> int:
        if not out:
            return E_POINTER
        requested = bytes(GUID.from_address(ctypes.addressof(riid.contents)))
        if requested in (self._iid_bytes, self._unknown_bytes):
            out[0] = self.pointer
            self._refs += 1
            return S_OK
        # Answering "yes" to an interface we do not implement is how a binding
        # hands the browser a vtable of the wrong shape.
        out[0] = None
        return E_NOINTERFACE

    def _add_ref(self, _this: int) -> int:
        self._refs += 1
        return self._refs

    def _release(self, _this: int) -> int:
        # Python owns this memory, so Release never frees; it only reports. The
        # object dies with the Callback, which is why callers must hold it.
        self._refs = max(0, self._refs - 1)
        return self._refs
