"""NetworkExtractor: the physical networks of a diagram, and their checks. Qt-free.

Physical wires join conserving ports into *nodes*, using the v0.2 union-find from
``core.schematic.connectivity`` with each port tagged by its domain, so a node
always has one domain. A *network* is a set of blocks connected through nodes; a
block whose ports are in several domains (a DC motor) joins them into one network.

Each network is checked (problems are ``NetworkDiagnostic`` on the block they are
about; nothing raises, rule 6):

- exactly one Solver Configuration (role "solver"): none is an error on the
  network's first block, a second one is an error on that extra block;
- a reference block (role "reference") for every domain the network uses: a
  missing one is an error on the first block with a port in that domain;
- a reference for every *part* of a domain: nodes of one domain that are joined
  only through a component's ports of another domain (two shafts linked by the
  electrical side of two motors) are separate parts, and ngspice would see the one
  without a reference as floating. The error is on the part's first block;
- a conserving port with no wire is a warning on its block;
- a Solver Configuration port that joins two domains, or no network at all, is an
  error on the Solver Configuration.

Blocks and nodes come out in diagram order (blocks as placed, ports in declared
order), so the mapper (F6) numbers them the same way every time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from core.blocks.diagram import Diagram
from core.netlist.model import ERROR, WARNING
from core.physical.component import (
    COMPONENT,
    REFERENCE,
    SOLVER,
    NetworkDiagnostic,
    PhysicalSpec,
    Port,
)
from core.physical.domains import ANY_DOMAIN, DOMAINS
from core.physical.registry import PhysicalRegistry
from core.schematic.connectivity import UnionFind

PortRef = tuple[str, str]
"""(block id, port name)."""

Report = Callable[..., None]


@dataclass(frozen=True)
class NetworkBlock:
    """One physical block of a network, with its effective (SI) settings."""

    id: str
    spec_name: str
    role: str
    settings: dict[str, object]


@dataclass
class PhysicalNode:
    """Conserving ports joined by physical wires. ``domain`` is "" only for a node
    made of nothing but wildcard (Solver Configuration) ports."""

    domain: str
    ports: list[PortRef]
    wired: bool
    """True if at least one physical wire ends here."""


@dataclass
class PhysicalNetwork:
    """One separate physical network."""

    blocks: list[NetworkBlock] = field(default_factory=list)
    nodes: list[PhysicalNode] = field(default_factory=list)
    reference_ports: set[PortRef] = field(default_factory=set)
    """Ports of reference blocks; their nodes map to circuit node 0."""

    @property
    def block_ids(self) -> list[str]:
        return [b.id for b in self.blocks]

    @property
    def domains(self) -> list[str]:
        """Domains used by the network's nodes, in first-seen order."""
        seen: dict[str, None] = {}
        for node in self.nodes:
            if node.domain:
                seen.setdefault(node.domain, None)
        return list(seen)

    @property
    def solvers(self) -> list[str]:
        return [b.id for b in self.blocks if b.role == SOLVER]

    def block(self, block_id: str) -> NetworkBlock:
        return next(b for b in self.blocks if b.id == block_id)

    def node_of(self, block_id: str, port: str) -> PhysicalNode | None:
        return next((n for n in self.nodes if (block_id, port) in n.ports), None)

    def is_reference_node(self, node: PhysicalNode) -> bool:
        return any(p in self.reference_ports for p in node.ports)


@dataclass
class ExtractResult:
    networks: list[PhysicalNetwork] = field(default_factory=list)
    diagnostics: list[NetworkDiagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.diagnostics)

    def for_block(self, block_id: str) -> list[NetworkDiagnostic]:
        return [d for d in self.diagnostics if d.block == block_id]

    def network_of(self, block_id: str) -> PhysicalNetwork | None:
        return next((n for n in self.networks if block_id in n.block_ids), None)


class NetworkExtractor:
    """Finds and checks the physical networks of a diagram."""

    def __init__(self, registry: PhysicalRegistry) -> None:
        self._registry = registry

    def extract(self, diagram: Diagram) -> ExtractResult:
        out = ExtractResult()

        def report(block: str, message: str, severity: str = ERROR) -> None:
            out.diagnostics.append(NetworkDiagnostic(block, severity, message))

        # Physical blocks, in diagram order.
        specs: dict[str, PhysicalSpec] = {}
        blocks: list[NetworkBlock] = []
        for inst in diagram.blocks:
            block_spec = diagram.spec(inst.id)
            spec = self._registry.spec(inst.spec_name)
            if spec is None:
                if block_spec.is_physical:
                    report(inst.id, f"{inst.spec_name} is not a known physical component")
                continue
            specs[inst.id] = spec
            blocks.append(
                NetworkBlock(
                    inst.id, inst.spec_name, spec.role, diagram.effective_settings(inst.id)
                )
            )

        # Nodes: union-find over conserving ports, tagged with their domain.
        ports: list[PortRef] = []
        uf = UnionFind()
        for block in blocks:
            for decl in specs[block.id].ports:
                if isinstance(decl, Port):
                    ref = (block.id, decl.name)
                    ports.append(ref)
                    uf.add(ref, None if decl.domain == ANY_DOMAIN else decl.domain)
        wired: set[PortRef] = set()
        for wire in diagram.physical_wires:
            a, b = wire.ends
            if a not in uf.parent or b not in uf.parent:
                continue  # a block that is not a known physical component
            wired.update((a, b))
            da, db = uf.domain(a), uf.domain(b)
            if not uf.union(a, b):
                culprit = next(
                    (r for r in (a, b) if self._is_wildcard(specs[r[0]], r[1])),
                    a,
                )
                report(
                    culprit[0],
                    f"{wire.a} ({da}) and {wire.b} ({db}) cannot be one node: "
                    "ports of different domains never connect",
                )

        nodes_by_root: dict[object, PhysicalNode] = {}
        for ref in ports:
            root = uf.find(ref)
            node = nodes_by_root.get(root)
            if node is None:
                node = PhysicalNode(uf.domain(ref) or "", [], False)
                nodes_by_root[root] = node
            node.ports.append(ref)
            node.wired = node.wired or ref in wired

        # Networks: blocks joined through nodes (all ports of a block are one network).
        groups = UnionFind()
        for block in blocks:
            groups.add(("b", block.id))
        for root, node in nodes_by_root.items():
            groups.add(("n", root))
            for block_id, _port in node.ports:
                groups.union(("n", root), ("b", block_id))
        by_group: dict[object, PhysicalNetwork] = {}
        for block in blocks:
            g = groups.find(("b", block.id))
            if g not in by_group:
                by_group[g] = PhysicalNetwork()
                out.networks.append(by_group[g])
            by_group[g].blocks.append(block)
            if block.role == REFERENCE:
                by_group[g].reference_ports.update(
                    (block.id, p.name) for p in specs[block.id].ports if isinstance(p, Port)
                )
        for root, node in nodes_by_root.items():
            by_group[groups.find(("n", root))].nodes.append(node)

        for net in out.networks:
            self._check(net, specs, report)
        return out

    # ---- checks

    @staticmethod
    def _is_wildcard(spec: PhysicalSpec, port: str) -> bool:
        return any(
            isinstance(p, Port) and p.name == port and p.domain == ANY_DOMAIN for p in spec.ports
        )

    def _check(self, net: PhysicalNetwork, specs: dict[str, PhysicalSpec], report: Report) -> None:
        for node in net.nodes:
            if not node.wired:
                for block_id, port in node.ports:
                    if specs[block_id].role == SOLVER:
                        report(block_id, "the Solver Configuration is not connected to a network")
                    else:
                        report(block_id, f"port {port!r} is not connected", WARNING)
        if not net.domains:
            return  # only lone Solver Configurations: already reported

        solvers = net.solvers
        if not solvers:
            first = next((b.id for b in net.blocks if b.role == COMPONENT), net.blocks[0].id)
            report(
                first,
                "this physical network has no Solver Configuration block "
                f"(network of {', '.join(net.block_ids)})",
            )
        for extra in solvers[1:]:
            report(
                extra,
                f"this physical network already has the Solver Configuration {solvers[0]}; "
                "a network needs exactly one",
            )

        referenced = {
            node.domain for node in net.nodes if node.domain and net.is_reference_node(node)
        }
        for domain in net.domains:
            if domain in referenced:
                continue
            first = next(
                (
                    b.id
                    for b in net.blocks
                    if b.role != SOLVER
                    and any(isinstance(p, Port) and p.domain == domain for p in specs[b.id].ports)
                ),
                net.blocks[0].id,
            )
            label = DOMAINS[domain].label.lower()
            report(
                first,
                f"no {domain} reference in this network: add a {label} reference block "
                f"connected to the {domain} ports",
            )

        # Each part of a domain needs its own reference: parts joined only through a
        # component's ports of another domain (two shafts linked by the electrical
        # side of two motors) share no node, so ngspice would see a floating node.
        for island in self._islands(net):
            domain = island[0].domain
            if domain not in referenced or any(net.is_reference_node(n) for n in island):
                continue
            in_island = {block_id for node in island for block_id, _port in node.ports}
            blocks = [b.id for b in net.blocks if b.id in in_island and b.role != SOLVER]
            label = DOMAINS[domain].label.lower()
            report(
                blocks[0],
                f"this {domain} part ({', '.join(blocks)}) is not connected to a {domain} "
                f"reference: add a {label} reference block to it",
            )

    @staticmethod
    def _islands(net: PhysicalNetwork) -> list[list[PhysicalNode]]:
        """Nodes of one domain joined by wires or by a block with ports on both.

        In network order; every island has a single domain.
        """
        uf = UnionFind()
        for i, node in enumerate(net.nodes):
            uf.add(i, node.domain or None)
        by_block: dict[tuple[str, str], int] = {}
        for i, node in enumerate(net.nodes):
            if not node.domain:
                continue
            for block_id, _port in node.ports:
                first = by_block.setdefault((block_id, node.domain), i)
                uf.union(first, i)
        groups: dict[object, list[PhysicalNode]] = {}
        for i, node in enumerate(net.nodes):
            if node.domain:
                groups.setdefault(uf.find(i), []).append(node)
        return list(groups.values())
