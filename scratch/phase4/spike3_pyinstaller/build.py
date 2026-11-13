"""Spike 3: a one-file PyInstaller app that compiles IEEE 13 and prints a voltage.

Builds app.py with PyInstaller --onefile in a temporary folder, once per variant:
  plain    no options at all
  collect  --collect-binaries dss_python_backend
  hidden   --hidden-import _cffi_backend
  hook     --additional-hooks-dir with hook-dss_python_backend.py (collects the
           backend's native libraries except the debug build)
then runs each exe with IEEE13Nodeckt.dss, checks its 671.1 voltage against the
reference file and lists the native libraries inside the exe.

Run:  python scratch/phase4/spike3_pyinstaller/build.py [variant ...] [--keep DIR]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import common  # noqa: E402

NATIVE = (".dll", ".so", ".dylib", ".pyd")
VARIANTS: dict[str, list[str]] = {
    "plain": [],
    "collect": ["--collect-binaries", "dss_python_backend"],
    "hidden": ["--hidden-import", "_cffi_backend"],
    "hook": ["--additional-hooks-dir", str(HERE / "hooks")],
}


@dataclass
class BuildReport:
    variant: str
    built: bool
    build_s: float
    exe_size_mb: float
    ran: bool
    output: str
    pu_671_1: float | None
    native_libs: list[str]  # native files inside the one-file archive (dss-related)


def native_files(exe: Path) -> list[str]:
    from PyInstaller.archive.readers import CArchiveReader

    names = CArchiveReader(str(exe)).toc.keys()
    keep = ("dss", "klusolve", "winpthread", "cffi", "gomp", "gcc", "stdc++")
    return sorted(
        n
        for n in names
        if n.lower().endswith(NATIVE) or ".so." in n.lower()
        if any(k in n.lower() for k in keep)
    )


def build(variant: str, workdir: Path) -> BuildReport:
    name = f"gridspike_{variant}"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
        "--name", name,
        "--distpath", str(workdir / "dist"), "--workpath", str(workdir / "build"),
        "--specpath", str(workdir),
        *VARIANTS[variant], str(HERE / "app.py"),
    ]  # fmt: skip
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    build_s = time.perf_counter() - t0
    exe = workdir / "dist" / (name + (".exe" if sys.platform == "win32" else ""))
    if proc.returncode != 0 or not exe.exists():
        tail = (proc.stderr or proc.stdout)[-2000:]
        return BuildReport(variant, False, build_s, 0.0, False, tail, None, [])
    run = subprocess.run(
        [str(exe), str(common.CASES["ieee13"].master)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (run.stdout + run.stderr).strip()
    pu = None
    if run.returncode == 0 and "671.1=" in out:
        pu = float(out.split("671.1=")[1].split()[0])
    return BuildReport(
        variant,
        True,
        build_s,
        exe.stat().st_size / 1e6,
        run.returncode == 0,
        out[-1500:],
        pu,
        native_files(exe),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("variants", nargs="*", default=list(VARIANTS))
    parser.add_argument("--keep", type=Path, help="build here and keep the files")
    args = parser.parse_args()
    ref = common.load_reference("ieee13")
    ref_pu = ref["node_pu"][ref["node_names"].index("671.1")]
    work = args.keep or Path(tempfile.mkdtemp(prefix="gridspike_"))
    try:
        for variant in args.variants:
            rep = build(variant, work / variant)
            print(
                f"== {variant}: built={rep.built} ({rep.build_s:.0f} s, {rep.exe_size_mb:.1f} MB)"
            )
            print(f"   ran={rep.ran}  output: {rep.output}")
            if rep.pu_671_1 is not None:
                print(f"   671.1 = {rep.pu_671_1:.6f} pu (reference {ref_pu:.6f})")
            for lib in rep.native_libs:
                print(f"   bundled: {lib}")
    finally:
        if args.keep is None:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
