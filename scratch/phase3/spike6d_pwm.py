"""M4 spike d: ngspice's voltage-controlled switch as the comparator.

S1 closes while V(d) - V(c) > vt. ngspice's switch limits the time step as its
control approaches the threshold, so the edge lands close to the crossing.
"""

from __future__ import annotations

import time

import numpy as np

from core.sim.ngspice_adapter import NgspiceAdapter
from scratch.phase3.spike6_pwm import edges

F = 20e3
T = 1 / F
TF = 1e-9


def deck(duty: float, vh: float, tmax: str, tstop: str = "10m") -> str:
    return f"""pwm spike d
Vd d 0 dc {duty}
Vc c 0 pulse(0 1 0 {T - TF!r} {TF!r} 0 {T!r})
V5 h 0 5
S1 h g d c swm
R1 g 0 1e3
.model swm sw vt=0 vh={vh!r} ron=1e-3 roff=1e12
.tran 1u {tstop} 0 {tmax}
.end
"""


def main() -> None:
    sim = NgspiceAdapter()
    assert sim.available(), sim.last_error
    for duty in (0.3, 0.75, 0.02, 0.98):
        for vh in (0.0, 1e-4):
            for tmax in ("10u", "100u"):
                sim.load(deck(duty, vh, tmax))
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
                    f"d={duty} vh={vh:g} tmax={tmax} pts={len(t):6d} {dt:5.2f}s "
                    f"rise n={len(r)} max={er.max() * 1e9:7.1f} ns  "
                    f"fall n={len(f)} max={ef.max() * 1e9:7.1f} ns"
                )


if __name__ == "__main__":
    main()
