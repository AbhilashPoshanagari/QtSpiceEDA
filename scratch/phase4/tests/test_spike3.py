"""Spike 3: the one-file PyInstaller build with the dss_python_backend hook runs and
prints the IEEE 13 671.1 voltage of the reference file. Takes about a minute."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import common
import pytest

HERE = Path(__file__).resolve().parents[1] / "spike3_pyinstaller"
sys.path.insert(0, str(HERE))

import build  # noqa: E402

pytestmark = [
    pytest.mark.opendss,
    pytest.mark.skipif(
        importlib.util.find_spec("PyInstaller") is None, reason="PyInstaller not installed"
    ),
]


def test_hook_build_runs_and_matches_reference(tmp_path: Path) -> None:
    rep = build.build("hook", tmp_path)
    assert rep.built, rep.output
    assert rep.ran, rep.output
    ref = common.load_reference("ieee13")
    assert rep.pu_671_1 == pytest.approx(ref["node_pu"][ref["node_names"].index("671.1")], abs=1e-6)
    libs = [lib.lower() for lib in rep.native_libs]
    assert any("klusolve" in lib for lib in libs)
    assert any("_cffi_backend" in lib for lib in libs)
    assert not any("capid" in lib for lib in libs)  # debug engine left out
    print(f"hook build: {rep.exe_size_mb:.1f} MB, {rep.build_s:.0f} s; " + ", ".join(libs))
