"""Shared parts of the PyInstaller spec files (qtspice-windows/macos/linux.spec).

The ngspice shared library is bundled into ``<bundle>/ngspice/``, where
``core/sim/ngspice_adapter.py`` looks first in a packaged app. Where it comes from:

- ``QTSPICE_NGSPICE_LIB`` (environment): the library file, or a folder holding it;
- otherwise Windows: every DLL in third_party/ngspice/lib and third_party/ngspice/bin
  (ngspice.dll and the DLLs it needs);
- otherwise macOS: Homebrew's libngspice (``brew install libngspice``);
- otherwise Linux: the system libngspice (``apt install libngspice0``).

PyInstaller also collects the libraries that ngspice itself links to. The build stops
with a message if no library is found: an installer without ngspice is useless.
"""

from __future__ import annotations

import ctypes.util
import glob
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "packaging" / "qtspice.py"
APP_NAME = "QtSpice"

DATAS = [
    (str(ROOT / "ui" / "qml"), "ui/qml"),
    (str(ROOT / "examples"), "examples"),
    (str(ROOT / "core" / "symbols" / "builtin"), "core/symbols/builtin"),
]
HIDDEN_IMPORTS = ["app.smoke"]
EXCLUDES = ["tkinter", "pytest", "pytestqt", "ruff", "jsonschema", "PyInstaller"]


def _library_files(folder: Path, patterns: list[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        found += [Path(p) for p in glob.glob(str(folder / pattern))]
    return [p for p in found if p.is_file()]


def _real(paths: list[Path]) -> list[Path]:
    """Symlinks resolved (libngspice.so.0 -> libngspice.so.0.0.0), without duplicates."""
    unique: dict[Path, None] = {}
    for path in paths:
        unique[path.resolve()] = None
    return list(unique)


def find_ngspice(platform: str) -> list[Path]:
    """The ngspice library file(s) to bundle for ``platform`` (windows/macos/linux)."""
    patterns = {
        "windows": ["*.dll"],
        "macos": ["libngspice*.dylib"],
        "linux": ["libngspice.so*"],
    }[platform]
    env = os.environ.get("QTSPICE_NGSPICE_LIB")
    if env:
        path = Path(env)
        return _real([path] if path.is_file() else _library_files(path, patterns))

    if platform == "windows":
        third = ROOT / "third_party" / "ngspice"
        return _real(
            _library_files(third / "lib", patterns) + _library_files(third / "bin", patterns)
        )
    if platform == "macos":
        folders = []
        try:
            prefix = subprocess.run(
                ["brew", "--prefix", "libngspice"], capture_output=True, text=True, check=True
            ).stdout.strip()
            folders.append(Path(prefix) / "lib")
        except (OSError, subprocess.CalledProcessError):
            pass
        folders += [Path("/opt/homebrew/lib"), Path("/usr/local/lib")]
        for folder in folders:
            files = _library_files(folder, patterns)
            if files:
                return _real(files)
        return []
    found = ctypes.util.find_library("ngspice")  # e.g. "libngspice.so.0"
    folders = [
        Path(p) for p in ("/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib", "/usr/local/lib")
    ]
    for folder in folders:
        files = _library_files(folder, [found] if found else patterns)
        if files:
            return _real(files)
    return []


def ngspice_binaries(platform: str) -> list[tuple[str, str]]:
    files = find_ngspice(platform)
    if not files:
        raise SystemExit(
            f"ngspice shared library not found for {platform}: set QTSPICE_NGSPICE_LIB "
            "or see packaging/common.py"
        )
    print(f"QtSpice: bundling ngspice from {', '.join(str(f) for f in files)}")
    return [(str(f), "ngspice") for f in files]
