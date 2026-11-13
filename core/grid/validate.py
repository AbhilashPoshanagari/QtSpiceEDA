"""Topology and electrical checks on a GridModel. Qt-free, engine-free.

``validate(model)`` returns NetworkDiagnostics and never raises (rule 6). Each
Diagnostic names the element id ("line.650632", "bus.671") as its location and carries
a stable ``code`` (the constants below). Everything is an error except
``OPEN_SWITCH_ISOLATED``, ``NO_VOLTAGE_BASES`` and a 1-phase delta element given one
node (``PHASE_NODE_MISMATCH``: OpenDSS grounds the second node; IEEE 34 does this),
which are warnings.

``incomplete=True`` says the model lacks elements the extractor does not support yet
(it warned "unsupported element ..."). One of them may be a branch, so reachability
findings are then reported as warnings instead of errors.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Iterable, Iterator

from core.grid.model import DELTA, WYE, GridModel, Terminal
from core.netlist.model import ERROR, WARNING
from core.physical.component import NetworkDiagnostic

# ---- stable codes

UNKNOWN_BUS = "unknown_bus"
DUPLICATE_NAME = "duplicate_name"
UNREACHABLE_BUS = "unreachable_bus"
OPEN_SWITCH_ISOLATED = "open_switch_isolated"
PHASE_NODE_MISMATCH = "phase_node_mismatch"
PHASE_NOT_ON_BUS1 = "phase_not_on_bus1"
UNKNOWN_LINECODE = "unknown_linecode"
LINECODE_PHASES = "linecode_phases"
NONPOSITIVE_LENGTH = "nonpositive_length"
WINDING_KV = "winding_kv"
ELEMENT_KV = "element_kv"
REGULATOR_TARGET = "regulator_target"
TRANSFORMER_KVA = "transformer_kva"
NO_VOLTAGE_BASES = "no_voltage_bases"
NO_SOURCE = "no_source"
VALIDATION_FAILED = "validation_failed"

CODES = (
    UNKNOWN_BUS,
    DUPLICATE_NAME,
    UNREACHABLE_BUS,
    OPEN_SWITCH_ISOLATED,
    PHASE_NODE_MISMATCH,
    PHASE_NOT_ON_BUS1,
    UNKNOWN_LINECODE,
    LINECODE_PHASES,
    NONPOSITIVE_LENGTH,
    WINDING_KV,
    ELEMENT_KV,
    REGULATOR_TARGET,
    TRANSFORMER_KVA,
    NO_VOLTAGE_BASES,
    NO_SOURCE,
    VALIDATION_FAILED,
)

KV_TOLERANCE = 0.05
"""Largest relative difference between a rated kV and the bus voltage base."""

MODEL = "circuit"
"""Location for problems of the whole model."""

_SQRT3 = math.sqrt(3.0)


def _diag(location: str, severity: str, code: str, message: str) -> NetworkDiagnostic:
    return NetworkDiagnostic(location, severity, message, code=code)


def validate(model: GridModel, *, incomplete: bool = False) -> list[NetworkDiagnostic]:
    """Run every check on ``model``; an exception inside a check becomes an error."""
    checks: list[Callable[[GridModel], Iterable[NetworkDiagnostic]]] = [
        _check_source,
        _check_voltage_bases,
        _check_duplicates,
        _check_unknown_buses,
        _check_phase_nodes,
        _check_branch_phases,
        _check_lines,
        _check_transformers,
        _check_regulators,
        _check_element_kv,
    ]
    out: list[NetworkDiagnostic] = []
    for check in checks:
        try:
            out.extend(check(model))
        except Exception as err:  # a malformed model; never raise (rule 6)
            out.append(_failed(check, err))
    try:
        out.extend(_check_reachability(model, incomplete))
    except Exception as err:
        out.append(_failed(_check_reachability, err))
    return out


def _failed(check: Callable[..., object], err: Exception) -> NetworkDiagnostic:
    name = getattr(check, "__name__", "check").removeprefix("_check_")
    return _diag(MODEL, ERROR, VALIDATION_FAILED, f"check '{name}' failed: {err!r}")


# ---- helpers


def _terminals(model: GridModel) -> Iterator[tuple[str, str, Terminal, int, str]]:
    """(element id, terminal label, terminal, phases, conn) of every element."""
    for s in model.sources.values():
        yield s.id, "bus1", s.bus1, s.phases, WYE
    for ln in model.lines.values():
        yield ln.id, "bus1", ln.bus1, ln.phases, ""
        yield ln.id, "bus2", ln.bus2, ln.phases, ""
    for sw in model.switches.values():
        yield sw.id, "bus1", sw.bus1, sw.phases, ""
        yield sw.id, "bus2", sw.bus2, sw.phases, ""
    for t in model.transformers.values():
        for i, w in enumerate(t.windings, start=1):
            yield t.id, f"winding {i}", w.terminal, t.phases, w.conn
    for group in (model.loads, model.capacitors, model.pvsystems):
        for el in group.values():
            yield el.id, "bus1", el.terminal, el.phases, el.conn


def _phases_of(term: Terminal, phases: int) -> set[int]:
    """Phase nodes (ground 0 excluded); OpenDSS uses 1..phases when none are given."""
    if not term.nodes:
        return set(range(1, phases + 1))
    return {n for n in term.nodes if n != 0}


def _rated_ln(kv: float, phases: int, conn: str, term: Terminal) -> tuple[float, str]:
    """A rated kV as line-to-neutral kV, and whether it was read as "LL" or "LN".

    LL for 3-phase and delta elements, and for a 1-phase wye element connected
    between two phases; as given (LN) for a 1-phase wye element to neutral.
    """
    between_phases = phases == 1 and len(_phases_of(term, phases)) >= 2
    if phases > 1 or conn == DELTA or between_phases:
        return kv / _SQRT3, "LL"
    return kv, "LN"


def _kv_off(rated_ln: float, base_ln: float) -> bool:
    return abs(rated_ln - base_ln) > KV_TOLERANCE * base_ln


# ---- model-wide checks


def _check_source(model: GridModel) -> Iterator[NetworkDiagnostic]:
    if not any(s.enabled for s in model.sources.values()):
        yield _diag(MODEL, ERROR, NO_SOURCE, "the circuit has no (enabled) source")


def _check_voltage_bases(model: GridModel) -> Iterator[NetworkDiagnostic]:
    if not model.voltage_bases:
        yield _diag(
            MODEL,
            WARNING,
            NO_VOLTAGE_BASES,
            "no voltage bases (Set VoltageBases): no pu values and no kV checks",
        )


def _check_duplicates(model: GridModel) -> Iterator[NetworkDiagnostic]:
    seen: set[str] = set()
    groups: list[Iterable[object]] = [
        model.buses.values(),
        model.sources.values(),
        model.linecodes.values(),
        model.lines.values(),
        model.switches.values(),  # also "line.<name>": shares names with lines
        model.transformers.values(),
        model.regulators.values(),
        model.loads.values(),
        model.capacitors.values(),
        model.pvsystems.values(),
    ]
    for group in groups:
        for el in group:
            element_id = f"{el.cls}.{el.name.lower()}"  # type: ignore[attr-defined]
            if element_id in seen:
                yield _diag(
                    element_id, ERROR, DUPLICATE_NAME, f"{element_id} is defined more than once"
                )
            seen.add(element_id)


# ---- topology


def _check_unknown_buses(model: GridModel) -> Iterator[NetworkDiagnostic]:
    for element_id, label, term, _, _ in _terminals(model):
        if term.bus not in model.buses:
            yield _diag(element_id, ERROR, UNKNOWN_BUS, f"{label} names unknown bus '{term.bus}'")
    for s in model.sources.values():
        if s.bus2.bus not in model.buses:
            yield _diag(s.id, ERROR, UNKNOWN_BUS, f"bus2 names unknown bus '{s.bus2.bus}'")
    for r in model.regulators.values():
        if r.monitored_bus and Terminal.parse(r.monitored_bus).bus not in model.buses:
            yield _diag(r.id, ERROR, UNKNOWN_BUS, f"monitored bus '{r.monitored_bus}' is unknown")


def _check_phase_nodes(model: GridModel) -> Iterator[NetworkDiagnostic]:
    for element_id, label, term, phases, conn in _terminals(model):
        n = len(term.nodes)
        if n == 0:
            continue
        if conn == DELTA and phases == 1:
            allowed = {2}
        elif conn == WYE:
            allowed = {phases, phases + 1}  # the extra node is the neutral
        else:
            allowed = {phases}
        if conn == DELTA and phases == 1 and n == 1 and term.nodes[0] != 0:
            # OpenDSS takes the missing node as ground (IEEE 34 has such loads).
            yield _diag(
                element_id,
                WARNING,
                PHASE_NODE_MISMATCH,
                f"{label} '{term}' gives one node for a 1-phase delta element: OpenDSS "
                "connects the second node to ground (0), not to a second phase",
            )
        elif n not in allowed:
            expected = " or ".join(str(a) for a in sorted(allowed))
            yield _diag(
                element_id,
                ERROR,
                PHASE_NODE_MISMATCH,
                f"{label} '{term}' has {n} nodes, a {phases}-phase "
                f"{conn + ' ' if conn else ''}element needs {expected}",
            )


def _check_branch_phases(model: GridModel) -> Iterator[NetworkDiagnostic]:
    """Lines and switches connect node i of bus1 to node i of bus2, so bus2 cannot
    carry a phase bus1 does not. Transformers are skipped: delta/wye and 1-phase
    units legitimately map phases differently."""
    for br in (*model.lines.values(), *model.switches.values()):
        extra = _phases_of(br.bus2, br.phases) - _phases_of(br.bus1, br.phases)
        if extra:
            phases = ", ".join(str(p) for p in sorted(extra))
            yield _diag(
                br.id,
                ERROR,
                PHASE_NOT_ON_BUS1,
                f"bus2 '{br.bus2}' has phase {phases}, which bus1 '{br.bus1}' does not",
            )


def _adjacency(model: GridModel, with_open: bool) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}

    def link(buses: list[str]) -> None:
        for a in buses:
            adj.setdefault(a, set()).update(b for b in buses if b != a)

    for ln in model.lines.values():
        if ln.enabled:
            link([ln.bus1.bus, ln.bus2.bus])
    for sw in model.switches.values():
        if sw.enabled and (sw.closed or with_open):
            link([sw.bus1.bus, sw.bus2.bus])
    for t in model.transformers.values():
        if t.enabled:
            link([w.terminal.bus for w in t.windings])
    return adj


def _reach(adj: dict[str, set[str]], start: Iterable[str]) -> set[str]:
    seen = set(start)
    queue = deque(seen)
    while queue:
        for nxt in adj.get(queue.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _check_reachability(model: GridModel, incomplete: bool) -> Iterator[NetworkDiagnostic]:
    starts = [s.bus1.bus for s in model.sources.values() if s.enabled]
    if not starts:
        return  # NO_SOURCE already says it
    closed = _reach(_adjacency(model, with_open=False), starts)
    with_open = _reach(_adjacency(model, with_open=True), starts)
    note = " (the model lacks unsupported elements; one may connect it)" if incomplete else ""
    for name in model.buses:
        bus_id = f"bus.{name}"
        if name in closed:
            continue
        if name in with_open:
            yield _diag(
                bus_id, WARNING, OPEN_SWITCH_ISOLATED, f"bus {name} is cut off by an open switch"
            )
        else:
            yield _diag(
                bus_id,
                WARNING if incomplete else ERROR,
                UNREACHABLE_BUS,
                f"bus {name} is not connected to a source{note}",
            )


# ---- electrical


def _check_lines(model: GridModel) -> Iterator[NetworkDiagnostic]:
    for ln in model.lines.values():
        code = model.linecodes.get(ln.linecode)
        if code is None:
            yield _diag(ln.id, ERROR, UNKNOWN_LINECODE, f"unknown linecode '{ln.linecode}'")
        elif code.phases != ln.phases:
            yield _diag(
                ln.id,
                ERROR,
                LINECODE_PHASES,
                f"{ln.phases}-phase line uses {code.phases}-phase linecode '{code.name}'",
            )
        if not ln.length_m > 0:  # also catches NaN
            yield _diag(ln.id, ERROR, NONPOSITIVE_LENGTH, f"length {ln.length_m:g} m is not > 0")


def _check_transformers(model: GridModel) -> Iterator[NetworkDiagnostic]:
    for t in model.transformers.values():
        for i, w in enumerate(t.windings, start=1):
            if not w.kva > 0:
                yield _diag(t.id, ERROR, TRANSFORMER_KVA, f"winding {i} kVA {w.kva:g} is not > 0")
            bus = model.buses.get(w.terminal.bus)
            if bus is None or bus.kv_base_ln <= 0:
                continue
            rated_ln, kind = _rated_ln(w.kv, t.phases, w.conn, w.terminal)
            if _kv_off(rated_ln, bus.kv_base_ln):
                yield _diag(
                    t.id,
                    ERROR,
                    WINDING_KV,
                    f"winding {i} is rated {w.kv:g} kV {kind} ({rated_ln:.4g} kV LN), "
                    f"bus {bus.name} has base {bus.kv_base_ln:.4g} kV LN "
                    f"(more than {KV_TOLERANCE:.0%} apart)",
                )


def _check_regulators(model: GridModel) -> Iterator[NetworkDiagnostic]:
    for r in model.regulators.values():
        t = model.transformers.get(r.transformer)
        if t is None:
            yield _diag(
                r.id, ERROR, REGULATOR_TARGET, f"names missing transformer '{r.transformer}'"
            )
        elif not 1 <= r.winding <= len(t.windings):
            yield _diag(
                r.id,
                ERROR,
                REGULATOR_TARGET,
                f"names winding {r.winding} of {t.id}, which has {len(t.windings)}",
            )


def _check_element_kv(model: GridModel) -> Iterator[NetworkDiagnostic]:
    for el in (*model.loads.values(), *model.capacitors.values()):
        bus = model.buses.get(el.terminal.bus)
        if bus is None or bus.kv_base_ln <= 0:
            continue
        rated_ln, kind = _rated_ln(el.kv, el.phases, el.conn, el.terminal)
        if _kv_off(rated_ln, bus.kv_base_ln):
            need = bus.kv_base_ln * (_SQRT3 if kind == "LL" else 1.0)
            yield _diag(
                el.id,
                ERROR,
                ELEMENT_KV,
                f"kV {el.kv:g} does not fit a {el.phases}-phase {el.conn} connection on "
                f"bus {bus.name}: expected about {need:.4g} kV {kind}",
            )
