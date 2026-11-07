"""M2: a v0.2 schematic (or netlist) with port flags as a physical Circuit block."""

from __future__ import annotations

import math

import numpy as np
import pytest

from core.blocks.runner import BlockRunner
from core.netlist.model import ERROR
from core.physical.circuit_block import (
    CircuitDefinition,
    circuit_from_netlist,
    circuit_from_schematic,
    is_circuit_block,
    load_definition,
)
from core.physical.component import Port, SubcircuitInstance, build_network
from core.physical.library import physical_registry
from core.schematic.model import GroundFlag, Instance, NetLabel, PortFlag, Schematic, TextItem
from core.schematic.netlister import netlist_schematic
from core.symbols.library import SymbolLibrary
from tests.fixtures.physical.library import add_solver, mapped, new_diagram, run, simulate, wire

R, C = 1e3, 1e-6
TAU = R * C
V_STEP, T_STEP = 5.0, 1e-3
EREF = "eref.G"

LIB = SymbolLibrary()


def lookup(name: str):  # noqa: ANN201
    return LIB.lookup(name)[0]


def rc_child() -> Schematic:
    """The v0.2 RC low-pass sheet: R = 1k from IN to OUT, C = 1u from OUT to ground,
    port flags IN (in) and OUT (out)."""
    return Schematic(
        title="RC low-pass",
        instances=[
            Instance("res", "R1", 0, 0, attrs={"Value": "1k"}),  # pins (16, 0) (16, 96)
            Instance("cap", "C1", 100, 96, attrs={"Value": "1u"}),  # pins (116, 96) (116, 160)
        ],
        labels=[NetLabel(116, 96, "OUT")],
        grounds=[GroundFlag(116, 160)],
        ports=[PortFlag(16, 0, "IN", "in"), PortFlag(16, 96, "OUT", "out")],
        texts=[TextItem(0, 200, ".ac dec 10 1 1k")],
    )


def rc_driven() -> Schematic:
    """The same RC sheet driven by a 5 V step at 1 ms, with a .tran: the v0.2 answer."""
    sheet = rc_child()
    return Schematic(
        title="RC step",
        instances=[
            *sheet.instances,
            Instance(
                "voltage",
                "V1",
                -200,
                0,
                attrs={"Value": f"PULSE(0 {V_STEP} {T_STEP} 1u 1u 1 0)"},
            ),  # pins (-200, 16) (-200, 96)
        ],
        labels=[
            *sheet.labels,
            NetLabel(-200, 16, "IN"),
            NetLabel(16, 0, "IN"),
            NetLabel(16, 96, "OUT"),
        ],
        grounds=[*sheet.grounds, GroundFlag(-200, 96)],
        texts=[TextItem(0, 200, ".tran 10u 8m")],
    )


@pytest.fixture
def rc() -> CircuitDefinition:
    block = circuit_from_schematic("rclp", rc_child(), lookup)
    assert block.ok, block.diagnostics
    assert block.definition is not None
    return block.definition


def test_a_schematic_with_port_flags_becomes_a_block_with_electrical_ports(
    rc: CircuitDefinition,
) -> None:
    assert rc.name == "rclp" and rc.ports == ("IN", "OUT") and rc.labels == ("in", "out")
    block = load_definition(rc)
    assert block.ok and block.component is not None
    assert is_circuit_block(block.component)
    reg = physical_registry(circuits=[rc.to_dict()])
    spec = reg.spec("rclp")
    assert spec is not None
    assert [(p.name, p.domain) for p in spec.ports if isinstance(p, Port)] == [
        ("IN", "electrical"),
        ("OUT", "electrical"),
    ]
    result = build_network(reg.get("rclp"), block="U1")  # type: ignore[arg-type]
    assert result.ok, result.diagnostics
    (prim,) = result.primitives
    assert isinstance(prim, SubcircuitInstance)
    assert prim.nodes == ("IN", "OUT") and prim.subckt == "rclp"
    # the text is self-contained: no .include, the analysis card is left out
    assert ".include" not in prim.text and ".ac" not in prim.text
    assert CircuitDefinition.from_dict(rc.to_dict()) == rc


def test_a_netlist_becomes_a_block_and_includes_are_expanded(tmp_path) -> None:  # noqa: ANN001
    (tmp_path / "parts.lib").write_text(".model dfast D(Is=1e-14)\n")
    text = "clamp\n.include parts.lib\nR1 a b 1k\nD1 b 0 dfast\n.tran 1m 1\n.end\n"
    block = circuit_from_netlist("clamp", text, [("a", "in"), ("b", "out")], tmp_path)
    assert block.ok, block.diagnostics
    assert block.definition is not None
    assert block.definition.ports == ("a", "b")
    assert ".model dfast" in block.definition.text
    assert ".include" not in block.definition.text


def test_bad_circuits_are_diagnostics_not_exceptions() -> None:
    no_ports = Schematic(
        title="no ports",
        instances=[Instance("res", "R1", 0, 0, attrs={"Value": "1k"})],
        grounds=[GroundFlag(16, 96)],
    )
    block = circuit_from_schematic("noports", no_ports, lookup)
    assert not block.ok and block.component is None
    assert any(d.severity == ERROR for d in block.diagnostics)

    # node "1" is a fine SPICE node but not a block port name
    block = circuit_from_netlist("num", "t\nR1 1 0 1k\n.end\n", [("1", "bidir")])
    assert not block.ok
    assert "valid identifier" in block.diagnostics[-1].message

    for bad in (
        {},
        {"name": "x", "ports": "IN", "text": ""},
        {"name": "x", "ports": ["a"], "text": "* t\n.end\n"},
        {"name": "x", "ports": ["a"], "text": "* t\n.subckt x a b\nR1 a b 1\n.ends x\n.end\n"},
    ):
        block = load_definition(bad)
        assert not block.ok and block.diagnostics, bad
    with pytest.raises(ValueError):
        physical_registry(circuits=[{"name": "x"}])


def _rc_diagram(rc: CircuitDefinition, blocks: int = 1):  # noqa: ANN202
    d, reg = new_diagram(physical_registry(circuits=[rc.to_dict()]))
    d.add_block("ElectricalReference", block_id="eref")
    add_solver(d, reg, EREF)
    pulse = {"v1": 0.0, "v2": V_STEP, "delay": T_STEP, "width": 1.0, "period": 0.0}
    d.add_block("PulseVoltageSource", block_id="src", settings=pulse)
    wire(d, "src.n", EREF)
    node = "src.p"
    for k in range(1, blocks + 1):
        d.add_block("rclp", block_id=f"U{k}")
        wire(d, node, f"U{k}.IN")
        node = f"U{k}.OUT"
    return d, reg


def test_two_circuit_blocks_share_one_subckt_definition(rc: CircuitDefinition) -> None:
    d, reg = _rc_diagram(rc, blocks=2)
    out = mapped(d, reg, ".tran 10u 8m", external=False)
    lines = out.deck.splitlines()
    assert sum(line.startswith(".subckt rclp") for line in lines) == 1
    xs = [line for line in lines if line.startswith("X_")]
    assert xs == ["X_U1_X electrical_1 electrical_2 rclp", "X_U2_X electrical_2 electrical_3 rclp"]


@pytest.mark.ngspice
def test_the_rc_symbol_as_a_circuit_block_matches_the_schematic_result(
    rc: CircuitDefinition,
) -> None:
    # v0.2: the schematic itself, netlisted and run
    sheet = netlist_schematic(rc_driven(), lookup)
    assert sheet.ok, sheet.diagnostics
    ref = run(sheet.deck)
    t_ref = ref.vectors["time"].real
    v_ref = np.asarray(ref.vectors["V(out)"]).real

    # v0.3: the same sheet as a Circuit block in a physical network
    d, reg = _rc_diagram(rc)
    out, res = simulate(d, reg, ".tran 10u 8m")
    t = res.vectors["time"].real
    v = np.asarray(res.vectors[out.vectors["U1.OUT.v"]]).real

    grid = np.linspace(0.0, 8e-3, 801)
    np.testing.assert_allclose(np.interp(grid, t, v), np.interp(grid, t_ref, v_ref), atol=2e-3)
    after = grid > T_STEP + 2e-6
    exact = V_STEP * (1 - np.exp(-(grid[after] - T_STEP) / TAU))
    np.testing.assert_allclose(np.interp(grid[after], t, v), exact, atol=5e-3)
    assert float(np.interp(T_STEP + TAU, t, v)) == pytest.approx(
        V_STEP * (1 - math.exp(-1)), rel=5e-3
    )


@pytest.mark.ngspice
def test_a_circuit_block_runs_in_the_block_runner_child(rc: CircuitDefinition) -> None:
    """Combined run in the BlockRunner child: the circuit travels as plain data."""
    d, reg = new_diagram(physical_registry(circuits=[rc.to_dict()]))
    d.add_block("ElectricalReference", block_id="eref")
    add_solver(d, reg, EREF, {"Ts": 1e-4})
    d.add_block("ControlledVoltageSource", block_id="src")
    d.add_block("rclp", block_id="U1")
    d.add_block("VoltageSensor", block_id="vs")
    d.add_block("Constant", settings={"value": V_STEP}, block_id="k")
    d.add_block("Scope", settings={"label": "vout"}, block_id="scope")
    wire(d, "src.p", "U1.IN")
    wire(d, "src.n", EREF)
    wire(d, "U1.OUT", "vs.p")
    wire(d, "vs.n", EREF)
    d.add_wire("k", "y", "src", "V")
    d.add_wire("vs", "V", "scope", "u")
    outcome = BlockRunner(d.to_dict(), {}, 8e-3, 1e-4, timeout=60.0, circuits=[rc.to_dict()]).run()
    assert outcome.ok, (outcome.message, outcome.traceback)
    assert outcome.result is not None
    t = np.asarray(outcome.result.vectors["time"]).real
    v = np.asarray(outcome.result.vectors["vout"]).real
    # the sensor value is one tick old (rule 14) and the source holds from tick 0
    assert float(np.interp(4e-3, t, v)) == pytest.approx(V_STEP * (1 - math.exp(-4)), rel=0.03)
    assert v[-1] == pytest.approx(V_STEP, rel=0.01)
