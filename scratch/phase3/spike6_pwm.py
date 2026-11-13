"""M4 spike: PWM edges made inside ngspice by comparing a held duty with a carrier.

Plain ngspice (no sync). Measures edge-time error against n/f (rise) and
n/f + d/f (fall) for a few comparator forms and step caps.
"""

from __future__ import annotations

import sys

import numpy as np

from core.sim.ngspice_adapter import NgspiceAdapter

F = 20e3
T = 1 / F
TF = 1e-9  # sawtooth flyback


def deck(duty: float, comparator: str, tmax: str) -> str:
    tr = T - TF
    return f"""pwm spike
Vd d 0 dc {duty}
Vc c 0 pulse(0 1 0 {tr!r} {TF!r} 0 {T!r})
B1 g 0 v={comparator}
R1 g 0 1k
.tran 1u 1m 0 {tmax}
.end
"""


FORMS = {
    "step": "(V(d) > V(c)) ? 5 : 0",
    "band1e-3": "5*min(max((V(d)-V(c))/1e-3+0.5,0),1)",
    "band1e-4": "5*min(max((V(d)-V(c))/1e-4+0.5,0),1)",
}


def edges(t: np.ndarray, g: np.ndarray, level: float) -> tuple[np.ndarray, np.ndarray]:
    above = g > level
    idx = np.nonzero(above[1:] != above[:-1])[0]
    times = []
    for i in idx:
        t0, t1, g0, g1 = t[i], t[i + 1], g[i], g[i + 1]
        times.append(t0 + (level - g0) * (t1 - t0) / (g1 - g0))
    times = np.asarray(times)
    rising = times[above[idx + 1]]
    falling = times[~above[idx + 1]]
    return rising, falling


def main() -> None:
    sim = NgspiceAdapter()
    assert sim.available(), sim.last_error
    for duty in (0.3, 0.75):
        for name, form in FORMS.items():
            for tmax in ("10u", "1u"):
                sim.load(deck(duty, form, tmax))
                sim.run()
                res = sim.results()
                t = np.asarray(res.vectors["time"]).real
                g = np.asarray(
                    res.vectors[next(k for k in res.vectors if k.lower() in ("g", "v(g)"))]
                ).real
                r, f = edges(t, g, 2.5)
                er = np.abs(r - np.round(r / T) * T)
                ef = np.abs(f - (np.floor(f / T) * T + duty * T))
                print(
                    f"d={duty} {name:9s} tmax={tmax:4s} pts={len(t):6d} "
                    f"rise n={len(r)} max_err={er.max() * 1e9:8.1f} ns  "
                    f"fall n={len(f)} max_err={ef.max() * 1e9:8.1f} ns"
                )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
