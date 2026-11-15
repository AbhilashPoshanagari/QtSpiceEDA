"""S4 c) Textbook sequence -> phase conversion against tests/fixtures/grid/
sequence_reference.json (made by scratch/phase5/make_sequence_reference.py).

Two candidate rules per case: "textbook" (sequence.phase_matrices for every n) and
"z1_only" (self = z1, mutual = 0, i.e. positive sequence only). Reported: the largest
absolute and relative difference per kind (linecode / line / line_with_linecode) and n.
"""

from __future__ import annotations

import json
from typing import Any

from common import FIXTURES
from sequence import phase_matrices

REFERENCE = FIXTURES / "sequence_reference.json"
MATRICES = ("r_matrix", "x_matrix", "c_matrix")
KINDS = ("linecode", "line", "line_with_linecode")


def _z1_only(seq: dict[str, float], n: int) -> dict[str, list[float]]:
    def m(s1: float) -> list[float]:
        return [s1 if i == j else 0.0 for i in range(n) for j in range(n)]

    return {"r_matrix": m(seq["r1"]), "x_matrix": m(seq["x1"]), "c_matrix": m(seq["c1"])}


RULES = {"textbook": phase_matrices, "z1_only": _z1_only}


def _diff(a: list[float], b: list[float]) -> tuple[float, float]:
    absd = max(abs(x - y) for x, y in zip(a, b, strict=True))
    scale = max(abs(y) for y in b) or 1.0
    return absd, absd / scale


def run() -> dict[str, Any]:
    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    inputs = data["inputs"]
    out: dict[str, Any] = {"input_sets": len(inputs), "cases": len(data["cases"]), "max": {}}
    units_kept = True
    for case in data["cases"]:
        seq = inputs[case["input"]]["sequence"]
        n = case["phases"]
        for kind in KINDS:
            got = case[kind]
            units_kept &= got["units"] == inputs[case["input"]]["units"]
            for rule, fn in RULES.items():
                want = fn(seq, n)
                key = f"{kind} n={n} {rule}"
                row = out["max"].setdefault(key, {"abs": 0.0, "rel": 0.0})
                for m in MATRICES:
                    absd, reld = _diff(want[m], got[m])
                    row["abs"] = max(row["abs"], absd)
                    row["rel"] = max(row["rel"], reld)
    out["units_kept"] = units_kept
    out["is_z1z0"] = all(c["linecode"]["is_z1z0"] for c in data["cases"])
    return out


if __name__ == "__main__":
    r = run()
    print(f"{r['input_sets']} input sets, {r['cases']} cases, units kept {r['units_kept']}")
    for key, row in sorted(r["max"].items()):
        print(f"{key:36s} abs {row['abs']:.3e}  rel {row['rel']:.3e}")
