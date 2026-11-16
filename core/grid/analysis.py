"""Analysis of a solved grid, outside the engine (rule 19). Qt-free, engine-free.

- ``find_violations``: under- and over-voltage per phase node, overloaded lines and
  transformers (the D7 loadings below), from the PowerFlowConfig limits.
- ``voltage_profile``: distance from the source per node, from the GridModel (the
  sum of line lengths along the shortest path). OpenDSS only reports distances when
  an EnergyMeter exists, so they are never read from the engine.
- ``loss_summary``: losses per element class (line, switch, transformer, other).

Feasibility (D7, rule 27): ``line_loading``, ``transformer_loading``,
``voltage_violations``, ``voltage_drop`` and ``feasibility_report``, which puts Level 1
(topology and validation) and Level 2 (electrical checks of ONE snapshot) together.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from core.grid.config import PowerFlowConfig
from core.grid.model import GridModel
from core.grid.result import GridResult
from core.grid.topology import LINE, Edge, GridGraph
from core.grid.validate import validate
from core.netlist.model import ERROR, INFO, WARNING
from core.physical.component import NetworkDiagnostic

UNDERVOLTAGE = "undervoltage"
OVERVOLTAGE = "overvoltage"
OVERLOAD = "overload"

PHASE_LETTERS = {1: "a", 2: "b", 3: "c"}

UNREACHABLE_IN_PROFILE = "profile_unreachable"


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


def find_violations(
    result: GridResult, config: PowerFlowConfig, model: GridModel | None = None
) -> list[Violation]:
    """Voltage violations in node order, then overloaded lines and transformers in
    model order.

    Only phase nodes (.1, .2, .3) with a voltage base are checked. A node at about
    0 V (cut off by an open switch) is an undervoltage. Overloads use the same
    loadings as the feasibility report (``line_loading``, ``transformer_loading``,
    ratings from ``model``) against ``line_loading_limit_pct`` and
    ``transformer_loading_limit_pct``; without a model there are none, and lines
    without a rating are skipped. Never raises; an unsolved result has no violations.
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
    if model is None:
        return out
    lines, _ = line_loading(model, result)
    for rows, limit in (
        (lines, config.line_loading_limit_pct),
        (transformer_loading(model, result), config.transformer_loading_limit_pct),
    ):
        out.extend(Violation(OVERLOAD, r.element, r.pct, limit, "%") for r in rows if r.pct > limit)
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
    """Shortest distance in m from any enabled source bus to every reachable bus
    (GridGraph.source_distances, rule 25).

    Lines count their length; closed switches and transformers count 0 m; open
    switches and disabled elements are not paths.
    """
    try:
        graph = GridGraph.build(model)
    except Exception:  # a malformed model: validate() reports it (rule 6)
        return {}

    def length(e: Edge) -> float:
        if e.kind != LINE:
            return 0.0
        ln = model.lines.get(e.element_id.split(".", 1)[1])
        return max(ln.length_m, 0.0) if ln is not None else 0.0

    return graph.source_distances(length)


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


# ---- feasibility (D7)

NO_RATING = "no_rating"

PASS = "pass"
WARN = "warn"
FAIL = "fail"
STATUSES = (PASS, WARN, FAIL)

CHECK_VOLTAGE_MIN = "voltage_min"
CHECK_VOLTAGE_MAX = "voltage_max"
CHECK_LINE_LOADING = "line_loading"
CHECK_TRANSFORMER_LOADING = "transformer_loading"

SNAPSHOT_LIMITATION = (
    "This report checks one snapshot power flow at the loads entered in the design. "
    "It does not prove that the design is feasible for other load levels, for "
    "contingencies (outages, switching) or for protection."
)
"""Rule 27: stated by every FeasibilityReport and its UI panel."""


@dataclass(frozen=True)
class Loading:
    element: str
    """Element id ("line.650632", "transformer.sub")."""
    pct: float
    """value / rating * 100."""
    value: float
    """Line: largest phase current at terminal 1 in A. Transformer: |S| in kVA at the
    most loaded winding terminal."""
    rating: float
    """Line: norm_amps in A. Transformer: kVA of that winding."""
    winding: int = 0
    """Transformer: the 1-based winding of ``value``; 0 for a line."""


def line_rating(model: GridModel, line_name: str) -> float | None:
    """The line's own norm_amps, else its linecode's; None when neither is set."""
    ln = model.lines[line_name]
    if ln.norm_amps is not None:
        return ln.norm_amps
    code = model.linecodes.get(ln.linecode)
    return code.norm_amps if code is not None else None


def line_loading(
    model: GridModel, result: GridResult
) -> tuple[list[Loading], list[NetworkDiagnostic]]:
    """Loading of every enabled line: max phase current at terminal 1 / norm_amps.

    The rating comes from the model, never from the engine (which defaults to 400 A):
    a line without one gets an info Diagnostic ``no_rating`` and no value. Switches
    have no rating and are not lines here. Lines are in model order. Never raises.
    """
    rows: list[Loading] = []
    diags: list[NetworkDiagnostic] = []
    for name, ln in model.lines.items():
        if not ln.enabled:
            continue
        rating = line_rating(model, name)
        if rating is None or not rating > 0:
            diags.append(
                NetworkDiagnostic(
                    ln.id, INFO, f"{ln.id}: no rating (norm_amps not set)", code=NO_RATING
                )
            )
            continue
        br = result.branches.get(ln.id) if result.converged else None
        if br is None:
            continue
        current = br.current[: ln.phases]
        peak = float(abs(current).max()) if current.size else 0.0
        rows.append(Loading(ln.id, peak / rating * 100.0, peak, float(rating)))
    return rows, diags


def transformer_loading(model: GridModel, result: GridResult) -> list[Loading]:
    """Loading of every enabled transformer: the largest |S| at a winding terminal /
    that winding's kVA (not OpenDSS's %, which uses NormHkVA = 1.1 kVA; S4).

    Windings without a kVA (a draft, which cannot run) are skipped. Model order.
    Never raises.
    """
    out: list[Loading] = []
    if not result.converged:
        return out
    for t in model.transformers.values():
        br = result.branches.get(t.id)
        if not t.enabled or br is None:
            continue
        best: Loading | None = None
        for k, w in enumerate(t.windings):
            if w.kva is None or not w.kva > 0 or k >= len(br.terminal_power):
                continue
            s = abs(br.terminal_power[k])
            row = Loading(t.id, s / w.kva * 100.0, s, float(w.kva), k + 1)
            if best is None or row.pct > best.pct:
                best = row
        if best is not None:
            out.append(best)
    return out


def voltage_violations(result: GridResult, config: PowerFlowConfig) -> list[Violation]:
    """Under- and over-voltages per phase node (the voltage part of find_violations)."""
    return [v for v in find_violations(result, config) if v.kind != OVERLOAD]


@dataclass(frozen=True)
class VoltageDrop:
    bus: str
    source_bus: str
    drop_pu: float
    """Largest over the common phases of |V| pu at the source bus - |V| pu at the bus.
    Negative = a rise (a regulator or capacitor raises the voltage)."""
    phase: str
    """"a", "b" or "c": the phase of ``drop_pu``."""
    path: tuple[str, ...]
    """Element ids from the source bus to the bus (GridGraph.path, closed branches)."""


def _phase_pu(result: GridResult) -> dict[str, dict[int, float]]:
    """bus -> {phase node: |V| pu} for phase nodes with a voltage base, node order."""
    out: dict[str, dict[int, float]] = {}
    for node, value in zip(result.node_names, result.node_voltage_pu, strict=False):
        bus, phase = _split_node(node)
        value = float(value)
        if phase in PHASE_LETTERS and not math.isnan(value):
            out.setdefault(bus, {})[phase] = value
    return out


def voltage_drop(model: GridModel, result: GridResult) -> list[VoltageDrop]:
    """Voltage drop in pu from the source to each energized bus, in node order.

    The source is the first enabled source with a closed path to the bus
    (GridGraph.path). Buses without a path, or without a phase node in common with
    the source bus, are left out. Never raises.
    """
    if not result.converged:
        return []
    try:
        graph = GridGraph.build(model)
    except Exception:  # a malformed model: validate() reports it (rule 6)
        return []
    pu = _phase_pu(result)
    sources = [bus for bus, _, _ in graph.source_buses]
    out: list[VoltageDrop] = []
    for bus, phases in pu.items():
        for src in sources:
            chain = graph.path(src, bus)
            if chain is None:
                continue
            at_src = pu.get(src, {})
            common = sorted(set(phases) & set(at_src))
            if common:
                ph = max(common, key=lambda n: at_src[n] - phases[n])
                out.append(
                    VoltageDrop(
                        bus,
                        src,
                        at_src[ph] - phases[ph],
                        PHASE_LETTERS[ph],
                        tuple(e.element_id for e in chain),
                    )
                )
            break
    return out


@dataclass(frozen=True)
class FeasibilityCheck:
    name: str
    """CHECK_VOLTAGE_MIN, CHECK_VOLTAGE_MAX, CHECK_LINE_LOADING or
    CHECK_TRANSFORMER_LOADING."""
    status: str
    """PASS, WARN or FAIL."""
    worst_value: float | None
    """pu or %; None when nothing was checked (e.g. no rated line)."""
    worst_element: str
    """Element id ("line.l1") or bus id ("bus.671"); "" when nothing was checked."""
    limit: float
    unit: str
    count: int
    """Number of phase nodes (of energized buses) or elements checked."""


@dataclass(frozen=True)
class FeasibilitySummary:
    load_kw: float
    """Sum of the enabled loads' specified kW (the design's values, at 1 pu)."""
    load_kvar: float
    source_kw: float
    """Supplied by the source(s), from the solve."""
    source_kvar: float
    losses_kw: float
    losses_kvar: float
    v_min_pu: float | None
    v_min_bus: str
    v_max_pu: float | None
    v_max_bus: str
    max_line_loading_pct: float | None
    max_line_loading_element: str
    max_transformer_loading_pct: float | None
    max_transformer_loading_element: str
    max_voltage_drop_pu: float | None
    max_voltage_drop_bus: str


@dataclass
class FeasibilityReport:
    level1: list[NetworkDiagnostic]
    """Topology and validation diagnostics (validate.validate, which asks topology.py)."""
    level2: list[FeasibilityCheck] = field(default_factory=list)
    """Electrical checks of the snapshot; empty when there is no converged solution."""
    summary: FeasibilitySummary | None = None
    """None when there is no converged solution."""
    line_loadings: list[Loading] = field(default_factory=list)
    transformer_loadings: list[Loading] = field(default_factory=list)
    voltage_drops: list[VoltageDrop] = field(default_factory=list)
    diagnostics: list[NetworkDiagnostic] = field(default_factory=list)
    """From the Level 2 checks (info ``no_rating`` per unrated line)."""
    solved: bool = False
    limitation: str = SNAPSHOT_LIMITATION

    @property
    def level1_ok(self) -> bool:
        return not any(d.severity == ERROR for d in self.level1)

    @property
    def level1_status(self) -> str:
        """FAIL with a Level 1 error, WARN with a warning, else PASS."""
        severities = {d.severity for d in self.level1}
        return FAIL if ERROR in severities else WARN if WARNING in severities else PASS

    @property
    def level2_status(self) -> str:
        """FAIL when not solved or a check fails, WARN when one warns, else PASS."""
        if not self.solved:
            return FAIL
        statuses = {c.status for c in self.level2}
        return FAIL if FAIL in statuses else WARN if WARN in statuses else PASS

    @property
    def status(self) -> str:
        """FAIL if Level 1 has an error, the snapshot is not solved or a check fails;
        WARN if a check warns; else PASS."""
        if not (self.level1_ok and self.solved):
            return FAIL
        statuses = {c.status for c in self.level2}
        return FAIL if FAIL in statuses else WARN if WARN in statuses else PASS

    def check(self, name: str) -> FeasibilityCheck:
        """The Level 2 check called ``name``; KeyError if there is none."""
        for c in self.level2:
            if c.name == name:
                return c
        raise KeyError(name)


def _loading_check(
    name: str, rows: list[Loading], limit: float, warn_fraction: float
) -> FeasibilityCheck:
    if not rows:
        return FeasibilityCheck(name, PASS, None, "", limit, "%", 0)
    worst = max(rows, key=lambda r: r.pct)
    status = loading_status(worst.pct, limit, warn_fraction)
    return FeasibilityCheck(name, status, worst.pct, worst.element, limit, "%", len(rows))


def loading_status(pct: float, limit: float, warn_fraction: float) -> str:
    """FAIL above ``limit``, WARN from ``warn_fraction * limit`` up to it, else PASS."""
    if pct > limit:
        return FAIL
    if pct >= warn_fraction * limit:
        return WARN
    return PASS


def _voltage_check(
    name: str, worst: tuple[str, float] | None, limit: float, failed: bool, count: int
) -> FeasibilityCheck:
    if worst is None:
        return FeasibilityCheck(name, PASS, None, "", limit, "pu", 0)
    status = FAIL if failed else PASS
    return FeasibilityCheck(name, status, worst[1], f"bus.{worst[0]}", limit, "pu", count)


def _energized(model: GridModel) -> set[str]:
    try:
        return GridGraph.build(model).energized_buses()
    except Exception:  # a malformed model: validate() reports it (rule 6)
        return set()


def feasibility_report(
    model: GridModel,
    result: GridResult | None,
    config: PowerFlowConfig,
    *,
    incomplete: bool = False,
) -> FeasibilityReport:
    """Level 1 (validate.validate) + Level 2 (one snapshot) for ``model``.

    ``result`` is the solve of exactly this model, or None when it was not run.
    Limits come from ``config`` (rule 19). The voltage checks and the min / max V of
    the summary cover only buses energized over closed branches. Never raises.
    """
    report = FeasibilityReport(validate(model, incomplete=incomplete))
    if result is None or not result.converged:
        return report
    report.solved = True
    lines, report.diagnostics = line_loading(model, result)
    xfs = transformer_loading(model, result)
    drops = voltage_drop(model, result)
    report.line_loadings, report.transformer_loadings, report.voltage_drops = lines, xfs, drops

    # Only energized buses: one cut off by an open switch reads about 0 pu and is
    # already a Level 1 diagnostic (open_switch_isolated / unreachable_bus).
    energized = _energized(model)
    volts = [
        (bus, v)
        for bus, phases in _phase_pu(result).items()
        if bus in energized
        for v in phases.values()
    ]
    vmin = min(volts, key=lambda bv: bv[1]) if volts else None
    vmax = max(volts, key=lambda bv: bv[1]) if volts else None
    lo, hi = config.voltage_min_pu, config.voltage_max_pu
    report.level2 = [
        _voltage_check(CHECK_VOLTAGE_MIN, vmin, lo, vmin is not None and vmin[1] < lo, len(volts)),
        _voltage_check(CHECK_VOLTAGE_MAX, vmax, hi, vmax is not None and vmax[1] > hi, len(volts)),
        _loading_check(
            CHECK_LINE_LOADING, lines, config.line_loading_limit_pct, config.warn_fraction
        ),
        _loading_check(
            CHECK_TRANSFORMER_LOADING,
            xfs,
            config.transformer_loading_limit_pct,
            config.warn_fraction,
        ),
    ]
    worst_line = max(lines, key=lambda r: r.pct) if lines else None
    worst_xf = max(xfs, key=lambda r: r.pct) if xfs else None
    worst_drop = max(drops, key=lambda d: d.drop_pu) if drops else None
    loads = [ld for ld in model.loads.values() if ld.enabled]
    report.summary = FeasibilitySummary(
        load_kw=float(sum(ld.kw or 0.0 for ld in loads)),
        load_kvar=float(sum(ld.kvar or 0.0 for ld in loads)),
        source_kw=result.total_power_kw,
        source_kvar=result.total_power_kvar,
        losses_kw=result.total_losses_kw,
        losses_kvar=result.total_losses_kvar,
        v_min_pu=vmin[1] if vmin else None,
        v_min_bus=vmin[0] if vmin else "",
        v_max_pu=vmax[1] if vmax else None,
        v_max_bus=vmax[0] if vmax else "",
        max_line_loading_pct=worst_line.pct if worst_line else None,
        max_line_loading_element=worst_line.element if worst_line else "",
        max_transformer_loading_pct=worst_xf.pct if worst_xf else None,
        max_transformer_loading_element=worst_xf.element if worst_xf else "",
        max_voltage_drop_pu=worst_drop.drop_pu if worst_drop else None,
        max_voltage_drop_bus=worst_drop.bus if worst_drop else "",
    )
    return report


# ---- results overlay (D8)

OFF = "off"
"""Overlay status of a de-energized bus or element (no closed path from a source)."""
NONE = "none"
"""Overlay status when nothing is checked: an unrated line, a switch, a shunt."""
OVERLAY_STATUSES = (PASS, WARN, FAIL, NONE, OFF)

SEVERITY_STATUS = {ERROR: FAIL, WARNING: WARN, INFO: NONE}
"""Status of a Level 1 diagnostic in the feasibility panel."""

WEIGHT_STEPS = 4
"""Branch line-width steps of the overlay: 0 .. WEIGHT_STEPS - 1."""


@dataclass(frozen=True)
class OverlayItem:
    element: str
    """"bus.671" or an element id ("line.650632")."""
    status: str
    """One of OVERLAY_STATUSES. A bus: FAIL when a phase is outside the voltage band,
    else PASS (the Level 2 voltage checks have no warn band). A line or transformer:
    loading_status of its loading. NONE when nothing is checked; OFF when
    de-energized."""
    value: float | None
    """A bus: |V| pu of the phase nearest to (or furthest beyond) a band limit. A
    line or transformer: loading in %. None when nothing was checked."""
    limit: float | None
    """The limit ``value`` is compared with (pu or %)."""
    unit: str
    ratio: float | None
    """Loading / loading limit; None for buses and unchecked elements."""
    weight: int
    """Line-width step 0 .. WEIGHT_STEPS - 1 from ``ratio`` (0 without one): below
    half the limit, below the warn threshold, up to the limit, above it."""
    text: str
    """One line for a tooltip, with the values."""


@dataclass
class ResultsOverlay:
    items: dict[str, OverlayItem] = field(default_factory=dict)
    """Per bus ("bus.<name>") and drawn element (sources, lines, switches,
    transformers, loads, capacitors, PV systems), buses first, then model order."""
    solved: bool = False
    """False when there was no converged result: ``items`` is empty."""


def _weight(ratio: float | None, warn_fraction: float) -> int:
    if ratio is None:
        return 0
    if ratio > 1.0:
        return 3
    if ratio >= warn_fraction:
        return 2
    return 1 if ratio >= 0.5 else 0


def _peak_current(br: object, phases: int) -> float | None:
    current = getattr(br, "current", None)
    if current is None:
        return None
    mags = abs(current[:phases]) if phases > 0 else abs(current)
    return float(mags.max()) if len(mags) else None


def results_overlay(
    model: GridModel, result: GridResult | None, config: PowerFlowConfig
) -> ResultsOverlay:
    """Status and values per bus and element of one solved snapshot, for the results
    overlay (rule 19: computed here from the PowerFlowConfig limits, never in QML).

    ``result`` must be the solve of exactly ``model``. The statuses agree with
    feasibility_report: the same energized buses, voltage band, loadings and
    warn_fraction. Never raises.
    """
    overlay = ResultsOverlay()
    if result is None or not result.converged:
        return overlay
    try:
        graph = GridGraph.build(model)
    except Exception:  # a malformed model: validate() reports it (rule 6)
        return overlay
    overlay.solved = True
    items = overlay.items
    energized = graph.energized_buses()
    live = {e.element_id for e in graph.edges if e.closed and e.bus_a in energized}
    pu = _phase_pu(result)
    lo, hi = config.voltage_min_pu, config.voltage_max_pu

    for bus in graph.buses:
        bid = f"bus.{bus}"
        phases = pu.get(bus, {})
        if bus not in energized:
            items[bid] = OverlayItem(bid, OFF, None, None, "pu", None, 0, f"{bus}: de-energized")
            continue
        if not phases:
            items[bid] = OverlayItem(bid, NONE, None, None, "pu", None, 0, f"{bus}: no phase nodes")
            continue
        vmin, vmax = min(phases.values()), max(phases.values())
        if vmin < lo:
            status, value, limit, note = FAIL, vmin, lo, f" - below {lo:g} pu"
        elif vmax > hi:
            status, value, limit, note = FAIL, vmax, hi, f" - above {hi:g} pu"
        elif vmin - lo <= hi - vmax:
            status, value, limit, note = PASS, vmin, lo, ""
        else:
            status, value, limit, note = PASS, vmax, hi, ""
        volts = "  ".join(f"{PHASE_LETTERS[n]} {v:.4f}" for n, v in sorted(phases.items()))
        items[bid] = OverlayItem(
            bid, status, value, limit, "pu", None, 0, f"{bus}: {volts} pu{note}"
        )

    lines, _ = line_loading(model, result)
    loadings = {r.element: (r, config.line_loading_limit_pct) for r in lines}
    loadings.update(
        {
            r.element: (r, config.transformer_loading_limit_pct)
            for r in transformer_loading(model, result)
        }
    )
    switch_ids = {sw.id for sw in model.switches.values()}

    def branch(element_id: str, enabled: bool, phases: int) -> None:
        if not enabled or element_id not in live:
            items[element_id] = OverlayItem(
                element_id, OFF, None, None, "%", None, 0, f"{element_id}: de-energized"
            )
            return
        if element_id in loadings:
            row, limit = loadings[element_id]
            ratio = row.pct / limit
            unit = "A" if row.winding == 0 else "kVA"
            where = f" (winding {row.winding})" if row.winding else ""
            text = (
                f"{element_id}: {row.pct:.1f} % of {row.rating:g} {unit}{where}, limit {limit:g} %"
            )
            items[element_id] = OverlayItem(
                element_id,
                loading_status(row.pct, limit, config.warn_fraction),
                row.pct,
                limit,
                "%",
                ratio,
                _weight(ratio, config.warn_fraction),
                text,
            )
            return
        peak = _peak_current(result.branches.get(element_id), phases)
        amps = f"{peak:.1f} A" if peak is not None else "no current"
        why = "" if element_id in switch_ids else ", no rating"
        items[element_id] = OverlayItem(
            element_id, NONE, None, None, "%", None, 0, f"{element_id}: {amps}{why}"
        )

    for src in model.sources.values():
        _shunt(items, src.id, src.enabled, src.bus1.bus, energized)
    for ln in model.lines.values():
        if ln.name not in model.switches:
            branch(ln.id, ln.enabled, ln.phases)
    for sw in model.switches.values():
        branch(sw.id, sw.enabled, sw.phases)
    for t in model.transformers.values():
        branch(t.id, t.enabled, t.phases)
    for attr in ("loads", "capacitors", "pvsystems"):
        for el in getattr(model, attr).values():
            _shunt(items, el.id, el.enabled, el.terminal.bus, energized)
    return overlay


def _shunt(
    items: dict[str, OverlayItem], element_id: str, enabled: bool, bus: str, energized: set[str]
) -> None:
    if enabled and bus in energized:
        items[element_id] = OverlayItem(
            element_id, NONE, None, None, "", None, 0, f"{element_id}: at bus {bus}"
        )
    else:
        items[element_id] = OverlayItem(
            element_id, OFF, None, None, "", None, 0, f"{element_id}: de-energized"
        )
