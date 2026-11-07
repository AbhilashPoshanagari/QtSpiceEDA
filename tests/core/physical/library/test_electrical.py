"""B1: electrical components against analytic answers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from core.physical.component import Pulse, Sine, VoltageSource, build_network, check_network
from core.physical.library.electrical.components import (
    ControlledSwitch,
    Diode,
    PulseVoltageSource,
    SineVoltageSource,
)
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

# ---- no ngspice


def test_sine_and_pulse_reach_the_deck() -> None:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="gnd")
    d.add_block(
        "SineVoltageSource",
        block_id="vs",
        settings={"offset": 1.0, "amplitude": 2.0, "freq": 50.0},
    )
    d.add_block("PulseCurrentSource", block_id="ip", settings={"ncycles": 3})
    d.add_block("Resistor", block_id="r1")
    d.add_block("Resistor", block_id="r2")
    wire(d, "vs.p", "r1.p")
    wire(d, "ip.n", "r2.p")
    for part in ("vs.n", "r1.n", "ip.p", "r2.n"):
        wire(d, part, "gnd.G")
    add_solver(d, reg, "gnd.G")
    deck = mapped(d, reg).deck
    assert "V_vs_V electrical_1 0 sin(1.0 2.0 50.0 0.0 0.0 0.0)" in deck
    assert "I_ip_I 0 electrical_2 pulse(0.0 1.0 0.0 1e-06 1e-06 0.0005 0.001 3)" in deck


def test_waveform_checks() -> None:
    spec = SineVoltageSource.__physical_spec__  # type: ignore[attr-defined]
    bad = [
        VoltageSource("A", "p", "n", 0.0, Pulse(0, 1, period=1e-3, width=2e-3)),
        VoltageSource("B", "p", "n", 0.0, Sine(0, 1, -5.0)),
        VoltageSource("C", "p", "n", 0.0, Sine(0, float("nan"), 5.0)),
    ]
    found = {d.primitive for d in check_network(spec, bad)}
    assert found == {"A", "B", "C"}


def test_bad_component_values_are_diagnostics() -> None:
    for cls, params in (
        (Diode, {"R_on": 0.0}),
        (ControlledSwitch, {"R_off": -1.0}),
        (PulseVoltageSource, {"rise": -1.0}),
    ):
        result = build_network(cls, params, "X1")
        assert not result.ok, cls


def test_switch_has_an_external_control() -> None:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="gnd")
    d.add_block("ControlledSwitch", block_id="sw")
    wire(d, "sw.n", "gnd.G")
    add_solver(d, reg, "gnd.G")
    out = mapped(d, reg)
    (ext,) = out.external_inputs
    assert (ext.block, ext.signal, ext.kind, ext.domain) == ("sw", "ctrl", "v", "electrical")


# ---- plain ngspice


def _ground(d, reg, *ports: str) -> None:  # noqa: ANN001
    d.add_block("ElectricalReference", block_id="gnd")
    for port in ports:
        wire(d, port, "gnd.G")
    add_solver(d, reg, "gnd.G")


@pytest.mark.ngspice
def test_rc_charges_with_time_constant_rc() -> None:
    vs, r, c = 5.0, 1e3, 1e-6
    d, reg = new_diagram()
    d.add_block("DCVoltageSource", block_id="src", settings={"V": vs})
    d.add_block("Resistor", block_id="r", settings={"R": r})
    d.add_block("Capacitor", block_id="cap", settings={"C": c})
    wire(d, "src.p", "r.p")
    wire(d, "r.n", "cap.p")
    _ground(d, reg, "src.n", "cap.n")
    out, res = simulate(d, reg, ".tran 5u 5m 0 5u uic")
    t = res.vectors["time"].real
    v = vector(out, res, "cap.p.v")
    tau = r * c
    assert at(t, v, tau) == pytest.approx(vs * (1 - math.exp(-1)), rel=2e-3)
    np.testing.assert_allclose(v, vs * (1 - np.exp(-t / tau)), atol=5e-3 * vs)


@pytest.mark.ngspice
def test_rl_decays_with_time_constant_l_over_r() -> None:
    r, ind = 10.0, 10e-3
    d, reg = new_diagram()
    d.add_block("DCVoltageSource", block_id="src", settings={"V": 1.0})
    d.add_block("Resistor", block_id="r", settings={"R": r})
    d.add_block("Inductor", block_id="ind", settings={"L": ind})
    wire(d, "src.p", "r.p")
    wire(d, "r.n", "ind.p")
    _ground(d, reg, "src.n", "ind.n")
    out, res = simulate(d, reg, ".tran 5u 5m 0 5u uic")
    t = res.vectors["time"].real
    v = vector(out, res, "ind.p.v")
    assert at(t, v, ind / r) == pytest.approx(math.exp(-1), rel=5e-3)


@pytest.mark.ngspice
def test_sine_and_pulse_voltage_values() -> None:
    d, reg = new_diagram()
    d.add_block(
        "SineVoltageSource",
        block_id="vs",
        settings={"offset": 1.0, "amplitude": 2.0, "freq": 50.0},
    )
    d.add_block(
        "PulseVoltageSource",
        block_id="vp",
        settings={"v1": 0.0, "v2": 5.0, "delay": 1e-3, "width": 2e-3, "period": 5e-3},
    )
    d.add_block("Resistor", block_id="r1")
    d.add_block("Resistor", block_id="r2")
    wire(d, "vs.p", "r1.p")
    wire(d, "vp.p", "r2.p")
    _ground(d, reg, "vs.n", "vp.n", "r1.n", "r2.n")
    out, res = simulate(d, reg, ".tran 10u 10m 0 10u")
    t = res.vectors["time"].real
    sine = vector(out, res, "vs.p.v")
    assert at(t, sine, 0.0) == pytest.approx(1.0, abs=1e-6)
    assert at(t, sine, 5e-3) == pytest.approx(3.0, rel=1e-3)
    assert at(t, sine, 15e-3 - 10e-3) == pytest.approx(3.0, rel=1e-3)
    p = vector(out, res, "vp.p.v")
    for when, want in ((0.5e-3, 0.0), (2e-3, 5.0), (4e-3, 0.0), (7e-3, 5.0), (9e-3, 0.0)):
        assert at(t, p, when) == pytest.approx(want, abs=1e-6), when


@pytest.mark.ngspice
def test_current_sources_drive_current_out_of_n() -> None:
    d, reg = new_diagram()
    d.add_block("DCCurrentSource", block_id="idc", settings={"I": 2e-3})
    d.add_block(
        "SineCurrentSource",
        block_id="isin",
        settings={"offset": 0.0, "amplitude": 1e-3, "freq": 100.0},
    )
    d.add_block("Resistor", block_id="r1", settings={"R": 1e3})
    d.add_block("Resistor", block_id="r2", settings={"R": 1e3})
    wire(d, "idc.n", "r1.p")
    wire(d, "isin.n", "r2.p")
    _ground(d, reg, "idc.p", "isin.p", "r1.n", "r2.n")
    out, res = simulate(d, reg, ".tran 10u 10m 0 10u")
    t = res.vectors["time"].real
    assert at(t, vector(out, res, "r1.p.v"), 5e-3) == pytest.approx(2.0, rel=1e-6)
    assert at(t, vector(out, res, "r2.p.v"), 2.5e-3) == pytest.approx(1.0, rel=1e-3)


@pytest.mark.ngspice
@pytest.mark.parametrize("vs", [5.0, -5.0])
def test_diode_forward_and_reverse(vs: float) -> None:
    r, vf, r_on, g_off = 100.0, 0.6, 0.3, 1e-8
    d, reg = new_diagram()
    d.add_block("DCVoltageSource", block_id="src", settings={"V": vs})
    d.add_block("Resistor", block_id="r", settings={"R": r})
    d.add_block("Diode", block_id="d1")
    wire(d, "src.p", "r.p")
    wire(d, "r.n", "d1.p")
    _ground(d, reg, "src.n", "d1.n")
    out, res = simulate(d, reg, ".tran 1u 10u")
    vd = vector(out, res, "d1.p.v")[-1]
    if vs > 0:
        # (vs - Vd)/R = (Vd - Vf)/R_on + Vf*G_off
        exact = (vs / r + vf / r_on - vf * g_off) / (1 / r + 1 / r_on)
    else:
        exact = vs / (1 + r * g_off)
    assert vd == pytest.approx(exact, rel=1e-6)


@pytest.mark.ngspice
def test_switch_is_open_without_a_controller() -> None:
    d, reg = new_diagram()
    d.add_block("DCVoltageSource", block_id="src", settings={"V": 1.0})
    d.add_block("Resistor", block_id="r", settings={"R": 1e3})
    d.add_block("ControlledSwitch", block_id="sw")
    wire(d, "src.p", "r.p")
    wire(d, "r.n", "sw.p")
    _ground(d, reg, "src.n", "sw.n")
    out, res = simulate(d, reg, ".tran 1u 10u")
    assert vector(out, res, "sw.p.v")[-1] == pytest.approx(1e6 / (1e6 + 1e3), rel=1e-6)


def _switch_closed() -> float:
    from core.blocks.engine import ExecutionEngine
    from core.blocks.library import builtin_blocks
    from core.physical.cosim import CoSimulator

    d, reg = new_diagram()
    d.add_block("DCVoltageSource", block_id="src", settings={"V": 1.0})
    d.add_block("Resistor", block_id="r", settings={"R": 1e3})
    d.add_block("ControlledSwitch", block_id="sw")
    d.add_block("Constant", settings={"value": 1.0}, block_id="on")
    wire(d, "src.p", "r.p")
    wire(d, "r.n", "sw.p")
    _ground(d, reg, "src.n", "sw.n")
    d.add_wire("on", "y", "sw", "ctrl")
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    run = CoSimulator(engine, reg).run(1e-3, 1e-4)
    return float(run.physical.vectors[run.mapped.vectors["sw.p.v"]].real[-1])


@pytest.mark.ngspice
def test_switch_closes_when_the_control_is_high() -> None:
    v = in_fresh_process(_switch_closed)
    assert v == pytest.approx(1e-3 / (1e3 + 1e-3), rel=1e-4)
