"""M4 spike f: where the combined run puts time points around a PWM falling edge."""

from __future__ import annotations

import numpy as np

from core.blocks.engine import ExecutionEngine
from core.blocks.library import builtin_blocks
from core.physical.cosim import CoSimulator, merge
from tests.fixtures.physical.library import new_diagram, wire

T = 1 / 20e3


def main() -> None:
    d, reg = new_diagram()
    d.add_block("ElectricalReference", block_id="eref")
    d.add_block("SolverConfiguration", block_id="solver", settings={"Ts": 100e-6})
    d.add_block("PWMGenerator", block_id="pwm", settings={"amplitude": 5.0, "initial": 0.3})
    d.add_block("Resistor", block_id="load")
    d.add_block("Constant", block_id="duty", settings={"value": 0.3})
    wire(d, "solver.port", "eref.G")
    wire(d, "pwm.ref", "eref.G")
    wire(d, "pwm.out", "load.p")
    wire(d, "load.n", "eref.G")
    d.add_wire("duty", "y", "pwm", "duty")
    engine = ExecutionEngine(builtin_blocks(), reg)
    engine.compile(d)
    cosim = CoSimulator(engine, reg)
    calls: list[float] = []
    run = None
    from core.sim.ngspice_adapter import NgspiceAdapter

    adapter = NgspiceAdapter(sync=True)
    orig = adapter.set_breakpoint

    def spy(t: float) -> bool:
        calls.append(t)
        return orig(t)

    adapter.set_breakpoint = spy  # type: ignore[method-assign]
    cosim.adapter = adapter
    run = cosim.run(0.3e-3, 100e-6)
    print("crossings", run.mapped.crossings)
    print("breakpoints", [f"{c / T:.6f}" for c in calls])
    res = merge(run)
    t = np.asarray(res.vectors["time"])
    g = np.asarray(res.vectors["pwm.out.v"])
    for i in range(len(t)):
        if 0.25 * T < t[i] < 0.35 * T or 1.25 * T < t[i] < 1.35 * T:
            print(f"{t[i] / T:.9f} {g[i]:.3f}")


if __name__ == "__main__":
    main()
