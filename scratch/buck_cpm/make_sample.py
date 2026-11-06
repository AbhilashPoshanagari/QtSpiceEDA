"""Writes examples/buck_cpm/: the 1 MHz peak-current-mode synchronous buck sample.

Scratch tool (never imported by app code). Run from the repo root:

    python scratch/buck_cpm/make_sample.py

It writes the symbols (CR+LF), copies buck_cpm.lib and the block code
(buck_blocks.txt) from this folder, and
builds buck_cpm.qsp: one schematic (the circuit of the course's LTspice
SyncBuck_switching_CPM_CL.asc, redrawn with QtSpice and sample symbols) and one block
diagram (the same converter as Python blocks).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.project.io import BlockSource, Project  # noqa: E402
from core.schematic.model import (  # noqa: E402
    GroundFlag,
    Instance,
    NetLabel,
    Schematic,
    TextItem,
    Wire,
    pin_points,
)
from core.symbols.library import SymbolLibrary  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = ROOT / "examples" / "buck_cpm"
LIB = "buck_cpm.lib"

# ---------------------------------------------------------------- symbols


def box_symbol(
    value: str, description: str, pins: list[tuple[int, int, str]], w: int, h: int, text: str
) -> str:
    """A box from (-w, -h) to (w, h) with pin stubs; pins are (x, y, name) in SpiceOrder."""
    lines = ["Version 4", "SymbolType CELL", f"RECTANGLE Normal {-w} {-h} {w} {h}"]
    for x, y, _ in pins:
        if x < -w:
            lines.append(f"LINE Normal {x} {y} {-w} {y}")
        elif x > w:
            lines.append(f"LINE Normal {w} {y} {x} {y}")
        elif y < -h:
            lines.append(f"LINE Normal {x} {y} {x} {-h}")
        elif y > h:
            lines.append(f"LINE Normal {x} {h} {x} {y}")
    lines.append(f"TEXT 0 0 Center 1 {text}")
    lines += [
        f"WINDOW 0 {-w} {-h - 16} Left 2",
        f"WINDOW 3 {-w} {h + 16} Left 2",
        f"SYMATTR Value {value}",
        "SYMATTR Prefix X",
        f"SYMATTR Description {description}",
        f"SYMATTR ModelFile {LIB}",
    ]
    for order, (x, y, name) in enumerate(pins, 1):
        align = "LEFT" if x < -w else "RIGHT" if x > w else "TOP" if y < -h else "BOTTOM"
        lines += [
            f"PIN {x} {y} {align} 8",
            f"PINATTR PinName {name}",
            f"PINATTR SpiceOrder {order}",
        ]
    return "\r\n".join(lines) + "\r\n"


NFET = """Version 4
SymbolType CELL
LINE Normal 0 48 12 48
LINE Normal 12 24 12 72
LINE Normal 20 20 20 36
LINE Normal 20 40 20 56
LINE Normal 20 60 20 76
LINE Normal 20 28 48 28
LINE Normal 48 28 48 0
LINE Normal 20 68 48 68
LINE Normal 48 68 48 96
LINE Normal 20 48 48 48
LINE Normal 48 48 48 68
LINE Normal 20 48 30 42
LINE Normal 20 48 30 54
LINE Normal 56 36 56 60
LINE Normal 50 44 62 44
LINE Normal 56 44 50 56
LINE Normal 56 44 62 56
LINE Normal 50 56 62 56
LINE Normal 48 28 56 28
LINE Normal 56 28 56 36
LINE Normal 48 68 56 68
LINE Normal 56 68 56 60
WINDOW 0 72 16 Left 2
WINDOW 3 72 80 Left 2
SYMATTR Value nfet_pwr
SYMATTR Prefix X
SYMATTR Description Generic 30 V logic-level power NMOS with body diode and Coss (buck_cpm sample)
SYMATTR ModelFile buck_cpm.lib
PIN 48 0 NONE 0
PINATTR PinName D
PINATTR SpiceOrder 1
PIN 0 48 NONE 0
PINATTR PinName G
PINATTR SpiceOrder 2
PIN 48 96 NONE 0
PINATTR PinName S
PINATTR SpiceOrder 3
"""

CCVS = """Version 4
SymbolType CELL
LINE Normal 0 16 0 24
LINE Normal 0 88 0 96
LINE Normal 0 24 32 56
LINE Normal 32 56 0 88
LINE Normal 0 88 -32 56
LINE Normal -32 56 0 24
LINE Normal -8 44 8 44
LINE Normal 0 36 0 52
LINE Normal -8 70 8 70
WINDOW 0 40 32 Left 2
WINDOW 3 40 72 Left 2
SYMATTR Value Vsense 1
SYMATTR Prefix H
SYMATTR Description CCVS: Value is <sense V source> <gain> (buck_cpm sample)
PIN 0 16 NONE 0
PINATTR PinName +
PINATTR SpiceOrder 1
PIN 0 96 NONE 0
PINATTR PinName -
PINATTR SpiceOrder 2
"""

SYMBOLS = {
    "cpm_modulator.asy": box_symbol(
        "CPM_modulator",
        "Peak current mode modulator, 1 MHz (buck_cpm sample; parameters fixed in buck_cpm.lib)",
        [(-80, -32, "vc"), (-80, 32, "vs"), (80, -32, "PWM"), (80, 32, "ramp")],
        48,
        64,
        "CPM",
    ),
    "dead_time.asy": box_symbol(
        "dead_time",
        "Dead-time generator, Td = 30 ns (buck_cpm sample)",
        [(-80, 0, "c"), (80, -16, "c1"), (80, 16, "c2")],
        48,
        32,
        "DT",
    ),
    "driver.asy": box_symbol(
        "driver",
        "Gate driver, Tdelay = 20 ns (buck_cpm sample)",
        [(-80, -32, "c"), (0, -80, "Vdd"), (0, 80, "Vss"), (-80, 32, "Vssin"), (80, 0, "out")],
        48,
        48,
        "DRV",
    ),
    "nfet_pwr.asy": NFET.replace("\n", "\r\n"),
    "ccvs.asy": CCVS.replace("\n", "\r\n"),
}

# ---------------------------------------------------------------- schematic

Point = tuple[int, int]


class Sheet:
    """Places parts by putting one named pin on a sheet point; returns pin points."""

    def __init__(self, library: SymbolLibrary) -> None:
        self.lib = library
        self.s = Schematic(title="1 MHz peak current mode synchronous buck, 5 V to 3.3 V")

    def part(
        self,
        symbol: str,
        name: str,
        pin: str,
        at: Point,
        rot: int = 0,
        mirror: bool = False,
        **attrs: str,
    ) -> dict[str, Point]:
        sym, diags = self.lib.lookup(symbol)
        assert sym is not None, diags
        probe = Instance(symbol, name, 0, 0, rot, mirror)
        local = dict(
            zip([p.attrs["PinName"] for p in sym.pins], pin_points(probe, sym), strict=True)
        )
        x, y = at[0] - local[pin][0], at[1] - local[pin][1]
        self.s.instances.append(Instance(symbol, name, x, y, rot, mirror, dict(attrs)))
        return {n: (x + px, y + py) for n, (px, py) in local.items()}

    def wire(self, *points: Point) -> None:
        for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
            assert x1 == x2 or y1 == y2, points
            self.s.wires.append(Wire(x1, y1, x2, y2))

    def label(self, point: Point, name: str) -> None:
        self.s.labels.append(NetLabel(point[0], point[1], name))

    def ground(self, point: Point) -> None:
        self.s.grounds.append(GroundFlag(point[0], point[1]))

    def text(self, x: int, y: int, text: str, directive: bool = False) -> None:
        self.s.texts.append(TextItem(x, y, text, directive))


def build_schematic(library: SymbolLibrary) -> Schematic:
    sh = Sheet(library)
    H = 270  # rotation that lays a two-pin part left (A or +) to right (B or -)

    # --- power stage: sw node at (208, 0), output along y = 0
    m1 = sh.part("nfet_pwr", "M1", "S", (208, -64))
    m2 = sh.part("nfet_pwr", "M2", "D", (208, 64))
    sh.wire(m1["S"], (208, 0), m2["D"])
    sh.label((208, 0), "sw")
    sh.label(m1["D"], "in")
    sh.ground(m2["S"])
    l1 = sh.part("ind", "L1", "A", (288, 0), H, Value="1u ic=0")
    sh.wire((208, 0), l1["A"])
    vs = sh.part("voltage", "Vsense", "+", (432, 0), H, Value="0")
    sh.wire(l1["B"], vs["+"])
    rl = sh.part("res", "RL", "A", (560, 0), H, Value="15m")
    sh.wire(vs["-"], rl["A"])
    sh.wire(rl["B"], (704, 0), (832, 0), (960, 0))
    sh.label((704, 0), "out")
    resr = sh.part("res", "Resr", "A", (704, 32), Value="0.8m")
    sh.wire((704, 0), resr["A"])
    c1 = sh.part("cap", "C1", "A", (704, 160), Value="200u ic={Vref}")
    sh.wire(resr["B"], c1["A"])
    sh.ground(c1["B"])
    rload = sh.part("res", "Rload", "A", (832, 32), Value="1k")
    sh.wire((832, 0), rload["A"])
    sh.ground(rload["B"])
    iload = sh.part("current", "Iload", "+", (960, 32), Value="PULSE(0 2 110u 1u 1u 30u 160u)")
    sh.wire((960, 0), iload["+"])
    sh.ground(iload["-"])

    # --- gate drivers and gate resistors
    u3 = sh.part("driver", "U3", "out", (-32, -112))
    rg1 = sh.part("res", "Rg1", "A", (32, -112), H, Value="5")
    sh.wire(u3["out"], rg1["A"])
    sh.wire(rg1["B"], m1["G"])
    sh.label(u3["c"], "c1")
    sh.label(u3["Vss"], "sw")
    sh.ground(u3["Vssin"])
    u4 = sh.part("driver", "U4", "out", (-32, 112))
    rg2 = sh.part("res", "Rg2", "A", (32, 112), H, Value="5")
    sh.wire(u4["out"], rg2["A"])
    sh.wire(rg2["B"], m2["G"])
    sh.label(u4["c"], "c2")
    sh.label(u4["Vdd"], "in")
    sh.ground(u4["Vss"])
    sh.ground(u4["Vssin"])

    # --- input and bootstrap supply for the high-side driver
    vg = sh.part("voltage", "Vg", "+", (-416, -256), Value="5")
    sh.ground(vg["-"])
    d1 = sh.part("diode", "D1", "A", (-176, -256), H, Value="schottky_1a")
    sh.wire(vg["+"], d1["A"])
    sh.label(vg["+"], "in")
    cb = sh.part("cap", "Cb", "A", (32, -256), Value="10u ic=5")
    assert d1["K"][0] == u3["Vdd"][0]
    sh.wire(d1["K"], cb["A"])
    sh.wire(d1["K"], u3["Vdd"])
    sh.label(d1["K"], "bootstrap")
    sh.label(cb["B"], "sw")

    # --- dead time, CPM modulator and current sense
    u2 = sh.part("dead_time", "U2", "c", (-480, 0))
    sh.label(u2["c"], "c")
    sh.label(u2["c1"], "c1")
    sh.label(u2["c2"], "c2")
    u1 = sh.part("cpm_modulator", "U1", "vc", (-320, 288), 0, True)  # inputs on the right
    sh.label(u1["vc"], "ctrl")
    sh.label(u1["PWM"], "c")
    sh.label(u1["ramp"], "ramp")
    rr = sh.part("res", "Rramp", "A", (u1["ramp"][0], u1["ramp"][1] + 32), Value="1meg")
    sh.wire(u1["ramp"], rr["A"])
    sh.ground(rr["B"])
    hs = sh.part("ccvs", "Hsense", "+", (-224, 352), Value="Vsense {Rf}")
    sh.wire(u1["vs"], hs["+"])
    sh.label(hs["+"], "vs")
    sh.ground(hs["-"])

    # --- error amplifier with type II compensation (inverting input = fb)
    # ea_opamp is in buck_cpm.lib, which the sample symbols already include
    u5 = sh.part("opamp", "U5", "In-", (240, 368), 0, True, SpiceModel="ea_opamp")
    sh.label(u5["V+"], "in")
    sh.ground(u5["V-"])
    sh.label(u5["OUT"], "ctrl")
    vref = sh.part("voltage", "Vref", "+", (336, 432), Value="{Vref}")
    sh.wire(u5["In+"], (336, u5["In+"][1]), vref["+"])
    sh.ground(vref["-"])
    r1 = sh.part("res", "R1", "A", (400, 368), H, Value="1.2k")
    sh.wire(u5["In-"], r1["A"])
    sh.label(r1["A"], "fb")
    sh.label(r1["B"], "out")
    c3 = sh.part("cap", "C3", "A", (400, 304), 90, Value="10p")  # A right, B left
    sh.label(c3["A"], "fb")
    sh.label(c3["B"], "ctrl")
    r2 = sh.part("res", "R2", "A", (400, 256), 90, Value="15k")
    c2 = sh.part("cap", "C2", "A", (r2["B"][0] - 32, 256), 90, Value="220p")
    sh.wire(r2["B"], c2["A"])
    sh.label(r2["A"], "fb")
    sh.label(c2["B"], "ctrl")

    sh.text(-480, 560, ".tran 10n 160u 100u 10n uic\n.param Rf=0.1 Vref=3.3", directive=True)
    sh.text(
        -480,
        640,
        "1 MHz peak current mode (CPM) synchronous buck POL regulator, 5 V -> 3.3 V, "
        "2 A load step at 110 us.\n"
        "Redrawn from the LTspice course example SyncBuck_switching_CPM_CL.asc. The driver, "
        "dead time, CPM modulator, error amplifier\n"
        "and power devices are QtSpice stand-ins in buck_cpm.lib (no vendor models). "
        "Vsense (0 V) senses the inductor current.",
    )
    return sh.s


# ---------------------------------------------------------------- diagram


def build_diagram() -> dict:
    blocks = [
        ("Vref", "Constant", 40, 120, {"value": 3.3}),
        ("EA", "TypeIICompensator", 180, 200, {}),
        ("CPM", "CPMModulator", 340, 200, {"Va": 0.3}),
        ("DT", "DeadTime", 480, 200, {}),
        ("Load", "LoadPulse", 520, 80, {}),
        ("Buck", "BuckPowerStage", 640, 200, {}),
        ("Rf", "Gain", 480, 380, {"k": 0.1}),
        ("ScopeVc", "Scope", 340, 60, {"label": "ctrl"}),
        ("ScopeVout", "Scope", 840, 80, {"label": "vout"}),
        ("ScopeIL", "Scope", 840, 200, {"label": "iL"}),
        ("ScopeSw", "Scope", 840, 320, {"label": "vsw"}),
    ]
    wires = [
        ("Load.y", "Buck.iload"),
        ("DT.c1", "Buck.c1"),
        ("DT.c2", "Buck.c2"),
        ("CPM.y", "DT.pwm"),
        ("Rf.y", "CPM.vs"),
        ("EA.y", "CPM.vc"),
        ("Buck.iL", "Rf.u"),
        ("Buck.vout", "EA.vout"),
        ("Vref.y", "EA.vref"),
        ("Buck.vout", "ScopeVout.u"),
        ("Buck.iL", "ScopeIL.u"),
        ("EA.y", "ScopeVc.u"),
        ("Buck.vsw", "ScopeSw.u"),
    ]
    return {
        "blocks": [
            {"id": i, "spec": s, "x": float(x), "y": float(y), "settings": st}
            for i, s, x, y, st in blocks
        ],
        "wires": [{"src": a, "dst": b} for a, b in wires],
    }


# ---------------------------------------------------------------- main


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in SYMBOLS.items():
        (OUT / name).write_bytes(text.encode("cp1252"))
    (OUT / LIB).write_bytes((HERE / LIB).read_bytes())
    library = SymbolLibrary(project_files=[OUT / n for n in SYMBOLS])
    schematic = build_schematic(library)
    code = (HERE / "buck_blocks.txt").read_text(encoding="utf-8")
    project = Project()
    project.schematic = schematic
    project.symbols = list(SYMBOLS)
    project.block_sources = [BlockSource("buck_blocks", code)]
    data = json.loads(project.to_json())
    data["diagrams"] = [{"name": "buck_cpm_blocks", "diagram": build_diagram()}]
    data["analysis"]["diagram_t_end"] = 160e-6
    data["analysis"]["diagram_dt"] = 5e-9
    data["plots"] = PLOTS
    (OUT / "buck_cpm.qsp").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print("wrote", OUT)


def _axis(log: bool = False) -> dict:
    return {"auto": True, "lo": -1.0, "hi": 1.0, "log": log, "abs": False}


PLOTS = {
    "tran": {
        "x": {"auto": True, "lo": 0.0, "hi": 1.0, "log": False},
        "panes": [
            {
                "title": "Output voltage",
                "traces": ["V(out)"],
                "y": _axis(),
                "phase": _axis(),
                "grid": True,
            },
            {
                "title": "Inductor current and control",
                "traces": ["I(vsense)", "V(ctrl)"],
                "y": _axis(),
                "phase": _axis(),
                "grid": True,
            },
            {
                "title": "Switch node and PWM",
                "traces": ["V(sw)", "V(c)"],
                "y": _axis(),
                "phase": _axis(),
                "grid": True,
            },
        ],
    }
}

if __name__ == "__main__":
    main()
