# PyInstaller hook (spike S3): bundle DSS-Python's native engine libraries, but not
# the debug build (_dss_capid / dss_capid), which is only loaded when
# DSS_EXTENSIONS_DEBUG=1.
from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = [
    (src, dest) for src, dest in collect_dynamic_libs("dss_python_backend") if "capid" not in src
]
hiddenimports = ["dss_python_backend._dss_capi", "_cffi_backend"]
excludedimports = ["dss_python_backend._dss_capid"]
