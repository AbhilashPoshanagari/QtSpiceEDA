"""M1: composite and cross-domain parts against analytic answers (plain ngspice)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from core.physical.component import build_network
from core.physical.library.composite.battery import Battery, parse_ocv_table
from tests.fixtures.physical.library import (
    add_solver,
    at,
    mapped,
    new_diagram,
    run,
    simulate,
    vector,
    wire,
)

EREF, RREF, TREF = "eref.G", "rref.R", "tref.R"


def _diagram(*refs: str):  # noqa: ANN202
    d, reg = new_diagram()
    names = {
        "eref": "ElectricalReference",
        "rref": "MechanicalRotationalReference",
        "tref": "ThermalReference",
    }
    for ref in refs:
        d.add_block(names[ref], block_id=ref)
    first = {"eref": EREF, "rref": RREF, "tref": TREF}[refs[0]]
    add_solver(d, reg, first)
    return d, reg


def _with_external(deck: str, element: str, value: float) -> str:
    """Set the held value of external source ``element`` in a plain (external=False)
    deck: the stand-in for the controller in tests without a combined run."""
    lines = []
    for line in deck.splitlines():
        if line.split()[:1] == [element]:
            head = line.split()[:3]
            line = " ".join([*head, f"dc {value!r}"])
        lines.append(line)
    return "\n".join(lines) + "\n"


def motor_response(
    t: np.ndarray, v: float, r: float, ind: float, k: float, j: float, b: float
) -> tuple[np.ndarray, np.ndarray]:
    """Exact i(t), w(t) of an unloaded DC motor from rest after a voltage step."""
    a = np.array([[-r / ind, -k / ind], [k / j, -b / j]])
    u = np.array([v / ind, 0.0])
    x_ss = -np.linalg.solve(a, u)
    lam, vec = np.linalg.eig(a)
    coeff = np.linalg.solve(vec, -x_ss)
    x = x_ss[:, None] + (vec * coeff) @ np.exp(np.outer(lam, t))
    return x[0].real, x[1].real


# ---- DC motor


@pytest.mark.ngspice
def test_unloaded_dc_motor_reaches_its_speed_with_the_expected_time_constants() -> None:
    V, R, L, k, J, b = 12.0, 1.0, 1e-3, 0.05, 1e-4, 1e-5  # noqa: N806
    d, reg = _diagram("eref", "rref")
    d.add_block("DCVoltageSource", block_id="src", settings={"V": V})
    d.add_block("DCMotor", block_id="motor", settings={"R": R, "L": L, "k": k, "J": J, "b": b})
    wire(d, "src.p", "motor.p")
    wire(d, "src.n", EREF)
    wire(d, "motor.n", EREF)
    wire(d, "motor.B", RREF)
    out, res = simulate(d, reg, ".tran 0.1m 0.4 0 0.1m uic")
    t = res.vectors["time"].real
    w = vector(out, res, "motor.A.w")
    i = np.asarray(res.vectors["I(v_motor_vi)"]).real

    w_ss = k * V / (k * k + R * b)
    assert w[-1] == pytest.approx(w_ss, rel=1e-3)
    assert i[-1] == pytest.approx(b * w_ss / k, rel=1e-2)
    i_ref, w_ref = motor_response(t, V, R, L, k, J, b)
    np.testing.assert_allclose(w, w_ref, atol=5e-3 * w_ss)
    np.testing.assert_allclose(i, i_ref, atol=5e-3 * V / R)
    # Two real poles: about -R/L (electrical) and -k^2/(J R) (mechanical).
    tau_m = J * R / (k * k + R * b)
    t63 = float(np.interp(1 - math.exp(-1), w / w_ss, t))
    assert t63 == pytest.approx(tau_m + L / R, rel=0.03)
    # The current peaks after a few electrical time constants (L / R = 1 ms).
    assert 1e-3 < t[np.argmax(i)] < 5e-3


@pytest.mark.ngspice
def test_dc_motor_back_emf_and_torque_use_the_same_constant() -> None:
    """Held at a speed by a speed source with no supply: the open armature shows
    E = k * w; shorted, it brakes with tau = k * i = k^2 w / R."""
    k, R, w = 0.1, 2.0, 50.0  # noqa: N806
    d, reg = _diagram("eref", "rref")
    d.add_block("AngularVelocitySource", block_id="drive", settings={"w": w})
    d.add_block("DCMotor", block_id="motor", settings={"k": k, "R": R})
    d.add_block("TorqueSensor", block_id="tq")
    d.add_block("Resistor", block_id="short", settings={"R": 1e-9})
    wire(d, "drive.A", "tq.A")
    wire(d, "tq.B", "motor.A")
    wire(d, "drive.B", RREF)
    wire(d, "motor.B", RREF)
    wire(d, "motor.p", "short.p")
    wire(d, "short.n", EREF)
    wire(d, "motor.n", EREF)
    out, res = simulate(d, reg, ".tran 1m 0.2")
    i = np.asarray(res.vectors["I(v_motor_vi)"]).real
    # generator: current flows out of p (negative into p), magnitude k w / R
    assert i[-1] == pytest.approx(-k * w / R, rel=1e-3)
    damping = 1e-5 * w  # the default viscous friction
    assert vector(out, res, "tq.tau")[-1] == pytest.approx(k * k * w / R + damping, rel=1e-3)


# ---- gearbox and transformer


@pytest.mark.ngspice
def test_gearbox_divides_speed_and_multiplies_torque() -> None:
    N, w, b = 5.0, 100.0, 0.02  # noqa: N806
    d, reg = _diagram("rref")
    d.add_block("AngularVelocitySource", block_id="drive", settings={"w": w})
    d.add_block("TorqueSensor", block_id="tin")
    d.add_block("Gearbox", block_id="gear", settings={"N": N})
    d.add_block("RotationalDamper", block_id="load", settings={"b": b})
    wire(d, "drive.A", "tin.A")
    wire(d, "tin.B", "gear.A")
    wire(d, "gear.B", "load.A")
    wire(d, "drive.B", RREF)
    wire(d, "load.B", RREF)
    out, res = simulate(d, reg, ".tran 1m 10m")
    assert vector(out, res, "gear.B.w")[-1] == pytest.approx(w / N, rel=1e-9)
    tau_out = b * w / N
    assert vector(out, res, "tin.tau")[-1] == pytest.approx(tau_out / N, rel=1e-6)


@pytest.mark.ngspice
def test_ideal_transformer_ratio_and_power_balance() -> None:
    n, V, R = 4.0, 100.0, 10.0  # noqa: N806
    d, reg = _diagram("eref")
    d.add_block("DCVoltageSource", block_id="src", settings={"V": V})
    d.add_block("CurrentSensor", block_id="i1")
    d.add_block("IdealTransformer", block_id="tr", settings={"n": n})
    d.add_block("Resistor", block_id="load", settings={"R": R})
    wire(d, "src.p", "i1.p")
    wire(d, "i1.n", "tr.p1")
    wire(d, "src.n", EREF)
    wire(d, "tr.n1", EREF)
    wire(d, "tr.p2", "load.p")
    wire(d, "tr.n2", EREF)
    wire(d, "load.n", EREF)
    out, res = simulate(d, reg, ".tran 1m 10m")
    v2 = vector(out, res, "tr.p2.v")[-1]
    assert v2 == pytest.approx(V / n, rel=1e-9)
    assert vector(out, res, "i1.I")[-1] == pytest.approx(v2 * v2 / R / V, rel=1e-6)


def test_zero_ratio_is_a_diagnostic() -> None:
    _, reg = new_diagram()
    for name, param in (("Gearbox", "N"), ("IdealTransformer", "n")):
        result = build_network(reg.get(name), {param: 0.0}, "X1")  # type: ignore[arg-type]
        assert not result.ok
        assert "must not be 0" in result.diagnostics[0].message


# ---- battery


def test_ocv_table_is_parsed_and_checked() -> None:
    assert parse_ocv_table("0:3, 0.5:3.7;1:4.2") == [(0.0, 3.0), (0.5, 3.7), (1.0, 4.2)]
    for bad in ("", "0:3", "0:3 0:4", "0:3 x", "0:3 1.5:4", "0-3 1:4"):
        with pytest.raises(ValueError):
            parse_ocv_table(bad)
    result = build_network(Battery, {"ocv": "nonsense"}, "bat")
    assert not result.ok
    assert result.diagnostics[0].block == "bat"


@pytest.mark.ngspice
def test_battery_at_1c_discharges_in_about_one_hour() -> None:
    cap, r_int = 2.0, 0.05
    table = "0:3.0 0.5:3.7 1:4.2"
    d, reg = _diagram("eref")
    d.add_block("Battery", block_id="bat", settings={"capacity": cap, "R_int": r_int, "ocv": table})
    d.add_block("DCCurrentSource", block_id="load", settings={"I": cap})  # 1C
    wire(d, "bat.p", "load.p")
    wire(d, "bat.n", EREF)
    wire(d, "load.n", EREF)
    out, res = simulate(d, reg, ".tran 5 4000 0 5")
    t = res.vectors["time"].real
    soc = vector(out, res, "bat.soc")
    v = vector(out, res, "bat.p.v")
    assert soc[0] == pytest.approx(1.0, abs=1e-9)  # the operating point did not move it
    assert at(t, soc, 1800.0) == pytest.approx(0.5, abs=1e-3)
    empty = float(np.interp(0.0, soc[::-1], t[::-1]))
    assert empty == pytest.approx(3600.0, rel=0.01)
    assert at(t, v, 1.0) == pytest.approx(4.2 - cap * r_int, abs=2e-3)
    assert at(t, v, 1800.0) == pytest.approx(3.7 - cap * r_int, abs=2e-3)
    assert v[-1] == pytest.approx(3.0 - cap * r_int, abs=2e-3)  # clamped at the table end


@pytest.mark.ngspice
def test_battery_soc0_and_charging() -> None:
    d, reg = _diagram("eref")
    d.add_block("Battery", block_id="bat", settings={"capacity": 1.0, "soc0": 0.2})
    d.add_block("DCCurrentSource", block_id="chg", settings={"I": 0.5})  # C/2 into p
    wire(d, "chg.n", "bat.p")
    wire(d, "chg.p", EREF)
    wire(d, "bat.n", EREF)
    out, res = simulate(d, reg, ".tran 5 3600 0 5")
    soc = vector(out, res, "bat.soc")
    assert soc[0] == pytest.approx(0.2, abs=1e-9)
    assert soc[-1] == pytest.approx(0.7, abs=2e-3)


# ---- bridges (switching), with the gate set in the deck


def _bridge_deck(name: str, settings: dict[str, object], u: float) -> tuple[object, object]:
    d, reg = _diagram("eref")
    d.add_block("DCVoltageSource", block_id="dc", settings={"V": 24.0})
    d.add_block(name, block_id="br", settings=settings)
    d.add_block("Resistor", block_id="load", settings={"R": 10.0})
    wire(d, "dc.p", "br.p")
    wire(d, "dc.n", EREF)
    wire(d, "br.n", EREF)
    if name == "HalfBridge":
        wire(d, "br.out", "load.p")
        wire(d, "load.n", EREF)
    else:
        wire(d, "br.a", "load.p")
        wire(d, "br.b", "load.n")
    out = mapped(d, reg, ".tran 1m 10m", external=False)
    return out, run(_with_external(out.deck, "V_br_Vc", u))


@pytest.mark.ngspice
@pytest.mark.parametrize(("u", "expected"), [(1.0, 24.0), (0.0, 0.0)])
def test_switching_half_bridge_follows_its_gate(u: float, expected: float) -> None:
    out, res = _bridge_deck("HalfBridge", {}, u)
    v = vector(out, res, "br.out.v")[-1]  # type: ignore[arg-type]
    assert v == pytest.approx(expected * 10.0 / 10.01, abs=1e-3)


@pytest.mark.ngspice
@pytest.mark.parametrize(("u", "sign"), [(1.0, 1.0), (0.0, -1.0)])
def test_switching_h_bridge_reverses_the_load_voltage(u: float, sign: float) -> None:
    out, res = _bridge_deck("HBridge", {}, u)
    va = vector(out, res, "br.a.v")[-1]  # type: ignore[arg-type]
    vb = vector(out, res, "br.b.v")[-1]  # type: ignore[arg-type]
    assert va - vb == pytest.approx(sign * 24.0 * 10.0 / 10.02, abs=1e-3)


@pytest.mark.ngspice
def test_half_bridge_gate_off_returns_inductive_current_through_the_low_leg() -> None:
    """Gate off with 2 A still in the load inductor: the closed low switch (R_on in
    parallel with the low diode) carries it, so out sits R_on * 2 A below n."""
    d, reg = _diagram("eref")
    d.add_block("DCVoltageSource", block_id="dc", settings={"V": 24.0})
    d.add_block("HalfBridge", block_id="br")
    d.add_block("Inductor", block_id="L", settings={"L": 1e-3, "i0": 2.0})
    d.add_block("Resistor", block_id="load", settings={"R": 1.0})
    wire(d, "dc.p", "br.p")
    wire(d, "dc.n", EREF)
    wire(d, "br.n", EREF)
    wire(d, "br.out", "L.p")
    wire(d, "L.n", "load.p")
    wire(d, "load.n", EREF)
    out, res = simulate(d, reg, ".tran 1u 20u 0 1u uic")
    v = vector(out, res, "br.out.v")
    assert -0.03 < v[1] < 0.0


# ---- parts with a thermal port


@pytest.mark.ngspice
def test_thermal_resistor_heats_a_thermal_mass_with_v_squared_over_r() -> None:
    """P = V^2 / R into a thermal mass C that loses heat to an ambient at T0 through a
    conductance G = h * area: T(t) = T0 + P / G * (1 - exp(-t G / C))."""
    V, R, C, T0, h, area = 10.0, 10.0, 100.0, 300.0, 10.0, 1e-2  # noqa: N806
    d, reg = _diagram("eref", "tref")
    d.add_block("DCVoltageSource", block_id="src", settings={"V": V})
    d.add_block("ThermalResistor", block_id="res", settings={"R": R})
    d.add_block("ThermalMass", block_id="mass", settings={"C": C, "T0": T0})
    d.add_block("Convection", block_id="conv", settings={"h": h, "area": area})
    d.add_block("TemperatureSource", block_id="amb", settings={"T": T0})
    wire(d, "src.p", "res.p")
    wire(d, "src.n", EREF)
    wire(d, "res.n", EREF)
    wire(d, "res.H", "mass.A")
    wire(d, "mass.A", "conv.A")
    wire(d, "conv.B", "amb.A")
    wire(d, "amb.B", TREF)
    out, res = simulate(d, reg, ".tran 0.5 2000 0 0.5 uic")
    t = res.vectors["time"].real
    temp = vector(out, res, "res.H.T")
    g, p = h * area, V * V / R
    np.testing.assert_allclose(temp, T0 + p / g * (1 - np.exp(-t * g / C)), atol=1e-2)


@pytest.mark.ngspice
@pytest.mark.parametrize(("gate", "power"), [(1.0, None), (0.0, 0.0)])
def test_thermal_mosfet_conduction_loss_is_its_heat(gate: float, power: float | None) -> None:
    V, R_load, R_on = 12.0, 1.0, 0.01  # noqa: N806
    d, reg = _diagram("eref", "tref")
    d.add_block("DCVoltageSource", block_id="src", settings={"V": V})
    d.add_block("Resistor", block_id="load", settings={"R": R_load})
    d.add_block("ThermalMOSFET", block_id="q", settings={"R_on": R_on})
    d.add_block("TemperatureSource", block_id="amb", settings={"T": 300.0})
    d.add_block("HeatFlowSensor", block_id="heat")
    wire(d, "src.p", "load.p")
    wire(d, "load.n", "q.d")
    wire(d, "q.s", EREF)
    wire(d, "src.n", EREF)
    wire(d, "q.H", "heat.A")
    wire(d, "heat.B", "amb.A")
    wire(d, "amb.B", TREF)
    out = mapped(d, reg, ".tran 1m 10m", external=False)
    res = run(_with_external(out.deck, "V_q_Vc", gate))
    q = vector(out, res, "heat.Q")[-1]
    if power is None:
        i = V / (R_load + R_on)
        power = i * i * R_on
    else:
        power = V * V * (1 / 1e6 + 1e-8)  # leakage: R_off and the body diode G_off
    assert q == pytest.approx(power, rel=1e-3, abs=1e-9)
