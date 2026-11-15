"""S3 spike: evaluate the auto-layout on IEEE 13, 34 and 123 -> results.json + PNGs.

    python scratch/phase5/spike3_layout/run_all.py

1. "auto": every coordinate removed, tree_layout in each variant (median time of
   RUNS runs; also 8 copies of IEEE 123 under one root, 1057 buses).
2. "mixed": file coordinates kept, place_missing for the rest: IEEE 123 as it is
   (300_open, 94_open, plus the buses that share a position), and IEEE 34 / 123 with
   30 % of the coordinates removed at random (seed 1).
The original layout is the file's coordinates scaled to diagram units (the median
branch becomes TARGET_LEN); buses without coordinates are left out of it.
"""

from __future__ import annotations

import json
import platform
import random
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from feeders import FEEDERS, build_graph, file_coordinates, load_model, measure, replicate  # noqa: E402
from layout import VARIANTS, place_missing, scale_coordinates, tree_layout  # noqa: E402

RUNS = 50


def timed(fn, runs: int) -> tuple[object, float]:
    out = None
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t)
    return out, statistics.median(times) * 1e3


def metrics_dict(m) -> dict:
    d = asdict(m)
    d.pop("crossing_points")
    return d


def main() -> None:
    import PySide6
    import render

    results: dict = {
        "machine": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "pyside6": PySide6.__version__,
        "runs": RUNS,
        "feeders": {},
    }
    for feeder in FEEDERS:
        model = load_model(feeder)
        g = build_graph(model)
        coords = file_coordinates(model)
        orig = scale_coordinates(g, coords)
        r: dict = {"original": metrics_dict(measure(g, orig)), "auto": {}, "mixed": {}}
        r["original"]["buses_without_coordinates"] = sorted(set(g.buses) - set(coords))
        layouts = {}
        for v in VARIANTS:
            pos, ms = timed(lambda v=v, g=g: tree_layout(g, v), RUNS)
            layouts[v] = pos
            r["auto"][v] = {"ms": round(ms, 3), **metrics_dict(measure(g, pos))}
        _, ms_measure = timed(lambda g=g, p=layouts["packed"]: measure(g, p), 5)
        r["measure_ms"] = round(ms_measure, 2)

        if feeder == "ieee123":
            big = replicate(g, 8)
            r["auto_8x"] = {}
            for v in VARIANTS:
                pos, ms = timed(lambda v=v, big=big: tree_layout(big, v), 10)
                m = measure(big, pos)
                r["auto_8x"][v] = {
                    "ms": round(ms, 2),
                    "buses": m.buses,
                    "crossings": m.crossings,
                    "overlaps": m.overlaps,
                    "through": m.through,
                    "max_len": m.max_len,
                }
            (pos, placed), ms = timed(lambda g=g, f=orig: place_missing(g, f), 3)
            r["mixed"]["as_is"] = {
                "ms": round(ms, 1),
                "placed": placed,
                **metrics_dict(measure(g, pos)),
            }
            render.save(
                HERE / "ieee123_mixed.png",
                [
                    render.Panel(
                        "IEEE 123 file coordinates (300_open, 94_open not drawn)", g, orig
                    ),
                    render.Panel("IEEE 123 mixed: place_missing (magenta)", g, pos, placed),
                ],
            )

        if feeder in ("ieee34", "ieee123"):
            rnd = random.Random(1)
            keep = sorted(rnd.sample(sorted(coords), round(len(coords) * 0.7)))
            if g.source not in keep:
                keep.append(g.source)
            part = scale_coordinates(g, {b: coords[b] for b in keep})
            (pos, placed), ms = timed(lambda g=g, f=part: place_missing(g, f), 3)
            r["mixed"]["30pct_removed"] = {
                "ms": round(ms, 1),
                "placed": placed,
                **metrics_dict(measure(g, pos)),
            }
            render.save(
                HERE / f"{feeder}_mixed30.png",
                [
                    render.Panel(f"{feeder.upper()} file coordinates", g, orig),
                    render.Panel(
                        f"{feeder.upper()} 30 % removed, place_missing (magenta)", g, pos, placed
                    ),
                ],
            )

        labels = feeder != "ieee123"
        render.save(
            HERE / f"{feeder}_original_vs_auto.png",
            [
                render.Panel(f"{feeder.upper()} file coordinates", g, orig),
                render.Panel(f"{feeder.upper()} auto: leaf-trunk", g, layouts["leaf-trunk"]),
                render.Panel(f"{feeder.upper()} auto: packed", g, layouts["packed"]),
                render.Panel(f"{feeder.upper()} auto: packed-mid", g, layouts["packed-mid"]),
            ],
            labels=labels,
        )
        results["feeders"][feeder] = r
        print(feeder, json.dumps({k: r[k] for k in r if k != "original"}, default=str)[:2000])

    (HERE / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
