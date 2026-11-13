"""Write tests/fixtures/grid/<feeder>/reference/published.json from the published IEEE
PES test feeder report in the same folder (the .doc from feederNN.zip, unchanged).

The report is read with antiword (Git for Windows ships it: mingw64/bin/antiword.exe;
on Linux/macOS install the antiword package). Two tables are taken from it:

- "V O L T A G E   P R O F I L E": per node and phase |V| in pu and the angle in
  degrees (A-N, B-N, C-N columns), plus the distance to the source in miles;
- "VOLTAGE REGULATOR DATA": per regulator its node, name and published tap per phase.

Names are kept as published (upper case, e.g. RG60, XFXFM1); the benchmark test maps
them to OpenDSS buses. Nobody types the numbers: re-run only on purpose:
    python scratch/phase4/make_published_reference.py [--antiword PATH]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "grid"
FEEDERS = ("ieee13", "ieee34", "ieee123")

_CELL = re.compile(r"^\s*([\d.]+)\s+at\s+(-?[\d.]+)\s*$")
_REG_HEAD = re.compile(r"^\s*(\S+)\s+(RG\S*)\s+(\S+)\s+(\S+)\s+(.+?)\s{2,}(\S+)\s+([\d.]+)\s*$")


def _report(folder: Path) -> Path:
    docs = sorted(folder.glob("*.doc"))
    if len(docs) != 1:
        raise SystemExit(f"{folder}: expected one .doc report, found {[d.name for d in docs]}")
    return docs[0]


def parse(text: str) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    regulators: list[dict[str, Any]] = []
    section = ""
    reg: dict[str, Any] | None = None
    for line in text.splitlines():
        if "V O L T A G E   P R O F I L E" in line:
            section = "profile"
            continue
        if "VOLTAGE REGULATOR DATA" in line:
            section = "regulator"
            continue
        if re.search(r"R A D I A L|S U M M A R Y", line):
            section = ""
            continue
        if section == "profile":
            parts = line.split("|")
            if len(parts) != 5 or parts[0].strip() in ("", "NODE") or "_" in parts[0]:
                continue
            name = parts[0].strip()
            phases: dict[str, list[float]] = {}
            for phase, cell in zip(("1", "2", "3"), parts[1:4], strict=True):
                m = _CELL.match(cell)
                if m:
                    phases[phase] = [float(m.group(1)), float(m.group(2))]
                elif cell.strip():
                    raise SystemExit(f"cannot read cell {cell!r} in line {line!r}")
            if name in nodes:
                raise SystemExit(f"node {name} appears twice")
            nodes[name] = {"phases": phases, "miles": float(parts[4])}
        elif section == "regulator":
            m = _REG_HEAD.match(line)
            if m:
                reg = {
                    "node": m.group(1),
                    "name": m.group(2),
                    "segment_to": m.group(4),
                    "model": m.group(5).strip(),
                    "taps": {},
                }
                regulators.append(reg)
                continue
            tokens = line.split()
            if reg is not None and len(tokens) >= 7 and tokens[0] in ("1", "2", "3"):
                reg["taps"][tokens[0]] = int(tokens[-1])
    return {"nodes": nodes, "regulators": regulators}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--antiword", default=shutil.which("antiword") or "antiword")
    args = ap.parse_args()
    banner = subprocess.run([args.antiword], capture_output=True, text=True, check=False)
    version = [v for v in (banner.stdout + banner.stderr).splitlines() if v.strip()]
    for feeder in FEEDERS:
        folder = FIXTURES / feeder / "reference"
        doc = _report(folder)
        text = subprocess.run(
            [args.antiword, "-w", "0", str(doc)],
            capture_output=True,
            text=True,
            encoding="latin-1",
            check=True,
        ).stdout
        data = {
            "about": {
                "what": "published IEEE PES test feeder solution (voltage profile, "
                "regulator taps), read from the report in this folder",
                "source": "https://cmte.ieee.org/pes-testfeeders/resources/ "
                f"({'feeder' + feeder[4:]}.zip)",
                "report": doc.name,
                "report_sha256": hashlib.sha256(doc.read_bytes()).hexdigest(),
                "made_by": "scratch/phase4/make_published_reference.py",
                "reader": "; ".join(v.strip() for v in version[:3]),
                "date": datetime.date.today().isoformat(),
                "units": "|V| in pu, angle in degrees (line-to-neutral), miles to source",
            },
            "feeder": feeder,
            **parse(text),
        }
        if not data["nodes"]:
            raise SystemExit(f"{doc}: no voltage profile found")
        out = folder / "published.json"
        out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
        n = sum(len(v["phases"]) for v in data["nodes"].values())
        print(
            f"{feeder}: {len(data['nodes'])} buses, {n} nodes, "
            f"{len(data['regulators'])} regulators -> {out}"
        )


if __name__ == "__main__":
    main()
