"""G5 benchmarks: every node of IEEE 13, 34 and 123 against the published IEEE PES
solution (tests/fixtures/grid/<feeder>/reference/published.json, read from the report
by scratch/phase4/make_published_reference.py; never typed).

Tolerance: 0.5 % on |V| in pu and 0.5 degree on the angle. Run with ``-s`` to see
the ten worst nodes per feeder and the regulator taps.

How the comparison is set up (decided with the user in G5):

- Regulator taps: the published solution has its own tap positions, which differ by
  1-2 steps from where OpenDSS's RegControl settles. The benchmark sets the published
  taps (from published.json) and solves with controls off, so both solve the same
  circuit. OpenDSS's own controlled solution is tested against opendss_reference.json.
- Names: a published node is mapped to the OpenDSS bus of the same name (lower
  case); a published regulator output node (RG10) is the bus on winding 2 of the
  regulator transformer whose winding 1 is on the regulator's published node.
- Published transformer pseudo-nodes (XFXFM1, XF10, XF1) have no OpenDSS bus. They
  are skipped by name and listed in the report; any other unmatched node fails.
- A bus on an ungrounded delta winding (IEEE 123: 610) has no defined
  line-to-neutral voltage, and the published report repeats the primary's values
  there; such buses are compared line-to-line (V12, V23, V31) instead.
"""

from __future__ import annotations

import cmath
import json
import math
from dataclasses import dataclass
from typing import Any

import pytest

from core.grid.config import CONTROL_OFF, PowerFlowConfig
from core.grid.model import DELTA, GridModel
from core.grid.result import GridResult
from tests.core.grid.helpers import AFTER_COMPILE, FEEDER_FILES, FIXTURES, solve_feeder

pytestmark = pytest.mark.opendss

MAG_TOL_PCT = 0.5
ANGLE_TOL_DEG = 0.5
FEEDERS = ("ieee13", "ieee34", "ieee123")

# Published-only transformer pseudo-nodes (the report program's internal node on an
# in-line transformer; its values repeat a neighbouring bus). Not in OpenDSS.
PSEUDO_NODES = {"ieee13": {"XFXFM1"}, "ieee34": {"XF10"}, "ieee123": {"XF1"}}


def published(feeder: str) -> dict[str, Any]:
    path = FIXTURES / feeder / "reference" / "published.json"
    if not path.exists():
        pytest.fail(f"{path} is missing (scratch/phase4/make_published_reference.py)")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Row:
    ref_node: str
    node: str
    ref_pu: float
    pu: float
    ref_deg: float
    deg: float

    @property
    def d_mag_pct(self) -> float:
        return (self.pu - self.ref_pu) / self.ref_pu * 100.0

    @property
    def d_deg(self) -> float:
        return (self.deg - self.ref_deg + 180.0) % 360.0 - 180.0

    @property
    def score(self) -> float:
        return max(abs(self.d_mag_pct) / MAG_TOL_PCT, abs(self.d_deg) / ANGLE_TOL_DEG)

    @property
    def misses(self) -> bool:
        return self.score > 1.0


@dataclass(frozen=True)
class TapSetting:
    published: str
    """Published regulator name and phase, e.g. "RG10.1"."""
    regcontrol: str
    transformer: str
    winding: int
    steps: int
    step: float
    """One tap step in pu: (max_tap - min_tap) / num_taps of the winding."""
    tap: float
    """Tap in pu: 1 + steps * step."""


# ---- mapping published -> OpenDSS


def regulator_buses(model: GridModel) -> dict[str, str]:
    """Bus on winding 1 of each regulator transformer -> bus on winding 2."""
    out: dict[str, str] = {}
    for reg in model.regulators.values():
        t = model.transformers.get(reg.transformer)
        if t is not None and len(t.windings) >= 2:
            out[t.windings[0].terminal.bus] = t.windings[1].terminal.bus
    return out


def bus_map(ref: dict[str, Any], model: GridModel) -> dict[str, str | None]:
    """Published bus name -> OpenDSS bus (None = no counterpart)."""
    reg_out = regulator_buses(model)
    reg_at = {r["name"]: r["node"].lower() for r in ref["regulators"]}
    out: dict[str, str | None] = {}
    for name in ref["nodes"]:
        low = name.lower()
        if low in model.buses:
            out[name] = low
        elif name in reg_at and reg_at[name] in reg_out:
            out[name] = reg_out[reg_at[name]]
        else:
            out[name] = None
    return out


def tap_settings(ref: dict[str, Any], model: GridModel) -> list[TapSetting]:
    """Each published tap -> the one OpenDSS regulator it belongs to. Fails the test
    when a published tap has no regulator or more than one."""
    out: list[TapSetting] = []
    for r in ref["regulators"]:
        for phase, steps in sorted(r["taps"].items()):
            found = []
            for reg in model.regulators.values():
                t = model.transformers.get(reg.transformer)
                if t is None or t.windings[0].terminal.bus != r["node"].lower():
                    continue
                nodes = t.windings[0].terminal.nodes
                reg_phase = str(nodes[0]) if t.phases == 1 and nodes else reg.pt_phase
                if reg_phase != phase:
                    continue
                w = t.windings[reg.winding - 1]
                step = (w.max_tap - w.min_tap) / w.num_taps
                found.append(
                    TapSetting(
                        f"{r['name']}.{phase}",
                        reg.name,
                        t.name,
                        reg.winding,
                        steps,
                        step,
                        1.0 + steps * step,
                    )
                )
            assert len(found) == 1, f"published tap {r['name']}.{phase}: regulators {found}"
            out.extend(found)
    return out


def floating_buses(model: GridModel) -> set[str]:
    """Buses whose only connection is a delta transformer winding: no line, switch,
    source, wye winding, load or capacitor gives them a neutral reference."""
    anchored: set[str] = set()
    on_delta: set[str] = set()
    for ln in (*model.lines.values(), *model.switches.values()):
        anchored.update((ln.bus1.bus, ln.bus2.bus))
    for s in model.sources.values():
        anchored.add(s.bus1.bus)
    for t in model.transformers.values():
        for w in t.windings:
            (on_delta if w.conn == DELTA else anchored).add(w.terminal.bus)
    for el in (*model.loads.values(), *model.capacitors.values(), *model.pvsystems.values()):
        if el.conn != DELTA:
            anchored.add(el.terminal.bus)
    return on_delta - anchored


# ---- solve and compare


def solve_with_published_taps(
    feeder: str, ref: dict[str, Any]
) -> tuple[GridModel, GridResult, list[TapSetting]]:
    from core.grid.engines.opendss.engine import OpenDSSEngine

    engine = OpenDSSEngine()
    try:
        assert engine.load_native(FEEDER_FILES[feeder]) == []
        assert engine.context is not None
        for command in AFTER_COMPILE[feeder]:
            engine.context.Text.Command = command
        model, _ = engine.extract_model()
        assert model is not None
        settings = tap_settings(ref, model)
        for s in settings:
            engine.context.Text.Command = (
                f"Transformer.{s.transformer}.wdg={s.winding} tap={s.tap!r}"
            )
        result = engine.run(PowerFlowConfig(control_mode=CONTROL_OFF))
        model, _ = engine.extract_model()
    finally:
        engine.close()
    assert result.converged and model is not None
    return model, result, settings


def _pu_phasor(result: GridResult, node: str) -> complex:
    v = result.voltage(node)
    return v / abs(v) * result.voltage_pu(node) if abs(v) > 0 else 0j


def compare(
    feeder: str, ref: dict[str, Any], model: GridModel, result: GridResult
) -> tuple[list[Row], list[str], list[str]]:
    """(rows, unmatched published nodes, skipped pseudo-nodes)."""
    rows: list[Row] = []
    unmatched: list[str] = []
    skipped: list[str] = []
    floating = floating_buses(model)
    for name, bus in bus_map(ref, model).items():
        phases = ref["nodes"][name]["phases"]
        if bus is None and name in PSEUDO_NODES[feeder]:
            skipped.append(name)
            continue
        if bus in floating:
            ref_v = {k: m * cmath.exp(1j * math.radians(a)) for k, (m, a) in phases.items()}
            for a, b in (("1", "2"), ("2", "3"), ("3", "1")):
                if a not in ref_v or b not in ref_v:
                    continue
                r = ref_v[a] - ref_v[b]
                o = _pu_phasor(result, f"{bus}.{a}") - _pu_phasor(result, f"{bus}.{b}")
                rows.append(
                    Row(
                        f"{name}.{a}{b}",
                        f"{bus}.{a}{b}",
                        abs(r),
                        abs(o),
                        math.degrees(cmath.phase(r)),
                        math.degrees(cmath.phase(o)),
                    )
                )
            continue
        for phase, (ref_pu, ref_deg) in phases.items():
            node = f"{bus}.{phase}"
            if bus is None or node not in result.node_names:
                unmatched.append(f"{name}.{phase}")
                continue
            rows.append(
                Row(
                    f"{name}.{phase}",
                    node,
                    ref_pu,
                    result.voltage_pu(node),
                    ref_deg,
                    math.degrees(cmath.phase(result.voltage(node))),
                )
            )
    return rows, unmatched, skipped


def tap_lines(settings: list[TapSetting], controlled: GridResult) -> list[str]:
    own = controlled.metadata.get("regulator_taps", {})
    lines = []
    for s in settings:
        ours = f"{(own[s.regcontrol] - 1.0) / s.step:+.0f}" if s.regcontrol in own else "?"
        lines.append(
            f"  {s.published:<8} {s.regcontrol:<8} published {s.steps:+d} "
            f"(set, tap {s.tap:.5f}); OpenDSS RegControl would settle at {ours}"
        )
    return lines


def report(
    feeder: str, rows: list[Row], unmatched: list[str], skipped: list[str], taps: list[str]
) -> str:
    worst = sorted(rows, key=lambda r: r.score, reverse=True)[:10]
    out = [
        f"{feeder}: {len(rows)} values compared, {sum(r.misses for r in rows)} outside "
        f"{MAG_TOL_PCT} % / {ANGLE_TOL_DEG} deg, {len(unmatched)} published nodes "
        "without an OpenDSS node",
        f"  {'published':<10} {'opendss':<10} {'ref pu':>8} {'pu':>8} {'d %':>7} "
        f"{'ref deg':>9} {'deg':>9} {'d deg':>7}",
    ]
    for r in worst:
        flag = "  MISS" if r.misses else ""
        out.append(
            f"  {r.ref_node:<10} {r.node:<10} {r.ref_pu:8.4f} {r.pu:8.4f} "
            f"{r.d_mag_pct:+7.3f} {r.ref_deg:9.2f} {r.deg:9.2f} {r.d_deg:+7.2f}{flag}"
        )
    if skipped:
        out.append(f"  skipped (published transformer pseudo-node): {', '.join(skipped)}")
    if unmatched:
        out.append(f"  no OpenDSS node: {', '.join(unmatched)}")
    out.append("  regulator taps (steps from neutral):")
    out.extend(taps)
    return "\n".join(out)


@pytest.mark.parametrize("feeder", FEEDERS)
def test_feeder_matches_published_solution(feeder: str) -> None:
    pytest.importorskip("dss")
    ref = published(feeder)
    model, result, settings = solve_with_published_taps(feeder, ref)
    _, controlled = solve_feeder(feeder)
    rows, unmatched, skipped = compare(feeder, ref, model, result)
    text = report(feeder, rows, unmatched, skipped, tap_lines(settings, controlled))
    print("\n" + text)
    assert rows, "no node compared"
    assert set(skipped) == PSEUDO_NODES[feeder], "pseudo-node list out of date"
    misses = [r.ref_node for r in rows if r.misses]
    assert not misses and not unmatched, text


def test_floating_bus_is_only_the_delta_delta_secondary() -> None:
    pytest.importorskip("dss")
    model, _ = solve_feeder("ieee123")
    assert floating_buses(model) == {"610"}
    for feeder in ("ieee13", "ieee34"):
        assert floating_buses(solve_feeder(feeder)[0]) == set()
