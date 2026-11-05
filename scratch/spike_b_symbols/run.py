"""Spike B: draw .asy primitives in QML with Shape on a 16-unit grid; pins as dots.

Throwaway. The .asy reader below is a minimal sketch, not the real AsyParser.
Run:   .venv/Scripts/python.exe scratch/spike_b_symbols/run.py
Shot:  .venv/Scripts/python.exe scratch/spike_b_symbols/run.py --grab out.png
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[1] / "tests" / "fixtures" / "asy"
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _arc(*v: float) -> str:
    """LTspice ARC (bbox, start ray point, end ray point; counter-clockwise) -> SVG path."""
    x1, y1, x2, y2, sx, sy, ex, ey = v
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    rx, ry = abs(x2 - x1) / 2, abs(y2 - y1) / 2
    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(ey - cy, ex - cx)
    p0 = (cx + rx * math.cos(a0), cy + ry * math.sin(a0))
    p1 = (cx + rx * math.cos(a1), cy + ry * math.sin(a1))
    # Screen y points down, so visually counter-clockwise means decreasing angle.
    span = (a0 - a1) % (2 * math.pi)
    large = 1 if span > math.pi else 0
    return f"M {p0[0]:.3f} {p0[1]:.3f} A {rx} {ry} 0 {large} 0 {p1[0]:.3f} {p1[1]:.3f}"


def read_asy(path: Path) -> dict[str, object]:
    parts: list[str] = []
    pins: list[dict[str, object]] = []
    texts: list[dict[str, object]] = []
    attrs: dict[str, str] = {}
    for line in path.read_text(encoding="cp1252").splitlines():
        w = line.split()
        if not w:
            continue
        key = w[0].upper()
        try:
            if key == "LINE":
                x1, y1, x2, y2 = map(float, w[2:6])
                parts.append(f"M {x1} {y1} L {x2} {y2}")
            elif key == "RECTANGLE":
                x1, y1, x2, y2 = map(float, w[2:6])
                parts.append(f"M {x1} {y1} L {x2} {y1} L {x2} {y2} L {x1} {y2} Z")
            elif key == "CIRCLE":
                x1, y1, x2, y2 = map(float, w[2:6])
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                rx, ry = abs(x2 - x1) / 2, abs(y2 - y1) / 2
                parts.append(
                    f"M {cx - rx} {cy} A {rx} {ry} 0 1 0 {cx + rx} {cy} "
                    f"A {rx} {ry} 0 1 0 {cx - rx} {cy}"
                )
            elif key == "ARC":
                parts.append(_arc(*map(float, w[2:10])))
            elif key == "TEXT":
                texts.append({"x": float(w[1]), "y": float(w[2]), "text": " ".join(w[5:])})
            elif key == "SYMATTR":
                attrs[w[1]] = " ".join(w[2:])
            elif key == "PIN":
                pins.append({"x": float(w[1]), "y": float(w[2]), "name": "", "order": 0})
            elif key == "PINATTR" and pins:
                if w[1] == "PinName":
                    pins[-1]["name"] = " ".join(w[2:])
                elif w[1] == "SpiceOrder":
                    pins[-1]["order"] = int(w[2])
        except (ValueError, IndexError):
            print(f"{path.name}: skipped bad line {line!r}")
    xs = [float(p["x"]) for p in pins] + [0.0]
    ys = [float(p["y"]) for p in pins] + [0.0]
    for seg in parts:
        if " A " in seg:  # arcs: endpoints are good enough for a spike's bounds
            continue
        nums = [float(n) for n in NUMBER.findall(seg)]
        xs += nums[0::2]
        ys += nums[1::2]
    return {
        "name": path.stem,
        "path": " ".join(parts),
        "pins": pins,
        "texts": texts,
        "prefix": attrs.get("Prefix", ""),
        "value": attrs.get("Value", ""),
        "minX": min(xs),
        "minY": min(ys),
        "maxX": max(xs),
        "maxY": max(ys),
    }


def main() -> int:
    app = QGuiApplication(sys.argv)
    names = ("res", "cap", "opamp5", "cmosn", "micro_cp1252")
    symbols = [read_asy(FIXTURES / f"{n}.asy") for n in names]
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setInitialProperties({"symbols": symbols})
    view.setSource(QUrl.fromLocalFile(str(HERE / "symbols.qml")))
    if view.status() == QQuickView.Status.Error:
        for err in view.errors():
            print(err.toString())
        return 1
    view.resize(1400, 440)
    view.setTitle("Spike B - .asy symbols")
    view.show()
    if "--grab" in sys.argv:
        out = sys.argv[sys.argv.index("--grab") + 1]

        def grab() -> None:
            view.grabWindow().save(out)
            print("saved", out)
            app.quit()

        QTimer.singleShot(500, grab)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
