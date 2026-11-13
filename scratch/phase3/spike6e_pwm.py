"""M4 spike e: edge error and cost when ngspice's step is capped (plain mode)."""

from __future__ import annotations

import time

import numpy as np

from core.sim.ngspice_adapter import NgspiceAdapter
from scratch.phase3.spike6_pwm import FORMS, deck, edges

T = 1 / 20e3


def main() -> None:
    sim = NgspiceAdapter()
    assert sim.available(), sim.last_error
    for tmax in ("2.5u", "1u", "500n", "250n"):
        worst = 0.0
        pts = 0
        t_run = 0.0
        for duty in (0.02, 0.3, 0.5, 0.75, 0.98):
            sim.load(deck(duty, FORMS["step"], tmax))
            t0 = time.perf_counter()
            sim.run()
            t_run += time.perf_counter() - t0
            res = sim.results()
            t = np.asarray(res.vectors["time"]).real
            g = np.asarray(
                res.vectors[next(k for k in res.vectors if k.lower() in ("g", "v(g)"))]
            ).real
            _, f = edges(t, g, 2.5)
            worst = max(worst, float(np.abs(f - (np.floor(f / T) * T + duty * T)).max()))
            pts += len(t)
        print(
            f"tmax={tmax:5s} worst fall err={worst * 1e9:7.1f} ns  "
            f"pts/ms={pts / 5:8.0f}  {t_run / 5 * 1000:6.1f} ms per 1 ms"
        )


if __name__ == "__main__":
    main()
