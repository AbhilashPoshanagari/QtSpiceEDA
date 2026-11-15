"""S5 spike: size and save/load time of the .qgrid file for IEEE 13/34/123 -> results.json.

python scratch/phase5/spike5_file/bench.py   (needs .[grid]; about 15 s)
"""

from __future__ import annotations

import gzip
import json
import pickle
import platform
import statistics
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import designfile as df  # noqa: E402
from feeders import FEEDERS, diagram_from_coordinates, extracted  # noqa: E402

REPEATS = 20
INDENTS = {"compact": None, "indent1": 1, "indent2": 2}


def _ms(fn, repeats: int = REPEATS) -> float:
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(times), 3)


def bench_feeder(feeder: str, tmp: Path) -> dict:
    model = extracted(feeder)
    diagram = diagram_from_coordinates(model)
    path = tmp / f"{feeder}.qgrid"
    out: dict = {
        "counts": model.counts(),
        "bus_glyphs": len(diagram.bus_glyphs),
        "element_glyphs": len(diagram.element_glyphs),
        "buses_without_coordinates": sum(b.x is None for b in model.buses.values()),
        "floats_in_model": df.count_floats(model),
        "floats_in_diagram": df.count_floats(diagram),
    }

    # Exactness, every indent: load(save(m)) == m, bit-identical floats, same key order.
    exact = {}
    for label, indent in INDENTS.items():
        assert df.save(path, model, diagram, indent) == []
        m2, d2, diags = df.load(path)
        exact[label] = {
            "equal_model": m2 == model,
            "equal_diagram": d2 == diagram,
            "mismatches": len(df.float_mismatches(model, m2) + df.float_mismatches(diagram, d2)),
            "diagnostics": len(diags),
        }
        # and once more: save(load(save(m))) gives the same bytes
        text1 = path.read_bytes()
        df.save(path, m2, d2, indent)
        exact[label]["same_bytes_on_resave"] = path.read_bytes() == text1
    out["exact"] = exact

    sizes = {}
    for label, indent in INDENTS.items():
        text = df.dumps(model, diagram, indent).encode("utf-8")
        sizes[label] = len(text)
        sizes[label + "_gzip"] = len(gzip.compress(text, 6))
    diagram_only = json.dumps(df.to_plain(diagram), indent=1).encode()
    sizes["diagram_part_indent1"] = len(diagram_only)
    sizes["pickle"] = len(pickle.dumps((model, diagram), protocol=pickle.HIGHEST_PROTOCOL))
    out["bytes"] = sizes

    text = df.dumps(model, diagram, 1)
    doc = df.document(model, diagram)
    times = {
        "to_plain": _ms(lambda: df.document(model, diagram)),
        "json_dumps": _ms(lambda: json.dumps(doc, indent=1, allow_nan=False)),
        "save_file": _ms(lambda: df.save(path, model, diagram, 1)),
        "json_loads": _ms(lambda: json.loads(text)),
        "from_plain_loads": _ms(lambda: df.loads(text)),
        "load_file": _ms(lambda: df.load(path)),
        "save_file_compact": _ms(lambda: df.save(path, model, diagram, None)),
        "pickle_dumps": _ms(lambda: pickle.dumps((model, diagram))),
        "dataclasses_asdict": _ms(lambda: asdict(model)),
    }
    out["ms"] = times
    return out


def main() -> None:
    results: dict = {
        "machine": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor(),
            "repeats": REPEATS,
            "statistic": "median",
        },
        "feeders": {},
    }
    with tempfile.TemporaryDirectory() as tmp:
        for feeder in FEEDERS:
            results["feeders"][feeder] = bench_feeder(feeder, Path(tmp))
            print(feeder, json.dumps(results["feeders"][feeder], indent=1))
    (HERE / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
