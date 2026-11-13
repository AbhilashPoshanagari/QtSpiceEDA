"""Spike 1: DSS-Python installs and solves IEEE 13 on this OS.

Prints the package and engine versions, then compiles IEEE13Nodeckt.dss in a new
context and prints converged, iterations, total power and the voltages at bus 671.

Run:  python scratch/phase4/spike1_install.py
"""

from __future__ import annotations

import cmath

import common


def bus_voltages(ctx, bus: str) -> list[tuple[str, complex, float]]:
    """(node, complex V line-to-neutral, pu) for each node of `bus`."""
    ckt = ctx.ActiveCircuit
    ckt.SetActiveBus(bus)
    b = ckt.ActiveBus
    v = list(b.Voltages)
    base_v = b.kVBase * 1000.0  # LN base in volts
    out = []
    for i, node in enumerate(b.Nodes):
        c = complex(v[2 * i], v[2 * i + 1])
        out.append((f"{bus}.{node}", c, abs(c) / base_v))
    return out


def main() -> None:
    for key, value in common.versions().items():
        print(f"{key:>10}: {value}")
    ctx = common.new_context()
    r = common.load_and_solve("ieee13", ctx)
    print(
        f"converged: {r.converged}  iterations: {r.iterations}  "
        f"control iterations: {r.control_iterations}"
    )
    print(
        f"total power: {r.total_power_kw:.3f} kW  {r.total_power_kvar:.3f} kvar "
        "(OpenDSS sign: negative = supplied by the source)"
    )
    print(f"losses: {r.losses_kw:.3f} kW  {r.losses_kvar:.3f} kvar")
    print(f"voltage bases: {r.voltage_bases}  base frequency: {r.base_frequency} Hz")
    for node, v, pu in bus_voltages(ctx, "671"):
        mag, ang = cmath.polar(v)
        print(f"  {node}: {mag:9.3f} V  {ang * 180 / cmath.pi:8.3f} deg  {pu:.5f} pu")


if __name__ == "__main__":
    main()
