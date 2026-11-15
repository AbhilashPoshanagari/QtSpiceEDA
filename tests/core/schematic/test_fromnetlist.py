"""N1: netlist -> Schematic (core/schematic/fromnetlist.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.netlist.model import ERROR, GROUND_NODES, WARNING, Element
from core.netlist.parser import parse
from core.schematic.connectivity import connect
from core.schematic.fromnetlist import GRID, schematic_from_netlist
from core.schematic.geometry import instance_box
from core.schematic.model import Schematic
from core.schematic.netlister import netlist_schematic
from core.symbols.library import SymbolLibrary

TESTS = Path(__file__).resolve().parents[2]
GOOD = sorted((TESTS / "netlists" / "good").glob("*.cir"))
BROKEN = sorted((TESTS / "netlists" / "broken").glob("*.cir"))
XOR = TESTS / "netlists" / "good" / "cmos_xor.cir"
FIXTURES = TESTS / "fixtures"


@pytest.fixture
def library() -> SymbolLibrary:
    return SymbolLibrary()


def lookup_of(library: SymbolLibrary):
    return lambda name: library.lookup(name)[0]


def norm(node: str) -> str:
    return "0" if node.casefold() in GROUND_NODES else node.casefold()


def key(e: Element) -> tuple:
    return (
        e.kind,
        [norm(n) for n in e.nodes],
        " ".join(e.value.split()).casefold(),
        {k: v.casefold() for k, v in e.params.items()},
    )


def assert_same_circuit(text: str, sheet: Schematic, library: SymbolLibrary, base=None, dirs=()):
    original = parse(text, base, dirs).circuit
    made = netlist_schematic(sheet, lookup_of(library), base, dirs)
    assert made.ok, [d.message for d in made.diagnostics]
    assert {e.name.casefold(): key(e) for e in made.circuit.elements} == {
        e.name.casefold(): key(e) for e in original.elements
    }
    assert sorted(m.name.casefold() for m in made.circuit.models) == sorted(
        m.name.casefold() for m in original.models
    )
    assert [(a.kind, a.args) for a in made.circuit.analyses] == [
        (a.kind, a.args) for a in original.analyses
    ]


# ---- round trip


@pytest.mark.parametrize("path", GOOD, ids=lambda p: p.name)
def test_every_good_netlist_round_trips(path: Path, library: SymbolLibrary) -> None:
    text = path.read_text()
    result = schematic_from_netlist(text, library, path.parent)
    assert result.ok, [d.message for d in result.diagnostics]
    assert_same_circuit(text, result.schematic, library, path.parent)


@pytest.mark.parametrize("path", BROKEN, ids=lambda p: p.name)
def test_a_netlist_with_errors_is_not_drawn(path: Path, library: SymbolLibrary) -> None:
    result = schematic_from_netlist(path.read_text(), library, path.parent)
    assert result.schematic is None
    messages = [d.message for d in result.diagnostics if d.severity == ERROR]
    assert messages[-1] == "fix the netlist errors before making a schematic"
    assert len(messages) >= 2  # the netlist's own errors come first


def test_empty_and_garbage_never_raise(library: SymbolLibrary) -> None:
    assert not schematic_from_netlist("", library).ok
    assert not schematic_from_netlist("\x00\n.end", library).ok
    assert not schematic_from_netlist(None, library).ok  # type: ignore[arg-type]


def test_analysis_is_required_like_the_check_button(library: SymbolLibrary) -> None:
    text = "no analysis\nR1 a 0 1k\nV1 a 0 1\n.end\n"
    assert not schematic_from_netlist(text, library).ok
    assert schematic_from_netlist(text, library, require_analysis=False).ok


# ---- the XOR


def test_xor_symbols_nets_and_texts(library: SymbolLibrary) -> None:
    result = schematic_from_netlist(XOR.read_text(), library)
    sheet = result.schematic
    assert sheet is not None and result.kept == []
    assert sheet.title == "* 8-transistor CMOS XOR gate"
    symbols = {i.name: i.symbol for i in sheet.instances}
    assert symbols == {
        "VDD": "voltage", "VA": "voltage", "VB": "voltage",
        "MP1": "pmos", "MN1": "nmos", "MP2": "pmos", "MN2": "nmos",
        "MN3": "nmos", "MP3": "pmos", "MN4": "nmos", "MP4": "pmos",
    }  # fmt: skip
    mp1 = next(i for i in sheet.instances if i.name == "MP1")
    assert mp1.attrs["Value"] == "PMOS" and mp1.attrs["SpiceLine"] == "w=2u l=0.18u"
    assert {lab.name for lab in sheet.labels} == {"vdd", "a", "b", "abar", "bbar", "y"}
    grounds = sum(1 for e in parse(XOR.read_text()).circuit.elements for n in e.nodes if n == "0")
    assert len(sheet.grounds) == grounds == 9
    nets = connect(sheet, lookup_of(library))
    elements = {e.name: e for e in parse(XOR.read_text()).circuit.elements}
    for inst, pins in zip(sheet.instances, nets.pins, strict=True):
        assert [norm(p) for p in pins] == [norm(n) for n in elements[inst.name].nodes]
    [directive] = [t for t in sheet.texts if t.directive]
    assert directive.text.splitlines() == [
        ".model NMOS NMOS (LEVEL=1 VTO=0.45 KP=200u)",
        ".model PMOS PMOS (LEVEL=1 VTO=-0.45 KP=100u)",
        ".tran 0.1n 80n",
    ]
    [comment] = [t for t in sheet.texts if not t.directive]
    assert "* Input signals" in comment.text.splitlines()


def test_layout_is_on_the_grid_without_overlaps(library: SymbolLibrary) -> None:
    sheet = schematic_from_netlist(XOR.read_text(), library).schematic
    assert sheet is not None
    lookup = lookup_of(library)
    for inst in sheet.instances:
        assert inst.x % GRID == 0 and inst.y % GRID == 0
    for w in sheet.wires:
        assert all(v % GRID == 0 for v in (w.x1, w.y1, w.x2, w.y2))
    boxes = [instance_box(i, lookup(i.symbol)) for i in sheet.instances]
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            x1, y1, x2, y2 = boxes[a]
            u1, v1, u2, v2 = boxes[b]
            assert x2 <= u1 or u2 <= x1 or y2 <= v1 or v2 <= y1, (a, b)
    rows = {i.x: 0 for i in sheet.instances}
    for i in sheet.instances:
        rows[i.x] += 1
    assert max(rows.values()) <= 4  # long columns are split


def test_sources_come_first_and_supply_on_top(library: SymbolLibrary) -> None:
    sheet = schematic_from_netlist(XOR.read_text(), library).schematic
    assert sheet is not None
    first = sorted((i for i in sheet.instances if i.x == sheet.instances[0].x), key=lambda i: i.y)
    assert [i.name for i in first] == ["VDD", "VA", "VB"]
    assert min(i.x for i in sheet.instances if i.name.startswith("M")) > first[0].x


def test_same_input_same_sheet_and_it_saves(library: SymbolLibrary) -> None:
    a = schematic_from_netlist(XOR.read_text(), library).schematic
    b = schematic_from_netlist(XOR.read_text(), library).schematic
    assert a is not None and b is not None and a.to_dict() == b.to_dict()
    back, diagnostics = Schematic.from_dict(a.to_dict())
    assert diagnostics == [] and back == a


# ---- symbols from the library


@pytest.fixture
def user_library(tmp_path: Path) -> tuple[SymbolLibrary, Path]:
    folder = tmp_path / "library"
    (folder / "symbols").mkdir(parents=True)
    (folder / "models").mkdir()
    shutil.copy(FIXTURES / "asy" / "cmosn.asy", folder / "symbols" / "cmosn.asy")
    shutil.copy(FIXTURES / "lib" / "tsmc018.lib", folder / "models" / "tsmc018.lib")
    return SymbolLibrary([folder]), folder


INVERTER = """inverter
.include tsmc018.lib
VDD vdd 0 1.8
VIN in 0 0.9
MP1 out in vdd vdd CMOSP W=2u L=0.18u
MN1 out in 0 0 CMOSN W=1u L=0.18u
.op
.end
"""


def test_library_symbol_is_used_for_its_model(user_library) -> None:
    library, folder = user_library
    result = schematic_from_netlist(INVERTER, library, None, [folder])
    assert result.ok, [d.message for d in result.diagnostics]
    symbols = {i.name: i.symbol for i in result.schematic.instances}
    assert symbols["MN1"] == "symbols/cmosn"  # from the library (Value CMOSN)
    assert symbols["MP1"] == "pmos"  # no cmosp symbol there: built-in by model type
    mn1 = next(i for i in result.schematic.instances if i.name == "MN1")
    assert mn1.attrs["ModelFile"] == ""  # the netlist's own .include is kept instead
    made = netlist_schematic(result.schematic, lookup_of(library), None, [folder])
    assert made.text.count(".include tsmc018.lib") == 1
    assert_same_circuit(INVERTER, result.schematic, library, None, [folder])


def test_library_symbol_found_by_spice_model_attribute(user_library) -> None:
    library, folder = user_library
    (folder / "symbols" / "cmosn.asy").rename(folder / "symbols" / "my_nfet.asy")
    library.refresh()
    result = schematic_from_netlist(INVERTER, library, None, [folder])
    assert result.ok
    symbols = {i.name: i.symbol for i in result.schematic.instances}
    assert symbols["MN1"] == "symbols/my_nfet"


def test_subcircuit_with_a_library_symbol_is_placed(tmp_path: Path) -> None:
    folder = tmp_path / "library"
    folder.mkdir()
    shutil.copy(FIXTURES / "asy" / "opamp5.asy", folder / "opamp5.asy")
    (folder / "opamp.lib").write_text(
        ".subckt opamp5 inp inn vcc vee out\nRin inp inn 10Meg\nRs vcc vee 100k\n"
        "E1 out 0 inp inn 100k\n.ends opamp5\n",
        encoding="utf-8",
    )
    library = SymbolLibrary([folder])
    text = (
        "buffer\n.include opamp.lib\nVCC vcc 0 5\nVEE vee 0 -5\nVIN in 0 SIN(0 1 1k)\n"
        "X1 in out vcc vee out opamp5\nRL out 0 10k\n.tran 1u 2m\n.end\n"
    )
    result = schematic_from_netlist(text, library, None, [folder])
    assert result.ok, [d.message for d in result.diagnostics]
    assert "X1" in result.placed
    assert_same_circuit(text, result.schematic, library, None, [folder])


def test_subcircuit_without_a_symbol_stays_as_text(library: SymbolLibrary) -> None:
    path = TESTS / "netlists" / "good" / "opamp_subckt.cir"
    result = schematic_from_netlist(path.read_text(), library, path.parent)
    assert result.ok and result.kept == ["X1"]
    [warning] = [d for d in result.diagnostics if d.severity == WARNING]
    assert "X1" in warning.message and "library folder" in warning.message
    assert warning.line == 10
    [directive] = [t for t in result.schematic.texts if t.directive]
    assert "X1 0 inv out opamp" in directive.text.splitlines()
    assert ".subckt opamp inp inn out" in directive.text.splitlines()


def test_controlled_source_stays_as_text(library: SymbolLibrary) -> None:
    text = "vcvs\nV1 in 0 1\nE1 out 0 in 0 10\nR1 out 0 1k\n.op\n.end\n"
    result = schematic_from_netlist(text, library)
    assert result.ok and result.kept == ["E1"] and set(result.placed) == {"V1", "R1"}
    assert_same_circuit(text, result.schematic, library)


def test_wrong_pin_count_is_not_used(library: SymbolLibrary) -> None:
    text = "q4\nV1 c 0 5\nV2 b 0 0.7\nQ1 c b 0 0 QN\n.model QN NPN\n.op\n.end\n"
    result = schematic_from_netlist(text, library)
    assert result.ok and result.kept == ["Q1"]  # built-in npn has 3 pins


# ---- texts and lines


def test_continuation_lines_and_gnd(library: SymbolLibrary) -> None:
    text = "cont\nV1 a gnd 1\nR1 a gnd\n+ 1k\n.op\n.end\nR9 junk after end\n"
    result = schematic_from_netlist(text, library)
    assert result.ok, [d.message for d in result.diagnostics]
    [directive] = [t for t in result.schematic.texts if t.directive]
    assert directive.text == ".op"
    assert len(result.schematic.grounds) == 2
    assert_same_circuit(text, result.schematic, library)


def test_elements_from_an_include_file_are_not_drawn(tmp_path: Path, library) -> None:
    (tmp_path / "load.inc").write_text("RL out 0 1k\n", encoding="utf-8")
    text = "inc\nV1 out 0 1\n.include load.inc\n.op\n.end\n"
    result = schematic_from_netlist(text, library, tmp_path)
    assert result.ok and result.placed == ["V1"]
    [directive] = [t for t in result.schematic.texts if t.directive]
    assert directive.text.splitlines() == [".include load.inc", ".op"]
    assert_same_circuit(text, result.schematic, library, tmp_path)


def test_params_and_values_survive(library: SymbolLibrary) -> None:
    text = (
        "param\n.param rval=2k\nV1 a 0 AC 1 SIN(0 1 1k)\nR1 a b {rval} tc1=0.001\n"
        "C1 b 0 10n ic=0\n.tran 1u 1m\n.end\n"
    )
    result = schematic_from_netlist(text, library)
    assert result.ok, [d.message for d in result.diagnostics]
    assert_same_circuit(text, result.schematic, library)
