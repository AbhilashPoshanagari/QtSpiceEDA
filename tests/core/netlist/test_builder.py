from __future__ import annotations

from pathlib import Path

import pytest

from core.netlist.builder import build
from core.netlist.model import Analysis
from core.netlist.parser import parse
from core.sim.ngspice_adapter import NgspiceAdapter
from tests.netlists import GOOD_FILES, INCLUDE_FILE

ALL_GOOD = [*GOOD_FILES, INCLUDE_FILE]


@pytest.mark.parametrize("path", ALL_GOOD, ids=lambda p: p.name)
def test_parse_build_parse_round_trip(path: Path) -> None:
    first = parse(path.read_text(), path.parent)
    deck = build(first.circuit)
    second = parse(deck)
    assert second.diagnostics == []
    assert second.circuit == first.circuit
    assert build(second.circuit) == deck


def test_deck_layout() -> None:
    text = (
        "Title here\n"
        "R1 in out 1k ; comment\n"
        "C1 out 0\n+ 1u\n"
        "V1 in 0 sin (0 1 1k)\n"
        ".model d1 D(is=1e-14)\n"
        ".param a=1\n"
        ".subckt s x y params: k=3\nR9 x y {k}\n.ends\n"
        "X1 out 0 s k=4\n"
        ".tran 1u 1m\n.end\n"
    )
    assert build(parse(text).circuit) == (
        "Title here\n"
        ".param a=1\n"
        ".model d1 D (is=1e-14)\n"
        ".subckt s x y params: k=3\n"
        "R9 x y {k}\n"
        ".ends s\n"
        "R1 in out 1k\n"
        "C1 out 0 1u\n"
        "V1 in 0 sin(0 1 1k)\n"
        "X1 out 0 s params: k=4\n"
        ".tran 1u 1m\n"
        ".end\n"
    )


def test_analysis_replaces_all_analysis_cards() -> None:
    circuit = parse("t\nR1 a 0 1\n.op\n.tran 1u 1m\n.end\n").circuit
    deck = build(circuit, Analysis("ac", ["dec", "10", "1", "1meg"]))
    assert deck.splitlines()[-2:] == [".ac dec 10 1 1meg", ".end"]
    assert ".op" not in deck and ".tran" not in deck
    assert build(circuit, ".dc V1 0 1 0.1").splitlines()[-2] == ".dc V1 0 1 0.1"
    # The circuit itself is unchanged.
    assert [a.kind for a in circuit.analyses] == ["op", "tran"]


def test_included_content_is_inlined() -> None:
    deck = build(parse(INCLUDE_FILE.read_text(), INCLUDE_FILE.parent).circuit)
    assert ".include" not in deck
    assert ".subckt opamp inp inn out params: gain=1e5" in deck


@pytest.mark.ngspice
@pytest.mark.parametrize("path", ALL_GOOD, ids=lambda p: p.name)
def test_ngspice_accepts_built_deck(path: Path) -> None:
    circuit = parse(path.read_text(), path.parent).circuit
    sim = NgspiceAdapter()
    sim.load(build(circuit))
    sim.run()
    assert sim.results() is not None
