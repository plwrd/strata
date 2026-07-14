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
]
datas += collect_data_files("certifi")

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
