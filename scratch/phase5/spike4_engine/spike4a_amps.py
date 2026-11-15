"""S4 a) NormAmps / EmergAmps of Lines and LineCodes.

- typed (Lines.NormAmps, LineCodes.NormAmps, CktElement.NormalAmps) vs Properties().Val
- values on IEEE 13/34/123 (none of the files sets a rating: all are OpenDSS defaults)
- survive `save circuit`? Default values and typed non-default values (set on a
  linecode, on a line, before and after linecode=)
"""

from __future__ import annotations

import collections
import tempfile
from pathlib import Path
from typing import Any

from common import FEEDERS, compile_feeder, each, new_context, prop


def read_ratings(ctx: Any) -> dict[str, dict[str, Any]]:
    """Every linecode and line: typed and Properties() ratings."""
    ckt = ctx.ActiveCircuit
    out: dict[str, dict[str, Any]] = {}
    lc = ckt.LineCodes
    for name in each(lc):
        lc.Name = name
        out[f"linecode.{name}"] = {
            "typed": (float(lc.NormAmps), float(lc.EmergAmps)),
            "props": (
                float(prop(ctx, f"linecode.{name}", "normamps")),
                float(prop(ctx, f"linecode.{name}", "emergamps")),
            ),
        }
    ln = ckt.Lines
    for name in each(ln):
        ln.Name = name
        el = ckt.ActiveCktElement
        typed = (float(ln.NormAmps), float(ln.EmergAmps))
        cktel = (float(el.NormalAmps), float(el.EmergAmps))
        out[f"line.{name}"] = {
            "typed": typed,
            "cktelement": cktel,
            "props": (
                float(prop(ctx, f"line.{name}", "normamps")),
                float(prop(ctx, f"line.{name}", "emergamps")),
            ),
            "switch": bool(ln.IsSwitch),
            "linecode": str(ln.LineCode).lower(),
        }
    return out


def set_in_file(ctx: Any, element: str) -> bool:
    """True when the file set normamps on ``element``: ToJSON(0) lists only
    properties that were set (an API extension; no flag means "not full")."""
    ckt = ctx.ActiveCircuit
    cls, _, obj = element.partition(".")
    ckt.SetActiveClass(cls)
    ckt.ActiveClass.Name = obj
    return '"NormAmps"' in str(ckt.ActiveDSSElement.ToJSON(0))


def _save_and_reload(ctx: Any, folder: Path) -> Any:
    ctx.Text.Command = f'save circuit dir="{folder}"'
    master = folder / "Master.dss"
    assert master.is_file(), list(folder.iterdir())
    again = new_context()
    again.Text.Command = f'compile "{master}"'
    return again


def feeder_case(feeder: str) -> dict[str, Any]:
    ctx = compile_feeder(feeder)
    before = read_ratings(ctx)
    marked = {k: set_in_file(ctx, k) for k in before}
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / feeder
        again = _save_and_reload(ctx, folder)
        saved_text = {p.name: p.read_text() for p in folder.glob("*.dss")}
        after = read_ratings(again)
    typed_eq_props = all(v["typed"] == v["props"] for v in before.values())
    typed_eq_cktel = all(
        v["typed"] == v["cktelement"] for v in before.values() if "cktelement" in v
    )
    survived = all(after.get(k, {}).get("typed") == v["typed"] for k, v in before.items())
    counts = collections.Counter((k.split(".")[0], v["typed"]) for k, v in before.items())
    mentions = sum(t.lower().count("normamps") for t in saved_text.values())
    return {
        "linecodes": sum(1 for k in before if k.startswith("linecode.")),
        "lines": sum(1 for k in before if k.startswith("line.")),
        "values": {f"{cls} {na:g}/{ea:g}": n for (cls, (na, ea)), n in sorted(counts.items())},
        "typed_equals_properties": typed_eq_props,
        "typed_equals_cktelement": typed_eq_cktel,
        "survive_save_circuit": survived,
        "saved_files": sorted(saved_text),
        "linecodes_with_normamps_set": sum(
            1 for k, v in marked.items() if v and k.startswith("linecode.")
        ),
        "lines_with_normamps_set": sum(1 for k, v in marked.items() if v and k.startswith("line.")),
        "normamps_words_in_saved_files": mentions,
    }


NON_DEFAULT = """\
Clear
New Circuit.amps basekv=12.47 bus1=src
New Linecode.lc_typed nphases=3 r1=0.1 x1=0.3 r0=0.3 x0=0.9 c1=10 c0=4 units=km
~ normamps=250 emergamps=350
New Linecode.lc_default nphases=3 r1=0.1 x1=0.3 r0=0.3 x0=0.9 c1=10 c0=4 units=km
New Line.inherit bus1=src bus2=b1 linecode=lc_typed length=1 units=km
New Line.after bus1=b1 bus2=b2 linecode=lc_typed normamps=111 emergamps=222 length=1 units=km
New Line.before bus1=b2 bus2=b3 normamps=111 emergamps=222 linecode=lc_typed length=1 units=km
New Line.nocode bus1=b3 bus2=b4 r1=0.1 x1=0.3 length=1 units=km
New Line.nocode_typed bus1=b4 bus2=b5 r1=0.1 x1=0.3 normamps=77 emergamps=88 length=1 units=km
New Line.api bus1=b5 bus2=b6 linecode=lc_default length=1 units=km
New Load.ld bus1=b6 kv=12.47 kw=100 kvar=50
Set VoltageBases=[12.47]
CalcVoltageBases
"""


def non_default_case() -> dict[str, Any]:
    """Typed ratings on a linecode and on lines; does linecode= after normamps= reset
    them (like Units, v0.4 S4)? Does a value set through the API survive save?"""
    ctx = new_context()
    for line in NON_DEFAULT.splitlines():
        ctx.Text.Command = line
    ckt = ctx.ActiveCircuit
    ckt.Lines.Name = "api"
    ckt.Lines.NormAmps = 123.0
    ckt.Lines.EmergAmps = 456.0
    ckt.Solution.Solve()
    before = read_ratings(ctx)
    marked = {k: set_in_file(ctx, k) for k in before}
    with tempfile.TemporaryDirectory() as tmp:
        again = _save_and_reload(ctx, Path(tmp) / "amps")
        after = read_ratings(again)
    rows = {}
    for key, v in before.items():
        rows[key] = {
            "typed": v["typed"],
            "props": v["props"],
            "after_save": after.get(key, {}).get("typed"),
            "set_per_tojson": marked[key],
        }
    return rows


def run() -> dict[str, Any]:
    return {
        "feeders": {f: feeder_case(f) for f in FEEDERS},
        "non_default": non_default_case(),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=1, default=str))
