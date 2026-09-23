"""The confinement yt-dlp and ffmpeg run under (Windows Job Objects).

Each limit is exercised against a real child: it cannot start another
program, cannot grow past its memory ceiling, and dies when its job closes —
which is what guarantees it cannot outlive Strata.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from app.infrastructure import sandbox

# The interpreter itself, not a venv's launcher: that launcher starts the real
# python.exe as a second process, which a one-process job rightly refuses.
PYTHON = getattr(sys, "_base_executable", sys.executable)

pytestmark = [
    pytest.mark.security,
    pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows-only"),
]


def _run(code: str, limits: sandbox.Limits) -> subprocess.CompletedProcess[bytes]:
    confined = sandbox.spawn(
        [PYTHON, "-c", code], limits, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        out, err = confined.process.communicate(timeout=60)
    finally:
        confined.close()
    return subprocess.CompletedProcess([], confined.process.returncode, out, err)


def test_a_confined_child_runs_normally() -> None:
    result = _run("print('ok')", sandbox.Limits())
    assert result.returncode == 0 and result.stdout.strip() == b"ok"


def test_a_single_process_job_cannot_start_programs() -> None:
    code = (
        "import subprocess, sys\n"
        "try:\n"
        "    subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
        "    print('started')\n"
        "except OSError:\n"
        "    print('refused')\n"
    )
    result = _run(code, sandbox.Limits(max_processes=1))
    assert result.stdout.strip() == b"refused"


def test_memory_past_the_ceiling_is_refused() -> None:
    code = (
        "try:\n"
        "    block = bytearray(400 * 1024 * 1024)\n"
        "    print('allocated')\n"
        "except MemoryError:\n"
        "    print('refused')\n"
    )
    result = _run(code, sandbox.Limits(memory_mb=128))
    assert b"allocated" not in result.stdout


def test_closing_the_job_kills_the_child() -> None:
    confined = sandbox.spawn(
        [PYTHON, "-c", "import time; time.sleep(60)"], sandbox.Limits()
    )
    assert confined.process.poll() is None
    confined.close()
    deadline = time.monotonic() + 10
    while confined.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert confined.process.poll() is not None
