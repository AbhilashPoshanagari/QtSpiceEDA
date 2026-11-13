"""Spike 5: does a German process locale (decimal comma) break DSS-Python?

Modes (each in a fresh spawned process, so the locale never leaks between them):
  "c"    : locale untouched
  "de"   : setlocale(LC_ALL, de_DE) before the engine is loaded
  "de+c" : setlocale(LC_ALL, de_DE), then setlocale(LC_NUMERIC, "C")

In each mode: a small circuit is built from DSS commands containing "1.5" values;
the values are read back through the typed API and as Properties() strings and
`? load.l1.kW`; `save circuit` writes DSS files whose numbers are checked for a
decimal comma; and IEEE 13 is compiled and compared with its reference file.

Run:  python scratch/phase4/spike5_locale.py
"""

from __future__ import annotations

import locale
import multiprocessing as mp
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import common

GERMAN_NAMES = ("de_DE.UTF-8", "de_DE.utf8", "de_DE", "German_Germany.1252", "de-DE")

SMALL = (
    "clear",
    "new circuit.loc basekv=12.47 pu=1.05 phases=3 bus1=src",
    "new line.l1 bus1=src bus2=b phases=3 length=1.5 units=km r1=0.25 x1=0.5 r0=0.75 x0=1.5",
    "new load.l1 bus1=b phases=3 kv=12.47 kw=1.5 kvar=0.5 model=1",
    "set voltagebases=[12.47]",
    "calcv",
    "solve",
)


@dataclass
class Spike5Result:
    mode: str
    platform: str
    locale_name: str  # the German locale setlocale accepted ("" if none)
    decimal_point: str  # Python's localeconv() after the mode's setlocale calls
    load_kw_typed: float = 0.0
    line_length_typed: float = 0.0
    load_kw_props: str = ""
    load_kw_query: str = ""
    saved_numbers_with_comma: list[str] = field(default_factory=list)
    saved_load_line: str = ""
    ieee13_max_dev: float = -1.0
    error: str = ""


def set_german() -> str:
    for name in GERMAN_NAMES:
        try:
            locale.setlocale(locale.LC_ALL, name)
            return name
        except locale.Error:
            continue
    return ""


def run(mode: str) -> Spike5Result:
    """Run in a fresh process: `dss` must not be loaded before the locale is set."""
    res = Spike5Result(mode=mode, platform=sys.platform, locale_name="", decimal_point="")
    if mode.startswith("de"):
        res.locale_name = set_german()
        if not res.locale_name:
            res.error = "no German locale installed"
            return res
    if mode == "de+c":
        locale.setlocale(locale.LC_NUMERIC, "C")
    res.decimal_point = locale.localeconv()["decimal_point"]
    try:
        ctx = common.new_context()
        for cmd in SMALL:
            common.command(ctx, cmd)
        ckt = ctx.ActiveCircuit
        ckt.Loads.Name = "l1"
        res.load_kw_typed = float(ckt.Loads.kW)
        ckt.Lines.Name = "l1"
        res.line_length_typed = float(ckt.Lines.Length)
        ckt.SetActiveElement("Load.l1")
        res.load_kw_props = ckt.ActiveDSSElement.Properties("kW").Val
        res.load_kw_query = common.command(ctx, "? load.l1.kW")
        with tempfile.TemporaryDirectory() as tmp:
            common.command(ctx, f'save circuit dir="{tmp}"')
            text = "\n".join(
                p.read_text(errors="replace") for p in Path(tmp).rglob("*") if p.is_file()
            )
        res.saved_numbers_with_comma = sorted(set(re.findall(r"=\s*-?\d+,\d+", text)))
        res.saved_load_line = next(
            (
                ln.strip()
                for ln in text.splitlines()
                if ln.lower().replace('"', "").startswith("new load.l1")
            ),
            "",
        )
        r13 = common.load_and_solve("ieee13", common.new_context())
        res.ieee13_max_dev = common.compare_to_reference(r13, common.load_reference("ieee13"))
    except Exception as exc:  # report, never hang the parent
        res.error = repr(exc)
    return res


def _child(conn, mode: str) -> None:
    conn.send(run(mode))
    conn.close()


def run_fresh(mode: str) -> Spike5Result:
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child, mode), daemon=True)
    proc.start()
    child.close()
    if not parent.poll(120):
        proc.terminate()
        raise TimeoutError(mode)
    res = parent.recv()
    proc.join(10)
    return res


def main() -> None:
    for mode in ("c", "de", "de+c"):
        r = run_fresh(mode)
        print(
            f"{r.platform} mode={mode:5} locale={r.locale_name or '-':20} "
            f"decimal_point={r.decimal_point!r}"
        )
        if r.error:
            print(f"   error: {r.error}")
            continue
        print(
            f"   read: Loads.kW={r.load_kw_typed} Lines.Length={r.line_length_typed} "
            f"Properties('kW')={r.load_kw_props!r} '? load.l1.kW'={r.load_kw_query!r}"
        )
        print(f"   save circuit: {r.saved_load_line}")
        print(f"   numbers written with a comma: {r.saved_numbers_with_comma or 'none'}")
        print(f"   IEEE 13 max dev from reference: {r.ieee13_max_dev:.1e} pu")


if __name__ == "__main__":
    main()
