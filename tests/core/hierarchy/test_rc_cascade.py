"""H2 + H3 done-when: an RC low-pass made into a circuit symbol and placed twice in a
row gives a second-order response.

The child sheet is R = 1k from IN to OUT and C = 1u from OUT to ground, with port
flags IN (in) and OUT (out). The parent sheet drives U1 -> U2 with an AC source. Two
identical loaded RC sections give H(s) = 1 / (s^2 R^2 C^2 + 3 s R C + 1): about 0 dB
well below f0 = 1 / (2 pi R C), -9.54 dB (1/3) at f0, and -40 dB/decade above it.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from core.hierarchy.symbolgen import SymbolGenerator
from core.schematic.model import GroundFlag, Instance, NetLabel, PortFlag, Schematic, TextItem
from core.schematic.netlister import netlist_schematic
from core.symbols.library import PROJECT, SymbolLibrary

R, C = 1e3, 1e-6
F0 = 1 / (2 * math.pi * R * C)


def rc_child() -> Schematic:
    return Schematic(
        title="RC low-pass",
        instances=[
            Instance("res", "R1", 0, 0, attrs={"Value": "1k"}),  # pins (16, 0) (16, 96)
            Instance("cap", "C1", 100, 96, attrs={"Value": "1u"}),  # pins (116, 96) (116, 160)
        ],
        labels=[NetLabel(116, 96, "OUT")],
        grounds=[GroundFlag(116, 160)],
        ports=[PortFlag(16, 0, "IN", "in"), PortFlag(16, 96, "OUT", "out")],
        texts=[TextItem(0, 200, ".ac dec 10 1 1k")],  # left out of the symbol
    )


def cascade() -> Schematic:
    """V1 -> U1 (rclp) -> U2 (rclp) -> 1G. rclp pins: IN (-16, 32), OUT (96, 32)."""
    return Schematic(
        title="Two RC sections",
        instances=[
            Instance("voltage", "V1", -200, 0, attrs={"Value": "AC 1"}),  # (-200,16) (-200,96)
            Instance("rclp", "U1", 0, 0),
            Instance("rclp", "U2", 160, 0),
            # an open output is an error in the netlist checks; 1G barely loads it
            Instance("res", "Rload", 240, 32, attrs={"Value": "1G"}),  # (256,32) (256,128)
        ],
        labels=[
            NetLabel(-200, 16, "in"),
            NetLabel(-16, 32, "in"),
            NetLabel(96, 32, "mid"),
            NetLabel(144, 32, "mid"),
            NetLabel(256, 32, "out"),
        ],
        grounds=[GroundFlag(-200, 96), GroundFlag(256, 128)],
        texts=[TextItem(0, 200, ".ac dec 20 1 100k")],
    )


@pytest.fixture
def library(tmp_path: Path) -> SymbolLibrary:
    lib = SymbolLibrary()
    made = SymbolGenerator.from_schematic("rclp", rc_child(), lambda n: lib.lookup(n)[0])
    assert made.ok, made.diagnostics
    assert made.write(tmp_path) == []
    lib.set_project_files([tmp_path / "rclp.asy"])
    return lib


def test_the_placed_symbol_netlists_to_x_lines_and_one_include(
    library: SymbolLibrary, tmp_path: Path
) -> None:
    entry = library.find("rclp")
    assert entry is not None and entry.source == PROJECT
    result = netlist_schematic(cascade(), lambda n: library.lookup(n)[0], tmp_path)
    assert result.ok, result.diagnostics
    lines = result.text.splitlines()
    assert lines.count(f".include {tmp_path / 'rclp.sub'}") == 1
    assert "XU1 in mid rclp" in lines and "XU2 mid out rclp" in lines
    [sub] = result.circuit.subcircuits
    assert sub.name == "rclp" and sub.ports == ["in", "out"]


@pytest.mark.ngspice
def test_two_rc_symbols_in_a_row_give_a_second_order_response(
    library: SymbolLibrary, tmp_path: Path
) -> None:
    from core.sim.ngspice_adapter import NgspiceAdapter

    result = netlist_schematic(cascade(), lambda n: library.lookup(n)[0], tmp_path)
    assert result.ok and result.deck, result.diagnostics
    sim = NgspiceAdapter()
    assert sim.available(), sim.last_error
    sim.load(result.deck)
    sim.run()
    data = sim.results()
    assert data is not None and data.analysis == "ac"
    f = np.real(data.vectors["frequency"])
    db = 20 * np.log10(np.abs(data.vectors["V(out)"]))

    def at(freq: float) -> float:
        return float(np.interp(math.log10(freq), np.log10(f), db))

    def expected(freq: float) -> float:
        s = 2j * math.pi * freq * R * C
        return 20 * math.log10(abs(1 / (s * s + 3 * s + 1)))

    for freq in (10.0, F0, 1e3, 1e4):
        assert at(freq) == pytest.approx(expected(freq), abs=0.05), freq
    assert at(10.0) == pytest.approx(0.0, abs=0.5)
    assert at(F0) == pytest.approx(20 * math.log10(1 / 3), abs=0.05)
    slope = at(1e5) - at(1e4)  # one decade far above f0
    assert slope == pytest.approx(-40.0, abs=0.5)  # one RC alone would give -20
