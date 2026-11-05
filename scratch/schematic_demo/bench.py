"""Throwaway: pan/zoom frame rate of the schematic canvas with 300 parts and 600 wires.

Run:  .venv/Scripts/python.exe scratch/schematic_demo/bench.py [--seconds 5] [--parts 300]
It opens a 1200x800 window, animates zoom (in and out, around the centre) and pan
on every frame, and prints frames per second and frame-gap statistics. Each phase
ends with a pause so the "settled" stroke-width refresh is measured too.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import bridge.diagram_controller  # noqa: E402, F401
import bridge.project_manager  # noqa: E402, F401
import bridge.schematic_controller  # noqa: E402, F401
import bridge.simulator  # noqa: E402, F401
import bridge.waveform_model  # noqa: E402, F401
from core.schematic.model import Instance, Schematic, Wire  # noqa: E402

QML = """
import QtQuick
import QtQuick.Controls
import "%s"
ApplicationWindow {
    width: 1200; height: 800; visible: true
    title: "Schematic canvas bench"
    SchematicCanvas { objectName: "canvas"; anchors.fill: parent }
}
"""


def big(n: int) -> Schematic:
    """n resistors on a 20-wide grid, each with two wires (2n wires), all connected."""
    s = Schematic()
    for i in range(n):
        x, y = (i % 20) * 160, (i // 20) * 192
        s.instances.append(Instance("res", f"R{i + 1}", x, y, attrs={"Value": "1k"}))
        s.wires.append(Wire(x + 16, y, x + 16, y - 48))
        s.wires.append(Wire(x + 16, y + 96, x + 176, y + 96))
    return s


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--parts", type=int, default=300)
    args, _ = parser.parse_known_args()

    QQuickStyle.setStyle("Fusion")
    app = QGuiApplication(sys.argv[:1])
    wrapper = Path(__file__).with_name("_bench.qml")
    wrapper.write_text(QML % (ROOT / "ui" / "qml").as_uri(), encoding="utf-8")
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(ROOT / "ui" / "qml"))
    engine.load(QUrl.fromLocalFile(str(wrapper)))
    window = engine.rootObjects()[0]
    canvas = window.findChild(QObject, "canvas")
    ctl = engine.singletonInstance("QtSpice", "SchematicController")
    t0 = time.perf_counter()
    ctl.load_schematic(big(args.parts))
    print(
        f"load {args.parts} parts / {2 * args.parts} wires: "
        f"{(time.perf_counter() - t0) * 1e3:.0f} ms (models + junctions)"
    )

    frames: list[float] = []
    zooms: list[float] = []

    def swapped() -> None:
        frames.append(time.perf_counter())
        zooms.append(canvas.property("zoom"))

    window.frameSwapped.connect(swapped)
    state = {"phase": "", "t": 0.0}
    results: dict[str, tuple[float, float, float]] = {}

    def step() -> None:
        t = time.perf_counter() - state["t"]
        w, h = window.width(), window.height()
        if state["phase"] == "zoom":
            # Zoom between 0.25x and 2x of the fit, around the window centre.
            target = state["fit"] * math.exp(math.sin(t * 2.0) * 1.0)
            canvas.zoomAt(w / 2, h / 2, target / canvas.property("zoom"))
        else:
            canvas.setProperty("panX", canvas.property("panX") + 6 * math.cos(t))
            canvas.setProperty("panY", canvas.property("panY") + 4 * math.sin(t))

    timer = QTimer()
    timer.setInterval(0)
    timer.timeout.connect(step)

    def run(phase: str, then) -> None:  # noqa: ANN001
        state.update(phase=phase, t=time.perf_counter())
        frames.clear()
        zooms.clear()
        timer.start()

        def stop() -> None:
            timer.stop()
            gaps = np.diff(frames) * 1e3 if len(frames) > 2 else np.zeros(1)
            if "--slow" in sys.argv:
                for k, g in enumerate(gaps):
                    if g > 40:
                        print(
                            f"  slow frame {g:.0f} ms at {frames[k + 1] - frames[0]:.2f} s, "
                            f"zoom {zooms[k + 1]:.3f}"
                        )
            span = frames[-1] - frames[0] if len(frames) > 1 else 1
            results[phase] = ((len(frames) - 1) / span, float(np.median(gaps)), float(gaps.max()))
            settle_start = len(frames)
            QTimer.singleShot(400, lambda: settled(phase, settle_start, then))

        QTimer.singleShot(int(args.seconds * 1000), stop)

    def settled(phase: str, start: int, then) -> None:  # noqa: ANN001
        fps, median, worst = results[phase]
        print(f"{phase:5s}: {fps:5.1f} fps, frame gap median {median:.1f} ms, worst {worst:.1f} ms")
        then()

    def settle_cost(then) -> None:  # noqa: ANN001
        """Change settledZoom (what a pause after zooming does) and time the next frame."""
        costs: list[float] = []

        def one(k: int) -> None:
            if k == 8:
                print(
                    f"settle: line-width refresh of every part takes "
                    f"median {np.median(costs):.0f} ms, worst {max(costs):.0f} ms"
                )
                then()
                return
            t0 = time.perf_counter()

            def done() -> None:
                window.frameSwapped.disconnect(done)
                costs.append((time.perf_counter() - t0) * 1e3)
                QTimer.singleShot(100, lambda: one(k + 1))

            window.frameSwapped.connect(done)
            canvas.setProperty("settledZoom", state["fit"] * (1.3 ** (k % 4 - 2)))

        one(0)

    def finish() -> None:
        wrapper.unlink(missing_ok=True)
        app.quit()

    def start() -> None:
        canvas.zoomToFit()
        state["fit"] = canvas.property("zoom")
        QTimer.singleShot(300, lambda: run("zoom", lambda: run("pan", lambda: settle_cost(finish))))

    QTimer.singleShot(800, start)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
