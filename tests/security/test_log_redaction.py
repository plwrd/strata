"""The log redactor covers everything that reaches the log.

SECURITY.md states the rule without exceptions: passwords, keys and decrypted
content are never logged, and paths are reduced to a basename so a log file a
user attaches to a bug report does not carry their folder structure or their
user name.

Two places the redactor used to miss, both closed here:

* **Tracebacks.** ``logger.exception`` was rendered to a string *after* the
  redactor ran, so every unhandled bridge error wrote absolute paths — the one
  case where a log line is guaranteed to be full of them.
* **Nested values.** Structured logging invites ``details={...}``; a rule that
  only inspected the top level held for ``password=`` and lapsed for
  ``details={"password": ...}``.
"""

from __future__ import annotations

import pytest

from app.infrastructure.logging.logger import _redactor

pytestmark = pytest.mark.security


def redact(**event: object) -> dict[str, object]:
    return dict(_redactor(None, "info", dict(event)))


# --- sensitive keys ---------------------------------------------------------


def test_sensitive_keys_are_redacted() -> None:
    out = redact(event="layer.unlock", password="correct horse", api_key="sk-live-123")

    assert out["password"] == "<redacted>"
    assert out["api_key"] == "<redacted>"


def test_sensitive_keys_are_redacted_inside_details() -> None:
    out = redact(event="bridge.error", details={"password": "hunter2", "layerId": "layer_1"})

    assert out["details"] == {"password": "<redacted>", "layerId": "layer_1"}


def test_sensitive_keys_are_redacted_deep_in_a_list() -> None:
    out = redact(event="x", items=[{"secret": "s3cret"}, {"ok": "fine"}])

    assert out["items"] == [{"secret": "<redacted>"}, {"ok": "fine"}]


# --- paths ------------------------------------------------------------------


def test_paths_are_reduced_to_a_basename() -> None:
    out = redact(event="x", detail=r"failed on C:\Users\ana\Documents\Strata\notes.md")

    assert "ana" not in str(out["detail"])
    assert "<path:notes.md>" in str(out["detail"])


def test_paths_are_reduced_inside_nested_values() -> None:
    out = redact(event="x", details={"file": "/home/ana/Strata/journal.md"})

    assert out["details"] == {"file": "<path:journal.md>"}


def test_a_rendered_traceback_is_redacted() -> None:
    """`format_exc_info` runs before the redactor, so the string it produces is
    still subject to it. If the order is ever swapped back, this fails."""
    traceback = (
        'Traceback (most recent call last):\n  File "C:\\Users\\ana\\strata\\app\\x.py", '
        "line 3, in f\nValueError: boom"
    )

    out = redact(event="bridge.unhandled", exception=traceback)

    assert "ana" not in str(out["exception"])
    assert "<path:x.py>" in str(out["exception"])


def test_an_unhandled_exception_is_logged_without_its_paths(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End to end, through the real processor chain.

    This is the case that regressed: `logger.exception` renders a traceback full
    of absolute paths, and the redactor has to run *after* that rendering to see
    them. Asserting on the emitted line — rather than on the shape of the chain
    — keeps the guarantee pinned to behaviour.
    """
    from app.infrastructure.logging.logger import get_logger

    logger = get_logger("tests.redaction")

    with caplog.at_level("WARNING"):
        try:
            raise ValueError("could not read " + r"C:\Users\ana\Documents\Strata\journal.md")
        except ValueError:
            logger.exception("bridge.unhandled", method="NotesBridge.read")

    emitted = caplog.text
    assert "bridge.unhandled" in emitted
    assert "ana" not in emitted
    assert "<path:journal.md>" in emitted


def test_a_non_string_value_survives_intact() -> None:
    out = redact(event="x", count=3, ok=True, ratio=1.5)

    assert (out["count"], out["ok"], out["ratio"]) == (3, True, 1.5)
