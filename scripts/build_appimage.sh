#!/usr/bin/env bash
# Build a host AppImage of Steam Desktop Importer (v1.0.0).
# Needs network the first time (PyInstaller + appimagetool). Does not write Steam.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -n1)"
DIST="$ROOT/dist"
BUILD="$ROOT/build/appimage"
APPDIR="$BUILD/AppDir"
SPEC="$ROOT/packaging/linux/steam-desktop-importer.spec"
DESKTOP="$ROOT/packaging/linux/steam-desktop-importer.desktop"
ICON="$ROOT/packaging/linux/steam-desktop-importer.svg"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
  echo "error: $PYTHON is not executable; create the venv or set PYTHON=" >&2
  exit 1
fi

mkdir -p "$DIST" "$BUILD"
if [[ -x "$ROOT/.venv/bin/uv" ]]; then
  UV="$ROOT/.venv/bin/uv"
elif command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
else
  UV=""
fi
if [[ -n "$UV" ]]; then
  "$UV" pip install --python "$PYTHON" -q "pyinstaller>=6.0"
else
  "$PYTHON" -m pip install -q "pyinstaller>=6.0"
fi
(
  cd "$BUILD"
  "$PYTHON" -m PyInstaller --noconfirm --clean --distpath "$BUILD/pyinstaller" "$SPEC"
)

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -a "$BUILD/pyinstaller/steam-desktop-importer/." "$APPDIR/usr/bin/"
cp "$DESKTOP" "$APPDIR/usr/share/applications/steam-desktop-importer.desktop"
cp "$DESKTOP" "$APPDIR/steam-desktop-importer.desktop"
cp "$ICON" "$APPDIR/usr/share/icons/hicolor/scalable/apps/steam-desktop-importer.svg"
cp "$ICON" "$APPDIR/steam-desktop-importer.svg"
mkdir -p "$APPDIR/usr/share/doc/steam-desktop-importer"
cp "$ROOT/LICENSE" "$APPDIR/usr/share/doc/steam-desktop-importer/LICENSE"
cp "$ROOT/CHANGELOG.md" "$APPDIR/usr/share/doc/steam-desktop-importer/CHANGELOG.md"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/bin:$PATH"
exec "$HERE/usr/bin/steam-desktop-importer" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# uv-managed CPython currently ships libpython with PT_GNU_STACK RWE.
# glibc 2.41+ refuses to dlopen that; CPython does not need an executable stack.
"$PYTHON" "$ROOT/scripts/clear_elf_execstack.py" "$APPDIR"

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) APPIMAGE_ARCH="x86_64" ;;
  aarch64) APPIMAGE_ARCH="aarch64" ;;
  *) APPIMAGE_ARCH="$ARCH" ;;
esac

TOOL="$BUILD/appimagetool-$APPIMAGE_ARCH.AppImage"
if [[ ! -x "$TOOL" ]]; then
  URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${APPIMAGE_ARCH}.AppImage"
  curl -fsSL "$URL" -o "$TOOL"
  chmod +x "$TOOL"
fi

OUTPUT="$DIST/Steam_Desktop_Importer-${VERSION}-${APPIMAGE_ARCH}.AppImage"
ARCH="$APPIMAGE_ARCH" "$TOOL" "$APPDIR" "$OUTPUT"
echo "built $OUTPUT"
