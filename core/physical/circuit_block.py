"""M2: a v0.2 schematic or netlist as a physical block with electrical ports. Qt-free.

The circuit goes through the v0.2 circuit-symbol generator (``core.hierarchy.symbolgen``,
H2): its port flags (or the ticked netlist nodes) become the pins, in ``.subckt``
order, and its body becomes ``.subckt <name> ...``. Each pin becomes an electrical
``Port`` of a physical component whose network is one ``SubcircuitInstance``, so the
block is written with the public PhysicalComponent API (rule 13) and solved by ngspice
like any other part (rule 11). A schematic is still only a way to make a Circuit
(rule 8): the netlister does the work.

``CircuitDefinition`` is the block as plain data (name, port names, a self-contained
netlist text with the ``.subckt`` and everything it includes). It is what the block
runner sends to its child process, which rebuilds the class with
``circuit_component`` (classes are never pickled). ``PhysicalRegistry.add`` takes the
class like any other component.

The subcircuit's node 0 is the circuit's ground, which the mapper puts on the
electrical reference: a circuit block therefore shares the network's electrical
reference. Nothing here raises on a bad circuit (rule 6): problems come back as
Diagnostics.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.hierarchy.ports import Port as SymbolPort
from core.hierarchy.symbolgen import GeneratedSymbol, SymbolGenerator
from core.netlist.builder import build
from core.netlist.model import ERROR, Circuit, Diagnostic
from core.netlist.parser import parse
from core.physical.component import (
    PhysicalComponent,
    PhysicalComponentError,
    Port,
    Ports,
    Primitive,
    SubcircuitInstance,
    physical_component,
    physical_spec_of,
)
from core.physical.domains import ELECTRICAL
from core.schematic.model import Schematic
from core.symbols.asy import Symbol


@dataclass(frozen=True)
class CircuitDefinition:
    """A Circuit block as plain data."""

    name: str
    """Component and ``.subckt`` name."""
    ports: tuple[str, ...]
    """Electrical port names, in ``.subckt`` order."""
    text: str
    """Self-contained netlist: title line, models, ``.subckt`` blocks, ``.end``."""
    labels: tuple[str, ...] = ()
    """Pin direction of each port (``in``, ``out``, ``bidir``), shown as its label."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ports": list(self.ports),
            "text": self.text,
            "labels": list(self.labels),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CircuitDefinition:
        """Raises ValueError for malformed data (use ``load_definition`` to get a
        Diagnostic instead)."""
        name, ports, text = data.get("name"), data.get("ports"), data.get("text")
        labels = data.get("labels", [])
        if not isinstance(name, str) or not isinstance(text, str):
            raise ValueError("a circuit block needs a name and a text")
        if not isinstance(ports, list | tuple) or not all(isinstance(p, str) for p in ports):
            raise ValueError("a circuit block's ports must be a list of names")
        if not isinstance(labels, list | tuple) or not all(isinstance(x, str) for x in labels):
            raise ValueError("a circuit block's labels must be a list of strings")
        return cls(name, tuple(ports), text, tuple(labels))


@dataclass
class CircuitBlock:
    """What ``circuit_from_*`` made: the definition, its component class, problems."""

    definition: CircuitDefinition | None = None
    component: type | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    symbol: GeneratedSymbol | None = None

    @property
    def ok(self) -> bool:
        return self.component is not None and not any(d.severity == ERROR for d in self.diagnostics)


def _error(out: CircuitBlock, message: str) -> CircuitBlock:
    out.diagnostics.append(Diagnostic(0, 0, ERROR, message))
    return out


def circuit_from_schematic(
    name: str,
    schematic: Schematic,
    lookup: Callable[[str], Symbol | None],
    base_dir: str | Path | None = None,
) -> CircuitBlock:
    """A schematic with port flags as a Circuit block named ``name``."""
    made = SymbolGenerator.from_schematic(name, schematic, lookup, base_dir)
    return _from_symbol(made, base_dir)


def circuit_from_netlist(
    name: str,
    text: str,
    chosen: list[tuple[str, str]],
    base_dir: str | Path | None = None,
) -> CircuitBlock:
    """A netlist as a Circuit block; ``chosen`` is (node, direction) per port."""
    made = SymbolGenerator.from_netlist(name, text, chosen, base_dir)
    return _from_symbol(made, base_dir)


def _from_symbol(made: GeneratedSymbol, base_dir: str | Path | None) -> CircuitBlock:
    out = CircuitBlock(diagnostics=list(made.diagnostics), symbol=made)
    if not made.ok:
        if not any(d.severity == ERROR for d in out.diagnostics):
            _error(out, "the circuit cannot become a block")
        return out
    # Expand the .sub's includes now, so the text no longer needs its folder.
    parsed = parse(f"* {made.name}\n{made.sub_text}.end\n", base_dir)
    errors = [d for d in parsed.diagnostics if d.severity == ERROR]
    if errors:
        return _error(out, f"{made.name}.sub: {errors[0].message}")
    body = Circuit(
        title=f"* QtSpice circuit block {made.name}",
        models=parsed.circuit.models,
        subcircuits=parsed.circuit.subcircuits,
        params=parsed.circuit.params,
    )
    definition = CircuitDefinition(
        made.name,
        tuple(p.name for p in made.ports),
        build(body),
        tuple(_label(p) for p in made.ports),
    )
    return _finish(out, definition)


def _label(port: SymbolPort) -> str:
    return port.direction


def load_definition(data: CircuitDefinition | Mapping[str, Any]) -> CircuitBlock:
    """Rebuild a Circuit block from its definition (e.g. in the runner's child)."""
    out = CircuitBlock()
    if isinstance(data, CircuitDefinition):
        definition = data
    else:
        try:
            definition = CircuitDefinition.from_dict(data)
        except (ValueError, AttributeError) as err:
            return _error(out, f"circuit block: {err}")
    parsed = parse(definition.text)
    errors = [d for d in parsed.diagnostics if d.severity == ERROR]
    if errors:
        return _error(out, f"circuit block {definition.name}: {errors[0].message}")
    subs = {s.name.casefold(): s for s in parsed.circuit.subcircuits}
    sub = subs.get(definition.name.casefold())
    if sub is None:
        return _error(out, f"circuit block {definition.name}: no .subckt {definition.name}")
    if len(sub.ports) != len(definition.ports):
        return _error(
            out,
            f"circuit block {definition.name}: .subckt has {len(sub.ports)} ports, "
            f"the block {len(definition.ports)}",
        )
    return _finish(out, definition)


def _finish(out: CircuitBlock, definition: CircuitDefinition) -> CircuitBlock:
    out.definition = definition
    for port in definition.ports:
        if not port.isidentifier():
            return _error(
                out,
                f"port {port}: a block port name must be a valid identifier "
                "(letters, digits and _, not starting with a digit)",
            )
    try:
        out.component = circuit_component(definition)
    except PhysicalComponentError as err:
        return _error(out, f"circuit block {definition.name}: {err}")
    return out


def circuit_component(definition: CircuitDefinition) -> type:
    """The ``@physical_component`` class of ``definition``. Raises
    PhysicalComponentError for names the component API refuses."""
    port_names = definition.ports
    labels = definition.labels if len(definition.labels) == len(port_names) else ()

    def network(
        self: PhysicalComponent, ports: Ports, params: Mapping[str, object]
    ) -> list[Primitive]:
        nodes = tuple(ports[p] for p in port_names)
        return [SubcircuitInstance("X", nodes, definition.name, definition.text)]

    doc = (
        f"Circuit block {definition.name}: a v0.2 circuit as a physical block with "
        f"electrical ports {', '.join(port_names)}."
    )
    namespace = {
        "__doc__": doc,
        "__module__": __name__,
        "ports": tuple(
            Port(p, ELECTRICAL, labels[i] if labels else "") for i, p in enumerate(port_names)
        ),
        "network": network,
    }
    cls = type(f"Circuit_{definition.name.replace('-', '_')}", (PhysicalComponent,), namespace)
    name = definition.name.replace("-", "_")
    return physical_component(cls, name=name)


def is_circuit_block(cls: object) -> bool:
    """True for a class made by ``circuit_component``."""
    spec = physical_spec_of(cls)
    return spec is not None and getattr(cls, "__module__", "") == __name__
