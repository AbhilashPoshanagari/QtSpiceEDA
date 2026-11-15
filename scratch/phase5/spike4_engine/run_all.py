"""S4 spike: every measurement -> results.json. `python scratch/phase5/spike4_engine/run_all.py`
(needs `.[grid]`). Regenerate tests/fixtures/grid/sequence_reference.json first with
scratch/phase5/make_sequence_reference.py if dss-python changes."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import spike4a_amps  # noqa: E402
import spike4b_voltage_bases  # noqa: E402
import spike4c_sequence  # noqa: E402
import spike4d_loading  # noqa: E402
import spike4e_compile_errors  # noqa: E402
from common import versions  # noqa: E402


def main() -> None:
    out: dict[str, object] = {"versions": versions()}
    for key, mod in (
        ("a_amps", spike4a_amps),
        ("b_voltage_bases", spike4b_voltage_bases),
        ("c_sequence", spike4c_sequence),
        ("d_loading", spike4d_loading),
        ("e_compile_errors", spike4e_compile_errors),
    ):
        t0 = time.perf_counter()
        out[key] = mod.run()
        print(f"{key}: {time.perf_counter() - t0:.1f} s", file=sys.stderr)
    (HERE / "results.json").write_text(
        json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8"
    )
    print(f"wrote {HERE / 'results.json'}", file=sys.stderr)


if __name__ == "__main__":
    main()
