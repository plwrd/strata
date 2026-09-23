# Bundled helper tools

The Windows build bundles two programs for streamed video (YouTube, X) in the
encrypted web archive. Neither is committed; `build.ps1` runs
`fetch_tools.py`, which downloads a pinned file and **refuses to continue
unless its SHA-256 matches**.

| Tool | Version | Licence | Why |
| --- | --- | --- | --- |
| ffmpeg | n8.1.3, **LGPL** build ([BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)) | LGPL-2.1+ (`ffmpeg/LICENSE.txt` ships beside it) | Joins the video and audio tracks into one stream on a pipe (`-c copy`, no encoding) |
| Deno | v2.9.7 ([denoland/deno](https://github.com/denoland/deno)) | MIT | The JavaScript runtime yt-dlp needs for YouTube's signature challenge |

At runtime Strata looks for the configured ffmpeg path (Settings > Saved
pages) first, then the bundled copy, then `PATH`. Both run out of process, in a
Windows Job Object (see `app/infrastructure/sandbox.py`).

To update, change the URL in `fetch_tools.py`, download the file once, and
replace the hash. For Deno, check it against the `.sha256sum` Deno publishes
beside the zip. BtbN prunes old autobuilds, so an outdated pin fails the build
with a 404, which is how you find out it needs updating.
