"""A project that uses an external model library from a local folder.

``tests/external_libraries/tsmc_180_nm`` holds the TSMC 180 nm BSIM3 (level 49)
models ``tsmc018.lib`` and the LTspice symbols ``cmosn.asy`` / ``cmosp.asy``, whose
``ModelFile`` names the library. ``cmos_inverter.qsp`` lists the two symbols as
project symbols by relative path; the netlister includes the library from beside
them, so the project holds no machine-specific path.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.netlist.builder import build
from core.netlist.libindex import LibraryIndex
from core.netlist.parser import parse
from core.project.io import Project, load_project
from core.schematic.netlister import NetlistedSchematic, netlist_schematic
from core.sim.ngspice_adapter import NgspiceAdapter
from core.sim.result import SimulationResult
from core.symbols.asy import AsyParser
from core.symbols.library import SymbolLibrary

FOLDER = Path(__file__).resolve().parents[2] / "external_libraries"
LIBRARY = FOLDER / "tsmc_180_nm"
PROJECT = FOLDER / "cmos_inverter.qsp"
VDD = 1.8


def load() -> Project:
    loaded = load_project(PROJECT)
    assert loaded.ok, loaded.errors
    assert loaded.warnings == []
    assert loaded.project is not None
    return loaded.project


def netlisted() -> NetlistedSchematic:
    """Netlist the project the way the app does: project symbols by absolute path,
    includes resolved from the project folder."""
    project = load()
    lib = SymbolLibrary(project_files=[PROJECT.parent / s for s in project.symbols])
    result = netlist_schematic(project.schematic, lambda n: lib.lookup(n)[0], PROJECT.parent)
    assert result.ok, result.diagnostics
    return result


def simulate(text: str, card: str | None = None) -> SimulationResult:
    parsed = parse(text)
    assert parsed.ok, parsed.diagnostics
    adapter = NgspiceAdapter()
    adapter.load(build(parsed.circuit, card))
    adapter.run()
    result = adapter.results()
    assert result is not None
    return result


# ---- the library folder


@pytest.mark.parametrize(
    ("name", "prefix", "model"), [("cmosn", "MN", "CMOSN"), ("cmosp", "MP", "CMOSP")]
)
def test_symbols_name_their_model_and_library(name: str, prefix: str, model: str) -> None:
    symbol, diagnostics = AsyParser.parse(LIBRARY / f"{name}.asy")
    assert [d for d in diagnostics if d.severity == "error"] == []
    assert symbol.prefix == prefix
    assert symbol.attr("Value") == model
    assert symbol.attr("ModelFile") == "tsmc018.lib"
    by_order = sorted(symbol.pins, key=lambda p: p.spice_order or 0)
    assert [p.name for p in by_order] == ["D", "G", "S", "B"]


def test_library_index_finds_both_models() -> None:
    index = LibraryIndex.scan([LIBRARY])
    assert index.diagnostics == []
    assert not index.encrypted_files
    (nmos,) = index.find_model("CMOSN")
    (pmos,) = index.find_model("CMOSP")
    assert (nmos.type.lower(), pmos.type.lower()) == ("nmos", "pmos")
    assert nmos.file.resolve() == (LIBRARY / "tsmc018.lib").resolve()


# ---- the project


def test_project_uses_the_symbols_by_relative_path() -> None:
    project = load()
    assert project.symbols == ["tsmc_180_nm/cmosn.asy", "tsmc_180_nm/cmosp.asy"]
    text = PROJECT.read_text(encoding="utf-8")
    assert ":\\\\" not in text and "/Users/" not in text  # no absolute path inside


def test_inverter_netlist_includes_the_local_library() -> None:
    lines = netlisted().text.splitlines()
    includes = [line for line in lines if line.lower().startswith(".include")]
    assert len(includes) == 1  # both symbols name the same file: included once
    included = Path(includes[0].split(None, 1)[1].strip('"'))
    assert included.resolve() == (LIBRARY / "tsmc018.lib").resolve()
    assert "MP1 out in vdd vdd CMOSP L=180n W=0.9u" in lines
    assert "MN1 out in 0 0 CMOSN L=180n W=0.36u" in lines
    assert ".tran 1p 40n" in lines


# ---- ngspice runs the TSMC models


@pytest.mark.ngspice
def test_inverter_dc_transfer() -> None:
    data = simulate(netlisted().text, f".dc Vin 0 {VDD} 1m")
    vin = np.real(data.vectors[data.axis])
    vout = np.real(data.vectors["V(out)"])
    # rail to rail, falling all the way (gate-drain coupling lifts it by 0.5 mV at
    # the very start of the sweep)
    assert vout[0] == pytest.approx(VDD, abs=1e-3)
    assert vout[-1] == pytest.approx(0.0, abs=1e-3)
    assert np.all(np.diff(vout) <= 1e-3)
    assert vout.max() < VDD + 1e-3
    # switching threshold Vm (vout = vin): below VDD/2, because W_p / W_n = 2.5 does
    # not make up for the hole mobility being about 4x lower; measured 0.82 V
    vm = float(np.interp(0.0, (vout - vin)[::-1], vin[::-1]))
    assert 0.75 < vm < VDD / 2
    gain = np.abs(np.gradient(vout, vin))
    assert float(np.interp(vm, vin, gain)) > 5.0


def _crossing(t: np.ndarray, x: np.ndarray, level: float, rising: bool, after: float) -> float:
    for i in np.nonzero(t > after)[0][:-1]:
        a, b = x[i], x[i + 1]
        if (a < level <= b) if rising else (a > level >= b):
            return float(t[i] + (level - a) * (t[i + 1] - t[i]) / (b - a))
    raise AssertionError("no crossing")


@pytest.mark.ngspice
def test_inverter_transient_delays() -> None:
    data = simulate(netlisted().text)  # the schematic's own .tran 1p 40n
    t = np.real(data.vectors["time"])
    vin, vout = np.real(data.vectors["V(in)"]), np.real(data.vectors["V(out)"])
    assert t[-1] == pytest.approx(40e-9)
    half = VDD / 2
    tp_hl = _crossing(t, vout, half, False, 0) - _crossing(t, vin, half, True, 0)
    tp_lh = _crossing(t, vout, half, True, 5e-9) - _crossing(t, vin, half, False, 5e-9)
    # into 10 fF: tens of ps (measured 55 ps and 61 ps)
    assert 20e-12 < tp_hl < 150e-12
    assert 20e-12 < tp_lh < 150e-12
    # settled levels just before the next input edge (input high 1..6 ns, low after)
    assert float(np.interp(5.9e-9, t, vout)) == pytest.approx(0.0, abs=1e-3)
    assert float(np.interp(10.9e-9, t, vout)) == pytest.approx(VDD, abs=1e-3)
    # the 50 ps input edges feed through C_gd: a small overshoot past each rail
    assert VDD < vout.max() < VDD + 0.1
    assert -0.1 < vout.min() < 0.0
