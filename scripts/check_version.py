"""Assert the version is the same everywhere (guards against the v1.0.1 drift).

The version lives in four hand-edited places — ``pyproject.toml`` (the source of
truth), ``APP_VERSION`` (what the app reports to the user), and the two packaging
scripts (what installers are named). They drifted once, so this runs in CI: if
any of them disagrees with ``pyproject``, the build fails with a precise message.

Usage::

    python scripts/check_version.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _extract(path: Path, pattern: str) -> str | None:
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text)
    return match.group(1) if match else None


def main() -> int:
    canonical = _extract(ROOT / "pyproject.toml", r'(?m)^version\s*=\s*"([^"]+)"')
    if canonical is None:
        print("error: could not read version from pyproject.toml", file=sys.stderr)
        return 1

    # (label, path, regex capturing the version) — every place the version is written.
    sources = [
        (
            "APP_VERSION (app/services/container.py)",
            ROOT / "app" / "services" / "container.py",
            r'APP_VERSION\s*=\s*"([^"]+)"',
        ),
        (
            "VERSION (packaging/linux/build.sh)",
            ROOT / "packaging" / "linux" / "build.sh",
            r'VERSION="([^"]+)"',
        ),
        (
            "AppVersion (packaging/windows/strata.iss)",
            ROOT / "packaging" / "windows" / "strata.iss",
            r'#define AppVersion "([^"]+)"',
        ),
    ]

    mismatches: list[str] = []
    for label, path, pattern in sources:
        found = _extract(path, pattern)
        if found is None:
            mismatches.append(f"  {label}: version not found")
        elif found != canonical:
            mismatches.append(f"  {label}: {found!r} != {canonical!r}")

    if mismatches:
        print(f"Version drift (pyproject is {canonical!r}):", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1

    print(f"version consistent everywhere: {canonical}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
