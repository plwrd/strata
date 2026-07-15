"""The Claude CLI process adapter.

This runs an external program with input derived from the user's notes, which is the
sharpest edge in the product. These tests use a fake "CLI" — a small Python script —
to prove the adapter's containment without needing the real tool:

* it is spawned without a shell (no interpolation, no injection);
* the prompt goes in on stdin, never as an argv element;
* the environment is allow-listed, so a stray API key is not passed through;
* cancellation kills the process;
* nothing labels it as local.
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
from pathlib import Path

import pytest

from app.domain.ai import AIMessage, AIRequest
from app.infrastructure.ai_providers.claude_cli import CLAUDE_CLI, ClaudeCliProvider

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


def write_fake_cli(tmp_path: Path, body: str) -> Path:
    """A stand-in 'claude' executable: a Python script we can inspect the behaviour of."""
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "import sys, os, json\n" + textwrap.dedent(body),
        encoding="utf-8",
    )
    launcher = tmp_path / ("claude.cmd" if sys.platform == "win32" else "claude.sh")
    if sys.platform == "win32":
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        launcher.chmod(0o755)
    return launcher


def request(prompt: str) -> AIRequest:
    return AIRequest(
        provider_id="claude-cli",
        model="default",
        messages=[AIMessage(role="user", content=prompt)],
    )


def test_it_is_never_labelled_local() -> None:
    assert CLAUDE_CLI.is_local is False


async def test_the_prompt_arrives_on_stdin_and_is_echoed_back(tmp_path: Path) -> None:
    cli = write_fake_cli(
        tmp_path,
        """
        data = sys.stdin.read()
        sys.stdout.write("GOT:" + data)
        """,
    )
    provider = ClaudeCliProvider(str(cli))

    events = [
        event async for event in provider.stream(request("hello from stdin"), asyncio.Event())
    ]

    text = "".join(event.text for event in events)
    assert "GOT:hello from stdin" in text
    assert events[-1].kind == "done"


async def test_the_prompt_is_not_passed_as_an_argument(tmp_path: Path) -> None:
    """argv is visible to every process on the machine. Private content must not be
    there — it must arrive on stdin only."""
    cli = write_fake_cli(
        tmp_path,
        """
        sys.stdout.write("ARGV:" + json.dumps(sys.argv[1:]))
        sys.stdin.read()
        """,
    )
    provider = ClaudeCliProvider(str(cli))

    events = [
        event async for event in provider.stream(request("SECRET-PAYLOAD-42"), asyncio.Event())
    ]

    argv_line = "".join(event.text for event in events)
    assert "SECRET-PAYLOAD-42" not in argv_line
    # Only the non-interactive flag is passed.
    assert "--print" in argv_line


async def test_the_environment_is_allow_listed(tmp_path: Path) -> None:
    """A secret sitting in the parent environment must not be passed to the child."""
    cli = write_fake_cli(
        tmp_path,
        """
        sys.stdin.read()
        sys.stdout.write("SECRET_ENV=" + os.environ.get("MY_SECRET_TOKEN", "<absent>"))
        """,
    )
    os.environ["MY_SECRET_TOKEN"] = "should-not-propagate"
    try:
        provider = ClaudeCliProvider(str(cli))
        events = [event async for event in provider.stream(request("hi"), asyncio.Event())]
    finally:
        del os.environ["MY_SECRET_TOKEN"]

    output = "".join(event.text for event in events)
    assert "SECRET_ENV=<absent>" in output


async def test_it_runs_without_a_shell(tmp_path: Path) -> None:
    """A prompt that looks like a shell command must be inert.

    If a shell were involved, the `; echo` below would run as a separate command.
    Because it goes to stdin of a no-shell exec, it is just text.
    """
    cli = write_fake_cli(
        tmp_path,
        """
        data = sys.stdin.read()
        sys.stdout.write("READ:" + repr(data))
        """,
    )
    provider = ClaudeCliProvider(str(cli))

    injection = "hello; echo PWNED > /tmp/pwned; rm -rf ~"
    events = [event async for event in provider.stream(request(injection), asyncio.Event())]

    output = "".join(event.text for event in events)
    # The whole thing arrived as one string on stdin — nothing was executed.
    assert "echo PWNED" in output
    assert not Path("/tmp/pwned").exists()  # noqa: S108 - asserting the injection did NOT run


async def test_cancellation_kills_the_process(tmp_path: Path) -> None:
    cli = write_fake_cli(
        tmp_path,
        """
        import time
        sys.stdin.read()
        for i in range(100):
            sys.stdout.write(f"line {i}\\n")
            sys.stdout.flush()
            time.sleep(0.2)
        """,
    )
    provider = ClaudeCliProvider(str(cli))
    cancel = asyncio.Event()

    collected = []
    async for event in provider.stream(request("go"), cancel):
        collected.append(event)
        if len([e for e in collected if e.kind == "delta"]) >= 1:
            cancel.set()

    # It stopped well before the 100 lines, and never claimed to be done.
    assert len([e for e in collected if e.kind == "delta"]) < 100
    assert all(event.kind != "done" for event in collected)


async def test_a_missing_executable_is_an_error_not_a_crash(tmp_path: Path) -> None:
    provider = ClaudeCliProvider(str(tmp_path / "does-not-exist"))

    events = [event async for event in provider.stream(request("hi"), asyncio.Event())]

    assert events[-1].kind == "error"
    assert "not found" in events[-1].error.lower()


async def test_health_check_reports_missing_cli() -> None:
    provider = ClaudeCliProvider("/nonexistent/claude")

    health = await provider.health_check()

    assert health.reachable is False
    assert health.configured is False
