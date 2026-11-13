"""Analysis of a solved grid, outside the engine (rule 19). Qt-free, engine-free.

- ``find_violations``: under- and over-voltage per phase node, overloaded lines and
  transformers, from the PowerFlowConfig limits.
- ``voltage_profile``: distance from the source per node, from the GridModel (the
  sum of line lengths along the shortest path). OpenDSS only reports distances when
  an EnergyMeter exists, so they are never read from the engine.
- ``loss_summary``: losses per element class (line, switch, transformer, other).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from core.grid.config import PowerFlowConfig
from core.grid.model import GridModel
from core.grid.result import GridResult
from core.netlist.model import WARNING
from core.physical.component import NetworkDiagnostic

UNDERVOLTAGE = "undervoltage"
OVERVOLTAGE = "overvoltage"
OVERLOAD = "overload"

PHASE_LETTERS = {1: "a", 2: "b", 3: "c"}

UNREACHABLE_IN_PROFILE = "profile_unreachable"

_OVERLOAD_CLASSES = ("line", "transformer")


@dataclass(frozen=True)
class Violation:
    kind: str
    """UNDERVOLTAGE, OVERVOLTAGE or OVERLOAD."""
    location: str
    """Node name ("671.1") for a voltage violation, element id for an overload."""
    value: float
    """pu for a voltage violation, % of the normal rating for an overload."""
    limit: float
    unit: str

    def message(self) -> str:
        if self.kind == OVERLOAD:
            return f"{self.location} loaded to {self.value:.1f} % (limit {self.limit:g} %)"
        side = "below" if self.kind == UNDERVOLTAGE else "above"
        return f"node {self.location} at {self.value:.4f} pu, {side} {self.limit:g} pu"

    def to_diagnostic(self) -> NetworkDiagnostic:
        block = self.location if self.kind == OVERLOAD else f"bus.{_split_node(self.location)[0]}"
        return NetworkDiagnostic(block, WARNING, self.message(), code=self.kind)


def _split_node(node: str) -> tuple[str, int | None]:
    bus, _, tail = node.rpartition(".")
    if not bus or not tail.isdigit():
        return node, None
    return bus, int(tail)


def find_violations(result: GridResult, config: PowerFlowConfig) -> list[Violation]:
    """Voltage violations in node order, then overloads in branch order.

    Only phase nodes (.1, .2, .3) with a voltage base are checked. A node at about
    0 V (cut off by an open switch) is an undervoltage. Branches without a rating
    are skipped. Never raises; an unsolved result has no violations.
    """
    out: list[Violation] = []
    if not result.converged:
        return out
    vmin, vmax = config.voltage_min_pu, config.voltage_max_pu
    for node, pu in zip(result.node_names, result.node_voltage_pu, strict=False):
        _, phase = _split_node(node)
        pu = float(pu)
        if phase not in PHASE_LETTERS or math.isnan(pu):
            continue
        if pu < vmin:
            out.append(Violation(UNDERVOLTAGE, node, pu, vmin, "pu"))
        elif pu > vmax:
            out.append(Violation(OVERVOLTAGE, node, pu, vmax, "pu"))
    limit = config.loading_limit_pct
    for element, br in result.branches.items():
        if element.split(".")[0] not in _OVERLOAD_CLASSES or br.loading_pct is None:
            continue
        if br.loading_pct > limit:
            out.append(Violation(OVERLOAD, element, float(br.loading_pct), limit, "%"))
    return out


# ---- voltage profile


@dataclass(frozen=True)
class ProfilePoint:
    node: str
    bus: str
    phase: str
    """"a", "b" or "c"."""
    distance_km: float
    v_pu: float


@dataclass
class VoltageProfile:
    points: list[ProfilePoint] = field(default_factory=list)
    """Sorted by distance, then bus, then phase."""
    unreachable: list[str] = field(default_factory=list)
    """Buses with no closed path from a source; they are not in ``points``."""
    diagnostics: list[NetworkDiagnostic] = field(default_factory=list)


def bus_distances(model: GridModel) -> dict[str, float]:
    """Shortest distance in m from any enabled source bus to every reachable bus.

    Lines count their length; closed switches and transformers count 0 m; open
    switches and disabled elements are not paths.
    """
    adj: dict[str, list[tuple[str, float]]] = {}

    def link(a: str, b: str, length: float) -> None:
        if a == b:
            return
        adj.setdefault(a, []).append((b, length))
        adj.setdefault(b, []).append((a, length))

    for ln in model.lines.values():
        if ln.enabled:
            link(ln.bus1.bus, ln.bus2.bus, max(ln.length_m, 0.0))
    for sw in model.switches.values():
        if sw.enabled and sw.closed:
            link(sw.bus1.bus, sw.bus2.bus, 0.0)
    for t in model.transformers.values():
        if t.enabled:
            buses = [w.terminal.bus for w in t.windings]
            for i, a in enumerate(buses):
                for b in buses[i + 1 :]:
                    link(a, b, 0.0)

    dist: dict[str, float] = {}
    heap = [(0.0, s.bus1.bus) for s in model.sources.values() if s.enabled]
    heapq.heapify(heap)
    while heap:
        d, bus = heapq.heappop(heap)
        if bus in dist:
            continue
        dist[bus] = d
        for nxt, length in adj.get(bus, ()):
            if nxt not in dist:
                heapq.heappush(heap, (d + length, nxt))
    return dist


def voltage_profile(model: GridModel, result: GridResult) -> VoltageProfile:
    """Per phase node: distance from the source (km) and |V| in pu. Never raises.

    Nodes without a voltage base (NaN pu) are left out. Buses the source cannot
    reach are listed in ``unreachable`` with a warning Diagnostic each.
    """
    profile = VoltageProfile()
    dist = bus_distances(model)
    missing: set[str] = set()
    for node, pu in zip(result.node_names, result.node_voltage_pu, strict=False):
        bus, phase = _split_node(node)
        pu = float(pu)
        if phase not in PHASE_LETTERS or math.isnan(pu):
            continue
        if bus not in dist:
            missing.add(bus)
            continue
        profile.points.append(ProfilePoint(node, bus, PHASE_LETTERS[phase], dist[bus] / 1000.0, pu))
    profile.points.sort(key=lambda p: (p.distance_km, p.bus, p.phase))
    profile.unreachable = sorted(missing)
    profile.diagnostics = [
        NetworkDiagnostic(
            f"bus.{bus}",
            WARNING,
            f"bus {bus} has no closed path from the source; left out of the voltage profile",
            code=UNREACHABLE_IN_PROFILE,
        )
        for bus in profile.unreachable
    ]
    return profile


# ---- losses


@dataclass(frozen=True)
class LossRow:
    element_class: str
    """"line", "switch", "transformer" or "other"."""
    count: int
    kw: float
    kvar: float


LOSS_CLASSES = ("line", "switch", "transformer", "other")


def loss_summary(model: GridModel, result: GridResult) -> list[LossRow]:
    """Losses per element class, in LOSS_CLASSES order.

    Switches are told apart from lines by the model (both are "line." ids). "other"
    holds branches of any other class plus ``total_losses - sum of branches``, so the
    rows always add up to the result's total losses.
    """
    switch_ids = {sw.id for sw in model.switches.values()}
    sums = {c: [0, 0.0, 0.0] for c in LOSS_CLASSES}
    for element, br in result.branches.items():
        prefix = element.split(".")[0]
        if element in switch_ids:
            cls = "switch"
        elif prefix in ("line", "transformer"):
            cls = prefix
        else:
            cls = "other"
        row = sums[cls]
        row[0] += 1
        row[1] += br.losses_kw
        row[2] += br.losses_kvar
    kw = sum(r[1] for r in sums.values())
    kvar = sum(r[2] for r in sums.values())
    sums["other"][1] += result.total_losses_kw - kw
    sums["other"][2] += result.total_losses_kvar - kvar
    return [LossRow(c, int(sums[c][0]), float(sums[c][1]), float(sums[c][2])) for c in LOSS_CLASSES]
