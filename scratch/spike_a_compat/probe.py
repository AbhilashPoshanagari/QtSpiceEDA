"""Spike A child process: load one deck in one compat setup and print a JSON report.

Run by compat_spike.py, one fresh process per case, because ngspice reads
.spiceinit only once (at ngSpice_Init) and keeps its state process-wide.
Usage: python probe.py <deck-file> <mode: none|set|set_late|unset|spiceinit> <vector>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.sim.backend import SimulationError  # noqa: E402
from core.sim.ngspice_adapter import NgspiceAdapter  # noqa: E402

PLAIN = "plain\nV1 1 0 1\nR1 1 0 1k\n.op\n"


def main() -> None:
    deck = Path(sys.argv[1]).read_text(encoding="utf-8")
    mode, vector = sys.argv[2], sys.argv[3]
    adapter = NgspiceAdapter()
    report: dict[str, object] = {"available": adapter.available()}
    if mode == "set":
        adapter._command("set ngbehavior=ltpsa")
    elif mode == "set_late":  # a plain circuit was already loaded in this session
        adapter.load(PLAIN)
        adapter.run()
        adapter._command("set ngbehavior=ltpsa")
    elif mode == "unset":  # mode can be switched off again
        adapter._command("set ngbehavior=ltpsa")
        adapter.load(PLAIN)
        adapter._command("unset ngbehavior")
    stage = "load"
    try:
        adapter.load(deck)
        stage = "run"
        adapter.run()
        stage = "done"
        result = adapter.results()
        if result is not None:
            report["vectors"] = list(result.vectors)
            for name, values in result.vectors.items():
                if name.lower() == vector.lower():
                    report["value"] = float(values[-1].real)
    except SimulationError as exc:
        report["error"] = str(exc)
    report["stage"] = stage
    report["compat_note"] = [s for s in adapter.console if "compatib" in s.lower()]
    report["warnings"] = [s for s in adapter.console if s.lower().startswith(("warning", "error"))][
        :4
    ]
    print("JSON" + json.dumps(report))


if __name__ == "__main__":
    main()
