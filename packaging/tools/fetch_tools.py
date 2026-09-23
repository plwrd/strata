"""Fetch the helper tools the Windows build bundles, verified by SHA-256.

Streamed video in the encrypted web archive (YouTube, X) needs two programs
Strata does not write itself:

* **ffmpeg** — joins the video and audio tracks into one stream on a pipe. The
  **LGPL** build (no GPL encoders: Strata only remuxes, ``-c copy``), so the
  installer can carry it beside an MIT app. Its licence ships next to it.
* **Deno** — the JavaScript runtime yt-dlp uses for YouTube's signature
  challenge. MIT.

Each download is pinned to one exact file and checked against the hash below
before anything is extracted. A mismatch stops the build: a tool that runs with
the user's session for every saved site is not something to take on trust.

Usage (``packaging/windows/build.ps1`` runs this)::

    python packaging/tools/fetch_tools.py

To update: change the URL, download it once, and replace the hash — for Deno,
check it against the ``.sha256sum`` Deno publishes beside the zip. BtbN's
autobuild releases are pruned over time, so an old pin will eventually 404;
that fails loudly here, which is the point.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

TOOLS = (
    {
        "name": "ffmpeg",
        "version": "n8.1.3 (LGPL)",
        "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
        "autobuild-2026-09-22-13-18/ffmpeg-n8.1.3-win64-lgpl-8.1.zip",
        "sha256": "812a431d8927f78d704bc7cef4dbc997d3ba17605ca74316081eb005b1e9b213",
        # member suffix in the zip -> file name in packaging/tools/<name>/
        "extract": {"/bin/ffmpeg.exe": "ffmpeg.exe", "/LICENSE.txt": "LICENSE.txt"},
    },
    {
        "name": "deno",
        "version": "v2.9.7",
        "url": "https://github.com/denoland/deno/releases/download/v2.9.7/"
        "deno-x86_64-pc-windows-msvc.zip",
        "sha256": "a0c3101b4158d1dfb7d6a78a7bf0f3de80c96bb423c152beec8beb22786f2238",
        "extract": {"deno.exe": "deno.exe"},
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(tool: dict[str, object], destination: Path) -> None:
    target = destination / str(tool["name"])
    wanted = dict(tool["extract"])  # type: ignore[call-overload]
    if all((target / name).is_file() for name in wanted.values()):
        stamp = target / "SOURCE.txt"
        if stamp.is_file() and str(tool["sha256"]) in stamp.read_text(encoding="utf-8"):
            print(f"{tool['name']}: present ({tool['version']})")
            return

    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / "download.zip"
        print(f"{tool['name']}: downloading {tool['version']}")
        with urllib.request.urlopen(str(tool["url"]), timeout=300) as reply:  # noqa: S310
            with archive.open("wb") as out:
                shutil.copyfileobj(reply, out)
        actual = _sha256(archive)
        if actual != tool["sha256"]:
            raise SystemExit(
                f"{tool['name']}: checksum mismatch — expected {tool['sha256']}, got {actual}. "
                "Refusing to bundle it."
            )
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                for suffix, name in wanted.items():
                    if member.endswith(suffix):
                        with bundle.open(member) as src, (target / name).open("wb") as dst:
                            shutil.copyfileobj(src, dst)
        missing = [name for name in wanted.values() if not (target / name).is_file()]
        if missing:
            raise SystemExit(f"{tool['name']}: not found in the archive: {missing}")
        (target / "SOURCE.txt").write_text(
            f"{tool['name']} {tool['version']}\n{tool['url']}\nsha256 {tool['sha256']}\n",
            encoding="utf-8",
        )
        print(f"{tool['name']}: verified and extracted to {target}")


def main() -> int:
    for tool in TOOLS:
        fetch(tool, HERE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
