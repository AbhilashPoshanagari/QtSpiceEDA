"""B4: thermal components against analytic answers (plain ngspice, uic)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from core.physical.component import build_network
from core.physical.library.thermal.components import Conduction, Convection
from tests.fixtures.physical.library import add_solver, at, new_diagram, simulate, vector, wire

REF = "ref.R"


def _diagram(t_amb: float):  # noqa: ANN202
    d, reg = new_diagram()
    d.add_block("ThermalReference", block_id="ref")
    d.add_block("TemperatureSource", block_id="ambient", settings={"T": t_amb})
    wire(d, "ambient.B", REF)
    add_solver(d, reg, REF)
    return d, reg


def test_conduction_and_convection_resistances() -> None:
    (r,) = build_network(Conduction, {"k": 200.0, "area": 1e-4, "thickness": 2e-3}).primitives
    assert r.value == pytest.approx(2e-3 / (200.0 * 1e-4))
    (r,) = build_network(Convection, {"h": 25.0, "area": 0.02}).primitives
    assert r.value == pytest.approx(1.0 / (25.0 * 0.02))
    assert not build_network(Convection, {"h": 0.0}).ok


@pytest.mark.ngspice
def test_heated_thermal_mass_settles_with_time_constant_rc() -> None:
    c, q, t_amb = 50.0, 10.0, 298.15
    k, area, thickness = 1.0, 1e-2, 2e-2  # R = 2 K/W
    r = thickness / (k * area)
    d, reg = _diagram(t_amb)
    d.add_block("ThermalMass", block_id="body", settings={"C": c, "T0": t_amb})
    d.add_block(
        "Conduction",
        block_id="wall",
        settings={"k": k, "area": area, "thickness": thickness},
    )
    d.add_block("HeatFlowSource", block_id="heater", settings={"Q": q})
    wire(d, "body.A", "wall.A")
    wire(d, "wall.B", "ambient.A")
    wire(d, "heater.A", "body.A")
    wire(d, "heater.B", REF)
    out, res = simulate(d, reg, ".tran 0.1 1000 0 0.1 uic")  # 10 RC
    t = res.vectors["time"].real
    temp = vector(out, res, "body.A.T")
    rise = q * r
    np.testing.assert_allclose(temp, t_amb + rise * (1 - np.exp(-t / (r * c))), atol=5e-3 * rise)
    assert at(t, temp, r * c) - t_amb == pytest.approx(rise * (1 - math.exp(-1)), rel=2e-3)
    assert temp[-1] == pytest.approx(t_amb + rise, abs=1e-3 * rise)


@pytest.mark.ngspice
def test_hot_mass_cools_by_convection() -> None:
    c, h, area, t0, t_amb = 20.0, 10.0, 0.1, 400.0, 300.0
    d, reg = _diagram(t_amb)
    d.add_block("ThermalMass", block_id="body", settings={"C": c, "T0": t0})
    d.add_block("Convection", block_id="air", settings={"h": h, "area": area})
    wire(d, "body.A", "air.A")
    wire(d, "air.B", "ambient.A")
    out, res = simulate(d, reg, ".tran 0.05 100 0 0.05 uic")
    t = res.vectors["time"].real
    temp = vector(out, res, "body.A.T")
    tau = c / (h * area)
    np.testing.assert_allclose(temp, t_amb + (t0 - t_amb) * np.exp(-t / tau), atol=0.05)
