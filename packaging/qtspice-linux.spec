# PyInstaller spec: QtSpice for Linux (x86-64), a one-folder app in dist/QtSpice/.
#   pyinstaller --noconfirm packaging/qtspice-linux.spec
# Needs the system libngspice (apt install libngspice0) or QTSPICE_NGSPICE_LIB.
# Check the result with: dist/QtSpice/QtSpice --smoke-test --report smoke.txt
import sys

sys.path.insert(0, SPECPATH)  # noqa: F821  (SPECPATH is set by PyInstaller)
from common import APP_NAME, DATAS, EXCLUDES, HIDDEN_IMPORTS, LAUNCHER, ROOT, ngspice_binaries

a = Analysis(  # noqa: F821
    [str(LAUNCHER)],
    pathex=[str(ROOT)],
    binaries=ngspice_binaries("linux"),
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
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
    strip=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)  # noqa: F821
