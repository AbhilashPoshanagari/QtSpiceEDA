"""S4 e) What OpenDSS reports for incomplete or wrong designs.

Each case is a small design (12.47 kV source -> line -> load) with one defect. It is
run twice: one command at a time (as OpenDSSEngine.load_model does) and as one
`compile` of a file. Reported: the exception (if any), Text.Result, the value OpenDSS
assumed, and what one Solve then gives. Plus: writer.write_dss on a GridModel with a
None field (the draft elements of D2).
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path
from typing import Any

from common import new_context, prop

HEAD = [
    "Clear",
    "Set DefaultBaseFrequency=60",
    "New Circuit.d basekv=12.47 pu=1.0 bus1=src r1=0.1 x1=1 r0=0.1 x0=1",
    "New Linecode.lc nphases=3 r1=0.3 x1=0.6 r0=0.6 x0=1.8 c1=10 c0=4 units=km normamps=300",
]
TAIL = ["Set VoltageBases=[12.47 0.48]", "CalcVoltageBases"]
LINE = "New Line.l1 phases=3 bus1=src bus2=b2 linecode=lc length=1000 units=m"
LOAD = "New Load.ld1 phases=3 bus1=b2 kv=12.47 kw=500 kvar=200"

CASES: dict[str, dict[str, Any]] = {
    "missing_linecode": {
        "body": ["New Line.l1 phases=3 bus1=src bus2=b2 linecode=nosuch length=1000 units=m", LOAD],
        "read": [("line.l1", "linecode"), ("line.l1", "r1")],
    },
    "load_kw_not_set": {
        "body": [LINE, "New Load.ld1 phases=3 bus1=b2 kv=12.47"],
        "read": [("load.ld1", "kW"), ("load.ld1", "kvar"), ("load.ld1", "pf")],
    },
    "load_kw_not_set_kvar_set": {
        "body": [LINE, "New Load.ld1 phases=3 bus1=b2 kv=12.47 kvar=200"],
        "read": [("load.ld1", "kW"), ("load.ld1", "kvar"), ("load.ld1", "pf")],
    },
    "load_kv_not_set": {
        "body": [LINE, "New Load.ld1 phases=3 bus1=b2 kw=500 kvar=200"],
        "read": [("load.ld1", "kV")],
    },
    "linecode_no_impedance": {
        "body": [
            "New Linecode.empty nphases=3 units=km",
            "New Line.l1 phases=3 bus1=src bus2=b2 linecode=empty length=1000 units=m",
            LOAD,
        ],
        "read": [
            ("linecode.empty", "r1"),
            ("linecode.empty", "x1"),
            ("linecode.empty", "normamps"),
        ],
    },
    "line_no_linecode_no_impedance": {
        "body": ["New Line.l1 phases=3 bus1=src bus2=b2 length=1000 units=m", LOAD],
        "read": [("line.l1", "r1"), ("line.l1", "x1"), ("line.l1", "c1")],
    },
    "line_phases_vs_linecode": {
        "body": [
            "New Line.l1 phases=1 bus1=src.1 bus2=b2.1 linecode=lc length=1000 units=m",
            "New Load.ld1 phases=1 bus1=b2.1 kv=7.2 kw=100 kvar=50",
        ],
        "read": [("line.l1", "phases")],
    },
    "transformer_no_kva_no_kv": {
        "body": [
            LINE,
            "New Transformer.tx1 phases=3 windings=2 buses=[b2 lv]",
            "New Load.ld1 phases=3 bus1=lv kv=0.48 kw=50 kvar=20",
        ],
        "read": [
            ("transformer.tx1", "kVAs"),
            ("transformer.tx1", "kVs"),
            ("transformer.tx1", "%Rs"),
            ("transformer.tx1", "xhl"),
        ],
    },
    "capacitor_no_kvar": {
        "body": [LINE, LOAD, "New Capacitor.c1 phases=3 bus1=b2 kv=12.47"],
        "read": [("capacitor.c1", "kvar")],
    },
    "pv_no_pmpp_no_kva": {
        "body": [LINE, LOAD, "New PVSystem.pv1 phases=3 bus1=b2 kv=12.47"],
        "read": [("pvsystem.pv1", "Pmpp"), ("pvsystem.pv1", "kVA"), ("pvsystem.pv1", "irradiance")],
    },
    "load_node_not_at_bus": {
        "body": [
            "New Linecode.lc1 nphases=1 r1=0.3 x1=0.6 r0=0.6 x0=1.8 c1=10 c0=4 units=km",
            "New Line.l1 phases=1 bus1=src.1 bus2=b2.1 linecode=lc1 length=1000 units=m",
            "New Load.ld1 phases=1 bus1=b2.3 kv=7.2 kw=100 kvar=50",
        ],
        "read": [("line.l1", "phases")],
    },
    "island_bus": {
        "body": [LINE, LOAD, "New Load.ld2 phases=3 bus1=far kv=12.47 kw=100 kvar=50"],
        "read": [],
    },
    "line_to_other_zone": {
        "body": [
            LINE,
            "New Transformer.tx1 phases=3 windings=2 buses=[b2 lv] conns=[delta wye] "
            "kvs=[12.47 0.48] kvas=[500 500] xhl=5",
            "New Line.l2 phases=3 bus1=lv bus2=src linecode=lc length=10 units=m",
            LOAD,
        ],
        "read": [],
    },
}


def _err(err: Exception) -> str:
    text = " ".join(str(err).split())
    return text[:300]


def _solve(ctx: Any) -> dict[str, Any]:
    ckt = ctx.ActiveCircuit
    try:
        ckt.Solution.Solve()
    except Exception as err:
        return {"solve_error": _err(err), "converged": False}
    pu = [float(v) for v in ckt.AllBusVmagPu]
    names = [n.lower() for n in ckt.AllNodeNames]
    return {
        "converged": bool(ckt.Solution.Converged),
        "iterations": int(ckt.Solution.Iterations),
        "min_pu": min(pu) if pu else None,
        "zero_nodes": [n for n, v in zip(names, pu, strict=True) if v < 1e-6],
        "nodes": len(names),
    }


def _by_command(lines: list[str], reads: list[tuple[str, str]]) -> dict[str, Any]:
    ctx = new_context()
    errors: list[dict[str, Any]] = []
    results: list[str] = []
    for number, line in enumerate(lines, start=1):
        try:
            ctx.Text.Command = line
            if ctx.Text.Result:
                results.append(f"{number}: {ctx.Text.Result}")
        except Exception as err:
            errors.append({"line": number, "command": line, "error": _err(err)})
    out: dict[str, Any] = {"errors": errors, "results": results}
    try:
        out["values"] = {f"{e}.{p}": prop(ctx, e, p) for e, p in reads}
    except Exception as err:
        out["values"] = {"error": _err(err)}
    if ctx.ActiveCircuit.Name:
        out["solve"] = _solve(ctx)
    return out


def _by_compile(lines: list[str]) -> dict[str, Any]:
    ctx = new_context()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "design.dss"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            ctx.Text.Command = f'compile "{path}"'
        except Exception as err:
            return {"error": _err(err), "circuit": str(ctx.ActiveCircuit.Name)}
    return {"error": None, "circuit": str(ctx.ActiveCircuit.Name)}


def writer_with_none() -> dict[str, Any]:
    """writer.write_dss on a model with a None field (D2 drafts): raises or not?"""
    from spike4b_voltage_bases import _original

    from core.grid.engines.opendss.writer import write_dss

    engine, _ = _original("ieee13")
    model, _ = engine.extract_model()
    engine.close()
    assert model is not None
    out: dict[str, Any] = {}
    load = next(iter(model.loads.values()))
    code = next(iter(model.linecodes.values()))
    for label, changed in (
        (
            "load.kw=None",
            dataclasses.replace(
                model, loads={**model.loads, load.name: dataclasses.replace(load, kw=None)}
            ),
        ),  # type: ignore[arg-type]
        (
            "linecode.r_matrix=()",
            dataclasses.replace(
                model,
                linecodes={**model.linecodes, code.name: dataclasses.replace(code, r_matrix=())},
            ),
        ),
    ):
        try:
            _text, diags = write_dss(changed)
            out[label] = {"raised": None, "diagnostics": [f"{d.block}: {d.message}" for d in diags]}
        except Exception as err:
            out[label] = {"raised": f"{type(err).__name__}: {err}"}
    return out


def run() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, case in CASES.items():
        lines = HEAD + case["body"] + TAIL
        out[name] = {
            "by_command": _by_command(lines, case["read"]),
            "by_compile": _by_compile(lines),
        }
    out["writer_with_none"] = writer_with_none()
    return out


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=1, default=str))
