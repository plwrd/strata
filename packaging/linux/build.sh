#!/usr/bin/env bash
#
# Build the Ubuntu / Linux packages for Strata: a PyInstaller bundle, an AppImage
# and a .deb.
#
# Linux packages are built on Linux. PyInstaller does not cross-compile and Qt
# WebEngine ships a different native payload per platform, so a "Linux build" made
# on Windows would be a Windows build with a different file extension.
#
# Usage:
#   ./packaging/linux/build.sh            # bundle only
#   ./packaging/linux/build.sh --appimage # bundle + AppImage
#   ./packaging/linux/build.sh --deb      # bundle + .deb

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

VERSION="1.0.0"
PYTHON="${PYTHON:-python3}"
[ -x ".venv/bin/python" ] && PYTHON=".venv/bin/python"

WANT_APPIMAGE=0
WANT_DEB=0
for arg in "$@"; do
  case "$arg" in
    --appimage) WANT_APPIMAGE=1 ;;
    --deb) WANT_DEB=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "==> Building the frontend"
"$PYTHON" scripts/sync_qwebchannel.py
npm --prefix frontend ci
npm --prefix frontend run build

echo "==> Freezing the Python host"
"$PYTHON" -m PyInstaller --noconfirm --clean packaging/pyinstaller/strata.spec

BUNDLE="dist/Strata"
test -x "$BUNDLE/Strata" || { echo "error: $BUNDLE/Strata is missing" >&2; exit 1; }

echo "==> Smoke-testing the packaged executable (offscreen)"
QT_QPA_PLATFORM=offscreen timeout 20s "$BUNDLE/Strata" &
SMOKE_PID=$!
sleep 8
if ! kill -0 "$SMOKE_PID" 2>/dev/null; then
  echo "error: the packaged application exited immediately" >&2
  exit 1
fi
kill "$SMOKE_PID" 2>/dev/null || true
echo "==> Packaged application starts"

if [ "$WANT_APPIMAGE" -eq 1 ]; then
  echo "==> Building the AppImage"
  command -v appimagetool >/dev/null 2>&1 || {
    echo "error: appimagetool is not on PATH (https://appimage.github.io/appimagetool/)" >&2
    exit 1
  }
  APPDIR="dist/Strata.AppDir"
  rm -rf "$APPDIR"
  mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
           "$APPDIR/usr/share/icons/hicolor/256x256/apps"

  cp -r "$BUNDLE/." "$APPDIR/usr/bin/"
  cp packaging/linux/strata.desktop "$APPDIR/usr/share/applications/"
  cp packaging/linux/strata.desktop "$APPDIR/"
  cp packaging/icons/strata.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/strata.png"
  cp packaging/icons/strata.png "$APPDIR/strata.png"

  cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/bin:$PATH"
exec "$HERE/usr/bin/Strata" "$@"
APPRUN
  chmod +x "$APPDIR/AppRun"

  ARCH=x86_64 appimagetool "$APPDIR" "dist/Strata-${VERSION}-x86_64.AppImage"
  echo "==> dist/Strata-${VERSION}-x86_64.AppImage"
fi

if [ "$WANT_DEB" -eq 1 ]; then
  echo "==> Building the .deb"
  DEBDIR="dist/strata_${VERSION}_amd64"
  rm -rf "$DEBDIR"
  mkdir -p "$DEBDIR/DEBIAN" \
           "$DEBDIR/opt/strata" \
           "$DEBDIR/usr/bin" \
           "$DEBDIR/usr/share/applications" \
           "$DEBDIR/usr/share/icons/hicolor/256x256/apps"

  cp -r "$BUNDLE/." "$DEBDIR/opt/strata/"
  cp packaging/linux/strata.desktop "$DEBDIR/usr/share/applications/"
  cp packaging/icons/strata.png "$DEBDIR/usr/share/icons/hicolor/256x256/apps/strata.png"
  ln -sf /opt/strata/Strata "$DEBDIR/usr/bin/strata"

  INSTALLED_KB="$(du -sk "$DEBDIR/opt/strata" | cut -f1)"
  cat > "$DEBDIR/DEBIAN/control" <<EOF
Package: strata
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Strata <security@strata.example>
Installed-Size: ${INSTALLED_KB}
Depends: libnss3, libxcomposite1, libxdamage1, libxrandr2, libxkbcommon0, libasound2t64 | libasound2, libgbm1
Description: Strata — local-first encrypted spatial knowledge workspace
 Markdown notes you own, layered by sensitivity, connected by a knowledge graph,
 and readable by an AI model only when you say so. Works offline.
EOF

  dpkg-deb --build --root-owner-group "$DEBDIR" "dist/strata_${VERSION}_amd64.deb"
  echo "==> dist/strata_${VERSION}_amd64.deb"
fi

echo "==> Done"
