"""Spike A: does `set ngbehavior=ltpsa` (sent before loading) or a .spiceinit change
how ngspice reads PSpice/LTspice syntax? Prints a table; not part of the app.

Run:  .venv/Scripts/python.exe scratch/spike_a_compat/compat_spike.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[1] / "tests" / "fixtures" / "lib"

# name -> (deck, vector to read, value expected when the construct is understood)
CASES: dict[str, tuple[str, str, float | None]] = {
    "pspice VSWITCH model": (
        "vswitch\nV1 ctl 0 2\nV2 in 0 1\nR1 in out 1k\nS1 out 0 ctl 0 SMOD\n"
        ".model SMOD VSWITCH(RON=1 ROFF=1MEG VON=1 VOFF=0)\n.op\n",
        "V(out)",
        1 / 1001,
    ),
    "pspice IF() in B-source": (
        "if\nV1 a 0 2\nB1 out 0 V=if(V(a)>1, 5, 0)\nR1 out 0 1k\n.op\n",
        "V(out)",
        5.0,
    ),
    "pspice TABLE in E-source": (
        "table\nV1 a 0 0.5\nE1 out 0 TABLE {V(a)} = (0,0) (1,10)\nR1 out 0 1k\n.op\n",
        "V(out)",
        5.0,
    ),
    "ltspice R=expression": (
        "rexpr\n.param rv=2k\nV1 in 0 1\nR1 in out 1k\nR2 out 0 R={rv}\n.op\n",
        "V(out)",
        2 / 3,
    ),
    "ltspice micro sign value": (
        "micro\nI1 0 out 1µ\nR1 out 0 1k\n.op\n",
        "V(out)",
        1e-3,
    ),
    "ltspice uplim/dnlim": (
        "uplim\nV1 a 0 7\nB1 out 0 V=uplim(V(a), 5, 0.1)\nR1 out 0 1k\n.op\n",
        "V(out)",
        5.0,
    ),
    "tsmc018 LEVEL=49 VERSION=3.1": (
        f".title nmos bias\n.include {FIXTURES / 'tsmc018.lib'}\n"
        "VDD d 0 1.8\nVG g 0 0.9\nM1 d g 0 0 CMOSN W=1u L=0.18u\n.op\n",
        "I(VDD)",
        None,
    ),
}

MODES = ("none", "set", "set_late", "unset", "spiceinit")


def run_case(deck: str, mode: str, vector: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as work:
        deck_file = Path(work) / "deck.cir"
        deck_file.write_text(deck, encoding="utf-8")
        if mode == "spiceinit":
            (Path(work) / ".spiceinit").write_text("set ngbehavior=ltpsa\n")
        proc = subprocess.run(
            [sys.executable, "-I", str(HERE / "probe.py"), str(deck_file), mode, vector],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=60,
        )
    for line in proc.stdout.splitlines():
        if line.startswith("JSON"):
            return json.loads(line[4:])
    return {"stage": "crash", "error": proc.stderr.strip()[-300:]}


def verdict(report: dict[str, object], expected: float | None) -> str:
    if report.get("stage") != "done":
        return f"FAIL@{report.get('stage')}: {str(report.get('error', ''))[:70]}"
    value = report.get("value")
    if value is None:
        return "ran, vector missing"
    if expected is None:
        return f"ok ({value:.4g})"
    ok = abs(value - expected) <= 1e-3 * max(1.0, abs(expected))
    return f"{'ok' if ok else 'WRONG'} ({value:.4g})"


def main() -> None:
    reports = {}
    for name, (deck, vector, expected) in CASES.items():
        row = []
        for mode in MODES:
            report = run_case(deck, mode, vector)
            reports[(name, mode)] = report
            row.append(verdict(report, expected))
        print(
            f"{name:32s} | "
            + " | ".join(f"{mode}: {r}" for mode, r in zip(MODES, row, strict=True))
        )
    print()
    for mode in MODES:
        print(mode, "compat notes:", reports[(next(iter(CASES)), mode)].get("compat_note"))
    if "-v" in sys.argv:
        for key, report in reports.items():
            print(key, report)


if __name__ == "__main__":
    main()
