"""The atomic-replace retry: transient Windows holds wait, real errors raise.

The flake this guards against: on Windows CI, an antivirus scan can hold a
just-written ``.tmp`` file (or its target) for a few milliseconds, making
``os.replace`` fail with WinError 5 even though nothing is wrong. Every atomic
write goes through ``replace_atomic``, which retries briefly — and this test
proves both halves: recovery from a transient hold, and a persistent error
still raising.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.infrastructure.storage.paths import replace_atomic


def test_replaces_on_the_first_try(tmp_path: Path) -> None:
    source = tmp_path / "file.tmp"
    target = tmp_path / "file.json"
    source.write_text("content", encoding="utf-8")

    replace_atomic(source, target)

    assert target.read_text(encoding="utf-8") == "content"
    assert not source.exists()


def test_recovers_from_a_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "file.tmp"
    target = tmp_path / "file.json"
    source.write_text("content", encoding="utf-8")

    real_replace = Path.replace
    failures = {"remaining": 2}

    def flaky(self: Path, other: Path) -> Path:
        if failures["remaining"] > 0:
            failures["remaining"] -= 1
            raise PermissionError(5, "Access is denied (simulated AV hold)")
        return real_replace(self, other)

    monkeypatch.setattr(Path, "replace", flaky)
    replace_atomic(source, target)

    assert failures["remaining"] == 0
    assert target.read_text(encoding="utf-8") == "content"


def test_a_persistent_error_still_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "file.tmp"
    source.write_text("content", encoding="utf-8")

    calls = {"count": 0}

    def always_denied(self: Path, other: Path) -> Path:
        calls["count"] += 1
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(Path, "replace", always_denied)

    with pytest.raises(PermissionError):
        replace_atomic(source, tmp_path / "file.json", attempts=3)
    assert calls["count"] == 3
