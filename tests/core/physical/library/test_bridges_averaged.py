"""M3: averaged and switching half-bridge / H-bridge, chosen by the ``model`` parameter."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from core.blocks.decorator import block, spec_of
from core.blocks.diagram import Diagram
from core.blocks.engine import ExecutionEngine
from core.blocks.library import builtin_blocks, builtin_specs
from core.blocks.spec import BlockSpec
from core.physical.component import build_network
from core.physical.cosim import CoSimulator
from core.physical.library import physical_registry
from tests.fixtures.physical.library import add_solver, mapped, new_diagram, run, vector, wire
from tests.fresh import in_fresh_process

EREF, RREF = "eref.G", "rref.R"
VDC = 12.0
MOTOR = {"R": 1.0, "L": 1e-3, "k": 0.05, "J": 1e-4, "b": 1e-5}
TS = 50e-6
PWM_TICKS = 20  # 1 kHz PWM at Ts = 50 us
DUTY = 0.75


@block(stateful=True)
class Pwm:
    """Gate signal: 1 for the first duty * n ticks of every n ticks, else 0."""

    def __init__(self, n=20, duty=0.5):  # noqa: ANN001, ANN204
        self.n = int(n)
        self.on = round(duty * self.n)
        self.k = 0

    def output(self):  # noqa: ANN201
        return 1.0 if self.k % self.n < self.on else 0.0

    def step(self):  # noqa: ANN201
        self.k += 1


def _with_external(deck: str, element: str, value: float) -> str:
    out = []
    for line in deck.splitlines():
        if line.split()[:1] == [element]:
            line = " ".join([*line.split()[:3], f"dc {value!r}"])
        out.append(line)
    return "\n".join(out) + "\n"


# ---- plain ngspice: the averaged legs with the duty set in the deck


@pytest.mark.ngspice
@pytest.mark.parametrize("duty", [0.0, 0.25, 0.8, 1.0, 1.5])
def test_averaged_half_bridge_output_is_duty_times_the_link(duty: float) -> None:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="eref")
    add_solver(d, reg, EREF)
    d.add_block("DCVoltageSource", block_id="dc", settings={"V": 24.0})
    d.add_block("CurrentSensor", block_id="idc")
    d.add_block("HalfBridge", block_id="br", settings={"model": "averaged", "R_on": 0.01})
    d.add_block("Resistor", block_id="load", settings={"R": 10.0})
    wire(d, "dc.p", "idc.p")
    wire(d, "idc.n", "br.p")
    wire(d, "dc.n", EREF)
    wire(d, "br.n", EREF)
    wire(d, "br.out", "load.p")
    wire(d, "load.n", EREF)
    out = mapped(d, reg, ".tran 1m 5m", external=False)
    res = run(_with_external(out.deck, "V_br_Vc", duty))
    d_eff = min(max(duty, 0.0), 1.0)  # clamped
    v_out = vector(out, res, "br.out.v")[-1]
    assert v_out == pytest.approx(d_eff * 24.0 * 10.0 / 10.01, abs=1e-6)
    # the link supplies the power the load takes, plus R_on's loss
    i_load = v_out / 10.0
    assert vector(out, res, "idc.I")[-1] == pytest.approx(d_eff * i_load, abs=1e-9)


@pytest.mark.ngspice
def test_averaged_h_bridge_output_is_two_d_minus_one_times_the_link() -> None:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="eref")
    add_solver(d, reg, EREF)
    d.add_block("DCVoltageSource", block_id="dc", settings={"V": 24.0})
    d.add_block("HBridge", block_id="br", settings={"model": "Averaged"})
    d.add_block("Resistor", block_id="load", settings={"R": 10.0})
    wire(d, "dc.p", "br.p")
    wire(d, "dc.n", EREF)
    wire(d, "br.n", EREF)
    wire(d, "br.a", "load.p")
    wire(d, "br.b", "load.n")
    out = mapped(d, reg, ".tran 1m 5m", external=False)
    res = run(_with_external(out.deck, "V_br_Vc", 0.25))
    vab = vector(out, res, "br.a.v")[-1] - vector(out, res, "br.b.v")[-1]
    assert vab == pytest.approx(-0.5 * 24.0 * 10.0 / 10.02, abs=1e-6)


def test_unknown_model_is_a_diagnostic() -> None:
    reg = physical_registry()
    for name in ("HalfBridge", "HBridge"):
        result = build_network(reg.get(name), {"model": "ideal"}, "br")  # type: ignore[arg-type]
        assert not result.ok
        assert "switching or averaged" in result.diagnostics[0].message


# ---- combined run: switching with a PWM gate vs averaged with the duty


def _motor_drive(model: str) -> Diagram:
    reg = physical_registry()
    signal: dict[str, BlockSpec] = {s.name: s for s in builtin_specs()}
    pwm_spec = spec_of(Pwm)
    assert pwm_spec is not None
    signal["Pwm"] = pwm_spec

    def lookup(name: str) -> BlockSpec | None:
        spec = reg.spec(name)
        return spec.block if spec is not None else signal.get(name)

    d = Diagram(lookup)
    d.add_block("ElectricalReference", block_id="eref")
    d.add_block("MechanicalRotationalReference", block_id="rref")
    d.add_block("SolverConfiguration", settings={"Ts": TS}, block_id="solver")
    d.add_block("DCVoltageSource", block_id="dc", settings={"V": VDC})
    d.add_block("HBridge", block_id="br", settings={"model": model})
    d.add_block("DCMotor", block_id="motor", settings=MOTOR)
    d.add_block("AngularVelocitySensor", block_id="ws")
    wire(d, "solver.port", EREF)
    wire(d, "dc.p", "br.p")
    wire(d, "dc.n", EREF)
    wire(d, "br.n", EREF)
    wire(d, "br.a", "motor.p")
    wire(d, "br.b", "motor.n")
    wire(d, "motor.B", RREF)
    wire(d, "ws.A", "motor.A")
    wire(d, "ws.B", RREF)
    if model == "switching":
        d.add_block("Pwm", settings={"n": PWM_TICKS, "duty": DUTY}, block_id="u")
    else:
        d.add_block("Constant", settings={"value": DUTY}, block_id="u")
    d.add_block("Scope", settings={"label": "w"}, block_id="scope")
    d.add_wire("u", "y", "br", "u")
    d.add_wire("ws", "w", "scope", "u")
    return d


@dataclass
class Speed:
    t: list[float] = field(default_factory=list)
    w: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _run_drive(model: str, t_end: float) -> Speed:
    reg = physical_registry()
    engine = ExecutionEngine({**builtin_blocks(), "Pwm": Pwm}, reg)
    engine.compile(_motor_drive(model))
    run_ = CoSimulator(engine, reg).run(t_end, TS)
    return Speed(
        list(run_.signal.vectors["time"].real),
        list(run_.signal.vectors["w"].real),
        list(run_.warnings),
    )


@pytest.mark.ngspice
def test_averaged_and_switching_h_bridges_give_the_same_average_motor_speed() -> None:
    t_end = 0.3  # about 7 mechanical time constants (J R / k^2 = 40 ms)
    sw = in_fresh_process(_run_drive, "switching", t_end, timeout=300)
    av = in_fresh_process(_run_drive, "averaged", t_end, timeout=300)
    assert sw.warnings == [] and av.warnings == []

    def mean_speed(s: Speed) -> float:
        t, w = np.asarray(s.t), np.asarray(s.w)
        window = t > t_end - 0.1  # whole PWM periods: 2000 ticks, 100 periods
        return float(np.mean(w[window]))

    w_sw, w_av = mean_speed(sw), mean_speed(av)
    assert w_sw == pytest.approx(w_av, rel=0.02)
    # and both are the analytic speed for V = (2d - 1) Vdc behind R + 2 R_on
    k, b = MOTOR["k"], MOTOR["b"]
    r = MOTOR["R"] + 2 * 0.01
    w_expected = k * (2 * DUTY - 1) * VDC / (k * k + r * b)
    assert w_av == pytest.approx(w_expected, rel=0.01)
    # the switching run really switches: its speed ripples around the averaged one
    late = np.asarray(sw.t) > t_end - 0.1
    ripple = np.asarray(sw.w)[late] - np.asarray(av.w)[late]
    assert np.ptp(ripple) > 0.05
    assert abs(float(np.mean(ripple))) < 0.02 * w_av
