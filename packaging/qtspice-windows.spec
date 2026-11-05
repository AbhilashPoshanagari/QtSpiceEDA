# PyInstaller spec: QtSpice for Windows (64-bit), a one-folder app in dist/QtSpice/.
#   pyinstaller --noconfirm packaging/qtspice-windows.spec
# Needs third_party/ngspice/lib/ngspice.dll (or QTSPICE_NGSPICE_LIB); see common.py.
# Check the result with: dist\QtSpice\QtSpice.exe --smoke-test --report smoke.txt
import sys

sys.path.insert(0, SPECPATH)  # noqa: F821  (SPECPATH is set by PyInstaller)
from common import APP_NAME, DATAS, EXCLUDES, HIDDEN_IMPORTS, LAUNCHER, ROOT, ngspice_binaries

a = Analysis(  # noqa: F821
    [str(LAUNCHER)],
    pathex=[str(ROOT)],
    binaries=ngspice_binaries("windows"),
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
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)  # noqa: F821
