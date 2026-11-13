"""M4 spike: peaks of the DC-motor PI speed loop for each anti-windup mode."""

from __future__ import annotations

import sys

import numpy as np

import tests.core.physical.test_pi_speed_loop as loop


def main() -> None:
    mode = sys.argv[1]
    if len(sys.argv) > 2:
        loop.W_REF = float(sys.argv[2])
    got = loop._run(mode)
    w, v = np.asarray(got.w), np.asarray(got.v)
    sat = np.count_nonzero(v >= loop.V_MAX - 1e-9) * loop.TS
    print(f"{mode:17s} ref={loop.W_REF} peak={w.max():.3f} end={w[-1]:.3f} sat={sat * 1e3:.1f} ms")


if __name__ == "__main__":
    main()
