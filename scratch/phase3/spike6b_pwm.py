"""M4 spike b: let ngspice's truncation-error control find the comparator edge.

Comparator (band form) -> R -> C; the gate is the capacitor voltage. The step
control sees the fast change on the capacitor and shortens the step there.
"""

from __future__ import annotations

import time

import numpy as np

from core.sim.ngspice_adapter import NgspiceAdapter
from scratch.phase3.spike6_pwm import edges

F = 20e3
T = 1 / F
TF = 1e-9


def deck(duty: float, band: float, tau: float, tmax: str) -> str:
    comp = f"5*min(max((V(d)-V(c))/{band!r}+0.5,0),1)"
    return f"""pwm spike b
Vd d 0 dc {duty}
Vc c 0 pulse(0 1 0 {T - TF!r} {TF!r} 0 {T!r})
B1 x 0 v={comp}
R1 x g 1
C1 g 0 {tau!r}
.tran 1u 10m 0 {tmax}
.end
"""


def main() -> None:
    sim = NgspiceAdapter()
    assert sim.available(), sim.last_error
    for duty in (0.3, 0.75):
        for band in (1e-2, 1e-3):
            for tau in (1e-9, 10e-9):
                sim.load(deck(duty, band, tau, "10u"))
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
                    f"d={duty} band={band:g} tau={tau:g} pts={len(t):6d} {dt:5.2f}s "
                    f"rise n={len(r)} max={er.max() * 1e9:7.1f} ns  "
                    f"fall n={len(f)} max={ef.max() * 1e9:7.1f} ns"
                )


if __name__ == "__main__":
    main()
