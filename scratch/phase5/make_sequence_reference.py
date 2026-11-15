"""Write tests/fixtures/grid/sequence_reference.json: linecodes given in sequence
values (r1 x1 r0 x0 c1 c0, with units) for 1, 2 and 3 phases, and the phase matrices
OpenDSS makes of them. core/grid/sequence.py (D2) is tested against this file; never
edit it by hand.

Inputs: one set of sequence values per linecode of IEEE 13, 34 and 123 (deduplicated by
value), taken from that linecode's own phase matrix in its own unit:
    zs = mean of the diagonal, zm = mean of the off-diagonal (0 for 1 phase),
    z1 = zs - zm, z0 = zs + 2 zm             (the same for c)
These are only a realistic spread of input numbers; how OpenDSS turns them back into
matrices is the reference. Each input set is defined as
    New Linecode.<id> nphases=<n> r1= x1= r0= x0= c1= c0= units=<unit>
and, in the same circuit, as a Line with the same values and no linecode, and as a
Line that uses the linecode:
    New Line.<id> phases=<n> ... r1= x1= r0= x0= c1= c0= length=1 units=<unit>
    New Line.<id>_lc phases=<n> ... linecode=<id> length=1 units=<unit>
After one Solve all are read back: LineCodes.Rmatrix/Xmatrix/Cmatrix (per linecode unit) and
Lines.Rmatrix/Xmatrix/Cmatrix (per line unit), row-major, ohm and nF per unit length.

Re-run only on purpose (new pinned dss-python):
    python scratch/phase5/make_sequence_reference.py
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "spike4_engine"))

from common import FEEDERS, ROOT, compile_feeder, each, new_context, versions  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "grid" / "sequence_reference.json"
UNIT_NAMES = {0: "none", 1: "mi", 2: "kft", 3: "km", 4: "m", 5: "ft", 6: "in", 7: "cm", 8: "mm"}
KEYS = ("r1", "x1", "r0", "x0", "c1", "c0")


def _seq_from_matrix(values: list[float], n: int) -> tuple[float, float]:
    values = [float(v) for v in values]
    diag = [values[i * n + i] for i in range(n)]
    off = [values[i * n + j] for i in range(n) for j in range(n) if i != j]
    zs = sum(diag) / n
    zm = sum(off) / len(off) if off else 0.0
    return zs - zm, zs + 2.0 * zm


def feeder_inputs() -> list[dict[str, Any]]:
    """One input set per linecode of the three feeders, first occurrence wins."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for feeder in FEEDERS:
        ctx = compile_feeder(feeder, solve=False)
        lc = ctx.ActiveCircuit.LineCodes
        for name in each(lc):
            lc.Name = name
            n = int(lc.Phases)
            r1, r0 = _seq_from_matrix(list(lc.Rmatrix), n)
            x1, x0 = _seq_from_matrix(list(lc.Xmatrix), n)
            c1, c0 = _seq_from_matrix(list(lc.Cmatrix), n)
            raw = {"r1": r1, "x1": x1, "r0": r0, "x0": x0, "c1": c1, "c0": c0}
            values = {k: float(v) for k, v in raw.items()}
            unit = int(lc.Units)
            key = (unit, *(round(values[k], 12) for k in KEYS))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "from": f"{feeder}/linecode.{name}",
                    "from_phases": n,
                    "units": unit,
                    "unit_name": UNIT_NAMES[unit],
                    "sequence": values,
                }
            )
    return out


def _matrices(iface: Any) -> dict[str, list[float]]:
    return {
        "r_matrix": [float(v) for v in iface.Rmatrix],
        "x_matrix": [float(v) for v in iface.Xmatrix],
        "c_matrix": [float(v) for v in iface.Cmatrix],
    }


def opendss_matrices(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Define every case, solve once (Line matrices are recalculated only by a solve:
    read before it, a Line still shows the default r1/x1/... matrices), read back."""
    ctx = new_context()
    ctx.Text.Command = "Clear"
    ctx.Text.Command = "Set DefaultBaseFrequency=60"
    ctx.Text.Command = "New Circuit.seq basekv=12.47 bus1=src"
    idents: list[tuple[str, int, int]] = []
    for i, item in enumerate(inputs):
        seq = item["sequence"]
        values = " ".join(f"{k}={seq[k]!r}" for k in KEYS)
        unit = "" if item["units"] == 0 else f" units={item['unit_name']}"
        for n in (1, 2, 3):
            ident = f"s{i}_{n}"
            nodes = ".".join(str(k) for k in range(1, n + 1))
            ctx.Text.Command = f"New Linecode.{ident} nphases={n} {values}{unit}"
            ctx.Text.Command = (
                f"New Line.{ident} phases={n} bus1=src.{nodes} bus2=b{ident}.{nodes} "
                f"{values} length=1{unit}"
            )
            ctx.Text.Command = (
                f"New Line.{ident}_lc phases={n} bus1=src.{nodes} bus2=c{ident}.{nodes} "
                f"linecode={ident} length=1{unit}"
            )
            idents.append((ident, i, n))
    ckt = ctx.ActiveCircuit
    ckt.Solution.Solve()
    cases: list[dict[str, Any]] = []
    for ident, i, n in idents:
        ckt.LineCodes.Name = ident
        lc = ckt.LineCodes
        case: dict[str, Any] = {
            "id": ident,
            "input": i,
            "phases": n,
            "linecode": {"units": int(lc.Units), "is_z1z0": bool(lc.IsZ1Z0), **_matrices(lc)},
        }
        for key, name in (("line", ident), ("line_with_linecode", f"{ident}_lc")):
            ckt.Lines.Name = name
            ln = ckt.Lines
            case[key] = {"units": int(ln.Units), **_matrices(ln)}
        cases.append(case)
    return cases


def main() -> None:
    inputs = feeder_inputs()
    data = {
        "about": {
            "what": "OpenDSS phase matrices of linecodes and lines given in sequence values",
            "made_by": "scratch/phase5/make_sequence_reference.py",
            "date": datetime.date.today().isoformat(),
            "matrices": "row-major n x n, ohm and nF per unit length of 'units' "
            "(OpenDSS LineUnits code; 0 = none)",
            **versions(),
        },
        "inputs": inputs,
        "cases": opendss_matrices(inputs),
    }
    OUT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(inputs)} input sets, {len(data['cases'])} cases")


if __name__ == "__main__":
    main()
