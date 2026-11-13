# PyInstaller hook (from spike S3, scratch/phase4/spike3_pyinstaller/): bundle
# DSS-Python's native engine libraries, but not the debug build (_dss_capid /
# dss_capid), which is only loaded when DSS_EXTENSIONS_DEBUG=1. _cffi_backend is
# imported from C by the compiled _dss_capi module, so PyInstaller can't see it.
from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = [
    (src, dest) for src, dest in collect_dynamic_libs("dss_python_backend") if "capid" not in src
]
hiddenimports = ["dss_python_backend._dss_capi", "_cffi_backend"]
excludedimports = ["dss_python_backend._dss_capid"]
