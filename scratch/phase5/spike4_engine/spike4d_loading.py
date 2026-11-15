"""S4 d) The API calls behind line and transformer loading %, and their signs.

Per PD element after the solve (feeders in their reference state):
- CktElement.Currents: complex current INTO each conductor of each terminal, terminal 1
  first (NumConductors entries per terminal). Line loading = max |I| over the phase
  conductors of terminal 1 / Lines.NormAmps.
- CktElement.Powers: complex kW + j kvar INTO each conductor of each terminal.
  Transformer winding k terminal power S_k = sum over the conductors of terminal k;
  transformer loading = max_k |S_k| / Transformers.kVA (CLAUDE.md rule).
- Compared with OpenDSS's own PDElements.AllPctNorm (first terminal, max conductor
  current / CktElement.NormalAmps) and Transformers.WdgCurrents.
Signs checked on IEEE 13: lines (terminal 1 / 2), transformer windings, the source,
loads and capacitors.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from common import FEEDERS, compile_feeder, each, prop


def _cx(values: Any) -> np.ndarray:
    a = np.asarray(values, dtype=float)
    return a[0::2] + 1j * a[1::2]


def _terminal(values: np.ndarray, conductors: int, k: int) -> np.ndarray:
    return values[k * conductors : (k + 1) * conductors]


def feeder_case(feeder: str) -> dict[str, Any]:
    ctx = compile_feeder(feeder)
    ckt = ctx.ActiveCircuit
    pd = ckt.PDElements
    pct = dict(
        zip([n.lower() for n in pd.AllNames], (float(v) for v in pd.AllPctNorm()), strict=True)
    )
    lines: dict[str, Any] = {}
    for name in each(ckt.Lines):
        ckt.SetActiveElement(f"line.{name}")
        el = ckt.ActiveCktElement
        if not el.Enabled:
            continue
        nc, ph = int(el.NumConductors), int(el.NumPhases)
        i1 = _terminal(_cx(el.Currents), nc, 0)[:ph]
        ours = float(np.max(np.abs(i1))) / float(ckt.Lines.NormAmps) * 100.0
        s = _cx(el.Powers)
        lines[name] = {
            "ours_pct": ours,
            "opendss_pct": pct[f"line.{name}"],
            "p1_kw": float(_terminal(s, nc, 0).real.sum()),
            "p2_kw": float(_terminal(s, nc, 1).real.sum()),
            "i_sum_terminals": float(
                np.max(
                    np.abs(_terminal(_cx(el.Currents), nc, 0) + _terminal(_cx(el.Currents), nc, 1))
                )
            ),
        }
    transformers: dict[str, Any] = {}
    xf = ckt.Transformers
    for name in each(xf):
        ckt.SetActiveElement(f"transformer.{name}")
        el = ckt.ActiveCktElement
        nc, ph, nt = int(el.NumConductors), int(el.NumPhases), int(el.NumTerminals)
        s = _cx(el.Powers)
        cur = _cx(el.Currents)
        xf.Name = name
        xf.Wdg = 1
        kva = float(xf.kVA)
        kv1 = float(xf.kV)
        s_k = [complex(_terminal(s, nc, k).sum()) for k in range(nt)]
        i_k = [float(np.max(np.abs(_terminal(cur, nc, k)[:ph]))) for k in range(nt)]
        rated_i1 = kva / (math.sqrt(3.0) * kv1) if ph >= 3 else kva / kv1
        wdg = _cx(xf.WdgCurrents)  # (ph1: wdg1, wdg2, ..., ph2: ...), see ITransformers
        transformers[name] = {
            "phases": ph,
            "conductors": nc,
            "kva": kva,
            "normhkva": float(prop(ctx, f"transformer.{name}", "normhkva")),
            "normal_amps": float(el.NormalAmps),
            "rated_i1": rated_i1,
            "s_k_kva": [abs(v) for v in s_k],
            "p_k_kw": [v.real for v in s_k],
            "loading_s_pct": max(abs(v) for v in s_k) / kva * 100.0,
            "loading_i1_pct": i_k[0] / rated_i1 * 100.0,
            "opendss_pct": pct[f"transformer.{name}"],
            "wdgcurrents_len": len(wdg),
        }
    signs: dict[str, Any] = {}
    if feeder == "ieee13":

        def total(element: str) -> complex:
            ckt.SetActiveElement(element)
            return complex(_cx(ckt.ActiveCktElement.Powers).sum())

        signs = {
            "vsource.source": total("vsource.source"),
            "load.671": total("load.671"),
            "capacitor.cap1": total("capacitor.cap1"),
            "circuit_total_power": complex(*ckt.TotalPower),
        }
    worst_line = max(abs(v["ours_pct"] - v["opendss_pct"]) for v in lines.values())
    return {
        "lines": len(lines),
        "line_ours_vs_opendss_max_abs_pct": worst_line,
        "line_p1_positive": sum(v["p1_kw"] > 0 for v in lines.values()),
        "line_p2_negative": sum(v["p2_kw"] < 0 for v in lines.values()),
        "line_examples": {k: lines[k] for k in list(lines)[:3]},
        "transformers": transformers,
        "signs": {k: [v.real, v.imag] for k, v in signs.items()},
    }


def run() -> dict[str, Any]:
    return {f: feeder_case(f) for f in FEEDERS}


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=1, default=str))
