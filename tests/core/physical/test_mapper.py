"""F6: NetworkMapper - physical network -> Circuit -> ngspice deck.

The two ngspice tests run the mapped decks in plain ngspice and compare them with
the analytic answers (done-when for F4-F6: within 0.5 %).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from core.netlist.model import ERROR
from core.netlist.parser import parse
from core.physical.component import (
    Behavioural,
    PhysicalComponent,
    Port,
    Sensor,
    SignalOutput,
    VoltageSource,
    build_network,
    physical_component,
)
from core.physical.domains import ELECTRICAL, THERMAL
from core.physical.mapper import NetworkMapper
from core.physical.network import NetworkExtractor
from core.sim.ngspice_adapter import NgspiceAdapter
from tests.fixtures.physical.components import new_diagram, registry, wire

# ---- networks


def msd_diagram(m: float = 1.0, k: float = 100.0, b: float = 4.0, F: float = 10.0):
    """Force F on a mass m held by spring k and damper b; position sensor Pos."""
    d, reg = new_diagram()
    d.add_block("Mass", block_id="Mass", settings={"m": m})
    d.add_block("Spring", block_id="Spring", settings={"k": k})
    d.add_block("Damper", block_id="Damper", settings={"b": b})
    d.add_block("Force", block_id="Push", settings={"F": F})
    d.add_block("Integrator", block_id="Pos")
    d.add_block("TransRef", block_id="Ref")
    d.add_block("Solver", block_id="Solver")
    for part in ("Spring", "Damper", "Push"):
        wire(d, "Mass.A", f"{part}.A")
        wire(d, f"{part}.B", "Ref.V")
    wire(d, "Pos.A", "Mass.A")
    wire(d, "Solver.port", "Mass.A")
    return d, reg


def thermal_diagram(C: float = 50.0, R: float = 2.0, Q: float = 10.0, T: float = 298.15):
    """Heat Q into thermal mass C, which loses it through R to an ambient at T."""
    d, reg = new_diagram()
    d.add_block("ThermalMass", block_id="Body", settings={"C": C, "T0": T})
    d.add_block("ThermalResistance", block_id="Rth", settings={"R": R})
    d.add_block("HeatFlow", block_id="Heater", settings={"Q": Q})
    d.add_block("TemperatureSource", block_id="Ambient", settings={"T": T})
    d.add_block("ThermalRef", block_id="Zero")
    d.add_block("Solver", block_id="Solver")
    wire(d, "Body.A", "Rth.A")
    wire(d, "Rth.B", "Ambient.A")
    wire(d, "Ambient.B", "Zero.T")
    wire(d, "Heater.A", "Body.A")
    wire(d, "Heater.B", "Zero.T")
    wire(d, "Solver.port", "Body.A")
    return d, reg


def mapped(d, reg, analysis=None, **kw):
    extracted = NetworkExtractor(reg).extract(d)
    assert extracted.ok, extracted.diagnostics
    (network,) = extracted.networks
    return NetworkMapper(reg).map(network, analysis, **kw)


def lines(deck: str) -> list[str]:
    return deck.splitlines()


# ---- mapping


def test_msd_elements_nodes_and_vectors() -> None:
    d, reg = msd_diagram()
    out = mapped(d, reg, ".tran 1m 1 uic")
    assert out.ok and out.diagnostics == []
    deck = lines(out.deck)
    assert deck[0] == "QtSpice physical network: Mass, Spring, Damper, Push, Pos, Ref, Solver"
    assert deck[1:] == [
        "C_Mass_C translational_1 0 1.0 ic=0.0",
        "L_Spring_L translational_1 0 0.01 ic=0.0",
        "R_Damper_R translational_1 0 0.25",
        "I_Push_I 0 translational_1 dc 10.0",
        "G_Pos_G 0 translational_2 translational_1 0 1.0",
        "C_Pos_C translational_2 0 1.0 ic=0.0",
        "B_Pos_S s_pos_x 0 V=V(translational_2)",
        ".tran 1m 1 uic",
        ".end",
    ]
    assert out.node_names[("Mass", "A")] == "translational_1"
    assert out.node_names[("Ref", "V")] == "0"
    assert out.node_names[("Solver", "port")] == "translational_1"
    assert out.vectors == {
        "Mass.A.v": "V(translational_1)",
        "Spring.A.v": "V(translational_1)",
        "Damper.A.v": "V(translational_1)",
        "Push.A.v": "V(translational_1)",
        "Pos.x": "V(s_pos_x)",
        "Pos.A.v": "V(translational_1)",
    }
    assert out.units["Pos.x"] == "m" and out.units["Mass.A.v"] == "m/s"
    # The deck is a clean netlist for the v0.2 parser.
    assert parse(out.deck).ok


def test_thermal_reference_and_source() -> None:
    d, reg = thermal_diagram()
    out = mapped(d, reg)
    assert lines(out.deck)[1:] == [
        "C_Body_C thermal_1 0 50.0 ic=298.15",
        "R_Rth_R thermal_1 thermal_2 2.0",
        "I_Heater_I 0 thermal_1 dc 10.0",
        "V_Ambient_V thermal_2 0 dc 298.15",
        ".end",
    ]
    assert out.vectors["Body.A.T"] == "V(thermal_1)"
    assert out.units["Body.A.T"] == "K"


def test_two_domains_controlled_sources_are_rewritten() -> None:
    d, reg = new_diagram()
    for spec in ("ElecResistor", "Machine", "RotDamper", "ElecRef", "RotRef", "Solver"):
        d.add_block(spec)
    wire(d, "ElecResistor1.p", "Machine1.p")
    wire(d, "ElecResistor1.n", "ElecRef1.G")
    wire(d, "Machine1.n", "ElecRef1.G")
    wire(d, "Machine1.shaft", "RotDamper1.A")
    wire(d, "RotDamper1.B", "RotRef1.W")
    wire(d, "Solver1.port", "Machine1.p")
    out = mapped(d, reg)
    assert lines(out.deck)[1:-1] == [
        "R_ElecResistor1_R electrical_1 0 1.0",
        "V_Machine1_Vs electrical_1 electrical_2 dc 0.0",
        "E_Machine1_E electrical_2 0 rotational_1 0 0.05",
        "F_Machine1_T 0 rotational_1 V_Machine1_Vs 0.05",
        "R_RotDamper1_R rotational_1 0 100.0",
    ]
    assert out.vectors["Machine1.shaft.w"] == "V(rotational_1)"
    assert out.units["Machine1.shaft.w"] == "rad/s"


def test_external_sources() -> None:
    d, reg = new_diagram()
    for spec in ("Mass", "ForceInput", "TransRef", "Solver"):
        d.add_block(spec)
    wire(d, "Mass1.A", "ForceInput1.A")
    wire(d, "ForceInput1.B", "TransRef1.V")
    wire(d, "Solver1.port", "Mass1.A")
    out = mapped(d, reg)
    assert "I_ForceInput1_I 0 translational_1 dc 0.5 external" in lines(out.deck)
    (ext,) = out.external_inputs
    assert (ext.element, ext.block, ext.signal, ext.kind, ext.initial) == (
        "I_ForceInput1_I",
        "ForceInput1",
        "F",
        "i",
        0.5,
    )
    plain = mapped(d, reg, external=False)
    assert "I_ForceInput1_I 0 translational_1 dc 0.5" in lines(plain.deck)
    assert len(plain.external_inputs) == 1


@physical_component
class Probe(PhysicalComponent):
    """Behavioural source and through sensor, to check expression rewriting."""

    ports = (Port("p", ELECTRICAL), Port("n", ELECTRICAL), SignalOutput("i", "A"))
    internal = {"mid": ELECTRICAL}

    def network(self, ports, params):
        return [
            VoltageSource("Vm", ports.p, ports.mid, 0.0),
            Behavioural("B", ports.mid, ports.n, "i", "V(p, n) * 2 + v(mid)"),
            Sensor("S", "i", "I(Vm)"),
        ]


def test_expressions_use_mapped_names() -> None:
    reg = registry()
    reg.add(Probe)
    d, _ = new_diagram(reg)
    for spec in ("Probe", "ElecRef", "Solver"):
        d.add_block(spec)
    wire(d, "Probe1.n", "ElecRef1.G")
    wire(d, "Probe1.p", "Solver1.port")
    out = mapped(d, reg)
    assert lines(out.deck)[1:-1] == [
        "V_Probe1_Vm electrical_1 electrical_2 dc 0.0",
        "B_Probe1_B electrical_2 0 I=V(electrical_1,0) * 2 + V(electrical_2)",
        "B_Probe1_S s_probe1_i 0 V=I(V_Probe1_Vm)",
    ]
    assert out.vectors["Probe1.i"] == "V(s_probe1_i)"


def test_sensor_checks() -> None:
    @physical_component
    class BadSensor(PhysicalComponent):
        ports = (Port("p", ELECTRICAL), SignalOutput("y"))

        def network(self, ports, params):
            return [Sensor("S", "nope", "V(p)"), Sensor("T", "y", "V(q)")]

    msgs = [x.message for x in build_network(BadSensor).diagnostics]
    assert msgs == [
        "signal 'nope' is not a SignalOutput of this component",
        "unknown node 'q' in expression",
    ]


def test_component_errors_reach_the_block() -> None:
    d, reg = msd_diagram(k=-1.0)
    out = mapped(d, reg)
    assert not out.ok
    (diag,) = out.diagnostics
    assert diag.block == "Spring" and diag.severity == ERROR
    assert "stiffness k must be a positive finite number" in diag.message
    assert not any(line.startswith("L_Spring") for line in lines(out.deck))


def test_names_that_clash_without_case_are_refused() -> None:
    d, reg = new_diagram()
    d.add_block("Mass", block_id="m1")
    d.add_block("Mass", block_id="M1")
    d.add_block("Solver", block_id="S")
    wire(d, "m1.A", "M1.A")
    wire(d, "S.port", "m1.A")
    extracted = NetworkExtractor(reg).extract(d)
    out = NetworkMapper(reg).map(extracted.networks[0])
    assert [(x.block, x.message) for x in out.diagnostics] == [
        (
            "M1",
            "element C_M1_C clashes with one of block m1 (SPICE names ignore case): "
            "rename the block",
        )
    ]


# ---- analytic answers in plain ngspice


def run(deck: str):
    sim = NgspiceAdapter()
    assert sim.available(), sim.last_error
    sim.load(deck)
    sim.run()
    result = sim.results()
    assert result is not None
    return result


def max_rel_error(sim: np.ndarray, exact: np.ndarray, scale: float) -> float:
    return float(np.max(np.abs(sim - exact)) / scale)


@pytest.mark.ngspice
def test_mass_spring_damper_matches_analytic_step_response() -> None:
    m, k, b, F = 1.0, 100.0, 4.0, 10.0
    d, reg = msd_diagram(m, k, b, F)
    out = mapped(d, reg, ".tran 0.5m 3 0 0.5m uic")
    result = run(out.deck)
    t = result.vectors["time"]
    x = result.vectors[out.vectors["Pos.x"]]
    v = result.vectors[out.vectors["Mass.A.v"]]

    wn = math.sqrt(k / m)
    zeta = b / (2.0 * math.sqrt(k * m))
    wd = wn * math.sqrt(1.0 - zeta**2)
    decay = np.exp(-zeta * wn * t)
    x_ss = F / k
    x_exact = x_ss * (
        1.0 - decay * (np.cos(wd * t) + zeta / math.sqrt(1 - zeta**2) * np.sin(wd * t))
    )
    v_exact = x_ss * wn / math.sqrt(1.0 - zeta**2) * decay * np.sin(wd * t)

    assert max_rel_error(x, x_exact, np.max(np.abs(x_exact))) < 0.005
    assert max_rel_error(v, v_exact, np.max(np.abs(v_exact))) < 0.005
    assert x[-1] == pytest.approx(x_ss, rel=0.005)


@pytest.mark.ngspice
def test_thermal_rc_matches_analytic_warmup() -> None:
    C, R, Q, T_amb = 50.0, 2.0, 10.0, 298.15
    d, reg = thermal_diagram(C, R, Q, T_amb)
    out = mapped(d, reg, ".tran 0.1 500 0 0.1 uic")
    result = run(out.deck)
    t = result.vectors["time"]
    T = result.vectors[out.vectors["Body.A.T"]]
    rise = Q * R
    T_exact = T_amb + rise * (1.0 - np.exp(-t / (R * C)))
    # 0.5 % of the temperature rise (much stricter than 0.5 % of T in kelvin).
    assert max_rel_error(T, T_exact, rise) < 0.005
    tau_index = int(np.searchsorted(t, R * C))
    assert T[tau_index] - T_amb == pytest.approx(rise * (1 - math.exp(-1)), rel=0.005)


def test_registry_has_the_fixtures() -> None:
    assert {"Mass", "Integrator", "ThermalMass"} <= set(registry().names())
    assert THERMAL in {
        p.domain for p in registry().spec("ThermalMass").ports if isinstance(p, Port)
    }


def test_external_source_knows_its_domain() -> None:
    d, reg = new_diagram()
    for spec in ("Mass", "ForceInput", "TransRef", "Solver"):
        d.add_block(spec)
    wire(d, "Mass1.A", "ForceInput1.A")
    wire(d, "ForceInput1.B", "TransRef1.V")
    wire(d, "Solver1.port", "Mass1.A")
    (ext,) = mapped(d, reg).external_inputs
    assert ext.domain == "translational"


def test_map_all_shares_node_and_element_numbering() -> None:
    d, reg = new_diagram()
    for i in (1, 2):
        d.add_block("Solver", block_id=f"S{i}")
        d.add_block("RotRef", block_id=f"R{i}")
        d.add_block("Inertia", block_id=f"J{i}")
        d.add_block("RotDamper", block_id=f"B{i}")
        wire(d, f"S{i}.port", f"J{i}.A")
        wire(d, f"J{i}.A", f"B{i}.A")
        wire(d, f"B{i}.B", f"R{i}.W")
    extracted = NetworkExtractor(reg).extract(d)
    assert extracted.ok and len(extracted.networks) == 2
    out = NetworkMapper(reg).map_all(extracted.networks, ".tran 1m 10m")
    assert out.ok
    assert out.vectors["J1.A.w"] == "V(rotational_1)"
    assert out.vectors["J2.A.w"] == "V(rotational_2)"
    names = [e.name for e in out.circuit.elements]
    assert names == ["C_J1_C", "R_B1_R", "C_J2_C", "R_B2_R"]
    # One network maps exactly as map() does.
    one = NetworkMapper(reg).map(extracted.networks[0], ".tran 1m 10m")
    assert one.vectors == {k: v for k, v in out.vectors.items() if k.startswith(("J1", "B1"))}
