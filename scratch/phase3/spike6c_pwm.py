"""M4 spike c: a smooth precursor makes ngspice's step control land near the edge.

An extra B current 1/(1 + ((V(d)-V(c))/w)^2) charges a capacitor. Its curvature grows
as duty - carrier -> 0, so the truncation-error control shortens the step before the
comparator edge, without any breakpoint.
"""

from __future__ import annotations

import time

import numpy as np

from core.sim.ngspice_adapter import NgspiceAdapter
from scratch.phase3.spike6_pwm import edges

F = 20e3
T = 1 / F
TF = 1e-9


def deck(duty: float, w: float, cap: float, tmax: str) -> str:
    return f"""pwm spike c
Vd d 0 dc {duty}
Vc c 0 pulse(0 1 0 {T - TF!r} {TF!r} 0 {T!r})
B1 g 0 v=(V(d) > V(c)) ? 5 : 0
R1 g 0 1k
B2 0 q i=1/(1+((V(d)-V(c))/{w!r})^2)
C2 q 0 {cap!r}
R2 q 0 1
.tran 1u 10m 0 {tmax}
.end
"""


def main() -> None:
    sim = NgspiceAdapter()
    assert sim.available(), sim.last_error
    for duty in (0.3, 0.75, 0.02, 0.98):
        for w in (1e-2, 1e-3, 1e-4):
            for cap in (1e-6,):
                sim.load(deck(duty, w, cap, "10u"))
                t0 = time.perf_counter()
                sim.run()
                dt = time.perf_counter() - t0
                res = sim.results()
                t = np.asarray(res.vectors["time"]).real
                g = np.asarray(
                    res.vectors[next(k for k in res.vectors if k.lower() in ("g", "v(g)"))]
                ).real
                r, f = edges(t, g, 2.5)
                er = np.abs(r - np.round(r / T) * T)
                ef = np.abs(f - (np.floor(f / T) * T + duty * T))
                print(
                    f"d={duty} w={w:g} C={cap:g} pts={len(t):6d} {dt:5.2f}s "
                    f"rise n={len(r)} max={er.max() * 1e9:7.1f} ns  "
                    f"fall n={len(f)} max={ef.max() * 1e9:7.1f} ns"
                )


if __name__ == "__main__":
    main()
