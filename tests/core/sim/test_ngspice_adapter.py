from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.sim import ngspice_adapter
from core.sim.ngspice_adapter import library_candidates


def test_packaged_app_looks_in_its_bundle_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ngspice_adapter.LIBRARY_ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    first = library_candidates()[0]
    assert isinstance(first, Path)
    assert first.parent == tmp_path / "ngspice"


def test_versioned_library_names_in_the_bundle_are_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "ngspice"
    folder.mkdir()
    for name in ("libngspice.so.0.0.0", "libngspice.0.dylib", "readme.txt"):
        (folder / name).write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    found = [c for c in library_candidates() if isinstance(c, Path) and c.parent == folder]
    assert folder / "libngspice.so.0.0.0" in found
    assert folder / "libngspice.0.dylib" in found
    assert folder / "readme.txt" not in found


def test_source_checkout_has_no_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    roots = {c.parent for c in library_candidates() if isinstance(c, Path)}
    assert all("ngspice" in str(r) or r.name == "lib" for r in roots)
