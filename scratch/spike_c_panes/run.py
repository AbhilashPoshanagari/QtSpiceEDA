"""Spike C: three stacked GraphsViews, shared X range, one cursor line, 1 M points each.

Throwaway. Zooms in and out automatically and reports the frame rate.
    --mode decimated  (default) re-reduce each trace to min/max per pixel on every zoom
    --mode pyramid    like decimated, from precomputed min/max levels (O(pixels))
    --mode axisonly   fill once from the pyramid, then only move the axes (baseline)
    --mode raw        put all 1 M points into each LineSeries once; zoom moves the axis
Run:  .venv/Scripts/python.exe scratch/spike_c_panes/run.py [--mode raw] [--seconds 5]
      add --interactive to keep the window open (wheel = zoom, move mouse = cursor).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QPointF, QTimer, QUrl, Slot
from PySide6.QtGraphs import QXYSeries
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from core.sim.decimate import minmax_buckets  # noqa: E402

N = 1_000_000


class Data(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.x = np.linspace(0.0, 1e-3, N)
        rng = np.random.default_rng(1)
        self.y = [
            np.sin(2 * np.pi * 5e3 * self.x),
            np.sign(np.sin(2 * np.pi * 1e3 * self.x)) * 2.0,
            np.sin(2 * np.pi * 2e4 * self.x) * np.exp(-self.x * 2e3) + rng.normal(0, 0.05, N),
        ]
        self.fill_ms: list[float] = []
        self.points = 0
        self.pyramid = False
        self.levels = [self._pyramid(y) for y in self.y]

    def _pyramid(self, y: np.ndarray) -> list[tuple[int, np.ndarray, np.ndarray]]:
        """(block size, block mins, block maxs) for block sizes 4, 16, 64, ..."""
        levels = []
        block = 4
        while len(y) // block >= 256:
            n = len(y) // block * block
            shaped = y[:n].reshape(-1, block)
            levels.append((block, shaped.min(axis=1), shaped.max(axis=1)))
            block *= 4
        return levels

    def _from_pyramid(
        self, pane: int, x0: float, x1: float, pixels: int
    ) -> tuple[np.ndarray, np.ndarray]:
        a = max(int(np.searchsorted(self.x, x0)) - 1, 0)
        b = min(int(np.searchsorted(self.x, x1, "right")) + 1, N)
        best = None
        for block, lo, hi in self.levels[pane]:  # coarsest level that still has >= pixels blocks
            if (b - a) // block >= pixels:
                best = (block, lo, hi)
        if best is None:
            return minmax_buckets(self.x, self.y[pane], x0, x1, pixels)
        block, lo, hi = best
        i, j = a // block, min(-(-b // block), len(lo))
        xs = np.repeat(self.x[i * block : j * block : block], 2)
        ys = np.empty(2 * (j - i))
        ys[0::2], ys[1::2] = lo[i:j], hi[i:j]
        return xs, ys

    @Slot(QObject, int, float, float, int)
    def fill(self, series: QObject, pane: int, x0: float, x1: float, pixels: int) -> None:
        if not isinstance(series, QXYSeries):
            return
        start = time.perf_counter()
        if self.pyramid:
            xs, ys = self._from_pyramid(pane, x0, x1, max(1, pixels))
        else:
            xs, ys = minmax_buckets(self.x, self.y[pane], x0, x1, max(1, pixels))
        series.replace([QPointF(a, b) for a, b in zip(xs.tolist(), ys.tolist(), strict=True)])
        self.fill_ms.append((time.perf_counter() - start) * 1e3)
        self.points = len(xs)

    @Slot(QObject, int)
    def fillAll(self, series: QObject, pane: int) -> None:  # noqa: N802 - QML API
        if not isinstance(series, QXYSeries):
            return
        start = time.perf_counter()
        series.replace(
            [QPointF(a, b) for a, b in zip(self.x.tolist(), self.y[pane].tolist(), strict=True)]
        )
        self.fill_ms.append((time.perf_counter() - start) * 1e3)
        self.points = N


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("decimated", "pyramid", "raw", "axisonly"), default="decimated"
    )
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--grab", help="save a frame at full zoom to this PNG")
    args = parser.parse_args()

    app = QGuiApplication(sys.argv)
    data = Data()
    data.pyramid = args.mode in ("pyramid", "axisonly")
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.setInitialProperties(
        {"store": data, "raw": args.mode == "raw", "frozen": args.mode == "axisonly"}
    )
    view.resize(1200, 800)
    view.setTitle(f"Spike C - 3 panes x 1M points ({args.mode})")
    load_start = time.perf_counter()
    view.setSource(QUrl.fromLocalFile(str(HERE / "panes.qml")))
    if view.status() == QQuickView.Status.Error:
        for err in view.errors():
            print(err.toString())
        return 1
    view.show()
    root = view.rootObject()
    frames: list[float] = []
    view.frameSwapped.connect(lambda: frames.append(time.perf_counter()))

    def start_zoom() -> None:
        print(f"[{args.mode}] window up after {time.perf_counter() - load_start:.2f} s")
        if args.grab:
            view.grabWindow().save(args.grab)
        data.fill_ms.clear()
        frames.clear()
        root.setProperty("animating", True)
        QTimer.singleShot(int(args.seconds * 1000), report)

    def report() -> None:
        root.setProperty("animating", False)
        span = frames[-1] - frames[0] if len(frames) > 1 else 0.0
        fps = (len(frames) - 1) / span if span else 0.0
        gaps = np.diff(frames) * 1e3 if len(frames) > 2 else np.zeros(1)
        fills = np.array(data.fill_ms or [0.0])
        print(
            f"[{args.mode}] {len(frames)} frames in {span:.2f} s -> {fps:.1f} fps; "
            f"frame gap median {np.median(gaps):.1f} ms, worst {gaps.max():.1f} ms; "
            f"series fill median {np.median(fills):.2f} ms, worst {fills.max():.1f} ms "
            f"({len(data.fill_ms)} fills, last {data.points} points/series)"
        )
        if not args.interactive:
            app.quit()

    QTimer.singleShot(1500, start_zoom)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
