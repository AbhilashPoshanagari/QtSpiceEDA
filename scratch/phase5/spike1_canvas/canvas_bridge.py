"""S1 spike: the QObject side of the test canvas (scratch only).

``SceneController`` serves the Scene to QML as list models (buses, branches, branch
symbols, shunts) and, for variant (b), as SVG path strings for the branches. Clicks,
rubber bands and bus drags come back as slots; hit-testing runs in Python on the
SpatialIndex, like SchematicController.itemAt does for the schematic. Positions sent
to QML are pixels at zoom 1 (diagram units * STEP).

The time spent inside the slots is summed in ``slot_seconds`` for the benchmark.
"""

from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    Signal,
    Slot,
)
from scene import Hit, Scene, SpatialIndex

STEP = 16.0  # pixels per diagram unit at zoom 1


class RowModel(QAbstractListModel):
    """A list of dict rows with fixed role names."""

    def __init__(self, roles: list[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._names = roles
        self._role = {Qt.ItemDataRole.UserRole + 1 + i: r for i, r in enumerate(roles)}
        self._rows: list[dict[str, Any]] = []

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> Any:
        name = self._role.get(role)
        if name is None or not index.isValid():
            return None
        return self._rows[index.row()][name]

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return {k: QByteArray(v.encode()) for k, v in self._role.items()}

    def reset(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def update(self, changed: dict[int, dict[str, Any]]) -> None:
        for i, row in changed.items():
            self._rows[i] = row
            ix = self.index(i, 0)
            self.dataChanged.emit(ix, ix)


class SceneController(QObject):
    sceneChanged = Signal()  # paths (variant b)
    selectionChanged = Signal()

    def __init__(self, scene: Scene, paths: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.scene = scene
        self.paths_enabled = paths
        self.index = SpatialIndex(scene)
        self._branches_of = scene.branches_of()
        self._shunts_of = scene.shunts_of()
        self.selection: set[Hit] = set()
        self.slot_seconds = 0.0
        self.slot_calls = 0
        self.buses = RowModel(["px", "py", "name", "selected"], self)
        self.branches = RowModel(["x1", "y1", "x2", "y2", "kind", "open", "selected"], self)
        self.symbols = RowModel(["mx", "my", "angle", "kind", "open", "selected"], self)
        self.shunts = RowModel(["px", "py", "bx", "by", "kind", "selected"], self)
        self._symbol_of = [i for i, br in enumerate(scene.branches) if br.kind != "line"]
        self._symbol_row = {b: r for r, b in enumerate(self._symbol_of)}
        self._seg: list[str] = []
        self._paths: dict[str, str] = {}
        self.buses.reset([self._bus_row(i) for i in range(len(scene.buses))])
        self.branches.reset([self._branch_row(i) for i in range(len(scene.branches))])
        self.symbols.reset([self._symbol_row_of(b) for b in self._symbol_of])
        self.shunts.reset([self._shunt_row(i) for i in range(len(scene.shunts))])
        self._seg = [self._segment(i) for i in range(len(scene.branches))]
        self._rebuild_paths()

    # ---- rows

    def _bus_row(self, i: int) -> dict[str, Any]:
        b = self.scene.buses[i]
        return {
            "px": b.x * STEP,
            "py": b.y * STEP,
            "name": b.name,
            "selected": ("bus", i) in self.selection,
        }

    def _ends(self, i: int) -> tuple[float, float, float, float]:
        br = self.scene.branches[i]
        p, q = self.scene.buses[br.bus1], self.scene.buses[br.bus2]
        return p.x * STEP, p.y * STEP, q.x * STEP, q.y * STEP

    def _branch_row(self, i: int) -> dict[str, Any]:
        x1, y1, x2, y2 = self._ends(i)
        br = self.scene.branches[i]
        return {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "kind": br.kind,
            "open": br.open,
            "selected": ("branch", i) in self.selection,
        }

    def _symbol_row_of(self, i: int) -> dict[str, Any]:
        x1, y1, x2, y2 = self._ends(i)
        br = self.scene.branches[i]
        return {
            "mx": (x1 + x2) / 2,
            "my": (y1 + y2) / 2,
            "angle": math.degrees(math.atan2(y2 - y1, x2 - x1)),
            "kind": br.kind,
            "open": br.open,
            "selected": ("branch", i) in self.selection,
        }

    def _shunt_row(self, i: int) -> dict[str, Any]:
        s = self.scene.shunts[i]
        x, y = self.scene.shunt_xy(s)
        b = self.scene.buses[s.bus]
        return {
            "px": x * STEP,
            "py": y * STEP,
            "bx": b.x * STEP,
            "by": b.y * STEP,
            "kind": s.kind,
            "selected": ("shunt", i) in self.selection,
        }

    # ---- variant (b): branch paths

    def _segment(self, i: int) -> str:
        x1, y1, x2, y2 = self._ends(i)
        return f"M{x1:g} {y1:g}L{x2:g} {y2:g}"

    def _rebuild_paths(self) -> None:
        if not self.paths_enabled:
            return
        groups: dict[str, list[str]] = {
            "line": [],
            "switch": [],
            "open": [],
            "transformer": [],
            "selected": [],
        }
        for i, br in enumerate(self.scene.branches):
            if ("branch", i) in self.selection:
                key = "selected"
            elif br.kind == "switch" and br.open:
                key = "open"
            else:
                key = br.kind
            groups[key].append(self._seg[i])
        self._paths = {k: " ".join(v) for k, v in groups.items()}
        self.sceneChanged.emit()

    def _path(self, key: str) -> str:
        return self._paths.get(key, "")

    linePath = Property(str, lambda self: self._path("line"), notify=sceneChanged)  # noqa: N815
    switchPath = Property(str, lambda self: self._path("switch"), notify=sceneChanged)  # noqa: N815
    openPath = Property(str, lambda self: self._path("open"), notify=sceneChanged)  # noqa: N815
    transformerPath = Property(  # noqa: N815
        str, lambda self: self._path("transformer"), notify=sceneChanged
    )
    selectedPath = Property(str, lambda self: self._path("selected"), notify=sceneChanged)  # noqa: N815

    busModel = Property(QObject, lambda self: self.buses, constant=True)  # noqa: N815
    branchModel = Property(QObject, lambda self: self.branches, constant=True)  # noqa: N815
    symbolModel = Property(QObject, lambda self: self.symbols, constant=True)  # noqa: N815
    shuntModel = Property(QObject, lambda self: self.shunts, constant=True)  # noqa: N815

    # ---- slots

    @Slot(result="QVariantList")
    def bounds(self) -> list[float]:
        x0, y0, x1, y1 = self.scene.bounds()
        return [x0 * STEP, y0 * STEP, x1 * STEP, y1 * STEP]

    @Slot(float, float, float, result="QVariantMap")
    def hitTest(self, x: float, y: float, tol: float) -> dict[str, Any]:  # noqa: N802
        t0 = time.perf_counter()
        hit = self.index.hit(x / STEP, y / STEP, tol / STEP)
        self._count(t0)
        return {} if hit is None else {"kind": hit[0], "index": hit[1]}

    def _apply_selection(self, new: set[Hit]) -> None:
        changed = self.selection ^ new
        self.selection = new
        rows: dict[str, dict[int, dict[str, Any]]] = {
            "bus": {},
            "branch": {},
            "symbol": {},
            "shunt": {},
        }
        for kind, i in changed:
            if kind == "bus":
                rows["bus"][i] = self._bus_row(i)
            elif kind == "shunt":
                rows["shunt"][i] = self._shunt_row(i)
            else:
                rows["branch"][i] = self._branch_row(i)
                if i in self._symbol_row:
                    rows["symbol"][self._symbol_row[i]] = self._symbol_row_of(i)
        self.buses.update(rows["bus"])
        self.shunts.update(rows["shunt"])
        self.branches.update(rows["branch"])
        self.symbols.update(rows["symbol"])
        if any(k == "branch" for k, _ in changed):
            self._rebuild_paths()
        self.selectionChanged.emit()

    @Slot(float, float, float, bool, result="QVariantMap")
    def clickAt(self, x: float, y: float, tol: float, add: bool) -> dict[str, Any]:  # noqa: N802
        """Select what is under (x, y) (Ctrl: toggle); nothing there clears."""
        t0 = time.perf_counter()
        hit = self.index.hit(x / STEP, y / STEP, tol / STEP)
        if add:
            new = set(self.selection)
            if hit is not None:
                new ^= {hit}
        else:
            new = set() if hit is None else {hit}
        self._apply_selection(new)
        self._count(t0)
        return {} if hit is None else {"kind": hit[0], "index": hit[1]}

    @Slot(float, float, float, float, bool)
    def selectRect(self, x1: float, y1: float, x2: float, y2: float, add: bool) -> None:  # noqa: N802
        t0 = time.perf_counter()
        found = set(self.index.in_rect(x1 / STEP, y1 / STEP, x2 / STEP, y2 / STEP))
        self._apply_selection((self.selection | found) if add else found)
        self._count(t0)

    @Slot(int, float, float)
    def moveBusTo(self, bus: int, x: float, y: float) -> None:  # noqa: N802
        """Move a bus glyph (snapped to the grid); its branches and shunts follow."""
        t0 = time.perf_counter()
        b = self.scene.buses[bus]
        nx, ny = round(x / STEP), round(y / STEP)
        if (nx, ny) == (b.x, b.y):
            self._count(t0)
            return
        b.x, b.y = nx, ny
        branches = self._branches_of.get(bus, [])
        shunts = self._shunts_of.get(bus, [])
        self.index.move_bus(bus, branches, shunts)
        self.buses.update({bus: self._bus_row(bus)})
        self.branches.update({i: self._branch_row(i) for i in branches})
        self.symbols.update(
            {self._symbol_row[i]: self._symbol_row_of(i) for i in branches if i in self._symbol_row}
        )
        self.shunts.update({i: self._shunt_row(i) for i in shunts})
        if self.paths_enabled:
            for i in branches:
                self._seg[i] = self._segment(i)
            self._rebuild_paths()
        self._count(t0)

    @Slot(int, result="QVariantList")
    def busPoint(self, bus: int) -> list[float]:  # noqa: N802
        b = self.scene.buses[bus]
        return [b.x * STEP, b.y * STEP]

    def _count(self, t0: float) -> None:
        self.slot_seconds += time.perf_counter() - t0
        self.slot_calls += 1
