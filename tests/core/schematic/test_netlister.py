"""L7: one placed symbol -> one SPICE line."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core.netlist.builder import build
from core.netlist.model import ERROR, Circuit
from core.netlist.parser import parse
from core.schematic.model import GroundFlag, Instance, NetLabel, PartInstance, Schematic, TextItem
from core.schematic.netlister import netlist_part, netlist_schematic
from core.symbols.asy import AsyParser, Symbol
from core.symbols.library import BUILTIN_DIR, SymbolLibrary

ASY = Path(__file__).resolve().parents[2] / "fixtures" / "asy"
BUILTIN = SymbolLibrary(builtin_dir=BUILTIN_DIR)


def _part(symbol: str, name: str, nets: list[str], **attrs: str) -> PartInstance:
    sym, diagnostics = BUILTIN.lookup(symbol)
    assert sym is not None and diagnostics == []
    return PartInstance(name, sym, nets, attrs)


BUILTIN_CASES = [
    (_part("res", "R1", ["in", "out"], Value="1k"), "R1 in out 1k"),
    (_part("cap", "C1", ["out", "0"], Value="100n"), "C1 out 0 100n"),
    (_part("ind", "L1", ["a", "b"], Value="10u"), "L1 a b 10u"),
    (_part("diode", "D1", ["a", "k"]), "D1 a k D"),
    (_part("diode", "D2", ["a", "k"], SpiceModel="1N4148"), "D2 a k 1N4148"),
    (_part("voltage", "V1", ["in", "0"], Value="DC 5", Value2="AC 1"), "V1 in 0 DC 5 AC 1"),
    (_part("current", "I1", ["0", "n1"], Value="1m"), "I1 0 n1 1m"),
    (_part("npn", "Q1", ["c", "b", "e"]), "Q1 c b e NPN"),
    (_part("pnp", "Q2", ["c", "b", "e"], Value="2N3906"), "Q2 c b e 2N3906"),
    (
        _part("nmos", "M1", ["d", "g", "s", "0"], SpiceLine="W=1u L=180n"),
        "M1 d g s 0 NMOS W=1u L=180n",
    ),
    (_part("pmos", "M2", ["d", "g", "s", "vdd"]), "M2 d g s vdd PMOS"),
    (_part("opamp", "U1", ["p", "n", "vcc", "vee", "o"]), "XU1 p n vcc vee o opamp"),
]


@pytest.mark.parametrize(("part", "line"), BUILTIN_CASES, ids=lambda c: getattr(c, "name", ""))
def test_each_builtin_symbol_netlists_to_its_spice_line(part: PartInstance, line: str) -> None:
    result = netlist_part(part)
    assert result.diagnostics == []
    assert result.line == line
    assert result.element is not None
    # The Element goes through the same builder as the netlist editor (which writes
    # parameter names in lower case).
    deck = build(Circuit(title="t", elements=[result.element]))
    assert deck.splitlines()[1].casefold() == line.casefold()
    assert parse(deck).circuit.elements == [result.element]


def test_every_builtin_with_a_prefix_is_covered() -> None:
    covered = {c[0].symbol.name for c in BUILTIN_CASES}
    with_prefix = {e.name for e in BUILTIN.entries() if BUILTIN.lookup(e.key)[0].prefix}
    assert covered == with_prefix


@pytest.mark.parametrize("name", ["ground", "label"])
def test_net_flags_make_no_line(name: str) -> None:
    result = netlist_part(_part(name, "G1", ["0"]))
    assert result.element is None and result.line == "" and result.diagnostics == []


def test_opamp5_fixture_nets_follow_spice_order_and_include_model_file() -> None:
    symbol, _ = AsyParser.parse(ASY / "opamp5.asy")
    # nets in symbol.pins order: In+ In- V+ V- OUT
    result = netlist_part(PartInstance("X1", symbol, ["inp", "inn", "vcc", "vee", "out"]))
    assert result.line == "X1 inp inn vcc vee out opamp5"
    assert result.includes == [".include opamp.lib"]  # not next to the symbol: as written


def test_cmosn_prefix_mn_gives_an_m_line_and_model_file_beside_symbol(tmp_path: Path) -> None:
    (tmp_path / "cmosn.asy").write_bytes((ASY / "cmosn.asy").read_bytes())
    (tmp_path / "tsmc018.lib").write_text("* models\n", encoding="utf-8")
    symbol, _ = AsyParser.parse(tmp_path / "cmosn.asy")
    result = netlist_part(PartInstance("M1", symbol, ["d", "g", "s", "0"]))
    assert result.line == "M1 d g s 0 CMOSN"
    assert result.includes == [f".include {tmp_path / 'tsmc018.lib'}"]
    assert netlist_part(PartInstance("1", symbol, ["d", "g", "s", "0"])).line == "M1 d g s 0 CMOSN"


def test_name_without_letter_gets_it() -> None:
    assert netlist_part(_part("res", "load", ["a", "b"], Value="50")).line == "Rload a b 50"


def test_micro_sign_becomes_u() -> None:
    symbol, _ = AsyParser.parse(ASY / "micro_cp1252.asy")
    result = netlist_part(PartInstance("C1", symbol, ["a", "0"]))
    assert result.line == "C1 a 0 4.7u"
    assert result.diagnostics == []


def test_model_file_with_space_is_quoted(tmp_path: Path) -> None:
    part = _part("opamp", "U1", ["a", "b", "c", "d", "e"], ModelFile=str(tmp_path / "my lib.sub"))
    assert netlist_part(part).includes == [f'.include "{tmp_path / "my lib.sub"}"']


def _errors(part: PartInstance) -> list[str]:
    result = netlist_part(part)
    assert result.element is None and not result.ok
    return [d.message for d in result.diagnostics if d.severity == ERROR]


def _custom(prefix: str, pins: int = 2) -> Symbol:
    text = f"SYMATTR Prefix {prefix}\nSYMATTR Value x\n" + "".join(
        f"PIN 0 {16 * i} NONE 0\nPINATTR SpiceOrder {i + 1}\n" for i in range(pins)
    )
    return AsyParser.parse_text(text, "custom")[0]


def test_ltspice_a_device_is_not_supported_by_ngspice() -> None:
    [message] = _errors(PartInstance("A1", _custom("A"), ["a", "b"]))
    assert "not supported by ngspice" in message and message.startswith("A1:")


def test_unknown_prefix_is_not_supported_by_ngspice() -> None:
    [message] = _errors(PartInstance("Z1", _custom("9"), ["a", "b"]))
    assert "not supported by ngspice" in message


def test_spice_letter_qtspice_does_not_handle_yet() -> None:
    [message] = _errors(PartInstance("B1", _custom("B"), ["a", "b"]))
    assert "not supported by QtSpice" in message


def test_symbol_without_prefix() -> None:
    [message] = _errors(PartInstance("P1", AsyParser.parse_text("PIN 0 0 NONE 0\n", "x")[0], ["a"]))
    assert "no Prefix" in message


def test_wrong_net_count_and_unconnected_pin() -> None:
    assert "2 pins" in _errors(_part("res", "R1", ["a"], Value="1"))[0]
    assert "pin B is not connected" in _errors(_part("res", "R1", ["a", " "], Value="1"))[0]


def test_missing_subcircuit_name() -> None:
    assert "no subcircuit name" in _errors(_part("opamp", "U1", list("abcde"), Value=""))[0]


def test_parser_checks_still_apply() -> None:
    # The default resistor Value "R" is not a number: same error as in the netlist editor.
    result = netlist_part(_part("res", "R1", ["a", "b"]))
    assert result.line == "R1 a b R"
    assert [d.message for d in result.diagnostics] == ["R1: bad number 'R'"]


# ---------------------------------------------------------------- S3: whole schematic

RC_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "schematics" / "rc"


def _lookup(name: str) -> Symbol | None:
    if name == "opamp5":
        return AsyParser.parse(ASY / "opamp5.asy")[0]
    return BUILTIN.lookup(name)[0]


def _rc() -> Schematic:
    schematic, diagnostics = Schematic.from_dict(
        json.loads((RC_DIR / "rc.json").read_text(encoding="utf-8"))
    )
    assert diagnostics == []
    return schematic


def test_rc_schematic_gives_the_circuit_of_its_hand_written_netlist() -> None:
    result = netlist_schematic(_rc(), _lookup)
    assert result.diagnostics == []
    hand = parse((RC_DIR / "rc.cir").read_text(encoding="utf-8"))
    assert hand.diagnostics == []
    assert result.circuit.elements == hand.circuit.elements
    assert result.circuit.analyses == hand.circuit.analyses
    assert result.deck.splitlines()[1:] == build(hand.circuit).splitlines()[1:]
    assert result.deck.splitlines()[0] == "AC RC filter"


@pytest.mark.ngspice
def test_rc_schematic_simulates_like_its_hand_written_netlist() -> None:
    from core.sim.ngspice_adapter import NgspiceAdapter

    def simulate(deck: str) -> dict[str, np.ndarray]:
        sim = NgspiceAdapter()
        assert sim.available(), sim.last_error
        sim.load(deck)
        sim.run()
        result = sim.results()
        assert result is not None and result.analysis == "ac"
        return result.vectors

    schematic = simulate(netlist_schematic(_rc(), _lookup).deck)
    hand = simulate(build(parse((RC_DIR / "rc.cir").read_text(encoding="utf-8")).circuit))
    assert schematic.keys() == hand.keys()
    assert "V(out)" in schematic
    for name, values in hand.items():
        np.testing.assert_allclose(schematic[name], values, rtol=1e-12)


def test_directive_texts_become_cards_and_comments_do_not() -> None:
    schematic = _rc()
    schematic.texts = [
        TextItem(0, 0, ".param rval=10k\n\n.tran 1u 50m"),
        TextItem(0, 32, ".op", directive=False),
    ]
    schematic.instance("R2").attrs["Value"] = "{rval}"  # type: ignore[union-attr]
    result = netlist_schematic(schematic, _lookup)
    assert result.diagnostics == []
    assert result.circuit.params == {"rval": "10k"}
    assert [a.kind for a in result.circuit.analyses] == ["tran"]


def test_unsupported_directive_names_its_text() -> None:
    schematic = _rc()
    schematic.texts.append(TextItem(0, 0, ".meas ac x max mag(v(out))"))
    result = netlist_schematic(schematic, _lookup)
    assert result.ok
    assert any(d.message.startswith("directive 2: ") for d in result.diagnostics)


def test_model_file_is_included_once_for_several_parts() -> None:
    lib = Path(__file__).resolve().parents[2] / "fixtures" / "lib" / "opamp.lib"
    attrs = {"ModelFile": str(lib)}
    schematic = Schematic(
        instances=[
            Instance("opamp5", "U1", 0, 0, attrs=attrs),
            Instance("opamp5", "U2", 0, 320, attrs=attrs),
        ],
        grounds=[GroundFlag(x, y) for y0 in (0, 320) for x, y in _opamp_pins(y0)],
    )
    result = netlist_schematic(schematic, _lookup)
    assert result.text.count(".include") == 1
    assert [s.name for s in result.circuit.subcircuits] == ["opamp5"]
    assert [e.name for e in result.circuit.elements] == ["XU1", "XU2"]


def _opamp_pins(y0: int) -> list[tuple[int, int]]:
    symbol = _lookup("opamp5")
    assert symbol is not None
    return [(p.x, p.y + y0) for p in symbol.pins]


def test_unconnected_pin_is_an_error_naming_the_part() -> None:
    schematic = _rc()
    schematic.wires = [w for w in schematic.wires if (w.x1, w.y1) != (304, 272)]
    result = netlist_schematic(schematic, _lookup)
    assert not result.ok and result.deck == ""
    assert any(d.message == "C1: pin B is not connected" for d in result.diagnostics)
    assert "C1 " not in result.text


def test_missing_symbol_and_no_ground_are_reported_not_raised() -> None:
    schematic = Schematic(
        instances=[
            Instance("nosuch", "X1", 0, 0),
            Instance("res", "R1", 0, 0, attrs={"Value": "1k"}),
        ],
        labels=[NetLabel(16, 0, "a"), NetLabel(16, 96, "b")],
    )
    result = netlist_schematic(schematic, _lookup)
    messages = [d.message for d in result.diagnostics if d.severity == ERROR]
    assert "X1: symbol not found: nosuch" in messages
    assert any(m.startswith("schematic: no ground node") for m in messages)
    assert result.text.splitlines()[0] == "QtSpice schematic"
