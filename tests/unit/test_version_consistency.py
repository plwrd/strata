"""The version is the same everywhere, and the guard that enforces it works."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_version.py"


def _load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("check_version", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_is_consistent_across_the_repo() -> None:
    assert _load().main() == 0


def test_the_guard_detects_a_mismatch(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = _load()
    # Point the guard at a fake tree where APP_VERSION disagrees with pyproject.
    (tmp_path / "pyproject.toml").write_text('version = "9.9.9"\n', encoding="utf-8")
    app_dir = tmp_path / "app" / "services"
    app_dir.mkdir(parents=True)
    (app_dir / "container.py").write_text('APP_VERSION = "0.0.1"\n', encoding="utf-8")
    linux = tmp_path / "packaging" / "linux"
    linux.mkdir(parents=True)
    (linux / "build.sh").write_text('VERSION="9.9.9"\n', encoding="utf-8")
    win = tmp_path / "packaging" / "windows"
    win.mkdir(parents=True)
    (win / "strata.iss").write_text('#define AppVersion "9.9.9"\n', encoding="utf-8")

    monkeypatch.setattr(module, "ROOT", tmp_path)
    assert module.main() == 1
