"""F5: NetworkExtractor - nodes, separate networks and their checks."""

from __future__ import annotations

import pytest

from core.blocks.spec import CONSERVING, BlockSpec, PortSpec
from core.netlist.model import ERROR, WARNING
from core.physical.component import (
    COMPONENT,
    REFERENCE,
    SOLVER,
    PhysicalComponent,
    PhysicalComponentError,
    Port,
    physical_component,
    physical_spec_of,
)
from core.physical.domains import ANY_DOMAIN, ROTATIONAL, THERMAL, TRANSLATIONAL
from core.physical.network import NetworkExtractor
from core.schematic.connectivity import UnionFind
from tests.fixtures.physical.components import (
    Mass,
    Solver,
    TransRef,
    new_diagram,
    wire,
)


def msd(d, prefix: str = "") -> None:
    """Mass-spring-damper with force: all between node A and the reference."""
    for spec in ("Mass", "Spring", "Damper", "Force", "TransRef", "Solver"):
        d.add_block(spec, block_id=f"{prefix}{spec}")
    p = prefix
    for part in ("Spring", "Damper", "Force"):
        wire(d, f"{p}Mass.A", f"{p}{part}.A")
        wire(d, f"{p}{part}.B", f"{p}TransRef.V")
    wire(d, f"{p}Solver.port", f"{p}Mass.A")


def thermal_rc(d, with_ref: bool = True) -> None:
    for spec in ("ThermalMass", "ThermalResistance", "TemperatureSource", "Solver"):
        d.add_block(spec, block_id=f"t{spec}")
    wire(d, "tThermalMass.A", "tThermalResistance.A")
    wire(d, "tThermalResistance.B", "tTemperatureSource.A")
    wire(d, "tSolver.port", "tThermalMass.A")
    if with_ref:
        d.add_block("ThermalRef", block_id="tRef")
        wire(d, "tTemperatureSource.B", "tRef.T")


def messages(result, severity=ERROR) -> list[tuple[str, str]]:
    return [(x.block, x.message) for x in result.diagnostics if x.severity == severity]


# ---- roles


def test_roles_are_part_of_the_spec() -> None:
    assert physical_spec_of(Solver).role == SOLVER
    assert physical_spec_of(TransRef).role == REFERENCE
    assert physical_spec_of(Mass).role == COMPONENT


def test_only_a_solver_may_have_a_wildcard_port() -> None:
    with pytest.raises(PhysicalComponentError, match="only a role='solver'"):

        @physical_component
        class Bad(PhysicalComponent):
            ports = (Port("p", ANY_DOMAIN),)

            def network(self, ports, params):
                return []

    with pytest.raises(PhysicalComponentError, match="exactly one conserving Port"):

        @physical_component
        class TwoPortRef(PhysicalComponent):
            role = REFERENCE
            ports = (Port("a", THERMAL), Port("b", THERMAL))

            def network(self, ports, params):
                return []

    with pytest.raises(PhysicalComponentError, match="role must be one of"):

        @physical_component
        class Odd(PhysicalComponent):
            role = "ground"
            ports = (Port("a", THERMAL),)

            def network(self, ports, params):
                return []


# ---- union-find with domains


def test_union_find_refuses_different_domains() -> None:
    uf = UnionFind()
    uf.add("a", TRANSLATIONAL)
    uf.add("b", TRANSLATIONAL)
    uf.add("t", THERMAL)
    uf.add("any")
    assert uf.union("any", "a")
    assert uf.domain("any") == TRANSLATIONAL
    assert uf.union("a", "b")
    assert not uf.union("b", "t")
    assert uf.find("t") != uf.find("a")
    # Untagged items (the schematic) behave as before.
    uf.add("x")
    uf.add("y")
    assert uf.union("x", "y") and uf.domain("x") is None


# ---- extraction


def test_mass_spring_damper_is_one_clean_network() -> None:
    d, reg = new_diagram()
    msd(d)
    result = NetworkExtractor(reg).extract(d)
    assert result.diagnostics == []
    assert len(result.networks) == 1
    net = result.networks[0]
    assert net.block_ids == ["Mass", "Spring", "Damper", "Force", "TransRef", "Solver"]
    assert net.domains == [TRANSLATIONAL]
    assert net.solvers == ["Solver"]
    # Two nodes: the mass node (with the solver port on it) and the reference node.
    assert len(net.nodes) == 2
    a, ref = net.nodes
    assert ("Solver", "port") in a.ports and a.domain == TRANSLATIONAL
    assert net.is_reference_node(ref) and not net.is_reference_node(a)
    assert net.node_of("Spring", "B") is ref


def test_separate_networks_are_found_and_checked_separately() -> None:
    d, reg = new_diagram()
    msd(d)
    thermal_rc(d, with_ref=False)
    d.add_block("Gain")  # signal blocks are not part of any network
    result = NetworkExtractor(reg).extract(d)
    assert len(result.networks) == 2
    assert result.network_of("tThermalMass").domains == [THERMAL]
    assert result.network_of("Gain1") is None
    # Missing reference is reported on the first block with a thermal port.
    assert messages(result) == [
        (
            "tThermalMass",
            "no thermal reference in this network: add a thermal reference block "
            "connected to the thermal ports",
        )
    ]
    assert result.for_block("Mass") == []
    assert not result.ok


def test_missing_reference_goes_to_the_block_of_that_domain() -> None:
    d, reg = new_diagram()
    for spec in ("ElecResistor", "Machine", "RotDamper", "ElecRef", "Solver"):
        d.add_block(spec)
    wire(d, "ElecResistor1.p", "Machine1.p")
    wire(d, "ElecResistor1.n", "ElecRef1.G")
    wire(d, "Machine1.n", "ElecRef1.G")
    wire(d, "Machine1.shaft", "RotDamper1.A")
    wire(d, "RotDamper1.B", "Machine1.shaft")
    wire(d, "Solver1.port", "Machine1.p")
    result = NetworkExtractor(reg).extract(d)
    assert len(result.networks) == 1  # the machine joins both domains
    assert result.networks[0].domains == ["electrical", ROTATIONAL]
    assert messages(result) == [
        (
            "Machine1",
            "no rotational reference in this network: add a mechanical rotational "
            "reference block connected to the rotational ports",
        )
    ]
    d.add_block("RotRef")
    wire(d, "RotDamper1.B", "RotRef1.W")
    assert NetworkExtractor(reg).extract(d).diagnostics == []


def test_no_solver_and_two_solvers() -> None:
    d, reg = new_diagram()
    msd(d)
    d.remove_block("Solver")
    result = NetworkExtractor(reg).extract(d)
    assert messages(result) == [
        (
            "Mass",
            "this physical network has no Solver Configuration block "
            "(network of Mass, Spring, Damper, Force, TransRef)",
        )
    ]
    d.add_block("Solver", block_id="S1")
    d.add_block("Solver", block_id="S2")
    wire(d, "S1.port", "Mass.A")
    wire(d, "S2.port", "Spring.B")
    result = NetworkExtractor(reg).extract(d)
    assert messages(result) == [
        (
            "S2",
            "this physical network already has the Solver Configuration S1; "
            "a network needs exactly one",
        )
    ]


def test_unconnected_port_is_a_warning_on_its_block() -> None:
    d, reg = new_diagram()
    msd(d)
    d.add_block("Damper", block_id="Loose")
    wire(d, "Loose.A", "Mass.A")
    result = NetworkExtractor(reg).extract(d)
    assert result.ok
    assert messages(result, WARNING) == [("Loose", "port 'B' is not connected")]


def test_lone_solver_and_solver_across_domains() -> None:
    d, reg = new_diagram()
    msd(d)
    d.add_block("Solver", block_id="Lonely")
    result = NetworkExtractor(reg).extract(d)
    assert messages(result) == [
        ("Lonely", "the Solver Configuration is not connected to a network")
    ]

    d, reg = new_diagram()
    msd(d)
    thermal_rc(d)
    wire(d, "Solver.port", "tThermalMass.A")  # F4 allows it: the port is a wildcard
    result = NetworkExtractor(reg).extract(d)
    assert messages(result) == [
        (
            "Solver",
            "Solver.port (translational) and tThermalMass.A (thermal) cannot be one node: "
            "ports of different domains never connect",
        )
    ]


def test_unknown_physical_block_is_reported() -> None:
    d, reg = new_diagram()
    alien = BlockSpec("Alien", [], [], conserving=[PortSpec("A", CONSERVING, THERMAL)])
    lookup = d._lookup
    d._lookup = lambda name: alien if name == "Alien" else lookup(name)
    d.add_block("Alien")
    result = NetworkExtractor(reg).extract(d)
    assert messages(result) == [("Alien1", "Alien is not a known physical component")]
    assert result.networks == []


def test_settings_are_snapshotted_with_defaults() -> None:
    d, reg = new_diagram()
    msd(d)
    d.set_settings("Mass", {"m": 2.5})
    net = NetworkExtractor(reg).extract(d).networks[0]
    assert net.block("Mass").settings == {"m": 2.5, "v0": 0.0}
    assert net.block("Mass").spec_name == "Mass"


def test_every_part_of_a_domain_needs_a_reference() -> None:
    """Two shafts joined only through the electrical side of two machines."""
    d, reg = new_diagram()
    for block_id, spec in [
        ("M1", "Machine"),
        ("D1", "RotDamper"),
        ("Gnd", "ElecRef"),
        ("W1", "RotRef"),
        ("M2", "Machine"),
        ("D2", "RotDamper"),
        ("S", "Solver"),
    ]:
        d.add_block(spec, block_id=block_id)
    wire(d, "M1.p", "M2.p")
    wire(d, "M1.n", "Gnd.G")
    wire(d, "M2.n", "Gnd.G")
    wire(d, "M1.shaft", "D1.A")
    wire(d, "D1.B", "W1.W")
    wire(d, "M2.shaft", "D2.A")
    wire(d, "D2.B", "D2.A")  # second shaft has no reference
    wire(d, "S.port", "M1.p")
    result = NetworkExtractor(reg).extract(d)
    assert len(result.networks) == 1
    assert messages(result) == [
        (
            "M2",
            "this rotational part (M2, D2) is not connected to a rotational reference: "
            "add a mechanical rotational reference block to it",
        )
    ]
    d.add_block("RotRef", block_id="W2")
    wire(d, "D2.B", "W2.W")
    assert NetworkExtractor(reg).extract(d).diagnostics == []


def test_a_gearbox_like_block_keeps_its_domain_in_one_part() -> None:
    """A block with two ports of one domain joins them: one reference is enough."""
    d, reg = new_diagram()
    msd(d)
    d.add_block("Damper", block_id="Link")
    d.add_block("Mass", block_id="Far")
    wire(d, "Link.A", "Mass.A")
    wire(d, "Link.B", "Far.A")
    assert NetworkExtractor(reg).extract(d).diagnostics == []
