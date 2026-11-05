from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.sim.result import SimulationResult


def _tran() -> SimulationResult:
    t = np.linspace(0, 1e-3, 5)
    return SimulationResult(
        axis="time",
        vectors={"time": t, "V(out)": 1 - np.exp(-t / 1e-3)},
        units={"time": "s", "V(out)": "V"},
        analysis="tran",
    )


def test_axis_must_be_a_vector() -> None:
    with pytest.raises(ValueError):
        SimulationResult(axis="time", vectors={"V(out)": np.zeros(3)})


def test_trace_names_exclude_axis() -> None:
    assert _tran().trace_names() == ["V(out)"]


def test_csv_has_header_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    text = _tran().to_csv(path)
    lines = text.splitlines()
    assert lines[0] == "time [s],V(out) [V]"
    assert len(lines) == 6
    assert path.read_text(encoding="utf-8") == text
    assert float(lines[-1].split(",")[0]) == pytest.approx(1e-3)


def test_csv_splits_complex_vectors() -> None:
    result = SimulationResult(
        axis="frequency",
        vectors={"frequency": np.array([1.0, 10.0]), "V(out)": np.array([1 + 2j, 3 - 4j])},
        analysis="ac",
    )
    lines = result.to_csv().splitlines()
    assert lines[0] == "frequency,V(out).re,V(out).im"
    assert lines[2] == "10.0,3.0,-4.0"


@pytest.mark.parametrize("complex_data", [False, True])
def test_npz_round_trip(tmp_path: Path, complex_data: bool) -> None:
    original = _tran()
    if complex_data:
        original.vectors["V(out)"] = original.vectors["V(out)"] * (1 + 1j)
    path = tmp_path / "r.npz"
    original.save_npz(path)
    loaded = SimulationResult.load_npz(path)

    assert loaded.axis == original.axis
    assert loaded.analysis == original.analysis
    assert loaded.units == original.units
    assert list(loaded.vectors) == list(original.vectors)
    for name, data in original.vectors.items():
        np.testing.assert_array_equal(loaded.vectors[name], data)


def test_load_npz_rejects_foreign_file(tmp_path: Path) -> None:
    path = tmp_path / "other.npz"
    np.savez(path, a=np.zeros(2))
    with pytest.raises(ValueError):
        SimulationResult.load_npz(path)
