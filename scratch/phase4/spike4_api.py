"""Spike 4: read every IEEE 13 element back through the API only.

For each element class this reads the values a GridModel will need (CLAUDE.md "Grid
model") and records, per value, whether a typed interface gave it ("typed") or it had
to come from ActiveDSSElement.Properties("<name>").Val ("props"). No DSS text is
parsed: Properties values are single numbers or names, converted with float().
Matrices, buses and windings come from typed interfaces.

Also shown: the length and linecode unit codes (and how Lines.Rmatrix is reported),
bus coordinates, voltage bases, and RegControl taps before and after the solve.

Run:  python scratch/phase4/spike4_api.py [feeder]
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import common

UNIT_CODES = {0: "none", 1: "mi", 2: "kft", 3: "km", 4: "m", 5: "ft", 6: "in", 7: "cm", 8: "mm"}


@dataclass
class Element:
    cls: str
    name: str
    values: dict[str, Any] = field(default_factory=dict)
    via: dict[str, str] = field(default_factory=dict)  # value name -> "typed" | "props"

    @property
    def id(self) -> str:
        return f"{self.cls}.{self.name}"


class Reader:
    """Collects values for one element; prop() reads the active DSS element."""

    def __init__(self, ctx: Any, cls: str, name: str) -> None:
        self.ctx = ctx
        self.el = Element(cls, name.lower())
        ctx.ActiveCircuit.SetActiveElement(f"{cls}.{name}")

    def typed(self, key: str, value: Any) -> None:
        if hasattr(value, "tolist"):
            value = value.tolist()
        elif isinstance(value, tuple | list):
            value = list(value)
        self.el.values[key] = value
        self.el.via[key] = "typed"

    def prop(self, key: str, dss_name: str, kind: type = float) -> None:
        raw = self.ctx.ActiveCircuit.ActiveDSSElement.Properties(dss_name).Val
        self.el.values[key] = kind(raw) if kind is not str else raw.lower()
        self.el.via[key] = "props"

    def common(self) -> None:
        """Buses, phases and state from ActiveCktElement (typed, every element)."""
        ce = self.ctx.ActiveCircuit.ActiveCktElement
        self.typed("buses", [b.lower() for b in ce.BusNames])
        self.typed("phases", int(ce.NumPhases))
        self.typed("enabled", bool(ce.Enabled))


def _each(iface: Any):
    i = iface.First
    while i > 0:
        yield iface.Name
        i = iface.Next


def read_sources(ctx: Any) -> list[Element]:
    out = []
    vs = ctx.ActiveCircuit.Vsources
    for name in list(_each(vs)):
        vs.Name = name
        r = Reader(ctx, "Vsource", name)
        r.common()
        r.typed("basekv", vs.BasekV)
        r.typed("pu", vs.pu)
        r.typed("angle_deg", vs.AngleDeg)
        r.typed("frequency", vs.Frequency)
        for key in ("R1", "X1", "R0", "X0", "MVAsc3", "MVAsc1", "Isc3", "Isc1"):
            r.prop(key.lower(), key)
        r.prop("basefreq", "BaseFreq")
        out.append(r.el)
    return out


def read_linecodes(ctx: Any) -> list[Element]:
    out = []
    lc = ctx.ActiveCircuit.LineCodes
    for name in list(_each(lc)):
        lc.Name = name
        r = Reader(ctx, "LineCode", name)
        r.typed("phases", lc.Phases)
        r.typed("units", lc.Units)
        r.typed("rmatrix", lc.Rmatrix)  # ohm per `units`
        r.typed("xmatrix", lc.Xmatrix)
        r.typed("cmatrix", lc.Cmatrix)  # nF per `units`
        r.typed("normamps", lc.NormAmps)
        r.typed("emergamps", lc.EmergAmps)
        r.typed("is_z1z0", lc.IsZ1Z0)
        r.prop("basefreq", "BaseFreq")
        r.prop("kron", "Kron", str)
        out.append(r.el)
    return out


def read_lines(ctx: Any) -> list[Element]:
    out = []
    ln = ctx.ActiveCircuit.Lines
    for name in list(_each(ln)):
        ln.Name = name
        r = Reader(ctx, "Line", name)
        r.common()
        r.typed("linecode", ln.LineCode.lower())
        r.typed("length", ln.Length)
        r.typed("units", ln.Units)
        r.typed("is_switch", ln.IsSwitch)
        r.typed("r1", ln.R1)
        r.typed("x1", ln.X1)
        r.typed("r0", ln.R0)
        r.typed("x0", ln.X0)
        r.typed("rmatrix", ln.Rmatrix)  # ohm per *line* unit (converted from the linecode)
        r.typed("xmatrix", ln.Xmatrix)
        r.typed("cmatrix", ln.Cmatrix)
        r.typed("normamps", ln.NormAmps)
        r.typed("geometry", ln.Geometry.lower())
        ce = ctx.ActiveCircuit.ActiveCktElement
        r.typed("open", [bool(ce.IsOpen(t, 0)) for t in (1, 2)])
        r.prop("basefreq", "BaseFreq")
        out.append(r.el)
    return out


def read_transformers(ctx: Any) -> list[Element]:
    out = []
    xf = ctx.ActiveCircuit.Transformers
    for name in list(_each(xf)):
        xf.Name = name
        r = Reader(ctx, "Transformer", name)
        r.common()
        r.typed("windings", xf.NumWindings)
        r.typed("xhl", xf.Xhl)
        r.typed("xht", xf.Xht)
        r.typed("xlt", xf.Xlt)
        r.typed("xfmrcode", xf.XfmrCode.lower())
        wdgs = []
        for w in range(1, xf.NumWindings + 1):
            xf.Wdg = w
            wdgs.append(
                {
                    "kv": xf.kV,
                    "kva": xf.kVA,
                    "pct_r": xf.R,
                    "conn": "delta" if xf.IsDelta else "wye",
                    "tap": xf.Tap,
                    "min_tap": xf.MinTap,
                    "max_tap": xf.MaxTap,
                    "num_taps": xf.NumTaps,
                    "rneut": xf.Rneut,
                    "xneut": xf.Xneut,
                }
            )
        r.typed("winding_list", wdgs)
        r.prop("pct_noloadloss", "%NoLoadLoss")
        r.prop("pct_imag", "%IMag")
        r.prop("pct_loadloss", "%LoadLoss")
        r.prop("bank", "Bank", str)
        r.prop("basefreq", "BaseFreq")
        out.append(r.el)
    return out


def read_regcontrols(ctx: Any) -> list[Element]:
    out = []
    rc = ctx.ActiveCircuit.RegControls
    for name in list(_each(rc)):
        rc.Name = name
        r = Reader(ctx, "RegControl", name)
        r.typed("enabled", bool(ctx.ActiveCircuit.ActiveCktElement.Enabled))
        r.typed("transformer", rc.Transformer.lower())
        r.typed("winding", rc.Winding)
        r.typed("vreg", rc.ForwardVreg)
        r.typed("band", rc.ForwardBand)
        r.typed("ptratio", rc.PTratio)
        r.typed("ctprim", rc.CTPrimary)
        r.typed("r", rc.ForwardR)
        r.typed("x", rc.ForwardX)
        r.typed("delay", rc.Delay)
        r.typed("tapdelay", rc.TapDelay)
        r.typed("maxtapchange", rc.MaxTapChange)
        r.typed("monitored_bus", rc.MonitoredBus.lower())
        r.typed("reversible", rc.IsReversible)
        r.typed("tap_number", rc.TapNumber)
        r.prop("ptphase", "PTPhase", str)
        out.append(r.el)
    return out


def read_loads(ctx: Any) -> list[Element]:
    out = []
    ld = ctx.ActiveCircuit.Loads
    for name in list(_each(ld)):
        ld.Name = name
        r = Reader(ctx, "Load", name)
        r.common()
        r.typed("kv", ld.kV)
        r.typed("kw", ld.kW)
        r.typed("kvar", ld.kvar)
        r.typed("pf", ld.PF)
        r.typed("model", ld.Model)
        r.typed("conn", "delta" if ld.IsDelta else "wye")
        r.typed("vminpu", ld.Vminpu)
        r.typed("vmaxpu", ld.Vmaxpu)
        r.typed("status", ld.Status)
        r.typed("zipv", ld.ZIPV)
        r.prop("vlowpu", "VLowpu")
        out.append(r.el)
    return out


def read_capacitors(ctx: Any) -> list[Element]:
    out = []
    cap = ctx.ActiveCircuit.Capacitors
    for name in list(_each(cap)):
        cap.Name = name
        r = Reader(ctx, "Capacitor", name)
        r.common()
        r.typed("kv", cap.kV)
        r.typed("kvar", cap.kvar)
        r.typed("conn", "delta" if cap.IsDelta else "wye")
        r.typed("num_steps", cap.NumSteps)
        r.typed("states", cap.States)
        out.append(r.el)
    return out


def read_pvsystems(ctx: Any) -> list[Element]:
    out = []
    pv = ctx.ActiveCircuit.PVSystems
    for name in list(_each(pv)):
        pv.Name = name
        r = Reader(ctx, "PVSystem", name)
        r.common()
        r.typed("pmpp", pv.Pmpp)
        r.typed("kva_rated", pv.kVArated)
        r.typed("irradiance", pv.Irradiance)
        r.typed("pf", pv.PF)
        r.prop("kv", "kV")
        r.prop("conn", "Conn", str)
        out.append(r.el)
    return out


READERS = {
    "Vsource": read_sources,
    "LineCode": read_linecodes,
    "Line": read_lines,
    "Transformer": read_transformers,
    "RegControl": read_regcontrols,
    "Load": read_loads,
    "Capacitor": read_capacitors,
    "PVSystem": read_pvsystems,
}


@dataclass
class Coverage:
    elements: list[Element]
    unsupported: list[str]  # element ids of classes we have no reader for
    bus_xy: dict[str, tuple[float, float] | None]
    bus_kv_base: dict[str, float]  # LN kV base per bus
    voltage_bases: list[float]
    base_frequency: float
    taps_before: dict[str, float]
    taps_after: dict[str, float]

    def props_needed(self) -> dict[str, list[str]]:
        """Element class -> value names that needed Properties()."""
        need: dict[str, set[str]] = defaultdict(set)
        for el in self.elements:
            need[el.cls].update(k for k, v in el.via.items() if v == "props")
        return {cls: sorted(keys) for cls, keys in need.items()}


def _taps(ctx: Any) -> dict[str, float]:
    ckt = ctx.ActiveCircuit
    out = {}
    for name in list(_each(ckt.RegControls)):
        ckt.RegControls.Name = name
        xf, w = ckt.RegControls.Transformer, ckt.RegControls.Winding
        ckt.Transformers.Name = xf
        ckt.Transformers.Wdg = w
        out[name.lower()] = float(ckt.Transformers.Tap)
    return out


def read_all(ctx: Any) -> tuple[list[Element], list[str]]:
    ckt = ctx.ActiveCircuit
    elements: list[Element] = []
    for reader in READERS.values():
        elements.extend(reader(ctx))
    known = {e.id.lower() for e in elements}
    unsupported = [n.lower() for n in ckt.AllElementNames if n.lower() not in known]
    return elements, unsupported


def coverage(feeder: str = "ieee13") -> Coverage:
    ctx = common.new_context()
    case = common.CASES[feeder]
    common.compile_case(ctx, case)
    if case.solve:
        before = _taps(ctx)  # the file does not solve: these are the defined taps
    else:
        # IEEE13Nodeckt.dss solves itself inside `compile`, so the defined taps are
        # gone. Recreate the pre-solve state: controls off, taps back to their
        # defined values (the file sets none: 1.0) and solve without controls.
        common.command(ctx, "Set ControlMode=OFF")
        for name in list(_each(ctx.ActiveCircuit.RegControls)):
            ctx.ActiveCircuit.RegControls.Name = name
            xf = ctx.ActiveCircuit.RegControls.Transformer
            common.command(ctx, f"Transformer.{xf}.Taps=[1 1]")
        ctx.ActiveCircuit.Solution.Solve()
        before = _taps(ctx)
        common.command(ctx, "Set ControlMode=STATIC")
    ctx.ActiveCircuit.Solution.Solve()
    after = _taps(ctx)
    elements, unsupported = read_all(ctx)
    ckt = ctx.ActiveCircuit
    xy: dict[str, tuple[float, float] | None] = {}
    kv: dict[str, float] = {}
    for bus in ckt.AllBusNames:
        ckt.SetActiveBus(bus)
        b = ckt.ActiveBus
        xy[bus.lower()] = (b.x, b.y) if b.Coorddefined else None
        kv[bus.lower()] = b.kVBase
    common.command(ctx, "get DefaultBaseFrequency")
    return Coverage(
        elements=elements,
        unsupported=unsupported,
        bus_xy=xy,
        bus_kv_base=kv,
        voltage_bases=[float(v) for v in ckt.Settings.VoltageBases],
        base_frequency=float(ctx.Text.Result),
        taps_before=before,
        taps_after=after,
    )


def line_units_after(order: str) -> tuple[int, float, float]:
    """(Lines.Units, Length, R1) of a line defined with the given property order.

    Found here: setting R1/X1 (or the matrices) after Units resets Units to 0 (none);
    length and impedance then share that unknown unit, so the total is unchanged.
    """
    ctx = common.new_context()
    for cmd in (
        "clear",
        "new circuit.order basekv=12.47 bus1=src",
        f"new line.l1 bus1=src bus2=b {order}",
    ):
        common.command(ctx, cmd)
    ln = ctx.ActiveCircuit.Lines
    ln.Name = "l1"
    return int(ln.Units), float(ln.Length), float(ln.R1)


def main() -> None:
    feeder = sys.argv[1] if len(sys.argv) > 1 else "ieee13"
    cov = coverage(feeder)
    counts: dict[str, int] = defaultdict(int)
    for el in cov.elements:
        counts[el.cls] += 1
    print(f"{feeder}: " + ", ".join(f"{n} {c}" for c, n in counts.items()))
    print(f"unsupported (no reader): {cov.unsupported or 'none'}")
    print("values that needed ActiveDSSElement.Properties():")
    for cls, keys in cov.props_needed().items():
        print(f"  {cls}: {', '.join(keys)}")
    print("unit codes (0 none, 1 mi, 2 kft, 3 km, 4 m, 5 ft):")
    for el in cov.elements:
        if el.cls == "LineCode":
            print(
                f"  linecode.{el.name}: units {el.values['units']} "
                f"({UNIT_CODES[el.values['units']]})"
            )
    for el in cov.elements:
        if el.cls == "Line":
            v = el.values
            sw = " SWITCH" if v["is_switch"] else ""
            print(
                f"  line.{el.name}: length {v['length']:g} units {v['units']} "
                f"({UNIT_CODES[v['units']]}) linecode '{v['linecode']}' "
                f"r11 {v['rmatrix'][0]:.6g} ohm/unit{sw}"
            )
    print(f"voltage bases: {cov.voltage_bases}  base frequency: {cov.base_frequency}")
    n_xy = sum(1 for p in cov.bus_xy.values() if p is not None)
    print(f"bus coordinates: {n_xy} of {len(cov.bus_xy)} buses")
    print("regulator taps before -> after the solve:")
    for name in cov.taps_after:
        print(f"  {name}: {cov.taps_before[name]:.5f} -> {cov.taps_after[name]:.5f}")
    for order in ("length=1.5 units=km r1=0.25 x1=0.5", "r1=0.25 x1=0.5 length=1.5 units=km"):
        units, length, r1 = line_units_after(order)
        print(f"line '{order}': Units {units} ({UNIT_CODES[units]}), Length {length}, R1 {r1}")


if __name__ == "__main__":
    main()
