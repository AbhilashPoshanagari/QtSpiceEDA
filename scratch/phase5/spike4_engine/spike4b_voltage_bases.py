"""S4 b) Derived voltage bases.

IEEE 13/34/123 -> GridModel (OpenDSSEngine.extract_model) -> writer.py with
Set VoltageBases replaced by a derived set, then CalcVoltageBases. Compared per bus
(kVBase) with the original file, and per node with opendss_reference.json.

Derivation variants (the CLAUDE.md wording is "the sorted set of source kV and every
transformer winding kV (LL)"):
- "as_stored": source kV and every Winding.kv as stored in the GridModel
  (a 1-phase wye winding keeps its LN kV, e.g. a regulator's 2.4).
- "ll": as_stored, but a 1-phase wye winding is converted to LL (kV * sqrt(3)).
- "ll_merged": ll, then values within 1 % of a larger one in the set are dropped
  (4.1569 vs 4.16).
- "three_phase_only": source kV and windings of 3-phase transformers only.
- "ll_3ph_first": source kV and 3-phase windings first; a 1-phase winding (wye: kV *
  sqrt(3)) is added only when no value already in the set is within 1 % of it.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

import numpy as np
from common import FEEDERS, compile_feeder, prop

from core.grid.config import CONTROL_STATIC, PowerFlowConfig
from core.grid.engines.opendss.engine import OpenDSSEngine
from core.grid.model import DELTA, GridModel
from tests.core.grid.helpers import AFTER_COMPILE, FEEDER_FILES, reference

TIGHT = PowerFlowConfig(control_mode=CONTROL_STATIC, tolerance=1e-10, max_control_iterations=30)
DEFAULT = PowerFlowConfig(control_mode=CONTROL_STATIC, max_control_iterations=30)


def _winding_ll(model: GridModel, single_phase_as_ll: bool, three_phase_only: bool) -> list[float]:
    out: list[float] = []
    for t in model.transformers.values():
        if three_phase_only and t.phases < 3:
            continue
        for w in t.windings:
            kv = w.kv
            if single_phase_as_ll and t.phases == 1 and w.conn != DELTA:
                kv *= math.sqrt(3.0)
            out.append(kv)
    return out


def derive(model: GridModel, variant: str) -> list[float]:
    values = [s.kv for s in model.sources.values()]
    values += _winding_ll(
        model,
        single_phase_as_ll=variant in ("ll", "ll_merged"),
        three_phase_only=variant == "three_phase_only",
    )
    bases = sorted({round(v, 12) for v in values})
    if variant == "ll_3ph_first":
        kept = sorted(
            {
                round(v, 12)
                for v in [s.kv for s in model.sources.values()]
                + _winding_ll(model, single_phase_as_ll=True, three_phase_only=True)
            }
        )
        for t in model.transformers.values():
            if t.phases >= 3:
                continue
            for w in t.windings:
                kv = w.kv * math.sqrt(3.0) if w.conn != DELTA else w.kv
                if not any(abs(k - kv) <= 0.01 * k for k in kept):
                    kept.append(kv)
        return sorted(kept)
    if variant == "ll_merged":
        kept: list[float] = []
        for v in sorted(bases, reverse=True):
            if not any(abs(k - v) <= 0.01 * k for k in kept):
                kept.append(v)
        bases = sorted(kept)
    return bases


VARIANTS = ("as_stored", "ll", "ll_merged", "three_phase_only", "ll_3ph_first")


def _bus_bases(ctx: Any) -> dict[str, float]:
    ckt = ctx.ActiveCircuit
    out: dict[str, float] = {}
    for i in range(int(ckt.NumBuses)):
        ckt.SetActiveBusi(i)
        out[str(ckt.ActiveBus.Name).lower()] = float(ckt.ActiveBus.kVBase)
    return out


def _original(feeder: str) -> tuple[OpenDSSEngine, Any]:
    engine = OpenDSSEngine()
    assert engine.load_native(FEEDER_FILES[feeder]) == []
    assert engine.context is not None
    for command in AFTER_COMPILE[feeder]:
        engine.context.Text.Command = command
    return engine, engine.run(TIGHT)


def feeder_case(feeder: str) -> dict[str, Any]:
    original, a = _original(feeder)
    assert original.context is not None
    model, _ = original.extract_model()
    assert model is not None
    orig_bases = _bus_bases(original.context)
    ref = reference(feeder)
    ref_pu = dict(zip(ref["node_names"], ref["node_pu"], strict=True))
    ref_v = {n: complex(*v) for n, v in zip(ref["node_names"], ref["node_v"], strict=True)}
    pa = dict(zip(a.node_names, a.node_voltage_pu, strict=True))
    out: dict[str, Any] = {
        "file_voltage_bases": [float(v) for v in model.voltage_bases],
        "original_tight_vs_reference_max_pu": max(abs(pa[n] - ref_pu[n]) for n in ref_pu),
        "variants": {},
    }
    for variant in VARIANTS:
        bases = derive(model, variant)
        designed = dataclasses.replace(model, voltage_bases=bases)
        row: dict[str, Any] = {"bases": bases}
        for label, config in (("tight", TIGHT), ("default_tol", DEFAULT)):
            engine = OpenDSSEngine()
            diags = engine.load_model(designed)
            assert not diags, diags
            b = engine.run(config)
            assert engine.context is not None
            got_bases = _bus_bases(engine.context)
            differ = {
                bus: (orig_bases[bus], kv)
                for bus, kv in got_bases.items()
                if not math.isclose(orig_bases.get(bus, -1.0), kv, rel_tol=1e-12)
            }
            pb = dict(zip(b.node_names, b.node_voltage_pu, strict=True))
            vb = dict(zip(b.node_names, b.node_voltage, strict=True))
            d_pu = {n: abs(float(np.nan_to_num(pb[n])) - ref_pu[n]) for n in ref_pu}
            d_v = {n: abs(vb[n] - ref_v[n]) / abs(ref_v[n]) for n in ref_v if abs(ref_v[n]) > 0}
            d_pu_a = {n: abs(float(np.nan_to_num(pb[n])) - pa[n]) for n in pa}
            worst = max(d_pu, key=d_pu.__getitem__)
            row[label] = {
                "converged": b.converged,
                "buses": len(got_bases),
                "buses_with_other_kvbase": len(differ),
                "examples": dict(list(sorted(differ.items()))[:6]),
                "max_pu_vs_reference": d_pu[worst],
                "worst_node": worst,
                "max_rel_volts_vs_reference": max(d_v.values()),
                "max_pu_vs_original_same_solve": max(d_pu_a.values()),
                "losses_rel_vs_reference": abs(b.total_losses_kw - ref["losses_kw"])
                / ref["losses_kw"],
                "taps": b.metadata["regulator_taps"] == a.metadata["regulator_taps"],
            }
            engine.close()
        out["variants"][variant] = row
    original.close()
    return out


def calc_voltage_bases_rule() -> dict[str, Any]:
    """How CalcVoltageBases picks a base: IEEE 13 with a base set that has two close
    candidates (4.1569, 4.16) - which bus gets which, and what each bus's zero-load
    voltage is."""
    ctx = compile_feeder("ieee13")
    ckt = ctx.ActiveCircuit
    ctx.Text.Command = "Set VoltageBases=[115 4.1569219381653 4.16 0.48]"
    ctx.Text.Command = "CalcVoltageBases"
    bases = {}
    for i in range(int(ckt.NumBuses)):
        ckt.SetActiveBusi(i)
        bases[str(ckt.ActiveBus.Name).lower()] = round(
            float(ckt.ActiveBus.kVBase) * math.sqrt(3), 4
        )
    return {
        "bus_kv_ll": bases,
        "regulator_reg1_kv": prop(ctx, "transformer.reg1", "kVs"),
    }


def run() -> dict[str, Any]:
    return {
        "feeders": {f: feeder_case(f) for f in FEEDERS},
        "close_bases": calc_voltage_bases_rule(),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=1, default=str))
