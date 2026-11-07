"""Spike 4: a translational mass-spring-damper built by hand as R, L and C.

Mapping from the "Physical domains" table in CLAUDE.md (translational row):
  across v [m/s] -> V,  through F [N] -> A
  mass m          -> C = m        (one-port, node to reference)
  damper b        -> R = 1/b
  spring k        -> L = 1/k      (inductor current = spring force = k*x)
  force source    -> current source pushing F into the mass node
  reference       -> node 0 (0 m/s)
Position is made inside the network by integrating velocity: a G source feeds
V(v) as a current into a 1 F capacitor, so V(x) = integral of v dt.

m*dv/dt + b*v + k*x = F  ->  underdamped step response, compared with the analytic
x(t) and v(t). Runs through the app's normal NgspiceAdapter (plain .tran).

In v0.3 this table lives only in core/physical/domains.py (rule 12); it is written
out here because this is a hand-built spike.

Run:  python scratch/phase3/spike4_msd.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from core.sim.ngspice_adapter import NgspiceAdapter

M, K, B, F0 = 2.0, 200.0, 8.0, 10.0  # kg, N/m, N*s/m, N  -> wn = 10 rad/s, zeta = 0.2
T_STOP = 3.0
T_MAX = 0.5e-3

DECK = """spike4 mass-spring-damper as RLC
* force source: F into the mass node v (through variable, positive into port A)
IF 0 v pulse(0 {f0} 0 1n 1n 100 200)
* mass m -> C to the reference
Cm v 0 {c}
* damper b -> R = 1/b
Rb v 0 {r}
* spring k -> L = 1/k
Lk v 0 {l}
* position x = integral of v: G source charges a 1 F capacitor
Gx 0 x v 0 1
Cx x 0 1
Rx x 0 1e12
.tran {tmax} {tstop} 0 {tmax}
.end
"""


@dataclass
class Spike4Result:
    wn: float
    zeta: float
    max_err_x: float  # max |x - analytic| / (F0/k)
    max_err_v: float  # max |v - analytic| / peak |v|
    x_final: float
    x_ss_analytic: float
    peak_time: float  # first peak of x
    peak_time_analytic: float
    points: int


def analytic(t: np.ndarray, m: float, k: float, b: float, f0: float):
    wn = math.sqrt(k / m)
    zeta = b / (2 * math.sqrt(k * m))
    wd = wn * math.sqrt(1 - zeta**2)
    decay = np.exp(-zeta * wn * t)
    x = f0 / k * (1 - decay * (np.cos(wd * t) + zeta / math.sqrt(1 - zeta**2) * np.sin(wd * t)))
    v = f0 / k * wn / math.sqrt(1 - zeta**2) * decay * np.sin(wd * t)
    return x, v, wn, zeta, wd


def deck(m: float = M, k: float = K, b: float = B, f0: float = F0) -> str:
    return DECK.format(f0=f0, c=m, r=1 / b, l=1 / k, tmax=T_MAX, tstop=T_STOP)


def run(m: float = M, k: float = K, b: float = B, f0: float = F0) -> Spike4Result:
    ad = NgspiceAdapter()
    if not ad.available():
        raise RuntimeError(ad.last_error)
    ad.load(deck(m, k, b, f0))
    ad.run()
    res = ad.results()
    t = res.vectors["time"]
    x = res.vectors["V(x)"]
    v = res.vectors["V(v)"]
    xa, va, wn, zeta, wd = analytic(t, m, k, b, f0)
    i_peak = int(np.argmax(x[t < 2 * math.pi / wd]))
    return Spike4Result(
        wn=wn,
        zeta=zeta,
        max_err_x=float(np.max(np.abs(x - xa)) / (f0 / k)),
        max_err_v=float(np.max(np.abs(v - va)) / np.max(np.abs(va))),
        x_final=float(x[-1]),
        x_ss_analytic=f0 / k,
        peak_time=float(t[i_peak]),
        peak_time_analytic=math.pi / wd,
        points=len(t),
    )


def main() -> None:
    r = run()
    print(f"wn = {r.wn:.3f} rad/s, zeta = {r.zeta:.3f}, {r.points} points")
    print(f"max |x - analytic| = {r.max_err_x * 100:.4f} % of F0/k")
    print(f"max |v - analytic| = {r.max_err_v * 100:.4f} % of peak v")
    print(f"x(3 s) = {r.x_final:.6f} m (analytic steady state {r.x_ss_analytic:.6f} m)")
    print(f"first peak at {r.peak_time:.5f} s (analytic pi/wd = {r.peak_time_analytic:.5f} s)")


if __name__ == "__main__":
    main()
