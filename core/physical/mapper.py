"""NetworkMapper: a physical network as a Circuit and an ngspice deck. Qt-free.

Physical networks are always solved by ngspice (rule 11); this module only
translates. It never knows how a domain maps to V and A: components build their
primitives with the Domain registry (rule 12), and the mapper copies values.

For one ``PhysicalNetwork`` (from ``NetworkExtractor``):

- every block's ``network()`` is expanded with ``build_network`` (diagnostics from
  it are passed on, on that block);
- element names are the SPICE letter, the block id and the primitive name:
  primitive ``L`` of block ``Spring1`` becomes ``L_Spring1_L``;
- nodes are named ``<domain>_<n>``, numbered per domain from 1 in network order
  (wired nodes first, then internal nodes block by block); nodes with a reference
  port are circuit node ``0``;
- ``V()``/``I()`` in behavioural expressions and the control of F/H elements are
  rewritten to the mapped node and element names;
- an ``ExternalSource`` becomes ``V.../I... a b dc <initial> external`` (ngspice
  takes its value from the GetVSRCData / GetISRCData callbacks, C1). With
  ``external=False`` it is a plain ``dc <initial>`` source, so the deck runs in
  plain ngspice;
- a ``Sensor`` becomes ``B_<block>_<name> s_<block>_<signal> 0 V=<expr>``, saved as
  the vector ``<block>.<signal>``;
- every conserving port not on the reference gets the vector
  ``<block>.<port>.<across>`` (e.g. ``Mass1.A.v``), in SI.

``vectors`` maps those physical names to the ``SimulationResult`` vector that holds
them (``V(translational_1)``). The deck is built with ``core.netlist.builder``.
Nothing here raises on a bad network (rule 6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.netlist.builder import build
from core.netlist.model import ERROR, Analysis, Circuit, Element
from core.physical.component import (
    CCCS,
    CCVS,
    GROUND,
    REFERENCE_RE,
    SOLVER,
    VCCS,
    VCVS,
    Behavioural,
    Capacitor,
    CurrentSource,
    ExternalSource,
    Inductor,
    NetworkDiagnostic,
    Port,
    Primitive,
    Resistor,
    Sensor,
    SignalOutput,
    VoltageSource,
    build_network,
)
from core.physical.domains import DOMAINS
from core.physical.network import PhysicalNetwork, PortRef
from core.physical.registry import PhysicalRegistry

_LETTERS: dict[type, str] = {
    Resistor: "R",
    Capacitor: "C",
    Inductor: "L",
    VoltageSource: "V",
    CurrentSource: "I",
    VCVS: "E",
    VCCS: "G",
    CCCS: "F",
    CCVS: "H",
    Behavioural: "B",
    Sensor: "B",
}


@dataclass(frozen=True)
class ExternalInput:
    """An external source and the signal input that drives it."""

    element: str
    """Circuit element name, e.g. ``I_Force1_I``."""
    block: str
    signal: str
    kind: str
    """"v" or "i"."""
    initial: float


@dataclass
class MappedNetwork:
    circuit: Circuit
    deck: str
    vectors: dict[str, str] = field(default_factory=dict)
    """Physical vector name -> SimulationResult vector name."""
    units: dict[str, str] = field(default_factory=dict)
    """Physical vector name -> SI unit."""
    external_inputs: list[ExternalInput] = field(default_factory=list)
    node_names: dict[PortRef, str] = field(default_factory=dict)
    """(block, port) -> circuit node."""
    diagnostics: list[NetworkDiagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.diagnostics)


def _num(value: float) -> str:
    return repr(float(value))


def _letter(prim: Primitive) -> str:
    if isinstance(prim, ExternalSource):
        return "V" if prim.kind == "v" else "I"
    return _LETTERS[type(prim)]


def element_name(prim: Primitive, block: str) -> str:
    """``<letter>_<block>_<primitive>``, e.g. ``R_Damper1_R``."""
    return f"{_letter(prim)}_{block}_{prim.name}"


def _rewrite(expression: str, nodes: dict[str, str], names: dict[str, str]) -> str:
    def sub(match: re.Match[str]) -> str:
        args = [a.strip() for a in match.group(2).split(",")]
        if match.group(1).upper() == "V":
            return "V(" + ",".join(nodes.get(a, a) for a in args) + ")"
        return "I(" + names.get(args[0], args[0]) + ")"

    return REFERENCE_RE.sub(sub, expression)


class NetworkMapper:
    """Turns PhysicalNetworks into Circuits, using the components of ``registry``."""

    def __init__(self, registry: PhysicalRegistry) -> None:
        self._registry = registry

    def map(
        self,
        network: PhysicalNetwork,
        analysis: Analysis | str | None = None,
        *,
        external: bool = True,
        title: str = "",
    ) -> MappedNetwork:
        """Map ``network``; ``analysis`` (e.g. ``".tran 1m 1 uic"``) goes into the deck."""
        circuit = Circuit(
            title=title or "QtSpice physical network: " + ", ".join(network.block_ids)
        )
        out = MappedNetwork(circuit=circuit, deck="")
        counters: dict[str, int] = {}

        def new_node(domain: str) -> str:
            counters[domain] = counters.get(domain, 0) + 1
            return f"{domain}_{counters[domain]}"

        for node in network.nodes:
            if not node.domain:
                continue  # only a lone Solver Configuration port: nothing refers to it
            name = GROUND if network.is_reference_node(node) else new_node(node.domain)
            for ref in node.ports:
                out.node_names[ref] = name

        used: dict[str, str] = {}  # casefolded element name -> block
        for block in network.blocks:
            cls = self._registry.get(block.spec_name)
            spec = self._registry.spec(block.spec_name)
            if cls is None or spec is None:
                out.diagnostics.append(
                    NetworkDiagnostic(
                        block.id, ERROR, f"{block.spec_name} is not a known physical component"
                    )
                )
                continue
            result = build_network(cls, block.settings, block.id)
            out.diagnostics.extend(result.diagnostics)
            if not result.ok:
                continue

            nodes: dict[str, str] = {GROUND: GROUND}
            for port in spec.ports:
                if isinstance(port, Port):
                    nodes[port.name] = out.node_names.get((block.id, port.name), "")
            for internal, domain in spec.internal.items():
                nodes[internal] = new_node(domain)
            names = {p.name: element_name(p, block.id) for p in result.primitives}
            clash = False
            for name in names.values():
                other = used.setdefault(name.casefold(), block.id)
                if other != block.id:
                    out.diagnostics.append(
                        NetworkDiagnostic(
                            block.id,
                            ERROR,
                            f"element {name} clashes with one of block {other} "
                            "(SPICE names ignore case): rename the block",
                        )
                    )
                    clash = True
            if clash:
                continue

            outputs = {p.name: p.unit for p in spec.ports if isinstance(p, SignalOutput)}
            for prim in result.primitives:
                circuit.elements.append(
                    self._element(prim, block.id, nodes, names, external, out, outputs)
                )

            if spec.role == SOLVER:
                continue
            for port in spec.ports:
                node = nodes.get(port.name, "") if isinstance(port, Port) else ""
                if node and node != GROUND:
                    across = DOMAINS[port.domain].across
                    key = f"{block.id}.{port.name}.{across.name}"
                    out.vectors[key] = f"V({node.lower()})"
                    out.units[key] = across.unit

        out.deck = build(circuit, analysis)
        return out

    @staticmethod
    def _element(
        prim: Primitive,
        block: str,
        nodes: dict[str, str],
        names: dict[str, str],
        external: bool,
        out: MappedNetwork,
        outputs: dict[str, str],
    ) -> Element:
        name = names[prim.name]
        kind = name[0]
        if isinstance(prim, Sensor):
            node = f"s_{block}_{prim.signal}".lower()
            key = f"{block}.{prim.signal}"
            out.vectors[key] = f"V({node})"
            out.units[key] = outputs.get(prim.signal, "")
            expr = _rewrite(prim.expression, nodes, names)
            return Element(name, kind, [node, GROUND], f"V={expr}")
        ab = [nodes[prim.a], nodes[prim.b]]
        if isinstance(prim, Capacitor | Inductor):
            params = {} if prim.ic is None else {"ic": _num(prim.ic)}
            return Element(name, kind, ab, _num(prim.value), params)
        if isinstance(prim, Resistor):
            return Element(name, kind, ab, _num(prim.value))
        if isinstance(prim, VoltageSource | CurrentSource):
            return Element(name, kind, ab, f"dc {_num(prim.value)}")
        if isinstance(prim, VCVS | VCCS):
            ctrl = [nodes[prim.ctrl_a], nodes[prim.ctrl_b]]
            return Element(name, kind, ab + ctrl, _num(prim.gain))
        if isinstance(prim, CCCS | CCVS):
            return Element(name, kind, ab, f"{names[prim.control]} {_num(prim.gain)}")
        if isinstance(prim, Behavioural):
            expr = _rewrite(prim.expression, nodes, names)
            return Element(name, kind, ab, f"{prim.kind.upper()}={expr}")
        assert isinstance(prim, ExternalSource)
        out.external_inputs.append(
            ExternalInput(name, block, prim.signal, prim.kind, float(prim.initial))
        )
        value = f"dc {_num(prim.initial)}" + (" external" if external else "")
        return Element(name, kind, ab, value)
