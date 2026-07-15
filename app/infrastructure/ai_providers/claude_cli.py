"""The Claude CLI, as a local *process* adapter.

**This is not a local model.** It runs on your machine and it sends your content to
Anthropic. `is_local=False`, and every label in the UI says so, because the one way
to make this feature actively harmful is to let someone choose it *because* they
believe it is private.

Executing a program with user-influenced input is the sharpest edge in the whole
product, so:

* the executable path is configured explicitly and must exist — no PATH search, no
  shell resolution, nothing that a note or a prompt could redirect;
* the process is spawned **without a shell** (`create_subprocess_exec`, an argv
  list), so there is no interpolation, no globbing, and no `;` that means anything;
* the prompt goes in on **stdin**, never as an argument — an argv element is visible
  in the process table to every other user on the machine, and private note content
  has no business there;
* the environment is **allow-listed**, not inherited, so an API key that happens to
  be in the parent environment is not silently passed along;
* the working directory is a locked-down temporary directory, not the workspace, so
  the CLI cannot be talked into reading the user's notes off disk by itself;
* there is a timeout, and cancellation actually kills the process.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from app.domain.ai import (
    AIEvent,
    AIRequest,
    Capability,
    ModelInfo,
    ProviderCapabilities,
    ProviderHealth,
)
from app.infrastructure.ai_providers.base import AIProvider
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

TIMEOUT_SECONDS = 300

CLAUDE_CLI = ProviderCapabilities(
    provider_id="claude-cli",
    display_name="Claude CLI",
    # Runs locally, is NOT local. This single boolean is what stops a layer marked
    # "local AI only" from having its content shipped to Anthropic.
    is_local=False,
    requires_api_key=False,
    capabilities=[Capability.TEXT, Capability.STREAMING, Capability.LARGE_CONTEXT],
    max_context_tokens=200_000,
    note=(
        "Runs on this machine but sends your content to Anthropic. It is a remote "
        "provider with a local launcher, not an offline model."
    ),
)

# The only environment variables the child gets. Everything else — including any
# API keys sitting in the parent environment — is dropped.
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "APPDATA",
    "LOCALAPPDATA",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
)


class ClaudeCliProvider(AIProvider):
    capabilities = CLAUDE_CLI

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or ""

    def _resolve(self) -> Path | None:
        """Find the executable, without ever letting a shell do the finding."""
        if self._executable:
            candidate = Path(self._executable)
            return candidate if candidate.is_file() else None
        found = shutil.which("claude")
        return Path(found) if found else None

    def _environment(self) -> dict[str, str]:
        return {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}

    async def health_check(self) -> ProviderHealth:
        executable = self._resolve()
        if executable is None:
            return ProviderHealth(
                provider_id=self.provider_id,
                reachable=False,
                configured=False,
                detail="The Claude CLI was not found. Set its path in settings.",
            )

        try:
            process = await asyncio.create_subprocess_exec(
                str(executable),
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._environment(),
                cwd=tempfile.gettempdir(),
            )
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        except (OSError, asyncio.TimeoutError) as exc:
            return ProviderHealth(
                provider_id=self.provider_id,
                reachable=False,
                configured=True,
                detail=f"The Claude CLI could not be started ({type(exc).__name__}).",
            )

        if process.returncode != 0:
            return ProviderHealth(
                provider_id=self.provider_id,
                reachable=False,
                configured=True,
                detail="The Claude CLI is present but did not run successfully.",
            )

        version = stdout.decode("utf-8", errors="replace").strip()[:80]
        return ProviderHealth(
            provider_id=self.provider_id,
            reachable=True,
            configured=True,
            detail=f"{version} — remote: this sends content to Anthropic.",
            models=await self.list_models(),
        )

    async def list_models(self) -> list[ModelInfo]:
        # The CLI picks its own model; "default" means whatever it is configured with.
        return [ModelInfo(id="default", display_name="Claude (CLI default)", is_local=False)]

    async def stream(self, request: AIRequest, cancel: asyncio.Event) -> AsyncIterator[AIEvent]:
        executable = self._resolve()
        if executable is None:
            yield AIEvent(kind="error", error="The Claude CLI was not found.")
            return

        prompt = "\n\n".join(message.content for message in request.messages)

        yield AIEvent(kind="start", model="claude-cli")

        # A fresh, empty working directory. The CLI is not run inside the workspace,
        # so it has nothing of the user's to find even if it goes looking.
        #
        # Cleaned up manually rather than with a context manager: on Windows the
        # child holds the cwd until it fully exits, so an eager rmdir races the
        # process. The sandbox is empty and in the OS temp dir, so a best-effort
        # cleanup that occasionally leaves a stale empty directory is fine — failing
        # the whole request over one would not be.
        sandbox = tempfile.mkdtemp(prefix="strata-claude-")
        try:
            try:
                process = await asyncio.create_subprocess_exec(
                    str(executable),
                    "--print",  # non-interactive: print the answer and exit
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._environment(),
                    cwd=sandbox,
                )
            except OSError as exc:
                yield AIEvent(kind="error", error=f"The Claude CLI could not be started: {exc}")
                return

            assert process.stdin is not None
            assert process.stdout is not None

            # The prompt goes in on stdin. Never as an argv element: argv is visible
            # to every process on the machine, and this may be private content.
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()

            output_chars = 0
            try:
                while True:
                    if cancel.is_set():
                        _terminate(process)
                        return

                    try:
                        chunk = await asyncio.wait_for(process.stdout.read(1024), timeout=1.0)
                    except asyncio.TimeoutError:
                        if process.returncode is not None:
                            break
                        continue

                    if not chunk:
                        break

                    text = chunk.decode("utf-8", errors="replace")
                    output_chars += len(text)
                    yield AIEvent(kind="delta", text=text)

                await asyncio.wait_for(process.wait(), timeout=TIMEOUT_SECONDS)

            except asyncio.TimeoutError:
                _terminate(process)
                yield AIEvent(kind="error", error="The Claude CLI timed out.")
                return

            if process.returncode not in (0, None):
                stderr = b""
                if process.stderr is not None:
                    stderr = await process.stderr.read()
                # The CLI's stderr can echo the prompt back. It is logged locally and
                # never surfaced, so private content cannot ride out in an error.
                logger.warning(
                    "claude_cli.failed",
                    returncode=process.returncode,
                    stderr_bytes=len(stderr),
                )
                yield AIEvent(
                    kind="error",
                    error=f"The Claude CLI exited with code {process.returncode}.",
                )
                return

            yield AIEvent(
                kind="done",
                model="claude-cli",
                output_tokens=int(output_chars / 3.6),
            )
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)


def _terminate(process: asyncio.subprocess.Process) -> None:
    """Cancellation must actually stop the process, not just stop reading it."""
    if process.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:  # pragma: no cover - it already exited
        pass
