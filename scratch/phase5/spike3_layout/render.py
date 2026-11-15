"""S3 spike: draw layouts side by side into a PNG with QPainter (offscreen; scratch only).

Branches are straight segments between bus centres (S1 variant b): lines grey,
switches blue, open branches dashed red, transformers green. Bus bars black, shunts as
small squares under their bus. Red circles: crossings. Orange outline: overlapping
glyphs. Magenta bus: placed by place_missing (mixed case).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# the offscreen platform has no font database of its own on Windows
if os.name == "nt":
    os.environ.setdefault(
        "QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
    )

from feeders import glyph_boxes, measure, segments  # noqa: E402
from layout import Graph, Pos  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen  # noqa: E402

PANEL = 900
TITLE_H = 54
MARGIN = 20


@dataclass
class Panel:
    title: str
    graph: Graph
    pos: Pos
    highlight: list[str] = field(default_factory=list)


_app: QGuiApplication | None = None


def _ensure_app() -> None:
    global _app
    if QGuiApplication.instance() is None:
        _app = QGuiApplication([])


def _kind(graph: Graph, a: str, b: str) -> tuple[str, bool]:
    kinds = [(e.kind, e.closed) for e in graph.edges if {e.a, e.b} == {a, b}]
    if any(k == "transformer" for k, _ in kinds):
        return "transformer", all(c for _, c in kinds)
    return kinds[0][0], all(c for _, c in kinds)


def _draw_panel(p: QPainter, x0: float, panel: Panel, labels: bool) -> None:
    g, pos = panel.graph, panel.pos
    m = measure(g, pos)
    boxes = glyph_boxes(g, pos)
    xs = [v for bs in boxes.values() for bx in bs for v in (bx[0], bx[2])]
    ys = [v for bs in boxes.values() for bx in bs for v in (bx[1], bx[3])]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    area = PANEL - 2 * MARGIN
    s = min(area / w, (area - TITLE_H) / h)
    ox = x0 + MARGIN + (area - w * s) / 2 - min(xs) * s
    oy = MARGIN + TITLE_H + (area - TITLE_H - h * s) / 2 - min(ys) * s

    def pt(x: float, y: float) -> QPointF:
        return QPointF(ox + x * s, oy + y * s)

    p.setPen(QColor("black"))
    p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
    p.drawText(QRectF(x0 + MARGIN, 8, area, 22), Qt.AlignmentFlag.AlignLeft, panel.title)
    p.setFont(QFont("Segoe UI", 9))
    p.drawText(
        QRectF(x0 + MARGIN, 30, area, 20),
        Qt.AlignmentFlag.AlignLeft,
        f"{m.buses} buses, {m.segments} branches | crossings {m.crossings} | "
        f"glyph overlaps {m.overlaps} | through a glyph {m.through} | "
        f"branch mean/max {m.mean_len:.1f}/{m.max_len:.0f} | {m.width:.0f} x {m.height:.0f} units",
    )
    colours = {"line": "#7a7a7a", "switch": "#1f5fbf", "transformer": "#1a8a3a"}
    for a, b in segments(g, pos):
        kind, closed = _kind(g, a, b)
        pen = QPen(
            QColor(colours[kind] if closed else "#d02020"), 2.2 if kind == "transformer" else 1.4
        )
        if not closed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(pt(*pos[a]), pt(*pos[b]))
    bad = set(m.overlap_buses)
    for bus, bs in boxes.items():
        bar, *shunts = bs
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#d000d0") if bus in panel.highlight else QColor("black"))
        p.drawRect(QRectF(pt(bar[0], bar[1]), pt(bar[2], bar[3])))
        p.setBrush(QColor("#f0c060"))
        p.setPen(QPen(QColor("#806020"), 0.8))
        for sb in shunts:
            p.drawLine(pt((sb[0] + sb[2]) / 2, bar[3]), pt((sb[0] + sb[2]) / 2, sb[1]))
            p.drawRect(QRectF(pt(sb[0], sb[1]), pt(sb[2], sb[3])))
        if bus in bad:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#ff8000"), 2.0))
            x_0 = min(b[0] for b in bs)
            y_0 = min(b[1] for b in bs)
            x_1 = max(b[2] for b in bs)
            y_1 = max(b[3] for b in bs)
            p.drawRect(QRectF(pt(x_0, y_0), pt(x_1, y_1)).adjusted(-2, -2, 2, 2))
    if labels:
        p.setPen(QColor("#202060"))
        p.setFont(QFont("Segoe UI", 7))
        for bus, (x, y) in pos.items():
            q = pt(x, y)
            p.drawText(QPointF(q.x() + 2, q.y() - 3), bus)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#e00000"), 2.0))
    for cx, cy in m.crossing_points:
        p.drawEllipse(pt(cx, cy), 7, 7)


def save(path: Path, panels: list[Panel], labels: bool = True) -> None:
    _ensure_app()
    img = QImage(PANEL * len(panels), PANEL, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i, panel in enumerate(panels):
        _draw_panel(p, i * PANEL, panel, labels)
        if i:
            p.setPen(QPen(QColor("#c0c0c0"), 1))
            p.drawLine(i * PANEL, 0, i * PANEL, PANEL)
    p.end()
    img.save(str(path))
