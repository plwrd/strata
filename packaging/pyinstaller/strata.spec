# PyInstaller spec — shared by the Windows and Linux pipelines.
#
# Build the frontend first: the bundle in `frontend/dist` is a data dependency, and
# shipping without it produces an application that starts and then shows nothing.
#
# Windows packages are built on Windows and Linux packages on Linux: PyInstaller
# does not cross-compile, and Qt WebEngine's native payload differs per platform.

# ruff: noqa
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent.parent

FRONTEND_DIST = ROOT / "frontend" / "dist"
if not (FRONTEND_DIST / "index.html").is_file():
    raise SystemExit(
        "frontend/dist/index.html is missing.\n"
        "Run `npm --prefix frontend ci && npm --prefix frontend run build` first."
    )

datas = [
    (str(FRONTEND_DIST), "frontend/dist"),
    (str(ROOT / "packaging" / "icons"), "packaging/icons"),
    # Identity prompt (Qwythos / Distill Qwen) — loaded at runtime via resource_root().
    (str(ROOT / "SystemPrompt.md"), "."),
]
datas += collect_data_files("certifi")

# The WebView2 loader, for the Edge-engine research pane (ADR-0012). A ~166 KB
# shim that finds the installed Evergreen runtime — the runtime itself is not
# bundled and not installed by us. Windows only, and optional: without it the
# pane falls back to Qt WebEngine, so a Linux build simply has nothing to add.
WEBVIEW2_LOADER = ROOT / "packaging" / "webview2" / "WebView2Loader.dll"
if WEBVIEW2_LOADER.is_file():
    datas.append((str(WEBVIEW2_LOADER), "packaging/webview2"))

# ffmpeg (LGPL) and Deno for streamed video in the encrypted web archive,
# fetched and checksum-verified by packaging/tools/fetch_tools.py. Optional:
# without them the archive still saves pages and plain videos, and says why a
# YouTube/X video was skipped. Their licences ship beside them.
for tool in ("ffmpeg", "deno"):
    folder = ROOT / "packaging" / "tools" / tool
    if folder.is_dir():
        for item in folder.iterdir():
            datas.append((str(item), f"packaging/tools/{tool}"))

hiddenimports = [
    "app.bridge.workspace_bridge",
    "app.bridge.layer_bridge",
    "app.bridge.notes_bridge",
    "app.bridge.graph_bridge",
    "app.bridge.search_bridge",
    "app.bridge.ai_bridge",
    "app.bridge.export_bridge",
    "app.bridge.collaboration_bridge",
    "app.bridge.settings_bridge",
    "app.bridge.snapshot_bridge",
    "app.bridge.job_bridge",
]

# Qt modules Strata does not use. Excluding them keeps the bundle smaller and
# removes attack surface we would otherwise ship without ever calling.
excludes = [
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtQuick3D",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtSerialPort",
    "tkinter",
    "matplotlib",
    "pytest",
]

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Strata",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX and Qt WebEngine's sandbox do not get along
    console=False,
    disable_windowed_traceback=True,  # a traceback dialog can contain private paths
    icon=str(ROOT / "packaging" / "icons" / "strata.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Strata",
)
