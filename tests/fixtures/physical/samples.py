"""O2: the v0.3 sample projects, built in code.

``examples/dc_motor_speed.qsp``, ``examples/heatsink_warmup.qsp`` and
``examples/battery_discharge.qsp`` are written by this module, so they can be
rebuilt after a library change::

    python -m tests.fixtures.physical.samples

The tests check that the shipped files equal what is built here and that each run
matches its analytic answer. The constants below are the sample values the README
and the tests use.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.blocks.diagram import Diagram
from core.project.io import AnalysisSettings, DiagramEntry, NetlistEntry, Project, save_project
from tests.fixtures.physical.library import new_diagram, wire

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"

# ---- DC motor speed control: PI -> averaged H-bridge -> DC motor


class Motor:
    V_DC = 24.0  # V, DC link
    R, L, K, J, B = 1.0, 1e-3, 0.05, 1e-4, 1e-5  # the M1 DC motor
    R_ON = 0.01  # Ohm, each bridge switch (two in the armature path)
    W_REF = 150.0  # rad/s (1432 rpm)
    TAU_LOAD = 0.01  # N*m, load torque step at T_LOAD
    T_LOAD = 0.15
    KP_V = 0.2  # V per rad/s
    KI_V = KP_V * K**2 / (J * R)  # zero on the mechanical pole
    TS = 100e-6
    T_END = 0.3

    @classmethod
    def duty_steady(cls, tau_load: float) -> float:
        """Duty cycle in steady state at W_REF: V(a, b) = (2d - 1) V_DC."""
        i = (cls.B * cls.W_REF + tau_load) / cls.K
        v = cls.K * cls.W_REF + (cls.R + 2 * cls.R_ON) * i
        return 0.5 + 0.5 * v / cls.V_DC


def dc_motor_speed() -> Diagram:
    m = Motor
    d, _reg = new_diagram()
    # physical side
    d.add_block("SolverConfiguration", 60, 520, {"Ts": m.TS}, "solver")
    d.add_block("ElectricalReference", 220, 520, {}, "eref")
    d.add_block("DCVoltageSource", 220, 380, {"V": m.V_DC}, "supply")
    d.add_block("HBridge", 400, 380, {"model": "averaged", "R_on": m.R_ON}, "bridge")
    d.add_block("DCMotor", 580, 380, {"R": m.R, "L": m.L, "k": m.K, "J": m.J, "b": m.B}, "motor")
    d.add_block("MechanicalRotationalReference", 760, 520, {}, "rref")
    d.add_block("AngularVelocitySensor", 760, 380, {}, "speed")
    d.add_block("ControlledTorqueSource", 760, 240, {}, "load")
    wire(d, "solver.port", "eref.G")
    wire(d, "supply.p", "bridge.p")
    wire(d, "supply.n", "eref.G")
    wire(d, "bridge.n", "eref.G")
    wire(d, "bridge.a", "motor.p")
    wire(d, "bridge.b", "motor.n")
    wire(d, "motor.B", "rref.R")
    wire(d, "speed.A", "motor.A")
    wire(d, "speed.B", "rref.R")
    wire(d, "load.A", "motor.A")  # torque is delivered at A
    wire(d, "load.B", "rref.R")
    # controller: u = PI(w_ref - w) in -1..1 (V / V_DC), duty = 0.5 + 0.5 u
    d.add_block("Constant", 40, 100, {"value": m.W_REF}, "w_ref")
    d.add_block("Sum", 200, 100, {"signs": "+-"}, "error")
    d.add_block(
        "DiscretePI",
        340,
        100,
        {
            "kp": m.KP_V / m.V_DC,
            "ki": m.KI_V / m.V_DC,
            "u_min": -1.0,
            "u_max": 1.0,
            "anti_windup": "clamping",
        },
        "pi",
    )
    d.add_block("Gain", 480, 100, {"k": 0.5}, "half")
    d.add_block("Constant", 480, 220, {"value": 0.5}, "mid")
    d.add_block("Sum", 620, 100, {"signs": "++"}, "duty")
    d.add_block(
        "Step", 620, 240, {"step_time": m.T_LOAD, "initial": 0.0, "final": -m.TAU_LOAD}, "tau_load"
    )
    d.add_block("Scope", 900, 380, {"label": "w"}, "scope_w")
    d.add_block("Scope", 900, 100, {"label": "duty"}, "scope_duty")
    d.add_wire("w_ref", "y", "error", "a")
    d.add_wire("speed", "w", "error", "b")
    d.add_wire("error", "y", "pi", "e")
    d.add_wire("pi", "y", "half", "u")
    d.add_wire("half", "y", "duty", "a")
    d.add_wire("mid", "y", "duty", "b")
    d.add_wire("duty", "y", "bridge", "u")
    d.add_wire("tau_load", "y", "load", "tau")
    d.add_wire("speed", "w", "scope_w", "u")
    d.add_wire("duty", "y", "scope_duty", "u")
    return d


# ---- heatsink warm-up: MOSFET with a thermal port on a heatsink


class Heatsink:
    V_DC = 12.0  # V
    R_LOAD = 1.0  # Ohm
    R_ON = 0.05  # Ohm, MOSFET on resistance
    C_TH = 50.0  # J/K, heatsink thermal mass
    H, AREA = 10.0, 0.05  # W/(m^2*K), m^2: R_th = 1 / (h A) = 2 K/W
    T_AMB = 298.15  # K (25 degC)
    TS = 0.1
    T_END = 600.0

    @classmethod
    def power(cls) -> float:
        i = cls.V_DC / (cls.R_LOAD + cls.R_ON)
        return i * i * cls.R_ON

    @classmethod
    def r_th(cls) -> float:
        return 1.0 / (cls.H * cls.AREA)

    @classmethod
    def temperature(cls, t: float) -> float:
        """Analytic heatsink temperature: first order, tau = R_th C_th."""
        import math

        tau = cls.r_th() * cls.C_TH
        return cls.T_AMB + cls.power() * cls.r_th() * (1.0 - math.exp(-t / tau))


def heatsink_warmup() -> Diagram:
    h = Heatsink
    d, _reg = new_diagram()
    d.add_block("SolverConfiguration", 60, 520, {"Ts": h.TS}, "solver")
    d.add_block("ElectricalReference", 220, 520, {}, "eref")
    d.add_block("DCVoltageSource", 220, 360, {"V": h.V_DC}, "supply")
    d.add_block("Resistor", 400, 240, {"R": h.R_LOAD}, "load")
    d.add_block("ThermalMOSFET", 400, 400, {"R_on": h.R_ON}, "mosfet")
    d.add_block("ThermalMass", 600, 520, {"C": h.C_TH, "T0": h.T_AMB}, "heatsink")
    d.add_block("Convection", 760, 400, {"h": h.H, "area": h.AREA}, "air")
    d.add_block("TemperatureSource", 920, 400, {"T": h.T_AMB}, "ambient")
    d.add_block("ThermalReference", 920, 520, {}, "tref")
    d.add_block("TemperatureSensor", 600, 260, {}, "temp")
    wire(d, "solver.port", "eref.G")
    wire(d, "supply.p", "load.p")
    wire(d, "load.n", "mosfet.d")
    wire(d, "mosfet.s", "eref.G")
    wire(d, "supply.n", "eref.G")
    wire(d, "mosfet.H", "heatsink.A")
    wire(d, "air.A", "heatsink.A")
    wire(d, "air.B", "ambient.A")
    wire(d, "ambient.B", "tref.R")
    wire(d, "temp.A", "heatsink.A")
    wire(d, "temp.B", "tref.R")
    d.add_block("Constant", 200, 100, {"value": 1.0}, "gate_on")
    d.add_block("Scope", 800, 260, {"label": "T_heatsink"}, "scope_t")
    d.add_wire("gate_on", "y", "mosfet", "gate")
    d.add_wire("temp", "T", "scope_t", "u")
    return d


# ---- battery discharge at constant current


class Battery:
    CAPACITY = 0.1  # A*h (small, so the sample runs in seconds of simulated time)
    R_INT = 0.05  # Ohm
    SOC0 = 1.0
    I_LOAD = 2.0  # A
    OCV = "0:3.0 0.1:3.45 0.2:3.55 0.5:3.7 0.8:3.9 0.9:4.0 1:4.2"
    TS = 0.1
    T_END = 150.0

    @classmethod
    def soc(cls, t: float) -> float:
        return cls.SOC0 - cls.I_LOAD * t / (3600.0 * cls.CAPACITY)

    @classmethod
    def voltage(cls, t: float) -> float:
        import numpy as np

        pts = [tuple(float(v) for v in p.split(":")) for p in cls.OCV.split()]
        soc, ocv = zip(*pts, strict=True)
        return float(np.interp(cls.soc(t), soc, ocv)) - cls.R_INT * cls.I_LOAD


def battery_discharge() -> Diagram:
    b = Battery
    d, _reg = new_diagram()
    d.add_block("SolverConfiguration", 60, 520, {"Ts": b.TS}, "solver")
    d.add_block("ElectricalReference", 220, 520, {}, "eref")
    d.add_block(
        "Battery",
        220,
        360,
        {"ocv": b.OCV, "R_int": b.R_INT, "capacity": b.CAPACITY, "soc0": b.SOC0},
        "battery",
    )
    d.add_block("CurrentSensor", 420, 240, {}, "ammeter")
    d.add_block("DCCurrentSource", 600, 360, {"I": b.I_LOAD}, "load")
    d.add_block("VoltageSensor", 420, 440, {}, "voltmeter")
    wire(d, "solver.port", "eref.G")
    wire(d, "battery.n", "eref.G")
    wire(d, "battery.p", "ammeter.p")
    wire(d, "ammeter.n", "load.p")
    wire(d, "load.n", "eref.G")
    wire(d, "voltmeter.p", "battery.p")
    wire(d, "voltmeter.n", "eref.G")
    # energy delivered: E = integral of V * I
    d.add_block("Product", 640, 100, {}, "power")
    d.add_block("Integrator", 780, 100, {"initial": 0.0}, "energy")
    d.add_block("Scope", 920, 100, {"label": "E"}, "scope_e")
    d.add_block("Scope", 640, 520, {"label": "V"}, "scope_v")
    d.add_block("Scope", 780, 520, {"label": "soc"}, "scope_soc")
    d.add_wire("voltmeter", "V", "power", "a")
    d.add_wire("ammeter", "I", "power", "b")
    d.add_wire("power", "y", "energy", "u")
    d.add_wire("energy", "y", "scope_e", "u")
    d.add_wire("voltmeter", "V", "scope_v", "u")
    d.add_wire("battery", "soc", "scope_soc", "u")
    return d


SAMPLES: dict[str, tuple[Callable[[], Diagram], float, float, bool]] = {
    "dc_motor_speed.qsp": (dc_motor_speed, Motor.T_END, Motor.TS, True),
    "heatsink_warmup.qsp": (heatsink_warmup, Heatsink.T_END, Heatsink.TS, True),
    "battery_discharge.qsp": (battery_discharge, Battery.T_END, Battery.TS, False),
}
"""file name -> (builder, end time, time step = Ts, display units rpm/degC)."""


def sample_project(name: str) -> Project:
    build, t_end, ts, display = SAMPLES[name]
    return Project(
        netlists=[NetlistEntry("main", "")],
        diagrams=[DiagramEntry("main", build().to_dict())],
        analysis=AnalysisSettings("", t_end, ts),
        display_units=display,
    )


def write_samples(folder: Path = EXAMPLES) -> None:
    for name in SAMPLES:
        save_project(folder / name, sample_project(name))


if __name__ == "__main__":
    write_samples()
