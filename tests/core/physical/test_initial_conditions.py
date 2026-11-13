"""M6: start values of storage elements, and the "start from steady state" option.

Each combined run is checked against its analytic answer. A run from the start values
(default) maps with ``uic``; one with ``steady_state`` runs an operating point first.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from core.blocks.diagram import Diagram, DiagramError
from core.blocks.engine import ExecutionEngine
from core.blocks.library import builtin_blocks
from core.blocks.runner import run_diagram
from core.netlist.model import WARNING
from core.physical.component import (
    GROUND,
    Capacitor,
    Parameter,
    PhysicalComponent,
    Port,
    physical_component,
)
from core.physical.cosim import CoSimulator, start_row
from core.physical.domains import ELECTRICAL
from core.physical.library import physical_registry
from core.physical.registry import PhysicalRegistry
from core.sim.ngspice_adapter import tran_card
from tests.fixtures.physical.library import add_solver, mapped, new_diagram, wire
from tests.fresh import in_fresh_process

T_AMB = 298.15


def _run(diagram: dict, t_end: float, dt: float = 1e-3) -> dict[str, np.ndarray]:
    result = run_diagram(diagram, {}, t_end=t_end, dt=dt)
    return {k: np.asarray(v).real for k, v in result.vectors.items()}


def _solver(d: Diagram, reg: PhysicalRegistry, port: str, steady: bool = False) -> None:
    add_solver(d, reg, port, {"Ts": 1e-3, "steady_state": steady})


# ---- mapping (no ngspice)


def test_tran_card_has_uic_only_when_asked() -> None:
    assert tran_card(1e-3, 0.1).endswith(" 0.001")
    assert tran_card(1e-3, 0.1, uic=True).endswith(" 0.001 uic")


def test_solver_configuration_has_a_steady_state_option() -> None:
    spec = physical_registry().spec("SolverConfiguration")
    assert spec is not None
    (param,) = [p for p in spec.parameters if p.name == "steady_state"]
    assert param.default is False


def _decay_diagram(steady: bool = False) -> tuple[Diagram, PhysicalRegistry]:
    d, reg = new_diagram()
    d.add_block("MechanicalRotationalReference", block_id="ref")
    d.add_block("Inertia", settings={"J": 0.01, "w0": 100.0}, block_id="inertia")
    d.add_block("RotationalDamper", settings={"b": 0.01}, block_id="damper")
    wire(d, "inertia.A", "damper.A")
    wire(d, "damper.B", "ref.R")
    _solver(d, reg, "ref.R", steady)
    return d, reg


def _cosim_deck(d: Diagram, reg: PhysicalRegistry) -> str:
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    return CoSimulator(engine, reg).map(0.1, 1e-3).deck


def test_start_values_map_to_ic_with_uic() -> None:
    d, reg = _decay_diagram()
    deck = _cosim_deck(d, reg)
    assert "C_inertia_C rotational_1 0 0.01 ic=100.0" in deck
    assert deck.splitlines()[-2].endswith(" uic")


def test_steady_state_maps_without_uic() -> None:
    d, reg = _decay_diagram(steady=True)
    deck = _cosim_deck(d, reg)
    assert ".tran" in deck and "uic" not in deck


def test_networks_must_agree_on_the_start() -> None:
    d, reg = _decay_diagram()
    d.add_block("ThermalReference", block_id="tref")
    d.add_block("ThermalMass", block_id="tm")
    d.add_block("Convection", block_id="conv")
    wire(d, "tm.A", "conv.A")
    wire(d, "conv.B", "tref.R")
    d.add_block("SolverConfiguration", settings={"steady_state": True}, block_id="solver2")
    wire(d, "solver2.port", "tref.R")
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    with pytest.raises(DiagramError, match="steady state"):
        CoSimulator(engine, reg).map(0.1, 1e-3)


def test_mapper_keeps_the_start_value_of_nodes_on_the_reference() -> None:
    d, reg = _decay_diagram()
    out = mapped(d, reg, None)
    assert out.initial == {"V(rotational_1)": 100.0}


@physical_component
class _TwoCaps(PhysicalComponent):
    """Two capacitors on one node with different start values, one of them turned
    round (the reference on its ``a`` side)."""

    ports = (Port("p", ELECTRICAL),)
    parameters = (Parameter("v", 1.0, "V"),)

    def network(self, ports, params):
        return [
            Capacitor("C1", ports.p, GROUND, 1e-6, float(params["v"])),
            Capacitor("C2", GROUND, ports.p, 1e-6, 2.0),
        ]


def test_turned_capacitor_and_clashing_start_values() -> None:
    reg = physical_registry()
    reg.add(_TwoCaps)
    d, reg = new_diagram(reg)
    d.add_block("ElectricalReference", block_id="g")
    d.add_block("_TwoCaps", settings={"v": -2.0}, block_id="caps")
    d.add_block("Resistor", block_id="r")
    wire(d, "caps.p", "r.p")
    wire(d, "r.n", "g.G")
    add_solver(d, reg, "g.G")
    out = mapped(d, reg, None)
    # C2 holds V(0) - V(p) = 2: V(p) = -2, the same as C1's
    assert out.initial == {"V(electrical_1)": -2.0}
    assert not [x for x in out.diagnostics if x.severity == WARNING]

    d2, _ = new_diagram(reg)
    d2.add_block("ElectricalReference", block_id="g")
    d2.add_block("_TwoCaps", settings={"v": 1.0}, block_id="caps")
    d2.add_block("Resistor", block_id="r")
    wire(d2, "caps.p", "r.p")
    wire(d2, "r.n", "g.G")
    add_solver(d2, reg, "g.G")
    out2 = mapped(d2, reg, None)
    warnings = [x for x in out2.diagnostics if x.severity == WARNING]
    assert len(warnings) == 1 and "differs" in warnings[0].message


def test_start_row_uses_the_known_start_value_else_the_first_point() -> None:
    t = np.array([1e-6, 2e-6])
    assert start_row(t, np.array([99.0, 98.0]), 100.0) == 100.0
    assert start_row(t, np.array([3.0, 5.0]), None) == 3.0


# ---- combined runs against analytic answers (ngspice)


def _inertia_run(steady: bool) -> dict[str, np.ndarray]:
    d, _ = _decay_diagram(steady)
    return _run(d.to_dict(), 0.5)


@pytest.mark.ngspice
def test_inertia_starts_at_w0_and_decays_with_j_over_b() -> None:
    v = in_fresh_process(_inertia_run, False)
    t, w = v["time"], v["inertia.A.w"]
    assert t[0] == 0.0 and w[0] == 100.0
    # J / b = 1 s
    np.testing.assert_allclose(w, 100.0 * np.exp(-t), rtol=1e-3, atol=1e-3)


@pytest.mark.ngspice
def test_inertia_from_steady_state_ignores_w0() -> None:
    v = in_fresh_process(_inertia_run, True)
    # no torque: the operating point is at rest
    assert v["time"][0] == 0.0
    assert np.max(np.abs(v["inertia.A.w"])) < 1e-9


def _mass_spring_run() -> dict[str, np.ndarray]:
    d, reg = new_diagram()
    d.add_block("MechanicalTranslationalReference", block_id="ref")
    d.add_block("Mass", settings={"m": 1.0, "v0": 1.0}, block_id="mass")
    d.add_block("TranslationalSpring", settings={"k": 100.0}, block_id="spring")
    wire(d, "mass.A", "spring.A")
    wire(d, "spring.B", "ref.R")
    _solver(d, reg, "ref.R")
    return _run(d.to_dict(), 0.5)


@pytest.mark.ngspice
def test_mass_on_a_spring_oscillates_from_v0() -> None:
    v = in_fresh_process(_mass_spring_run)
    t, vel = v["time"], v["mass.A.v"]
    assert vel[0] == 1.0
    # omega = sqrt(k / m) = 10 rad/s, undamped: v = v0 cos(omega t)
    np.testing.assert_allclose(vel, np.cos(10.0 * t), atol=2e-3)


def _thermal_run(steady: bool) -> dict[str, np.ndarray]:
    d, reg = new_diagram()
    d.add_block("ThermalReference", block_id="tref")
    d.add_block("TemperatureSource", settings={"T": T_AMB}, block_id="amb")
    # R = 1 / (h A) = 10 K/W, C = 0.05 J/K: tau = 0.5 s
    d.add_block("Convection", settings={"h": 10.0, "area": 0.01}, block_id="conv")
    d.add_block("ThermalMass", settings={"C": 0.05, "T0": 350.0}, block_id="mass")
    d.add_block("HeatFlowSource", settings={"Q": 2.0}, block_id="heat")
    wire(d, "amb.B", "tref.R")
    wire(d, "amb.A", "conv.B")
    wire(d, "conv.A", "mass.A")
    wire(d, "heat.A", "mass.A")
    wire(d, "heat.B", "tref.R")
    _solver(d, reg, "tref.R", steady)
    return _run(d.to_dict(), 1.0)


@pytest.mark.ngspice
def test_thermal_mass_starts_at_t0_and_relaxes() -> None:
    v = in_fresh_process(_thermal_run, False)
    t, temp = v["time"], v["mass.A.T"]
    final = T_AMB + 2.0 * 10.0
    assert temp[0] == 350.0
    np.testing.assert_allclose(temp, final + (350.0 - final) * np.exp(-t / 0.5), atol=1e-2)


@pytest.mark.ngspice
def test_thermal_mass_from_steady_state() -> None:
    v = in_fresh_process(_thermal_run, True)
    # operating point: T = T_amb + Q R from the start, T0 is ignored
    np.testing.assert_allclose(v["mass.A.T"], T_AMB + 20.0, atol=1e-6)


def _rl_rc_run() -> dict[str, np.ndarray]:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="g")
    # a charged capacitor with neither side on the reference: R1 + R2 = 100 Ohm,
    # C = 1 mF, tau = 0.1 s
    d.add_block("Capacitor", settings={"C": 1e-3, "v0": 5.0}, block_id="c")
    d.add_block("Resistor", settings={"R": 50.0}, block_id="r1")
    d.add_block("Resistor", settings={"R": 50.0}, block_id="r2")
    d.add_block("VoltageSensor", block_id="vs")
    wire(d, "c.p", "r1.p")
    wire(d, "r1.n", "g.G")
    wire(d, "c.n", "r2.p")
    wire(d, "r2.n", "g.G")
    wire(d, "vs.p", "c.p")
    wire(d, "vs.n", "c.n")
    # an inductor with a start current: L = 1 H, R = 10 Ohm, tau = 0.1 s
    d.add_block("Inductor", settings={"L": 1.0, "i0": 2.0}, block_id="l")
    d.add_block("Resistor", settings={"R": 10.0}, block_id="rl")
    d.add_block("CurrentSensor", block_id="cs")
    wire(d, "l.p", "cs.p")
    wire(d, "cs.n", "rl.p")
    wire(d, "rl.n", "g.G")
    wire(d, "l.n", "g.G")
    _solver(d, reg, "g.G")
    d.add_block("Scope", settings={"label": "vc"}, block_id="scope_v")
    d.add_block("Scope", settings={"label": "il"}, block_id="scope_i")
    d.add_wire("vs", "V", "scope_v", "u")
    d.add_wire("cs", "I", "scope_i", "u")
    return _run(d.to_dict(), 0.3)


@pytest.mark.ngspice
def test_floating_capacitor_voltage_and_inductor_current_start_values() -> None:
    v = in_fresh_process(_rl_rc_run)
    t = v["time"]
    expected = np.exp(-t / 0.1)
    # the sensor vectors are on ngspice's points; the first point is the first step
    np.testing.assert_allclose(v["vs.V"], 5.0 * expected, rtol=2e-3, atol=2e-3)
    # i0 flows from p to n in the inductor, so through the sensor from n to p
    np.testing.assert_allclose(v["cs.I"], -2.0 * expected, rtol=2e-3, atol=2e-3)
    # the Scopes read them at the ticks, tick 0 included
    assert v["vc"][0] == pytest.approx(5.0, rel=1e-3)
    assert v["il"][0] == pytest.approx(-2.0, rel=1e-3)


# ---- the M6 acceptance case: a DC motor started at 100 rad/s

R_A, K_M, B_M, V_S = 1.0, 0.05, 1e-5, 6.0


def _motor_run(steady: bool) -> dict[str, np.ndarray]:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="g")
    d.add_block("MechanicalRotationalReference", block_id="ref")
    d.add_block("DCVoltageSource", settings={"V": V_S}, block_id="supply")
    d.add_block(
        "DCMotor",
        settings={"R": R_A, "k": K_M, "b": B_M, "w0": 100.0},
        block_id="motor",
    )
    d.add_block("AngularVelocitySensor", block_id="ws")
    wire(d, "supply.p", "motor.p")
    wire(d, "supply.n", "g.G")
    wire(d, "motor.n", "g.G")
    wire(d, "motor.B", "ref.R")
    wire(d, "ws.A", "motor.A")
    wire(d, "ws.B", "ref.R")
    _solver(d, reg, "ref.R", steady)
    d.add_block("Scope", settings={"label": "w"}, block_id="scope")
    d.add_wire("ws", "w", "scope", "u")
    return _run(d.to_dict(), 0.5)


@pytest.mark.ngspice
def test_motor_started_at_100_rad_s_shows_100_at_t0() -> None:
    v = in_fresh_process(_motor_run, False)
    assert v["time"][0] == 0.0
    assert v["motor.A.w"][0] == 100.0
    assert v["w"][0] == pytest.approx(100.0, rel=1e-3)
    # then it settles at the unloaded speed k V / (k^2 + R b) = 119.5 rad/s
    w_ss = K_M * V_S / (K_M**2 + R_A * B_M)
    assert v["motor.A.w"][-1] == pytest.approx(w_ss, rel=1e-3)


@pytest.mark.ngspice
def test_motor_from_steady_state_starts_at_the_unloaded_speed() -> None:
    v = in_fresh_process(_motor_run, True)
    w_ss = K_M * V_S / (K_M**2 + R_A * B_M)
    assert v["motor.A.w"][0] == pytest.approx(w_ss, rel=1e-6)
    assert not math.isnan(v["w"][0])
    np.testing.assert_allclose(v["motor.A.w"], w_ss, rtol=1e-6)
