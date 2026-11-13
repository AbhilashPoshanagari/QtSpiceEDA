# PyInstaller spec: QtSpice for macOS, dist/QtSpice.app (built for the runner's CPU;
# unsigned, so a downloaded copy needs right-click > Open the first time).
#   pyinstaller --noconfirm packaging/qtspice-macos.spec
# Needs Homebrew's libngspice (brew install libngspice) or QTSPICE_NGSPICE_LIB.
# Check the result with:
#   dist/QtSpice.app/Contents/MacOS/QtSpice --smoke-test --report smoke.txt
import sys

sys.path.insert(0, SPECPATH)  # noqa: F821  (SPECPATH is set by PyInstaller)
from common import (
    APP_NAME,
    DATAS,
    EXCLUDES,
    HIDDEN_IMPORTS,
    HOOKS_DIR,
    LAUNCHER,
    ROOT,
    grid_hidden_imports,
    ngspice_binaries,
)

a = Analysis(  # noqa: F821
    [str(LAUNCHER)],
    pathex=[str(ROOT)],
    binaries=ngspice_binaries("macos"),
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS + grid_hidden_imports(),
    hookspath=[str(HOOKS_DIR)],
    excludes=EXCLUDES,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)  # noqa: F821
app = BUNDLE(  # noqa: F821
    coll,
    name=f"{APP_NAME}.app",
    bundle_identifier="org.qtspice.QtSpice",
    info_plist={
        "CFBundleShortVersionString": "0.4.0",
        "NSHighResolutionCapable": True,
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "QtSpice project",
                "CFBundleTypeExtensions": ["qsp"],
                "CFBundleTypeRole": "Editor",
            }
        ],
    },
)
