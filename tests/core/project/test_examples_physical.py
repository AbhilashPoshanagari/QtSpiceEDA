"""O2: the v0.3 sample projects (DC motor speed control, heatsink warm-up, battery
discharge). Each opens clean, equals what tests/fixtures/physical/samples.py builds,
passes the network checks and, run in the BlockRunner child, matches its analytic
answer."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from core.blocks.runner import OK, BlockRunner
from core.physical.network import NetworkExtractor
from core.project.io import load_project
from tests.fixtures.physical.library import library_registry, load_diagram
from tests.fixtures.physical.samples import SAMPLES, Battery, Heatsink, Motor, sample_project

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


@pytest.mark.parametrize("name", list(SAMPLES))
def test_sample_opens_clean_and_equals_its_builder(name: str) -> None:
    loaded = load_project(EXAMPLES / name)
    assert loaded.ok and loaded.warnings == [], loaded.errors
    assert loaded.project is not None
    assert loaded.project.to_dict() == sample_project(name).to_dict()
    assert loaded.project.results == {}  # samples ship without results


@pytest.mark.parametrize("name", list(SAMPLES))
def test_sample_has_one_checked_network_with_ts_equal_to_the_time_step(name: str) -> None:
    project = sample_project(name)
    diagram = load_diagram(project.diagrams[0].diagram)
    extracted = NetworkExtractor(library_registry()).extract(diagram)
    assert extracted.ok, extracted.diagnostics
    assert len(extracted.networks) == 1
    solver = diagram.block("solver")
    assert solver.settings["Ts"] == project.analysis.diagram_dt


def run(name: str) -> dict[str, np.ndarray]:
    project = sample_project(name)
    outcome = BlockRunner(
        project.diagrams[0].diagram,
        {},
        project.analysis.diagram_t_end,
        project.analysis.diagram_dt,
        timeout=300,
    ).run()
    assert outcome.status == OK, outcome.traceback
    assert outcome.result is not None
    return {k: np.asarray(v).real for k, v in outcome.result.vectors.items()}


@pytest.mark.ngspice
def test_dc_motor_reaches_the_set_speed_with_the_analytic_duty_cycle() -> None:
    v = run("dc_motor_speed.qsp")
    t, w, duty = v["time"], v["w"], v["duty"]
    assert "motor.A.w" in v  # physical vectors are <component>.<port>.<quantity>
    # clamping anti-windup: no overshoot after the saturated start
    assert w.max() <= Motor.W_REF * 1.001
    assert w[-1] == pytest.approx(Motor.W_REF, rel=1e-3)
    # steady state: V(a, b) = k w + (R + 2 R_on) i with i = (b w + tau_load) / k
    before = np.searchsorted(t, Motor.T_LOAD) - 1
    assert duty[before] == pytest.approx(Motor.duty_steady(0.0), abs=1e-4)
    assert duty[-1] == pytest.approx(Motor.duty_steady(Motor.TAU_LOAD), abs=1e-4)
    assert duty.min() > 0.0 and duty.max() <= 1.0


@pytest.mark.ngspice
def test_heatsink_follows_the_first_order_warm_up() -> None:
    v = run("heatsink_warmup.qsp")
    t, temp = v["time"], v["T_heatsink"]
    tau = Heatsink.r_th() * Heatsink.C_TH
    assert tau == pytest.approx(100.0)
    assert Heatsink.power() * Heatsink.r_th() == pytest.approx(13.06, abs=0.01)
    for when in (10.0, tau, 3 * tau, Heatsink.T_END - Heatsink.TS):
        assert np.interp(when, t, temp) == pytest.approx(Heatsink.temperature(when), abs=0.01)
    # 63 % of the final rise after one time constant
    rise = Heatsink.power() * Heatsink.r_th()
    assert np.interp(tau, t, temp) - Heatsink.T_AMB == pytest.approx(
        rise * (1 - math.exp(-1)), rel=1e-3
    )


@pytest.mark.ngspice
def test_battery_soc_falls_linearly_and_voltage_follows_the_ocv_table() -> None:
    v = run("battery_discharge.qsp")
    t = v["time"]
    for when in (1.0, 50.0, 100.0, Battery.T_END - Battery.TS):
        assert np.interp(when, t, v["soc"]) == pytest.approx(Battery.soc(when), abs=1e-6)
        assert np.interp(when, t, v["V"]) == pytest.approx(Battery.voltage(when), abs=1e-4)
    assert np.allclose(v["ammeter.I"], Battery.I_LOAD)
    # energy: forward-Euler integral of V * I on the controller ticks
    ticks = np.arange(0.0, Battery.T_END, Battery.TS)
    expected = sum(Battery.voltage(k) * Battery.I_LOAD * Battery.TS for k in ticks[:-1])
    assert v["E"][-1] == pytest.approx(expected, rel=2e-3)
