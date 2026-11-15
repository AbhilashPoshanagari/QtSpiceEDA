"""S1 spike: run one canvas variant on IEEE 123 (N copies side by side) and measure it.

    python scratch/phase5/spike1_canvas/bench.py --variant a --copies 1
    python scratch/phase5/spike1_canvas/bench.py --variant b --copies 4 --no-vsync
    python scratch/phase5/spike1_canvas/bench.py --variant b --interactive   # just look

Phases (all driven from frameSwapped, so every step is one real frame):
1. first frame: from engine.load(Canvas.qml) to the first frameSwapped.
2. idle 1 s, then memory and the number of QQuickItems.
3. pan and zoom, 5 s: 2.5 s pan in a 400 px circle at the fit zoom, then 2.5 s zoom
   around the window centre between 1/8 and 8 times the fit zoom (sine).
4. drag, 3 s: the bus with the most branches moves on a 6-unit circle, one
   SceneController.moveBusTo per frame (the slot a mouse drag calls).
5. hit-test: 10 000 random points through SpatialIndex.hit, the first 1000 also
   through the linear scan (they must agree); then 30 real clicks (QTest.mouseClick)
   on a random bus centred at 100 % zoom, timed from the click to the next frameSwapped.
Prints one JSON object (and writes it to --out).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import random
import statistics
import sys
import time
from pathlib import Path

T_PROCESS = time.perf_counter()
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(ROOT)]

# ---------------------------------------------------------------- memory (Windows)


class _PMC(ctypes.Structure):
    _fields_ = [  # noqa: RUF012
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def memory_mb() -> dict[str, float]:
    """Working set, peak working set and private bytes of this process, in MB."""
    if sys.platform != "win32":
        import resource

        return {"peak_ws": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC), ctypes.c_ulong]
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    mb = 1024 * 1024
    return {
        "ws": round(pmc.WorkingSetSize / mb, 1),
        "peak_ws": round(pmc.PeakWorkingSetSize / mb, 1),
        "private": round(pmc.PrivateUsage / mb, 1),
    }


def frame_stats(times: list[float]) -> dict[str, float]:
    if len(times) < 3:
        return {"frames": len(times)}
    gaps = sorted((b - a) * 1000 for a, b in zip(times, times[1:], strict=False))
    return {
        "frames": len(times),
        "fps": round((len(times) - 1) / (times[-1] - times[0]), 1),
        "p50_ms": round(statistics.median(gaps), 2),
        "p95_ms": round(gaps[int(0.95 * (len(gaps) - 1))], 2),
        "max_ms": round(gaps[-1], 2),
    }


def sg_stats(ms: list[float]) -> dict[str, float]:
    if not ms:
        return {}
    ms = sorted(ms)
    return {
        "sg_ms_p50": round(statistics.median(ms), 2),
        "sg_ms_p95": round(ms[int(0.95 * (len(ms) - 1))], 2),
        "sg_ms_max": round(ms[-1], 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["a", "b"], required=True)
    ap.add_argument("--copies", type=int, default=1)
    ap.add_argument("--no-vsync", action="store_true", help="swap interval 0 (uncapped)")
    ap.add_argument("--curve", action="store_true", help="variant b: Shape.CurveRenderer")
    ap.add_argument("--msaa", type=int, default=0, help="window multisampling (e.g. 4)")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--shot", type=Path, help="save a PNG of the first idle frame")
    args = ap.parse_args()

    from PySide6.QtCore import QObject, QPoint, Qt, QTimer, QUrl, Slot
    from PySide6.QtGui import QGuiApplication, QSurfaceFormat
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem, QQuickWindow
    from PySide6.QtTest import QTest

    fmt = QSurfaceFormat.defaultFormat()
    if args.no_vsync:
        fmt.setSwapInterval(0)
    if args.msaa:
        fmt.setSamples(args.msaa)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QGuiApplication(sys.argv)
    t_qt = time.perf_counter()

    from canvas_bridge import STEP, SceneController
    from scene import SpatialIndex, build_scene, hit_linear, load_ieee123, replicate

    model, t_load = load_ieee123()
    t0 = time.perf_counter()
    scene = replicate(build_scene(model), args.copies)
    t_scene = time.perf_counter() - t0
    t0 = time.perf_counter()
    ctrl = SceneController(scene, paths=args.variant == "b")
    t_ctrl = time.perf_counter() - t0
    mem_before = memory_mb()

    res: dict[str, object] = {
        "variant": args.variant
        + ("-curve" if args.curve else "")
        + (f"-msaa{args.msaa}" if args.msaa else ""),
        "copies": args.copies,
        "vsync": not args.no_vsync,
        "counts": {
            "buses": len(scene.buses),
            "branches": len(scene.branches),
            "branch_symbols": sum(1 for b in scene.branches if b.kind != "line"),
            "shunts": len(scene.shunts),
        },
        "startup_s": {
            "python_and_qt": round(t_qt - T_PROCESS, 3),
            "opendss_load_extract": round(t_load, 3),
            "scene": round(t_scene, 4),
            "controller_models": round(t_ctrl, 4),
        },
        "memory_mb": {"before_qml": mem_before},
    }

    engine = QQmlApplicationEngine()
    engine.setInitialProperties(
        {"scene": ctrl, "variant": args.variant, "curveRenderer": args.curve}
    )
    t_qml = time.perf_counter()
    engine.load(QUrl.fromLocalFile(str(HERE / "Canvas.qml")))
    t_loaded = time.perf_counter()
    if not engine.rootObjects():
        print("QML failed to load", file=sys.stderr)
        return 1
    win: QQuickWindow = engine.rootObjects()[0]
    win.setProperty("info", f"{args.copies} x IEEE 123, {len(scene.buses)} buses")
    screen = app.primaryScreen()
    res["screen"] = {
        "refresh_hz": round(screen.refreshRate(), 1),
        "dpr": screen.devicePixelRatio(),
        "window": [win.width(), win.height()],
    }
    if args.interactive:
        return app.exec()

    def count_items(item: QQuickItem) -> int:
        return 1 + sum(count_items(c) for c in item.childItems())

    rnd = random.Random(1)
    hub = max(range(len(scene.buses)), key=lambda i: len(ctrl._branches_of.get(i, [])))

    class Driver(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.phase = "first"
            self.times: list[float] = []
            self.sg: list[float] = []
            self.polish: list[float] = []
            self.t_phase = 0.0
            self.fit = (1.0, 0.0, 0.0)
            self.hub0 = (scene.buses[hub].x, scene.buses[hub].y)
            self.slot0 = 0.0
            self.clicks: list[dict[str, float]] = []
            self.click_t = 0.0
            self.click_bus = -1

        def _props(self) -> tuple[float, float, float]:
            return (
                float(win.property("zoom")),
                float(win.property("panX")),
                float(win.property("panY")),
            )

        def log(self, text: str) -> None:
            print(f"[{time.perf_counter() - T_PROCESS:7.2f}] {text}", file=sys.stderr, flush=True)

        @Slot()
        def on_frame(self) -> None:
            now = time.perf_counter()
            if self.phase != getattr(self, "_logged", None):
                self._logged = self.phase
                self.log(f"frame in phase {self.phase}")
            if self.phase == "first":
                res["first_frame_s"] = {
                    "qml_load": round(t_loaded - t_qml, 3),
                    "load_to_first_frame": round(now - t_qml, 3),
                    "process_start_to_first_frame": round(now - T_PROCESS, 3),
                }
                self.phase = "idle"
                QTimer.singleShot(1000, self.after_idle)
            elif self.phase == "panzoom":
                self.times.append(now)
                self.step_panzoom(now - self.t_phase)
            elif self.phase == "drag":
                self.times.append(now)
                self.step_drag(now - self.t_phase)
            elif self.phase == "click_wait":
                self.clicks[-1]["to_frame_ms"] = (now - self.click_t) * 1000
                self.phase = "click_gap"
                QTimer.singleShot(60, self.click)

        def after_idle(self) -> None:
            res["memory_mb"]["after_first_frame"] = memory_mb()
            res["quick_items"] = count_items(win.contentItem())
            content = win.findChild(QQuickItem, "content")
            res["quick_items_content"] = count_items(content) if content else -1
            if args.shot:
                win.grabWindow().save(str(args.shot))
            # Connected only now: grabWindow, like QTest.mouseClick, waits for the
            # render thread while holding the GIL (see the disconnect after drag).
            win.beforeSynchronizing.connect(before_sync, Qt.ConnectionType.DirectConnection)
            win.afterRendering.connect(after_render, Qt.ConnectionType.DirectConnection)
            self.fit = self._props()
            self.phase = "panzoom"
            self.times = []
            self.sg = []
            self.t_phase = time.perf_counter()
            win.update()

        def step_panzoom(self, t: float) -> None:
            z0, px0, py0 = self.fit
            if t < 2.5:
                a = 2 * math.pi * t / 2.5
                win.setProperty("panX", px0 + 400 * (math.cos(a) - 1))
                win.setProperty("panY", py0 + 400 * math.sin(a))
            elif t < 5.0:
                cx, cy = win.width() / 2, win.height() / 2
                sx, sy = (cx - px0) / z0, (cy - py0) / z0
                z = z0 * 2 ** (3 * math.sin(2 * math.pi * (t - 2.5) / 2.5))
                win.setProperty("zoom", z)
                win.setProperty("panX", cx - sx * z)
                win.setProperty("panY", cy - sy * z)
            else:
                res["panzoom"] = frame_stats(self.times) | sg_stats(self.sg)
                win.setProperty("zoom", z0)
                win.setProperty("panX", px0)
                win.setProperty("panY", py0)
                self.phase = "drag"
                self.times = []
                self.sg = []
                self.slot0 = ctrl.slot_seconds
                self.calls0 = ctrl.slot_calls
                self.t_phase = time.perf_counter()
            win.update()

        def step_drag(self, t: float) -> None:
            x0, y0 = self.hub0
            if t < 3.0:
                a = 2 * math.pi * t / 1.5
                ctrl.moveBusTo(hub, (x0 + 6 * math.cos(a)) * STEP, (y0 + 6 * math.sin(a)) * STEP)
                shape = win.findChild(QQuickItem, "branchShape")
                if shape is not None:  # variant b: time the re-tessellation (polish)
                    t0 = time.perf_counter()
                    shape.ensurePolished()
                    self.polish.append((time.perf_counter() - t0) * 1000)
                win.update()
                return
            stats = frame_stats(self.times) | sg_stats(self.sg)
            calls = ctrl.slot_calls - self.calls0
            stats["moved_bus"] = scene.buses[hub].name
            stats["bus_branches"] = len(ctrl._branches_of.get(hub, []))
            stats["slot_ms_mean"] = round((ctrl.slot_seconds - self.slot0) / calls * 1000, 3)
            if self.polish:
                stats["shape_polish_ms_mean"] = round(statistics.mean(self.polish), 3)
                stats["shape_polish_ms_max"] = round(max(self.polish), 3)
            res["drag"] = stats
            res["settle_ms"] = self.settle()
            # Render-thread Python callbacks must go before QTest.mouseClick: it spins
            # the event loop while holding the GIL, the render thread then blocks in
            # before_sync waiting for the GIL while the GUI thread waits for the sync.
            win.beforeSynchronizing.disconnect(before_sync)
            win.afterRendering.disconnect(after_render)
            ctrl.moveBusTo(hub, x0 * STEP, y0 * STEP)
            self.phase = "hit"
            QTimer.singleShot(200, self.hit_tests)

        def settle(self) -> dict[str, float]:
            """GUI-thread cost when the zoom settles: pen widths and SVG raster sizes
            change for every glyph (and variant b re-tessellates its Shape)."""
            out = {}
            shape = win.findChild(QQuickItem, "branchShape")
            for label, z in (("to_4x_fit", self.fit[0] * 4), ("back_to_fit", self.fit[0])):
                t0 = time.perf_counter()
                win.setProperty("settledZoom", z)
                if shape is not None:
                    shape.ensurePolished()
                out[label] = round((time.perf_counter() - t0) * 1000, 2)
            return out

        def hit_tests(self) -> None:
            x0, y0, x1, y1 = scene.bounds()
            tol = 6 / self.fit[0] / STEP  # 6 px at the fit zoom, in units
            pts = [(rnd.uniform(x0, x1), rnd.uniform(y0, y1)) for _ in range(10_000)]
            idx = SpatialIndex(scene)
            t0 = time.perf_counter()
            a = [idx.hit(x, y, tol) for x, y in pts]
            t_idx = time.perf_counter() - t0
            lin_pts = pts[:1000]  # the linear scan is slow; 1000 points are enough
            t0 = time.perf_counter()
            b = [hit_linear(scene, x, y, tol) for x, y in lin_pts]
            t_lin = time.perf_counter() - t0
            t0 = time.perf_counter()
            SpatialIndex(scene)
            t_build = time.perf_counter() - t0
            res["hit_test"] = {
                "points": len(pts),
                "hits": sum(h is not None for h in a),
                "index_vs_linear_mismatches": sum(p != q for p, q in zip(a, b, strict=False)),
                "index_us": round(t_idx / len(pts) * 1e6, 1),
                "linear_us": round(t_lin / len(lin_pts) * 1e6, 1),
                "index_build_ms": round(t_build * 1000, 2),
            }
            self.phase = "click_gap"
            self.click()

        def click(self) -> None:
            """Centre a random bus at 100 % zoom, wait for that frame, then click it."""
            if len(self.clicks) >= 30:
                self.finish()
                return
            bus = rnd.randrange(len(scene.buses))
            bx, by = ctrl.busPoint(bus)
            win.setProperty("zoom", 1.0)
            win.setProperty("settledZoom", 1.0)
            win.setProperty("panX", win.width() / 2 - bx)
            win.setProperty("panY", win.height() / 2 - by)
            self.phase = "click_pan"
            win.update()
            QTimer.singleShot(100, lambda: self.do_click(bus))

        def do_click(self, bus: int) -> None:
            zoom, px, py = self._props()
            bx, by = ctrl.busPoint(bus)
            pos = QPoint(round(px + bx * zoom), round(py + by * zoom))
            self.phase = "click_wait"
            self.click_t = time.perf_counter()
            QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
            handled = time.perf_counter()
            self.clicks.append(
                {
                    "handled_ms": (handled - self.click_t) * 1000,
                    "right": float(ctrl.selection == {("bus", bus)}),
                    "got": sorted(ctrl.selection),
                    "bus": scene.buses[bus].name,
                }
            )
            win.update()

        def finish(self) -> None:
            h = [c["handled_ms"] for c in self.clicks]
            f = [c["to_frame_ms"] for c in self.clicks]
            res["click"] = {
                "clicks": len(self.clicks),
                "selected_the_clicked_bus": int(sum(c["right"] for c in self.clicks)),
                "event_handled_ms_median": round(statistics.median(h), 2),
                "event_handled_ms_max": round(max(h), 2),
                "to_next_frame_ms_median": round(statistics.median(f), 2),
                "to_next_frame_ms_max": round(max(f), 2),
                "misses": [
                    {
                        "clicked": c["bus"],
                        "got": [(k, scene.buses[i].name if k == "bus" else i) for k, i in c["got"]],
                    }
                    for c in self.clicks
                    if not c["right"]
                ],
            }
            res["memory_mb"]["end"] = memory_mb()
            text = json.dumps(res, indent=1)
            print(text)
            if args.out:
                args.out.write_text(text, encoding="utf-8")
            self.log("done")
            app.quit()

    driver = Driver()
    win.frameSwapped.connect(driver.on_frame, Qt.ConnectionType.QueuedConnection)
    # Scene-graph CPU time per frame on the render thread: sync + render (recording);
    # the present / vsync wait comes after afterRendering and is not counted.
    sg_start = [0.0]

    def before_sync() -> None:
        sg_start[0] = time.perf_counter()

    def after_render() -> None:
        driver.sg.append((time.perf_counter() - sg_start[0]) * 1000)

    def give_up() -> None:
        driver.log(f"timeout in phase {driver.phase}")
        os._exit(3)  # never hang (a normal quit can hang in the render thread)

    QTimer.singleShot(90_000, give_up)
    code = app.exec()
    del engine
    return code


if __name__ == "__main__":
    os.environ.setdefault("QT_LOGGING_RULES", "qt.scenegraph.general=true;qt.rhi.general=true")
    os.environ.setdefault("QT_FORCE_STDERR_LOGGING", "1")
    sys.exit(main())
