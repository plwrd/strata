"""Run a helper process on a short leash.

yt-dlp and ffmpeg parse whatever a website sends them. Strata's own process
holds the layer keys, so neither runs inside it, and each child is confined:

* **Windows** — a Job Object, assigned before the child runs a single
  instruction (created suspended, assigned, then resumed): a memory ceiling, a
  cap on how many processes it may have (ffmpeg: itself only — it has no
  business starting programs), and *kill on close*, so a child can never
  outlive Strata, even if Strata crashes.
* **POSIX** — an address-space limit (``RLIMIT_AS``) and a new session.

Not a full sandbox: the child still runs as the user, with the user's file
access. See THREAT_MODEL.md T-35.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Limits:
    memory_mb: int = 1024
    # Processes the job may hold at once, the child included.
    max_processes: int = 1


# -- Windows job objects ---------------------------------------------------------

_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x0100
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x0400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]  # fmt: skip


class _BASIC_LIMITS(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _EXTENDED_LIMITS(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BASIC_LIMITS),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32() -> Any:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = ctypes.c_void_p
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    k.SetInformationJobObject.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
    ]  # fmt: skip
    k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    return k


def _job(limits: Limits) -> int:
    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
    info = _EXTENDED_LIMITS()
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    info.BasicLimitInformation.ActiveProcessLimit = max(1, limits.max_processes)
    info.ProcessMemoryLimit = max(64, limits.memory_mb) * 1024 * 1024
    if not kernel32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
    ):
        kernel32.CloseHandle(job)
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    return int(job)


def _resume(process: subprocess.Popen[bytes]) -> None:
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    status = ntdll.NtResumeProcess(ctypes.c_void_p(int(process._handle)))  # type: ignore[attr-defined]
    if status != 0:
        raise OSError(status, "NtResumeProcess failed")


class Confined:
    """A running child plus the job that holds it. Close to release the job."""

    def __init__(self, process: subprocess.Popen[bytes], job: int) -> None:
        self.process = process
        self._job = job

    def close(self) -> None:
        if self._job:
            # KILL_ON_JOB_CLOSE: anything still running in the job ends here.
            _kernel32().CloseHandle(ctypes.c_void_p(self._job))
            self._job = 0


def _is_windows() -> bool:
    """A function, not an inline check, so type checkers see both branches."""
    return sys.platform == "win32"


def spawn(argv: list[str], limits: Limits, **popen: Any) -> Confined:
    """Start ``argv`` confined by ``limits``. Pipes etc. go in ``popen``."""
    if _is_windows():
        job = _job(limits)
        flags = popen.pop("creationflags", 0) | _CREATE_SUSPENDED | _CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(argv, creationflags=flags, **popen)  # noqa: S603
        except BaseException:
            _kernel32().CloseHandle(ctypes.c_void_p(job))
            raise
        kernel32 = _kernel32()
        if not kernel32.AssignProcessToJobObject(
            ctypes.c_void_p(job),
            ctypes.c_void_p(int(process._handle)),  # type: ignore[attr-defined]
        ):
            # Never let an unconfined child run: kill it before it starts.
            process.kill()
            kernel32.CloseHandle(ctypes.c_void_p(job))
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        _resume(process)
        return Confined(process, job)

    def limit() -> None:  # pragma: no cover - POSIX only
        import importlib

        # Imported by name: `resource` does not exist on Windows, where mypy runs.
        resource: Any = importlib.import_module("resource")
        size = max(64, limits.memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (size, size))

    process = subprocess.Popen(  # noqa: S603
        argv, preexec_fn=limit, start_new_session=True, **popen
    )
    return Confined(process, 0)
