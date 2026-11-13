"""M4: PWM Generator, edges made inside ngspice, and the Crossing breakpoint hint."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from core.blocks.engine import ExecutionEngine
from core.blocks.library import builtin_blocks
from core.physical.component import (
    Crossing,
    ExternalSource,
    Parameter,
    PhysicalComponent,
    Port,
    Pulse,
    SignalInput,
    VoltageSource,
    build_network,
    physical_component,
)
from core.physical.cosim import CoSimulator, merge, pulse_crossings
from core.physical.domains import ELECTRICAL
from core.physical.library.converters.pwm import PWMGenerator, sawtooth
from core.physical.network import NetworkExtractor
from tests.fixtures.physical.library import mapped, new_diagram, wire
from tests.fresh import in_fresh_process

F_C = 20e3
T = 1 / F_C
TS = 100e-6
EDGE_TOL = 20e-9  # 0.04 % of the carrier period


# ---- pulse_crossings (pure)


def test_sawtooth_crossings_are_at_n_plus_duty_periods() -> None:
    wave = sawtooth(F_C)
    times = pulse_crossings(wave, 0.3, 0.0, 2 * T + 1e-9)
    ramp = [n * T + 0.3 * wave.rise for n in range(2)]  # n = 2 is past the interval
    fly = [n * T + wave.rise + 0.7 * wave.fall for n in range(2)]
    assert times == pytest.approx(sorted(ramp + fly), abs=1e-15)


def test_pulse_crossings_open_interval_levels_and_cycles() -> None:
    wave = Pulse(v1=0.0, v2=2.0, delay=1.0, rise=1.0, fall=1.0, width=1.0, period=4.0, ncycles=2)
    assert pulse_crossings(wave, 1.0, 0.0, 20.0) == [1.5, 3.5, 5.5, 7.5]  # two cycles only
    assert pulse_crossings(wave, 1.0, 1.5, 3.5) == []  # open interval
    assert pulse_crossings(wave, 0.0, 0.0, 20.0) == []  # at v1: corners only
    assert pulse_crossings(wave, 2.5, 0.0, 20.0) == []  # never reached
    one = Pulse(v1=1.0, v2=0.0, delay=0.0, rise=2.0, fall=0.0, width=1.0, period=0.0)
    assert pulse_crossings(one, 0.25, -1.0, 10.0) == [1.5]  # falling ramp, no period


# ---- the component


def test_pwm_network_is_a_comparator_with_a_crossing_hint() -> None:
    result = build_network(PWMGenerator, {"f_c": F_C, "amplitude": 5.0}, "pwm")
    assert result.ok, result.diagnostics
    kinds = {type(p).__name__ for p in result.primitives}
    assert kinds == {"ExternalSource", "VoltageSource", "Behavioural", "Crossing"}
    carrier = next(p for p in result.primitives if isinstance(p, VoltageSource))
    assert carrier.waveform is not None and carrier.waveform.period == pytest.approx(T)


@pytest.mark.parametrize("bad", [0.0, -5.0, "fast"])
def test_bad_carrier_frequency_is_a_diagnostic(bad: object) -> None:
    assert not build_network(PWMGenerator, {"f_c": bad}, "pwm").ok


def test_crossing_must_name_a_pulse_carrier_and_a_voltage_level() -> None:
    @physical_component
    class Bad(PhysicalComponent):
        ports = (Port("A", ELECTRICAL), SignalInput("u"))
        parameters = (Parameter("x", 1.0),)

        def network(self, ports, params):  # noqa: ANN001, ANN201
            return [
                VoltageSource("V", ports.A, ports.ground, 1.0),
                ExternalSource("I", ports.A, ports.ground, "i", "u"),
                Crossing("X", "V", "I"),
            ]

    messages = [d.message for d in build_network(Bad).diagnostics]
    assert any("carrier 'V' must be a VoltageSource with a Pulse" in m for m in messages)
    assert any("level 'I' must be a kind 'v' ExternalSource" in m for m in messages)


def test_mapper_records_the_hint_and_adds_no_element() -> None:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="eref")
    d.add_block("SolverConfiguration", block_id="solver", settings={"Ts": TS})
    d.add_block("PWMGenerator", block_id="pwm", settings={"f_c": F_C})
    d.add_block("Resistor", block_id="load")
    wire(d, "solver.port", "eref.G")
    wire(d, "pwm.ref", "eref.G")
    wire(d, "pwm.out", "load.p")
    wire(d, "load.n", "eref.G")
    out = mapped(d, reg, ".tran 1u 1m")
    (hint,) = out.crossings
    assert hint.block == "pwm" and hint.level == "V_pwm_Vd"
    assert hint.carrier == sawtooth(F_C)
    assert "pwm_X" not in out.deck


def test_bridge_gate_port_is_optional() -> None:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="eref")
    d.add_block("SolverConfiguration", block_id="solver")
    d.add_block("DCVoltageSource", block_id="dc")
    d.add_block("HalfBridge", block_id="br")
    d.add_block("Resistor", block_id="load")
    wire(d, "solver.port", "eref.G")
    wire(d, "dc.p", "br.p")
    wire(d, "dc.n", "eref.G")
    wire(d, "br.n", "eref.G")
    wire(d, "br.out", "load.p")
    wire(d, "load.n", "eref.G")
    extracted = NetworkExtractor(reg).extract(d)
    assert extracted.ok
    assert not [x for x in extracted.diagnostics if "'g'" in x.message]


# ---- combined run: 20 kHz PWM at Ts = 100 us


@dataclass
class Edges:
    rising: list[float] = field(default_factory=list)
    falling: list[float] = field(default_factory=list)
    gate_high: list[float] = field(default_factory=list)
    """The bridge output's level while the gate is high (M3 + M4)."""
    warnings: list[str] = field(default_factory=list)


def _edges(t: np.ndarray, y: np.ndarray, level: float) -> tuple[list[float], list[float]]:
    above = y > level
    idx = np.nonzero(above[1:] != above[:-1])[0]
    rising, falling = [], []
    for i in idx:
        when = t[i] + (level - y[i]) * (t[i + 1] - t[i]) / (y[i + 1] - y[i])
        (rising if above[i + 1] else falling).append(float(when))
    return rising, falling


def _run_pwm(duty_before: float, duty_after: float, step_time: float, t_end: float) -> Edges:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="eref")
    d.add_block("SolverConfiguration", block_id="solver", settings={"Ts": TS})
    d.add_block(
        "PWMGenerator",
        block_id="pwm",
        settings={"f_c": F_C, "amplitude": 5.0, "initial": duty_before},
    )
    d.add_block("DCVoltageSource", block_id="dc", settings={"V": 24.0})
    d.add_block("HalfBridge", block_id="br", settings={"threshold": 2.5})
    d.add_block("Resistor", block_id="load", settings={"R": 10.0})
    d.add_block(
        "Step",
        block_id="duty",
        settings={"step_time": step_time, "initial": duty_before, "final": duty_after},
    )
    wire(d, "solver.port", "eref.G")
    wire(d, "pwm.ref", "eref.G")
    wire(d, "pwm.out", "br.g")
    wire(d, "dc.p", "br.p")
    wire(d, "dc.n", "eref.G")
    wire(d, "br.n", "eref.G")
    wire(d, "br.out", "load.p")
    wire(d, "load.n", "eref.G")
    d.add_block("Constant", block_id="zero", settings={"value": 0.0})
    d.add_wire("duty", "y", "pwm", "duty")
    d.add_wire("zero", "y", "br", "u")  # the gate comes from port g
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    run = CoSimulator(engine, reg).run(t_end, TS)
    res = merge(run)
    t = np.asarray(res.vectors["time"], dtype=float)
    gate = np.asarray(res.vectors["pwm.out.v"], dtype=float)
    out = np.asarray(res.vectors["br.out.v"], dtype=float)
    rising, falling = _edges(t, gate, 2.5)
    return Edges(rising, falling, list(out[gate > 4.9]), list(run.warnings))


@pytest.mark.ngspice
def test_20khz_pwm_switches_at_the_right_times_with_ts_100us() -> None:
    step_time = 0.5e-3  # a tick: the duty changes there, mid-run
    t_end = 1e-3
    got = in_fresh_process(_run_pwm, 0.3, 0.75, step_time, t_end, timeout=300)
    assert got.warnings == []
    # high at every carrier reset n*T (after t = 0), low at n*T + d*T
    # (the last one, at t_end, ends the flyback a fraction of a ns before t_end)
    expected_rise = [n * T for n in range(1, round(t_end / T) + 1)]
    assert len(got.rising) == len(expected_rise)
    assert got.rising == pytest.approx(expected_rise, abs=EDGE_TOL)
    # the Step block sees t >= step_time from the tick at step_time on; the PWM uses it
    # from that tick (zero-order hold)
    expected_fall = [
        n * T + (0.3 if n * T < step_time - 1e-12 else 0.75) * T for n in range(round(t_end / T))
    ]
    assert len(got.falling) == len(expected_fall)
    assert got.falling == pytest.approx(expected_fall, abs=EDGE_TOL)
    # and the half-bridge follows the gate port: out = 24 V * 10 / 10.01 while high
    assert np.median(got.gate_high) == pytest.approx(24.0 * 10.0 / 10.01, rel=1e-3)


@pytest.mark.ngspice
@pytest.mark.parametrize(("duty", "n_rise", "n_fall"), [(0.0, 0, 0), (1.2, 0, 0)])
def test_duty_outside_0_1_holds_the_output(duty: float, n_rise: int, n_fall: int) -> None:
    got = in_fresh_process(_run_pwm, duty, duty, 1.0, 0.5e-3, timeout=300)
    assert (len(got.rising), len(got.falling)) == (n_rise, n_fall)
