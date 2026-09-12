# PyInstaller spec for the v1.0.0 AppImage payload.
# Built by scripts/build_appimage.sh; do not invoke from an arbitrary cwd.
#
# Do not collect_all("PySide6"): that pulls Qt3D/Charts/QML/SQL and inflates
# the image by hundreds of megabytes. The Qt* hooks follow real imports.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
ENTRY = ROOT / "src" / "steam_desktop_importer" / "__main__.py"

datas = []
binaries = []
hiddenimports = collect_submodules("steam_desktop_importer")
hiddenimports += [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
]
pkg_datas, pkg_binaries, pkg_hidden = collect_all("shiboken6")
datas += pkg_datas
binaries += pkg_binaries
hiddenimports += pkg_hidden

a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "PySide6.scripts",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="steam-desktop-importer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="steam-desktop-importer",
)
