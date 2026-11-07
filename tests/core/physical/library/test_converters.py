"""B5: signal-controlled sources, sensors and the Solver Configuration."""

from __future__ import annotations

import numpy as np
import pytest

from core.blocks.diagram import DiagramError
from core.blocks.engine import ExecutionEngine
from core.blocks.library import builtin_blocks
from core.physical.component import ExternalSource, build_network
from core.physical.cosim import CoSimulator
from core.physical.library.converters.solver import SolverConfiguration
from core.physical.mapper import SolverSettings, max_step, options_card
from core.sim.ngspice_adapter import tran_card
from tests.fixtures.physical.library import (
    add_solver,
    at,
    mapped,
    new_diagram,
    simulate,
    vector,
    wire,
)
from tests.fresh import in_fresh_process

# ---- Solver Configuration (no ngspice)


def test_solver_settings_reach_the_deck() -> None:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="gnd")
    d.add_block("Resistor", block_id="r")
    wire(d, "r.n", "gnd.G")
    add_solver(d, reg, "gnd.G", {"reltol": 1e-4, "abstol": 1e-9, "max_step": 2e-4})
    out = mapped(d, reg, ".tran 1m 10m")
    assert out.deck.splitlines()[1] == ".options reltol=0.0001 abstol=1e-09"
    (solver,) = out.solvers
    assert solver == SolverSettings("solver", 1e-3, 1e-4, 1e-9, 2e-4)


def test_strictest_solver_wins_and_max_step_never_exceeds_ts() -> None:
    a = SolverSettings("a", 1e-3, 1e-3, 1e-12, None)
    b = SolverSettings("b", 1e-3, 1e-5, 1e-10, 5e-4)
    assert options_card([a, b]) == ".options reltol=1e-05 abstol=1e-12"
    assert options_card([SolverSettings("c")]) == ""
    assert max_step([a, b]) == 5e-4
    assert max_step([a]) is None
    assert tran_card(1e-3, 0.1, 5e-4).endswith(" 0 0.0005")
    assert tran_card(1e-3, 0.1, 5e-3).endswith(" 0 0.001")
    assert tran_card(1e-3, 0.1).endswith(" 0 0.001")


@pytest.mark.parametrize(
    "params",
    [{"Ts": 5e-6}, {"Ts": 0.0}, {"reltol": 0.0}, {"abstol": -1.0}, {"max_step": -1e-3}],
)
def test_bad_solver_settings_are_diagnostics(params: dict[str, float]) -> None:
    assert not build_network(SolverConfiguration, params, "solver").ok


def _speed_diagram(ts: float):  # noqa: ANN202
    d, reg = new_diagram()
    d.add_block("MechanicalRotationalReference", block_id="ref")
    d.add_block("ControlledTorqueSource", block_id="motor")
    d.add_block("RotationalDamper", block_id="damper")
    d.add_block("Constant", settings={"value": 1.0}, block_id="k")
    wire(d, "motor.A", "damper.A")
    wire(d, "motor.B", "ref.R")
    wire(d, "damper.B", "ref.R")
    add_solver(d, reg, "ref.R", {"Ts": ts})
    d.add_wire("k", "y", "motor", "tau")
    return d, reg


def test_combined_run_refuses_a_ts_other_than_the_solver_ts() -> None:
    d, reg = _speed_diagram(1e-3)
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    with pytest.raises(DiagramError, match="Ts = 0.001 s"):
        CoSimulator(engine, reg).run(0.01, 5e-4)


# ---- signal-controlled sources (no ngspice)

CONTROLLED = {
    # name: (signal, kind, domain, (a, b))
    "ControlledVoltageSource": ("V", "v", "electrical", ("p", "n")),
    "ControlledCurrentSource": ("I", "i", "electrical", ("p", "n")),
    "ControlledTorqueSource": ("tau", "i", "rotational", ("B", "A")),
    "ControlledAngularVelocitySource": ("w", "v", "rotational", ("A", "B")),
    "ControlledForceSource": ("F", "i", "translational", ("B", "A")),
    "ControlledVelocitySource": ("v", "v", "translational", ("A", "B")),
    "ControlledHeatFlowSource": ("Q", "i", "thermal", ("B", "A")),
    "ControlledTemperatureSource": ("T", "v", "thermal", ("A", "B")),
}


@pytest.mark.parametrize("name", list(CONTROLLED))
def test_controlled_sources_are_external_sources(name: str) -> None:
    signal, kind, domain, ab = CONTROLLED[name]
    _, reg = new_diagram()
    spec = reg.spec(name)
    assert spec is not None
    assert spec.signal_inputs() == [signal]
    assert {p.domain for p in spec.ports if hasattr(p, "domain")} == {domain}
    (prim,) = build_network(reg.get(name), {"initial": 2.0}).primitives  # type: ignore[arg-type]
    assert isinstance(prim, ExternalSource)
    assert (prim.signal, prim.kind, (prim.a, prim.b), prim.initial) == (signal, kind, ab, 2.0)


# ---- every sensor reads a known value (plain ngspice, uic)


@pytest.mark.ngspice
def test_electrical_sensors() -> None:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="gnd")
    d.add_block("DCVoltageSource", block_id="src", settings={"V": 3.0})
    d.add_block("CurrentSensor", block_id="isens")
    d.add_block("Resistor", block_id="r", settings={"R": 1e3})
    d.add_block("VoltageSensor", block_id="vsens")
    wire(d, "src.p", "isens.p")
    wire(d, "isens.n", "r.p")
    wire(d, "vsens.p", "r.p")
    for port in ("src.n", "r.n", "vsens.n"):
        wire(d, port, "gnd.G")
    add_solver(d, reg, "gnd.G")
    out, res = simulate(d, reg, ".tran 10u 1m")
    assert vector(out, res, "vsens.V")[-1] == pytest.approx(3.0, rel=1e-9)
    assert vector(out, res, "isens.I")[-1] == pytest.approx(3e-3, rel=1e-9)


@pytest.mark.ngspice
def test_rotational_sensors() -> None:
    w, b = 5.0, 0.5
    d, reg = new_diagram()
    d.add_block("MechanicalRotationalReference", block_id="ref")
    d.add_block("AngularVelocitySource", block_id="src", settings={"w": w})
    d.add_block("TorqueSensor", block_id="tsens")
    d.add_block("RotationalDamper", block_id="damper", settings={"b": b})
    d.add_block("AngularVelocitySensor", block_id="wsens")
    d.add_block("AngleSensor", block_id="phisens")
    wire(d, "src.A", "tsens.A")
    wire(d, "tsens.B", "damper.A")
    wire(d, "wsens.A", "damper.A")
    wire(d, "phisens.A", "damper.A")
    for port in ("src.B", "damper.B", "wsens.B", "phisens.B"):
        wire(d, port, "ref.R")
    add_solver(d, reg, "ref.R")
    out, res = simulate(d, reg, ".tran 1m 2 0 1m uic")
    t = res.vectors["time"].real
    assert vector(out, res, "wsens.w")[-1] == pytest.approx(w, rel=1e-9)
    assert vector(out, res, "tsens.tau")[-1] == pytest.approx(w * b, rel=1e-9)
    np.testing.assert_allclose(vector(out, res, "phisens.phi"), w * t, atol=1e-6 * w)


@pytest.mark.ngspice
def test_translational_sensors() -> None:
    v, b = 2.0, 3.0
    d, reg = new_diagram()
    d.add_block("MechanicalTranslationalReference", block_id="ref")
    d.add_block("VelocitySource", block_id="src", settings={"v": v})
    d.add_block("ForceSensor", block_id="fsens")
    d.add_block("TranslationalDamper", block_id="damper", settings={"b": b})
    d.add_block("VelocitySensor", block_id="vsens")
    d.add_block("PositionSensor", block_id="xsens")
    wire(d, "src.A", "fsens.A")
    wire(d, "fsens.B", "damper.A")
    wire(d, "vsens.A", "damper.A")
    wire(d, "xsens.A", "damper.A")
    for port in ("src.B", "damper.B", "vsens.B", "xsens.B"):
        wire(d, port, "ref.R")
    add_solver(d, reg, "ref.R")
    out, res = simulate(d, reg, ".tran 1m 2 0 1m uic")
    t = res.vectors["time"].real
    assert vector(out, res, "vsens.v")[-1] == pytest.approx(v, rel=1e-9)
    assert vector(out, res, "fsens.F")[-1] == pytest.approx(v * b, rel=1e-9)
    np.testing.assert_allclose(vector(out, res, "xsens.x"), v * t, atol=1e-6 * v)


@pytest.mark.ngspice
def test_thermal_sensors() -> None:
    temp, h, area = 350.0, 10.0, 0.1  # R = 1 K/W
    d, reg = new_diagram()
    d.add_block("ThermalReference", block_id="ref")
    d.add_block("TemperatureSource", block_id="src", settings={"T": temp})
    d.add_block("HeatFlowSensor", block_id="qsens")
    d.add_block("Convection", block_id="air", settings={"h": h, "area": area})
    d.add_block("TemperatureSensor", block_id="tsens")
    wire(d, "src.A", "qsens.A")
    wire(d, "qsens.B", "air.A")
    wire(d, "tsens.A", "air.A")
    for port in ("src.B", "air.B", "tsens.B"):
        wire(d, port, "ref.R")
    add_solver(d, reg, "ref.R")
    out, res = simulate(d, reg, ".tran 1m 10m")
    assert vector(out, res, "tsens.T")[-1] == pytest.approx(temp, rel=1e-9)
    assert vector(out, res, "qsens.Q")[-1] == pytest.approx(temp * h * area, rel=1e-9)


# ---- signal-controlled sources in a combined run (sync ngspice, fresh process)

TS, T_END = 1e-3, 0.05
VALUES = {"V": 2.0, "I": 1e-3, "tau": 0.5, "w": 4.0, "F": 3.0, "v": 1.0, "Q": 20.0, "T": 350.0}


def _controlled_run() -> dict[str, np.ndarray]:
    d, reg = new_diagram()

    def const(name: str, block: str, port: str) -> None:
        d.add_block("Constant", settings={"value": VALUES[name]}, block_id=f"k_{block}")
        d.add_wire(f"k_{block}", "y", block, port)

    def block(spec: str, block_id: str, **settings: float) -> None:
        d.add_block(spec, settings=settings, block_id=block_id)

    # electrical: V source across a sensed 1 kOhm; I source into another 1 kOhm.
    block("ElectricalReference", "gnd")
    block("ControlledVoltageSource", "cv")
    block("CurrentSensor", "ci")
    block("Resistor", "r1", R=1e3)
    block("ControlledCurrentSource", "cc")
    block("Resistor", "r2", R=1e3)
    block("VoltageSensor", "vr2")
    wire(d, "cv.p", "ci.p")
    wire(d, "ci.n", "r1.p")
    wire(d, "cc.n", "r2.p")
    wire(d, "vr2.p", "r2.p")
    for port in ("cv.n", "r1.n", "cc.p", "r2.n", "vr2.n"):
        wire(d, port, "gnd.G")
    d.add_block("SolverConfiguration", block_id="s_e")
    wire(d, "s_e.port", "gnd.G")
    const("V", "cv", "V")
    const("I", "cc", "I")

    # rotational: torque into a damper; speed source into an angle sensor.
    block("MechanicalRotationalReference", "rref")
    block("ControlledTorqueSource", "ct")
    block("RotationalDamper", "rd", b=0.1)
    block("AngularVelocitySensor", "ws")
    block("ControlledAngularVelocitySource", "cw")
    block("AngleSensor", "phis")
    wire(d, "ct.A", "rd.A")
    wire(d, "ws.A", "rd.A")
    wire(d, "cw.A", "phis.A")
    for port in ("ct.B", "rd.B", "ws.B", "cw.B", "phis.B"):
        wire(d, port, "rref.R")
    d.add_block("SolverConfiguration", block_id="s_r")
    wire(d, "s_r.port", "rref.R")
    const("tau", "ct", "tau")
    const("w", "cw", "w")

    # translational: force into a damper; velocity source into a position sensor.
    block("MechanicalTranslationalReference", "tref")
    block("ControlledForceSource", "cf")
    block("TranslationalDamper", "td", b=1.5)
    block("VelocitySensor", "vs")
    block("ControlledVelocitySource", "cvel")
    block("PositionSensor", "xs")
    wire(d, "cf.A", "td.A")
    wire(d, "vs.A", "td.A")
    wire(d, "cvel.A", "xs.A")
    for port in ("cf.B", "td.B", "vs.B", "cvel.B", "xs.B"):
        wire(d, port, "tref.R")
    d.add_block("SolverConfiguration", block_id="s_t")
    wire(d, "s_t.port", "tref.R")
    const("F", "cf", "F")
    const("v", "cvel", "v")

    # thermal: temperature source; heat flow through 1 K/W to absolute zero.
    block("ThermalReference", "href")
    block("ControlledTemperatureSource", "ctemp")
    block("TemperatureSensor", "temps")
    block("ControlledHeatFlowSource", "cq")
    block("Convection", "air", h=10.0, area=0.1)
    block("HeatFlowSensor", "qs")
    wire(d, "temps.A", "ctemp.A")
    wire(d, "cq.A", "qs.A")
    wire(d, "qs.B", "air.A")
    for port in ("ctemp.B", "temps.B", "cq.B", "air.B"):
        wire(d, port, "href.R")
    d.add_block("SolverConfiguration", block_id="s_h")
    wire(d, "s_h.port", "href.R")
    const("T", "ctemp", "T")
    const("Q", "cq", "Q")

    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    run = CoSimulator(engine, reg).run(T_END, TS)
    phys = run.physical.vectors
    out = {"time": np.asarray(phys["time"]).real}
    for key, vec in run.mapped.vectors.items():
        if "." in key and key.count(".") == 1:  # sensor outputs <block>.<signal>
            out[key] = np.asarray(phys[vec]).real
    return out


@pytest.mark.ngspice
def test_controlled_sources_follow_their_signals() -> None:
    out = in_fresh_process(_controlled_run)
    t = out["time"]

    def final(key: str) -> float:
        return at(t, out[key], T_END)

    assert final("ci.I") == pytest.approx(VALUES["V"] / 1e3, rel=1e-6)
    assert final("vr2.V") == pytest.approx(VALUES["I"] * 1e3, rel=1e-6)
    assert final("ws.w") == pytest.approx(VALUES["tau"] / 0.1, rel=1e-6)
    assert final("vs.v") == pytest.approx(VALUES["F"] / 1.5, rel=1e-6)
    assert final("temps.T") == pytest.approx(VALUES["T"], rel=1e-9)
    assert final("qs.Q") == pytest.approx(VALUES["Q"], rel=1e-6)
    # Integrals start at 0 at the operating point (every source starts at 0); the
    # signal reaches the source at tick 0, so they grow linearly from about t = 0.
    assert final("phis.phi") == pytest.approx(VALUES["w"] * T_END, abs=VALUES["w"] * TS)
    assert final("xs.x") == pytest.approx(VALUES["v"] * T_END, abs=VALUES["v"] * TS)
