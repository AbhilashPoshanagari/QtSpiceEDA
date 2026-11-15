"""S1 spike: run bench.py for every variant and size, each in a fresh process, and
write results.json plus the Markdown tables used in README.md.

    python scratch/phase5/spike1_canvas/run_all.py

Opens a window per run (about 16 s each); keep it visible and unobstructed, a hidden or
minimised window renders no frames.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

CONFIGS = [(variant, copies) for variant in ("a", "b", "b-curve") for copies in (1, 4, 8)]


def run(variant: str, copies: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "r.json"
        cmd = [sys.executable, str(HERE / "bench.py"), "--copies", str(copies), "--out", str(out)]
        cmd += ["--variant", variant[0]] + (["--curve"] if variant.endswith("curve") else [])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"{variant} x{copies} failed:\n{proc.stderr[-2000:]}")
        result = json.loads(out.read_text(encoding="utf-8"))
        result["qt_log"] = [
            line
            for line in proc.stderr.splitlines()
            if "render loop" in line
            or "backend" in line
            or "using this adapter" in line
            or line.startswith("qt.rhi.general: Adapter 0")
        ]
        return result


def table(results: list[dict]) -> str:
    head = (
        "| variant | copies | buses | items | first frame s | pan/zoom fps (p95 ms) | "
        "SG ms p50/p95/max | drag fps | drag slot ms | Shape polish ms | settle ms | "
        "click handled / to frame ms | clicks right | WS MB before/after/end |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    rows = []
    for r in results:
        pz, dr, cl, mem = r["panzoom"], r["drag"], r["click"], r["memory_mb"]
        polish = dr.get("shape_polish_ms_mean")
        rows.append(
            f"| {r['variant']} | {r['copies']} | {r['counts']['buses']} | "
            f"{r['quick_items_content']} | {r['first_frame_s']['load_to_first_frame']} | "
            f"{pz['fps']} ({pz['p95_ms']}) | "
            f"{pz['sg_ms_p50']}/{pz['sg_ms_p95']}/{pz['sg_ms_max']} | "
            f"{dr['fps']} | {dr['slot_ms_mean']} | "
            f"{'-' if polish is None else f'{polish} (max {dr["shape_polish_ms_max"]})'} | "
            f"{r['settle_ms']['to_4x_fit']} | "
            f"{cl['event_handled_ms_median']} / {cl['to_next_frame_ms_median']} | "
            f"{cl['selected_the_clicked_bus']}/{cl['clicks']} | "
            f"{mem['before_qml']['ws']}/{mem['after_first_frame']['ws']}/{mem['end']['ws']} |"
        )
    return head + "\n".join(rows) + "\n"


def main() -> int:
    results = []
    for variant, copies in CONFIGS:
        print(f"running {variant} x{copies} ...", flush=True)
        results.append(run(variant, copies))
    import PySide6

    meta = {
        "python": sys.version.split()[0],
        "pyside6": PySide6.__version__,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "qt_log": results[0]["qt_log"],
        "screen": results[0]["screen"],
    }
    try:
        import dss

        meta["dss_python"] = dss.__version__
    except ImportError:
        pass
    (HERE / "results.json").write_text(
        json.dumps({"meta": meta, "runs": results}, indent=1), encoding="utf-8"
    )
    print(json.dumps(meta, indent=1))
    print(table(results))
    hits = [(r["variant"], r["copies"], r["hit_test"]) for r in results]
    for v, c, h in hits:
        print(v, c, h)
    for r in results:
        if r["click"]["misses"]:
            print(r["variant"], r["copies"], "misses:", r["click"]["misses"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
