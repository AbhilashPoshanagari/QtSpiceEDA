"""The ports of a circuit that becomes a symbol (H1). Qt-free.

A port is one pin of the generated symbol and one node on its ``.subckt`` line.

* Schematic: each port flag (in / out / bidir) is a port. Its node is the net the flag
  is on (``NetMap.ports``), which is the flag's name unless a net label on the same
  net wins. Two flags with the same name are one port (they are one net).
* Netlist: the user ticks top-level nodes and gives each a direction.

A port must be on a net that a part uses, not on ground (``0`` / ``gnd`` are global in
SPICE), and two ports may not share a net. Names must be one word without the
characters SPICE uses for syntax. Problems are Diagnostics; nothing here raises.

``ordered`` gives the pin / ``.subckt`` order: inputs, then outputs, then
bidirectional ports, each in the order they were placed (or ticked).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.netlist.model import ERROR, WARNING, Diagnostic
from core.schematic.connectivity import NetMap
from core.schematic.model import PORT_DIRECTIONS, Schematic

GROUND_NAMES = frozenset({"0", "gnd"})
_BAD_CHARS = frozenset("(),={}[]\"';*")


@dataclass(frozen=True)
class Port:
    """``name`` is the pin name on the symbol; ``node`` the net in the circuit."""

    name: str
    node: str
    direction: str = "bidir"


@dataclass
class PortRow:
    """One port flag (schematic) or ticked node (netlist), for the ports list.
    ``severity`` is "" (fine), WARNING or ERROR; ``message`` says why."""

    index: int
    name: str
    direction: str
    net: str
    severity: str = ""
    message: str = ""


@dataclass
class PortList:
    ports: list[Port] = field(default_factory=list)  # one per port, placement order
    rows: list[PortRow] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.diagnostics)

    def ordered(self) -> list[Port]:
        return ordered(self.ports)


def ordered(ports: list[Port]) -> list[Port]:
    """Inputs, then outputs, then bidirectional ports; stable within each."""
    rank = {d: i for i, d in enumerate(PORT_DIRECTIONS)}
    return sorted(ports, key=lambda p: rank.get(p.direction, len(rank)))


def name_problem(name: str) -> str:
    """Why ``name`` cannot be a port name ("" when it can)."""
    if not name or any(c.isspace() for c in name):
        return "a port name must be one word"
    if name.casefold() in GROUND_NAMES:
        return f"{name} is the ground node; ground is shared, so it cannot be a port"
    bad = sorted({c for c in name if c in _BAD_CHARS})
    if bad:
        return f"a port name cannot contain {' '.join(bad)}"
    return ""


class _Collector:
    def __init__(self) -> None:
        self.out = PortList()

    def row(self, row: PortRow, severity: str = "", message: str = "") -> None:
        row.severity, row.message = severity, message
        self.out.rows.append(row)
        if severity:
            self.out.diagnostics.append(Diagnostic(0, 0, severity, f"port {row.name}: {message}"))


def schematic_ports(schematic: Schematic, nets: NetMap) -> PortList:
    """The ports of ``schematic``; ``nets`` is ``connect(schematic, ...)``."""
    c = _Collector()
    used = {n for pins in nets.pins for n in pins if n}
    by_name: dict[str, Port] = {}
    by_node: dict[str, Port] = {}
    for i, flag in enumerate(schematic.ports):
        net = nets.ports[i] if i < len(nets.ports) else ""
        row = PortRow(i, flag.name, flag.direction, net)
        problem = name_problem(flag.name)
        if problem:
            c.row(row, ERROR, problem)
            continue
        same = by_name.get(flag.name.casefold())
        if same is not None:
            if same.direction != flag.direction:
                c.row(
                    row,
                    WARNING,
                    f"another {flag.name} flag is {same.direction}; {same.direction} is used",
                )
            else:
                c.row(row)
            continue
        if not net:
            c.row(row, ERROR, "not connected to anything")
            continue
        if net == "0":
            c.row(row, ERROR, "it is on the ground net; ground cannot be a port")
            continue
        if net not in used:
            c.row(row, ERROR, "no part is connected to its net")
            continue
        other = by_node.get(net.casefold())
        if other is not None:
            c.row(row, ERROR, f"it is on the same net as port {other.name}")
            continue
        port = Port(flag.name, net, flag.direction)
        by_name[flag.name.casefold()] = port
        by_node[net.casefold()] = port
        c.out.ports.append(port)
        c.row(row)
    return c.out


def netlist_ports(nodes: list[str], chosen: list[tuple[str, str]]) -> PortList:
    """Ports from ticked netlist nodes. ``nodes`` are the circuit's top-level nodes;
    ``chosen`` is (node, direction) in the order ticked."""
    c = _Collector()
    known = {n.casefold(): n for n in nodes}
    seen: set[str] = set()
    for i, (node, direction) in enumerate(chosen):
        node = node.strip()
        row = PortRow(i, node, direction, known.get(node.casefold(), ""))
        problem = name_problem(node)
        if problem:
            c.row(row, ERROR, problem)
        elif direction not in PORT_DIRECTIONS:
            c.row(row, ERROR, f"direction must be in, out or bidir, not {direction!r}")
        elif node.casefold() not in known:
            c.row(row, ERROR, "no element uses this node")
        elif node.casefold() in seen:
            c.row(row, ERROR, "ticked twice")
        else:
            seen.add(node.casefold())
            c.out.ports.append(Port(node, known[node.casefold()], direction))
            c.row(row)
    return c.out
