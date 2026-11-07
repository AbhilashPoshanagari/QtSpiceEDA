"""M1: battery with a state-of-charge table.

Ports ``p`` (+) and ``n`` (-), signal output ``soc`` (0 empty .. 1 full).

    V(p, n) = Voc(soc) - R_int * i_out
    d soc / dt = -i_out / (3600 * capacity)        capacity in A*h

``ocv`` is the open-circuit voltage table as text: ``soc:V`` pairs with soc rising
from 0 to 1, separated by spaces or commas, e.g. ``"0:3.0 0.1:3.45 0.9:4.0 1:4.2"``.
It becomes an ngspice ``pwl()`` of the state of charge; the state of charge is
clamped to the table's range, so an over-discharged battery holds its last voltage.

The state of charge is integrated inside the network (rule 11): a 1 F capacitor on
internal node ``q`` is charged by I(Vi) / (3600 * capacity), so V(q) is the charge
moved since the start and soc = soc0 + V(q). The combined run starts from the
operating point without ``uic``, where the capacitor is open; so the integrating
source is held at 0 while ``time`` is 0 (the operating point) and V(q) starts at 0.
A 1e12 shunt keeps the operating point defined. Real initial conditions are M6.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from core.physical.component import (
    GROUND,
    Behavioural,
    Capacitor,
    Parameter,
    PhysicalComponent,
    Port,
    Ports,
    Primitive,
    Resistor,
    Sensor,
    SignalOutput,
    VoltageSource,
    physical_component,
)
from core.physical.domains import ELECTRICAL, get_domain
from core.physical.library._helpers import num, positive, real, text

ELE = get_domain(ELECTRICAL)

SECONDS_PER_HOUR = 3600.0
_SHUNT = 1e12
DEFAULT_OCV = "0:3.0 0.1:3.45 0.2:3.55 0.5:3.7 0.8:3.9 0.9:4.0 1:4.2"
_SPLIT = re.compile(r"[\s,;]+")


def parse_ocv_table(table: str) -> list[tuple[float, float]]:
    """``"soc:V soc:V ..."`` as (soc, V) pairs. Raises ValueError for a bad table."""
    points: list[tuple[float, float]] = []
    for item in _SPLIT.split(table.strip()):
        if not item:
            continue
        soc, sep, volt = item.partition(":")
        try:
            if not sep:
                raise ValueError
            point = (float(soc), float(volt))
        except ValueError:
            raise ValueError(f"ocv: {item!r} is not soc:voltage") from None
        points.append(point)
    if len(points) < 2:
        raise ValueError("ocv needs at least two soc:voltage points")
    socs = [s for s, _ in points]
    if any(b <= a for a, b in zip(socs, socs[1:], strict=False)):
        raise ValueError("ocv: the soc values must rise strictly")
    if socs[0] < 0.0 or socs[-1] > 1.0:
        raise ValueError("ocv: soc values must lie between 0 and 1")
    return points


@physical_component
class Battery(PhysicalComponent):
    """Battery: open-circuit voltage from a state-of-charge table, internal
    resistance R_int, capacity in A*h. Signal output soc (0..1), starting at soc0.

    ocv is text: soc:V pairs, e.g. "0:3.0 0.5:3.7 1:4.2".
    """

    ports = (
        Port("p", ELECTRICAL, "+"),
        Port("n", ELECTRICAL, "-"),
        SignalOutput("soc", "", "state of charge"),
    )
    parameters = (
        Parameter("ocv", DEFAULT_OCV, "", "open-circuit voltage table soc:V"),
        Parameter("R_int", 0.05, "Ohm", "internal resistance"),
        Parameter("capacity", 2.0, "A*h", "capacity"),
        Parameter("soc0", 1.0, "", "initial state of charge"),
    )
    internal = {"y": ELECTRICAL, "x": ELECTRICAL, "q": ELECTRICAL}

    def network(self, ports: Ports, params: Mapping[str, object]) -> list[Primitive]:
        points = parse_ocv_table(text(params, "ocv"))
        soc0 = real(params, "soc0")
        if not 0.0 <= soc0 <= 1.0:
            raise ValueError(f"soc0 must lie between 0 and 1, not {soc0!r}")
        coulombs = SECONDS_PER_HOUR * positive(params, "capacity")  # A*s per full charge
        r_int = ELE.resistance_from(positive(params, "R_int"))
        soc = f"({num(soc0)} + {ELE.across_expr(ports.q)})"
        lo, hi = num(points[0][0]), num(points[-1][0])
        clamped = f"max({lo}, min({hi}, {soc}))"
        table = ", ".join(f"{num(s)}, {num(ELE.to_voltage(v))}" for s, v in points)
        charge = f"(time > 0) ? ({ELE.through_expr('Vi')} / {num(coulombs)}) : 0"
        return [
            VoltageSource("Vi", ports.p, ports.y, 0.0),  # current into p (charging)
            Resistor("R", ports.y, ports.x, r_int),
            Behavioural("Voc", ports.x, ports.n, "v", f"pwl({clamped}, {table})"),
            # charge fraction: flows from ground into q, integrated on 1 F
            Behavioural("Gq", GROUND, ports.q, "i", charge),
            Capacitor("Cq", ports.q, GROUND, ELE.capacitance_from(1.0)),
            Resistor("Rq", ports.q, GROUND, _SHUNT),
            Sensor("S", "soc", soc),
        ]
