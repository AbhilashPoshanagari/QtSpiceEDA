"""DiagramController: the QML-facing object for function blocks and block diagrams.

QML uses it as ``import QtSpice`` / ``DiagramController.addBlock(...)``. It owns:

- the block library: the built-in blocks plus the blocks written in the function
  editor (``library``, ``apply``, ``check``, ``newDocument``, ``openBlock``);
- the block diagram on the canvas, shown to QML as two list models, ``blocks`` and
  ``wires``. Every edit goes through a QUndoStack, so ``undo()`` / ``redo()`` work for
  all of them. Selection is view state and is not undoable;
- diagram runs: ``run(t_end, dt)`` checks the diagram here (no user code runs in this
  process), then hands it to a BlockRunner on a background thread. Scope results go to
  the attached WaveformModel.

Function editor: ``apply(code)`` reads the code with the registry (which never executes
it). Without errors the document's blocks replace its previous ones and ``library``
updates; with errors the library keeps the last good version. ``diagnostics`` is a list
of {line, col, severity, message}; ``removedPorts`` lists the ports that disappeared in
the last successful apply, as "block: input a".

When a successful ``apply`` changes a block that is already placed, the placed blocks
get their new size and ports and keep the wires on ports that still exist. Wires on
ports that are gone, placed blocks whose function is gone, and settings that are gone
are removed in one undo step; undo brings back only what is valid again. Because the
library itself is not on the undo stack, every undo step skips what no longer exists.

Projects: ``export_state()`` gives the user block code, the diagram and the run
settings (``tEnd``, ``dt``) for saving; ``load_state(...)`` replaces all of them, clears
the undo stack and sends ``documentReplaced(code)`` so the function editor shows the
loaded code. Loading only reads the code, like ``apply``; nothing is executed.
``sourcesChanged`` is sent whenever the saved code of a document changes.

Canvas geometry (block size, port positions, wire bends) is computed here, in canvas
units, so QML only draws it and hit-testing (``portAt``, ``wireAt``) uses the same
numbers. Positions snap to a ``GRID`` unit grid.

Physical blocks (U1). The library also holds the built-in physical components
(``core.physical.library``). Their conserving ports are placed below the signal ports,
the first half on the left edge and the rest on the right; each carries its domain
and the domain's colour from the Domain registry (``domainColors``). Physical wires
(``physicalWires``) are undirected and drawn as right-angled polylines; a port with
two or more physical wires gets a junction dot (``junctions``). ``canConnectPhysical``
gives the reason a physical wire is refused (``Diagram.check_physical_wire``), and
connecting, disconnecting and removing physical wires are undo steps like the rest.
"""

from __future__ import annotations

import ast
import math
import re
import threading
import weakref
from dataclasses import dataclass, field

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QCoreApplication,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QUndoCommand, QUndoStack
from PySide6.QtQml import QmlElement, QmlSingleton

from core.blocks.diagram import BlockInstance, Diagram, DiagramError, PhysicalWire, Wire
from core.blocks.engine import AlgebraicLoopError, ExecutionEngine
from core.blocks.library import builtin_specs
from core.blocks.registry import BlockRegistry, SourceResult
from core.blocks.runner import DEFAULT_TIMEOUT, BlockRunner, RunOutcome
from core.blocks.spec import BlockSpec, is_plain_constant
from core.physical.domains import ANY_DOMAIN, DOMAINS
from core.physical.library import physical_registry

QML_IMPORT_NAME = "QtSpice"
QML_IMPORT_MAJOR_VERSION = 1

GRID = 20
PORT_PITCH = 20
MIN_SIDE = 60
PORT_RADIUS = 10  # hit radius of a port, in canvas units
SCOPE = "Scope"
ANY_COLOR = "#4a5568"  # the Solver Configuration port joins any domain
WIRE_STUB = 10.0  # a physical wire leaves its port straight for this long
DEFAULT_T_END = 1.0
DEFAULT_DT = 0.001
_DOCUMENT_ID = re.compile(r"^function(\d+)$")


@dataclass
class DiagramState:
    """What a project stores of the block side: code, canvas and run settings."""

    sources: list[tuple[str, str]] = field(default_factory=list)  # (document, code)
    diagram: dict = field(default_factory=lambda: {"blocks": [], "wires": []})
    t_end: float = DEFAULT_T_END
    dt: float = DEFAULT_DT


def domain_color(domain: str | None) -> str:
    """The colour of a domain's ports and wires (from the Domain registry)."""
    found = DOMAINS.get(domain or "")
    return found.color if found is not None else ANY_COLOR


def spec_to_dict(spec: BlockSpec, builtin: bool) -> dict[str, object]:
    """A library entry as QML sees it."""
    return {
        "name": spec.name,
        "shape": spec.shape,
        "color": spec.color,
        "inputs": list(spec.inputs),
        "outputs": list(spec.outputs),
        "conserving": [
            {"name": p.name, "domain": p.domain, "color": domain_color(p.domain)}
            for p in spec.conserving
        ],
        "physical": spec.is_physical,
        "settings": [{"name": k, "value": v} for k, v in spec.settings.items()],
        "stateful": spec.stateful,
        "doc": spec.doc,
        "builtin": builtin,
    }


# ---- geometry


def snap(value: float) -> float:
    return float(round(value / GRID) * GRID)


@dataclass(frozen=True)
class BlockGeometry:
    """Size of a block and its ports' positions relative to its top-left corner."""

    width: float
    height: float
    inputs: list[dict[str, object]]  # [{name, x, y}]
    outputs: list[dict[str, object]]
    conserving: list[dict[str, object]] = field(default_factory=list)
    """[{name, x, y, side, domain, color}]; side is "left" or "right"."""


def block_geometry(spec: BlockSpec) -> BlockGeometry:
    """Ports evenly spaced and centred; inputs on the left edge, outputs on the right.

    Conserving ports come after the signal ports of their edge: the first half of
    them (rounded up) on the left, the rest on the right.
    """
    cons = list(spec.conserving)
    n_left = (len(cons) + 1) // 2
    left_cons, right_cons = cons[:n_left], cons[n_left:]
    ports = max(len(spec.inputs) + len(left_cons), len(spec.outputs) + len(right_cons), 1)
    height = float(max(MIN_SIDE, GRID * math.ceil((ports + 1) * PORT_PITCH / GRID)))
    width = height * 1.5 if spec.shape == "rectangle" else height
    width = float(GRID * math.ceil(width / GRID))

    def inset(y: float) -> float:
        # Ports sit on the outline: circles curve away from the bounding box.
        if spec.shape != "circle":
            return 0.0
        r = height / 2
        dy = y - r
        return r - math.sqrt(max(0.0, r * r - dy * dy))

    def place(names: list[str], left: bool) -> list[dict[str, object]]:
        first = height / 2 - (len(names) - 1) * PORT_PITCH / 2
        out = []
        for i, name in enumerate(names):
            y = first + i * PORT_PITCH
            x = inset(y) if left else width - inset(y)
            out.append({"name": name, "x": x, "y": y})
        return out

    left = place([*spec.inputs, *(p.name for p in left_cons)], True)
    right = place([*spec.outputs, *(p.name for p in right_cons)], False)
    conserving = []
    for side, placed, group in (
        ("left", left[len(spec.inputs) :], left_cons),
        ("right", right[len(spec.outputs) :], right_cons),
    ):
        for where, port in zip(placed, group, strict=True):
            conserving.append(
                {**where, "side": side, "domain": port.domain, "color": domain_color(port.domain)}
            )
    return BlockGeometry(
        width, height, left[: len(spec.inputs)], right[: len(spec.outputs)], conserving
    )


def physical_route(
    x1: float, y1: float, side1: str, x2: float, y2: float, side2: str
) -> list[dict[str, float]]:
    """Corner points of a physical wire: out of each port for ``WIRE_STUB``, then a
    vertical run half way between the two stubs."""
    s1 = x1 + (-WIRE_STUB if side1 == "left" else WIRE_STUB)
    s2 = x2 + (-WIRE_STUB if side2 == "left" else WIRE_STUB)
    mx = (s1 + s2) / 2
    raw = [(x1, y1), (s1, y1), (mx, y1), (mx, y2), (s2, y2), (x2, y2)]
    points: list[tuple[float, float]] = []
    for p in raw:
        if not points or points[-1] != p:
            points.append(p)
    return [{"x": x, "y": y} for x, y in points]


def wire_bend(x1: float, x2: float) -> float:
    """Horizontal distance of a wire's control points from its ends."""
    return max(30.0, abs(x2 - x1) / 2)


def _bezier_points(x1: float, y1: float, x2: float, y2: float, n: int = 24) -> list[tuple]:
    d = wire_bend(x1, x2)
    c1x, c2x = x1 + d, x2 - d
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        x = u**3 * x1 + 3 * u * u * t * c1x + 3 * u * t * t * c2x + t**3 * x2
        y = u**3 * y1 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y2
        pts.append((x, y))
    return pts


def _segment_distance(px: float, py: float, a: tuple, b: tuple) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


# ---- settings text


def format_setting(value: object) -> str:
    """A setting as the properties panel shows it."""
    if isinstance(value, str):
        return value
    return repr(value)


def parse_setting(text: str, default: object) -> object:
    """Read ``text`` as a value of the default's type. Raises ValueError."""
    text = text.strip() if not isinstance(default, str) else text
    if isinstance(default, bool):
        lowered = text.lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"{text!r} is not true or false")
    if isinstance(default, int):
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"{text!r} is not a whole number") from None
    if isinstance(default, float):
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{text!r} is not a number") from None
        if not math.isfinite(value):
            raise ValueError(f"{text!r} is not a finite number")
        return value
    if isinstance(default, str):
        return text
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        raise ValueError(f"{text!r} is not a constant such as 1.0, 'a' or [1, 2]") from None
    if not is_plain_constant(value):
        raise ValueError(f"{text!r} is not a number, string, bool, None or a list of those")
    return list(value) if isinstance(value, tuple) else value


# ---- list models


class RowModel(QAbstractListModel):
    """A list of dict rows with fixed role names. ``count`` is exposed to QML."""

    countChanged = Signal()

    def __init__(self, names: list[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._names = names
        self._role_of = {
            name: int(Qt.ItemDataRole.UserRole) + 1 + i for i, name in enumerate(names)
        }
        self._name_of = {role: name for name, role in self._role_of.items()}
        self._rows: list[dict[str, object]] = []

    def rowCount(  # noqa: N802 - Qt API
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        name = self._name_of.get(role)
        return None if name is None else self._rows[index.row()].get(name)

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802 - Qt API
        return {role: QByteArray(name.encode()) for role, name in self._name_of.items()}

    def _get_count(self) -> int:
        return len(self._rows)

    count = Property(int, _get_count, notify=countChanged)

    # ---- Python side

    def rows(self) -> list[dict[str, object]]:
        return self._rows

    def row(self, i: int) -> dict[str, object]:
        return self._rows[i]

    def append(self, row: dict[str, object]) -> None:
        n = len(self._rows)
        self.beginInsertRows(QModelIndex(), n, n)
        self._rows.append(row)
        self.endInsertRows()
        self.countChanged.emit()

    def remove(self, i: int) -> None:
        self.beginRemoveRows(QModelIndex(), i, i)
        del self._rows[i]
        self.endRemoveRows()
        self.countChanged.emit()

    def update(self, i: int, **changes: object) -> None:
        row = self._rows[i]
        changed = [k for k, v in changes.items() if row.get(k) != v]
        if not changed:
            return
        row.update(changes)
        idx = self.index(i, 0)
        self.dataChanged.emit(idx, idx, [self._role_of[k] for k in changed])


BLOCK_ROLES = [
    "blockId",
    "specName",
    "blockX",
    "blockY",
    "blockWidth",
    "blockHeight",
    "shape",
    "color",
    "inputs",
    "outputs",
    "conserving",
    "selected",
    "inLoop",
]
WIRE_ROLES = ["src", "dst", "x1", "y1", "x2", "y2", "bend", "selected"]
PHYSICAL_WIRE_ROLES = ["a", "b", "domain", "color", "points", "selected"]


# ---- undo commands


class _Command(QUndoCommand):
    """Base of the undo commands.

    The QUndoStack (a child of the controller) owns the commands, so a command keeps
    only a weak reference back to the controller: a strong one would make a cycle
    through C++ ownership, and freeing it at exit corrupts the heap.
    """

    def __init__(self, ctl: DiagramController, text: str) -> None:
        super().__init__(text)
        self._ctl = weakref.ref(ctl)

    @property
    def ctl(self) -> DiagramController:
        ctl = self._ctl()
        if ctl is None:
            raise RuntimeError("the DiagramController is gone")
        return ctl


class _AddBlock(_Command):
    def __init__(self, ctl: DiagramController, inst: BlockInstance) -> None:
        super().__init__(ctl, f"add {inst.id}")
        self.inst = inst

    def redo(self) -> None:
        self.ctl._insert_block(self.inst)

    def undo(self) -> None:
        self.ctl._delete_block(self.inst.id)


class _Remove(_Command):
    def __init__(
        self,
        ctl: DiagramController,
        blocks: list[BlockInstance],
        wires: list[Wire],
        physical: list[PhysicalWire] | None = None,
    ):
        physical = physical or []
        names = [b.id for b in blocks] + [f"{w.src}→{w.dst}" for w in wires]
        names += [f"{w.a}—{w.b}" for w in physical]
        super().__init__(ctl, "remove " + ", ".join(names))
        self.blocks = blocks
        self.wires = wires
        self.physical = physical

    def redo(self) -> None:
        for pwire in self.physical:
            self.ctl._delete_physical_wire(pwire)
        for wire in self.wires:
            self.ctl._delete_wire(wire)
        for inst in self.blocks:
            self.ctl._delete_block(inst.id)

    def undo(self) -> None:
        for inst in self.blocks:
            self.ctl._insert_block(inst)
        for wire in self.wires:
            self.ctl._insert_wire(wire)
        for pwire in self.physical:
            self.ctl._insert_physical_wire(pwire)


class _Move(_Command):
    ID = 1

    def __init__(
        self, ctl: DiagramController, session: int, moves: dict[str, tuple[tuple, tuple]]
    ) -> None:
        super().__init__(ctl, "move " + ", ".join(moves))
        self.session = session
        self.moves = moves  # id -> ((old x, old y), (new x, new y))

    def id(self) -> int:
        return self.ID

    def mergeWith(self, other: QUndoCommand) -> bool:  # noqa: N802 - Qt API
        if not isinstance(other, _Move) or other.session != self.session:
            return False
        if set(other.moves) != set(self.moves):
            return False
        self.moves = {k: (self.moves[k][0], other.moves[k][1]) for k in self.moves}
        return True

    def redo(self) -> None:
        for bid, (_, new) in self.moves.items():
            self.ctl._place_block(bid, *new)

    def undo(self) -> None:
        for bid, (old, _) in self.moves.items():
            self.ctl._place_block(bid, *old)


class _Connect(_Command):
    def __init__(self, ctl: DiagramController, wire: Wire) -> None:
        super().__init__(ctl, f"connect {wire.src}→{wire.dst}")
        self.wire = wire

    def redo(self) -> None:
        self.ctl._insert_wire(self.wire)

    def undo(self) -> None:
        self.ctl._delete_wire(self.wire)


class _Disconnect(_Connect):
    def __init__(self, ctl: DiagramController, wire: Wire) -> None:
        super().__init__(ctl, wire)
        self.setText(f"disconnect {wire.src}→{wire.dst}")

    def redo(self) -> None:
        super().undo()

    def undo(self) -> None:
        super().redo()


class _PhysicalConnect(_Command):
    def __init__(self, ctl: DiagramController, wire: PhysicalWire) -> None:
        super().__init__(ctl, f"connect {wire.a}—{wire.b}")
        self.wire = wire

    def redo(self) -> None:
        self.ctl._insert_physical_wire(self.wire)

    def undo(self) -> None:
        self.ctl._delete_physical_wire(self.wire)


class _PhysicalDisconnect(_PhysicalConnect):
    def __init__(self, ctl: DiagramController, wire: PhysicalWire) -> None:
        super().__init__(ctl, wire)
        self.setText(f"disconnect {wire.a}—{wire.b}")

    def redo(self) -> None:
        super().undo()

    def undo(self) -> None:
        super().redo()


class _SetSettings(_Command):
    def __init__(
        self, ctl: DiagramController, block_id: str, name: str, old: dict, new: dict
    ) -> None:
        super().__init__(ctl, f"set {block_id}.{name}")
        self.block_id = block_id
        self.old = old
        self.new = new

    def redo(self) -> None:
        self.ctl._apply_settings(self.block_id, self.new)

    def undo(self) -> None:
        self.ctl._apply_settings(self.block_id, self.old)


class _LibraryUpdate(_Command):
    """Removes what a function-editor change made invalid: wires on ports that are
    gone, placed blocks whose function is gone, and settings that are gone.

    Undo puts back only what is valid again (for example after the port is restored
    in the editor); the rest is skipped by the primitives.
    """

    def __init__(
        self,
        ctl: DiagramController,
        names: list[str],
        blocks: list[BlockInstance],
        wires: list[Wire],
        settings: dict[str, tuple[dict, dict]],
    ) -> None:
        parts = [f"remove {b.id}" for b in blocks] + [f"remove wire {w.src}→{w.dst}" for w in wires]
        parts += [f"drop settings of {bid}" for bid in settings]
        super().__init__(ctl, f"update {', '.join(names)}: " + ", ".join(parts))
        self.blocks = blocks
        self.wires = wires
        self.settings = settings  # id -> (old, new)

    def redo(self) -> None:
        for wire in self.wires:
            self.ctl._delete_wire(wire)
        for inst in self.blocks:
            self.ctl._delete_block(inst.id)
        for bid, (_, new) in self.settings.items():
            self.ctl._apply_settings(bid, new)

    def undo(self) -> None:
        for inst in self.blocks:
            self.ctl._insert_block(inst)
        for bid, (old, _) in self.settings.items():
            self.ctl._apply_settings(bid, old)
        for wire in self.wires:
            self.ctl._insert_wire(wire)


# ---- the controller


@QmlElement
@QmlSingleton
class DiagramController(QObject):
    """Block library, function-editor checking, and the block diagram canvas."""

    libraryChanged = Signal()
    diagnosticsChanged = Signal()
    removedPortsChanged = Signal()
    statusTextChanged = Signal()
    documentChanged = Signal()
    hintChanged = Signal()
    selectionChanged = Signal()
    undoStateChanged = Signal()
    runningChanged = Signal()
    runStatusChanged = Signal()
    resultReady = Signal()
    sourcesChanged = Signal()
    runSettingsChanged = Signal()
    junctionsChanged = Signal()
    diagramEdited = Signal()  # an undo-stack step was done, undone or redone
    documentReplaced = Signal(str)  # code of the current document after load_state
    _runDone = Signal(object)  # RunOutcome, emitted from the run thread

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._registry = BlockRegistry(builtin_specs())
        self._physical = physical_registry()
        self._library: list[dict[str, object]] = []
        self._diagnostics: list[dict[str, object]] = []
        self._removed_ports: list[str] = []
        self._status_text = ""
        self._next_document = 1
        self._document = self._new_document_id()
        self._refresh_library()

        self._diagram = Diagram(self._spec)
        self._blocks = RowModel(BLOCK_ROLES, self)
        self._wires = RowModel(WIRE_ROLES, self)
        self._physical_wires = RowModel(PHYSICAL_WIRE_ROLES, self)
        self._junctions: list[dict[str, object]] = []
        self._selected_physical: set[PhysicalWire] = set()
        self._geometry: dict[str, BlockGeometry] = {}
        self._specs_of: dict[str, BlockSpec] = {}  # spec each placed block was drawn from
        self._selected_blocks: set[str] = set()
        self._selected_wires: set[Wire] = set()
        self._loop_blocks: set[str] = set()
        self._hint = ""
        self._move_session = 1

        self._undo = QUndoStack(self)
        for signal in (
            self._undo.canUndoChanged,
            self._undo.canRedoChanged,
            self._undo.undoTextChanged,
            self._undo.redoTextChanged,
        ):
            signal.connect(self.undoStateChanged)
        # Relayed, not exposed: the stack outlives this object's connections while it
        # is destroyed, and its last indexChanged must not reach other objects.
        self._undo.indexChanged.connect(self.diagramEdited)

        self._runner: BlockRunner | None = None
        self._running = False
        self._run_status = ""
        self._run_details = ""
        self._waveforms: QObject | None = None
        self._result = None
        self._t_end = DEFAULT_T_END
        self._dt = DEFAULT_DT
        self.timeout = DEFAULT_TIMEOUT
        self._runDone.connect(self._on_run_done, Qt.ConnectionType.QueuedConnection)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    # ---- library properties

    def _get_library(self) -> list[dict[str, object]]:
        return self._library

    def _get_diagnostics(self) -> list[dict[str, object]]:
        return self._diagnostics

    def _get_removed_ports(self) -> list[str]:
        return self._removed_ports

    def _get_status_text(self) -> str:
        return self._status_text

    def _get_document(self) -> str:
        return self._document

    library = Property("QVariantList", _get_library, notify=libraryChanged)
    diagnostics = Property("QVariantList", _get_diagnostics, notify=diagnosticsChanged)
    removedPorts = Property(  # noqa: N815
        "QStringList", _get_removed_ports, notify=removedPortsChanged
    )
    statusText = Property(str, _get_status_text, notify=statusTextChanged)  # noqa: N815
    document = Property(str, _get_document, notify=documentChanged)

    # ---- diagram properties

    def _get_blocks(self) -> QObject:
        return self._blocks

    def _get_wires(self) -> QObject:
        return self._wires

    def _get_physical_wires(self) -> QObject:
        return self._physical_wires

    def _get_junctions(self) -> list[dict[str, object]]:
        return self._junctions

    def _get_domain_colors(self) -> dict[str, str]:
        return {**{d: domain_color(d) for d in DOMAINS}, ANY_DOMAIN: ANY_COLOR}

    def _get_hint(self) -> str:
        return self._hint

    def _get_can_undo(self) -> bool:
        return self._undo.canUndo()

    def _get_can_redo(self) -> bool:
        return self._undo.canRedo()

    def _get_undo_text(self) -> str:
        return self._undo.undoText()

    def _get_redo_text(self) -> str:
        return self._undo.redoText()

    def _get_running(self) -> bool:
        return self._running

    def _get_run_status(self) -> str:
        return self._run_status

    def _get_run_details(self) -> str:
        return self._run_details

    def _get_grid(self) -> int:
        return GRID

    def _get_t_end(self) -> float:
        return self._t_end

    def _set_t_end(self, value: float) -> None:
        if value != self._t_end:
            self._t_end = float(value)
            self.runSettingsChanged.emit()

    def _get_dt(self) -> float:
        return self._dt

    def _set_dt(self, value: float) -> None:
        if value != self._dt:
            self._dt = float(value)
            self.runSettingsChanged.emit()

    def _get_selected_block(self) -> dict[str, object]:
        """The one selected block as {id, spec, settings: [{name, value, text}]}, or {}."""
        if len(self._selected_blocks) != 1 or self._selected_wires or self._selected_physical:
            return {}
        (bid,) = self._selected_blocks
        try:
            spec = self._diagram.spec(bid)
            values = self._diagram.effective_settings(bid)
        except DiagramError:
            return {}
        return {
            "id": bid,
            "spec": spec.name,
            "doc": spec.doc,
            "settings": [
                {"name": k, "value": v, "text": format_setting(v)} for k, v in values.items()
            ],
        }

    blocks = Property(QObject, _get_blocks, constant=True)
    wires = Property(QObject, _get_wires, constant=True)
    physicalWires = Property(QObject, _get_physical_wires, constant=True)  # noqa: N815
    junctions = Property("QVariantList", _get_junctions, notify=junctionsChanged)
    domainColors = Property("QVariantMap", _get_domain_colors, constant=True)  # noqa: N815
    hint = Property(str, _get_hint, notify=hintChanged)
    canUndo = Property(bool, _get_can_undo, notify=undoStateChanged)  # noqa: N815
    canRedo = Property(bool, _get_can_redo, notify=undoStateChanged)  # noqa: N815
    undoText = Property(str, _get_undo_text, notify=undoStateChanged)  # noqa: N815
    redoText = Property(str, _get_redo_text, notify=undoStateChanged)  # noqa: N815
    running = Property(bool, _get_running, notify=runningChanged)
    runStatus = Property(str, _get_run_status, notify=runStatusChanged)  # noqa: N815
    runDetails = Property(str, _get_run_details, notify=runStatusChanged)  # noqa: N815
    grid = Property(int, _get_grid, constant=True)
    tEnd = Property(float, _get_t_end, _set_t_end, notify=runSettingsChanged)  # noqa: N815
    dt = Property(float, _get_dt, _set_dt, notify=runSettingsChanged)
    selectedBlock = Property(  # noqa: N815
        "QVariantMap", _get_selected_block, notify=selectionChanged
    )

    @property
    def registry(self) -> BlockRegistry:
        return self._registry

    @property
    def diagram(self) -> Diagram:
        return self._diagram

    def _spec(self, name: str) -> BlockSpec | None:
        """The spec of block type ``name``: a signal block first, else a physical one."""
        spec = self._registry.get(name)
        if spec is not None:
            return spec
        physical = self._physical.spec(name)
        return physical.block if physical is not None else None

    @property
    def undo_stack(self) -> QUndoStack:
        return self._undo

    @property
    def result(self) -> object:
        return self._result

    # ---- library slots

    @Slot(str, result=bool)
    def check(self, code: str) -> bool:
        """Report problems in ``code`` without changing the library."""
        result = self._registry.check_source(code, self._document)
        self._set_diagnostics(result)
        return result.ok

    @Slot(str, result=bool)
    def apply(self, code: str) -> bool:
        """Make ``code`` the current document's code if it has no errors."""
        had_document = self._document in self._registry.documents()
        before = self._registry.source(self._document)
        result = self._registry.add_source(code, self._document)
        self._set_diagnostics(result)
        if not result.ok:
            self._set_status("library not updated: it keeps the last version without errors")
            return False
        self._set_removed_ports(result.removed_ports)
        self._refresh_library()
        self._set_status(self._describe(result))
        self._sync_with_library()
        if code != before or not had_document:
            self.sourcesChanged.emit()
        return True

    def _sync_with_library(self) -> None:
        """Bring placed blocks in line with the library after a function-editor change.

        Blocks keep their wires on ports that still exist and get their new size and
        ports. What is no longer valid is removed in one undo step.
        """
        gone: list[BlockInstance] = []
        refresh: list[str] = []
        settings: dict[str, tuple[dict, dict]] = {}
        for inst in self._diagram.blocks:
            spec = self._spec(inst.spec_name)
            if spec is None:
                gone.append(self._copy(inst))
                continue
            if spec != self._specs_of.get(inst.id):
                refresh.append(inst.id)
            kept = {k: v for k, v in inst.settings.items() if k in spec.settings}
            if kept != inst.settings:
                settings[inst.id] = (dict(inst.settings), kept)
        gone_ids = {b.id for b in gone}
        wires = [
            w
            for w in self._diagram.wires
            if w.src_block in gone_ids or w.dst_block in gone_ids or not self._wire_valid(w)
        ]
        if gone or wires or settings:
            names = sorted(
                {b.spec_name for b in gone}
                | {self._diagram.block(bid).spec_name for bid in refresh + list(settings)}
            )
            command = _LibraryUpdate(self, names, gone, wires, settings)
            self._push(command)
            self._set_hint(f"Canvas updated: {command.text().split(': ', 1)[1]}")
        for bid in refresh:
            self._refresh_block(bid)

    @Slot(result=str)
    def newDocument(self) -> str:  # noqa: N802
        """Start a new, empty document; blocks of the current one stay in the library."""
        self._set_document(self._new_document_id())
        self._clear_feedback()
        return self._document

    @Slot(str, result=str)
    def openBlock(self, name: str) -> str:  # noqa: N802
        """Make the document defining user block ``name`` current; return its code.

        Returns "" (and changes nothing) for built-in or unknown blocks.
        """
        document = self._registry.document_of(name)
        if document is None:
            return ""
        self._set_document(document)
        self._clear_feedback()
        return self._registry.source(document)

    @Slot(str, result=bool)
    def isBuiltin(self, name: str) -> bool:  # noqa: N802
        return self._registry.is_builtin(name)

    # ---- diagram edits (all undoable)

    @Slot(str, float, float, result=str)
    def addBlock(self, spec: str, x: float, y: float) -> str:  # noqa: N802
        """Place a block of type ``spec`` with its top-left corner at (x, y), snapped.

        Returns the new block's id, or "" (with ``hint`` set) if ``spec`` is unknown.
        """
        if self._spec(spec) is None:
            self._set_hint(f"Unknown block type {spec!r}")
            return ""
        # Let the diagram pick the id, then hand the instance to the command.
        inst = self._diagram.add_block(spec, snap(x), snap(y))
        self._diagram.remove_block(inst.id)
        self._push(_AddBlock(self, inst))
        self._set_hint("")
        return inst.id

    @Slot(str, float, float)
    def moveBlock(self, block_id: str, x: float, y: float) -> None:  # noqa: N802
        """Move ``block_id`` so its top-left is at (x, y), snapped to the grid.

        If the block is selected, the rest of the selection moves with it. Moves made
        before the next ``endMove()`` merge into one undo step (one mouse drag).
        """
        try:
            inst = self._diagram.block(block_id)
        except DiagramError as err:
            self._set_hint(str(err))
            return
        dx, dy = snap(x) - inst.x, snap(y) - inst.y
        if dx == 0 and dy == 0:
            return
        ids = self._selected_order() if block_id in self._selected_blocks else [block_id]
        moves = {}
        for bid in ids:
            b = self._diagram.block(bid)
            moves[bid] = ((b.x, b.y), (b.x + dx, b.y + dy))
        self._push(_Move(self, self._move_session, moves))

    @Slot()
    def endMove(self) -> None:  # noqa: N802
        """End the current drag: the next move is a new undo step."""
        self._move_session += 1

    @Slot()
    def removeSelected(self) -> None:  # noqa: N802
        """Remove the selected blocks (with their wires) and the selected wires."""
        if not (self._selected_blocks or self._selected_wires or self._selected_physical):
            return
        blocks = [self._copy(self._diagram.block(b)) for b in self._selected_order()]
        ids = {b.id for b in blocks}
        wires = [
            w
            for w in self._diagram.wires
            if w in self._selected_wires or w.src_block in ids or w.dst_block in ids
        ]
        physical = [
            w
            for w in self._diagram.physical_wires
            if w in self._selected_physical or w.a_block in ids or w.b_block in ids
        ]
        self._push(_Remove(self, blocks, wires, physical))
        self._set_hint("")

    @Slot(str, str, result=bool)
    def connectPorts(self, src: str, dst: str) -> bool:  # noqa: N802
        # Not "connect": PySide looks slots up by Python name, and that is QObject.connect.
        """Wire output ``src`` ("Block1.y") to input ``dst``. On failure sets ``hint``."""
        reason = self.canConnect(src, dst)
        if reason:
            self._set_hint(reason)
            return False
        (sb, sp), (db, dp) = _split(src), _split(dst)
        self._push(_Connect(self, Wire(sb, sp, db, dp)))
        self._set_hint("")
        return True

    @Slot(str, str, result=str)
    def canConnect(self, src: str, dst: str) -> str:  # noqa: N802
        """ "" if ``src`` -> ``dst`` is a valid new wire, else the reason it is not."""
        try:
            (sb, sp), (db, dp) = _split(src), _split(dst)
            wire = self._diagram.add_wire(sb, sp, db, dp)
        except DiagramError as err:
            return str(err)
        self._diagram.remove_wire(wire)
        return ""

    @Slot(int, result=bool)
    def disconnectWire(self, index: int) -> bool:  # noqa: N802
        # Not "disconnect", for the same reason as connectPorts.
        """Remove the wire at row ``index`` of ``wires``."""
        if not 0 <= index < self._wires.rowCount():
            return False
        self._push(_Disconnect(self, self._wire_at_row(index)))
        return True

    @Slot(str, str, result=str)
    def canConnectPhysical(self, a: str, b: str) -> str:  # noqa: N802
        """ "" if a physical wire between ports ``a`` and ``b`` is allowed, else why not."""
        try:
            (ab, ap), (bb, bp) = _split(a), _split(b)
        except DiagramError as err:
            return str(err)
        return self._diagram.check_physical_wire(ab, ap, bb, bp)

    @Slot(str, str, result=bool)
    def connectPhysical(self, a: str, b: str) -> bool:  # noqa: N802
        """Join conserving ports ``a`` and ``b`` ("Block1.A"). On failure sets ``hint``."""
        reason = self.canConnectPhysical(a, b)
        if reason:
            self._set_hint(reason)
            return False
        (ab, ap), (bb, bp) = _split(a), _split(b)
        self._push(_PhysicalConnect(self, PhysicalWire(ab, ap, bb, bp)))
        self._set_hint("")
        return True

    @Slot(int, result=bool)
    def disconnectPhysicalWire(self, index: int) -> bool:  # noqa: N802
        """Remove the physical wire at row ``index`` of ``physicalWires``."""
        if not 0 <= index < self._physical_wires.rowCount():
            return False
        self._push(_PhysicalDisconnect(self, self._physical_wire_at_row(index)))
        return True

    @Slot(str, str, str, result=bool)
    def setSetting(self, block_id: str, name: str, text: str) -> bool:  # noqa: N802
        """Set setting ``name`` of ``block_id`` from the text the user typed.

        The text is read as the type of the setting's default. On failure sets ``hint``.
        """
        try:
            inst = self._diagram.block(block_id)
            spec = self._diagram.spec(block_id)
        except DiagramError as err:
            self._set_hint(str(err))
            return False
        if name not in spec.settings:
            self._set_hint(f"{spec.name} has no setting {name!r}")
            return False
        try:
            value = parse_setting(text, spec.settings[name])
        except ValueError as err:
            self._set_hint(f"{name}: {err}")
            return False
        old = dict(inst.settings)
        if self._diagram.effective_settings(block_id)[name] == value and name in old:
            self._set_hint("")
            return True
        new = {**old, name: value}
        self._push(_SetSettings(self, block_id, name, old, new))
        self._set_hint("")
        return True

    @Slot()
    def undo(self) -> None:
        self.endMove()
        self._undo.undo()

    @Slot()
    def redo(self) -> None:
        self.endMove()
        self._undo.redo()

    # ---- selection (not undoable)

    @Slot(str, bool)
    def select(self, block_id: str, additive: bool) -> None:
        """Select a block; ``additive`` toggles it and keeps the rest of the selection."""
        if additive:
            blocks = set(self._selected_blocks) ^ {block_id}
            self._set_selection(blocks, set(self._selected_wires), set(self._selected_physical))
        elif block_id not in self._selected_blocks:
            self._set_selection({block_id}, set())

    @Slot(int, bool)
    def selectWire(self, index: int, additive: bool) -> None:  # noqa: N802
        if not 0 <= index < self._wires.rowCount():
            return
        wire = self._wire_at_row(index)
        if additive:
            self._set_selection(
                set(self._selected_blocks),
                set(self._selected_wires) ^ {wire},
                set(self._selected_physical),
            )
        else:
            self._set_selection(set(), {wire})

    @Slot(int, bool)
    def selectPhysicalWire(self, index: int, additive: bool) -> None:  # noqa: N802
        if not 0 <= index < self._physical_wires.rowCount():
            return
        wire = self._physical_wire_at_row(index)
        if additive:
            self._set_selection(
                set(self._selected_blocks),
                set(self._selected_wires),
                set(self._selected_physical) ^ {wire},
            )
        else:
            self._set_selection(set(), set(), {wire})

    @Slot(float, float, float, float, bool)
    def selectInRect(self, x: float, y: float, w: float, h: float, additive: bool) -> None:  # noqa: N802
        """Select the blocks that touch the rectangle (canvas units)."""
        x0, x1 = sorted((x, x + w))
        y0, y1 = sorted((y, y + h))
        hit = set()
        for row in self._blocks.rows():
            bx, by = row["blockX"], row["blockY"]
            bw, bh = row["blockWidth"], row["blockHeight"]
            if bx <= x1 and bx + bw >= x0 and by <= y1 and by + bh >= y0:  # type: ignore[operator]
                hit.add(str(row["blockId"]))
        if additive:
            self._set_selection(
                self._selected_blocks | hit,
                set(self._selected_wires),
                set(self._selected_physical),
            )
        else:
            self._set_selection(hit, set())

    @Slot()
    def clearSelection(self) -> None:  # noqa: N802
        self._set_selection(set(), set())

    @Slot(float, float, float, result=int)
    def wireAt(self, x: float, y: float, tolerance: float) -> int:  # noqa: N802
        """Row of the wire passing within ``tolerance`` of (x, y), or -1."""
        best, best_d = -1, tolerance
        for i, row in enumerate(self._wires.rows()):
            x1, y1, x2, y2 = (float(row[k]) for k in ("x1", "y1", "x2", "y2"))  # type: ignore[arg-type]
            if not (min(x1, x2) - 60 - tolerance <= x <= max(x1, x2) + 60 + tolerance):
                continue
            pts = _bezier_points(x1, y1, x2, y2)
            d = min(_segment_distance(x, y, a, b) for a, b in zip(pts, pts[1:], strict=False))
            if d <= best_d:
                best, best_d = i, d
        return best

    @Slot(float, float, float, result=int)
    def physicalWireAt(self, x: float, y: float, tolerance: float) -> int:  # noqa: N802
        """Row of the physical wire passing within ``tolerance`` of (x, y), or -1."""
        best, best_d = -1, tolerance
        for i, row in enumerate(self._physical_wires.rows()):
            pts = [(float(p["x"]), float(p["y"])) for p in row["points"]]  # type: ignore[union-attr]
            d = min(
                (_segment_distance(x, y, a, b) for a, b in zip(pts, pts[1:], strict=False)),
                default=math.inf,
            )
            if d <= best_d:
                best, best_d = i, d
        return best

    @Slot(float, float, result=str)
    def conservingPortAt(self, x: float, y: float) -> str:  # noqa: N802
        """The conserving port ("Block1.A") within reach of (x, y), or ""."""
        best, best_d = "", float(PORT_RADIUS)
        for row in self._blocks.rows():
            bx, by = float(row["blockX"]), float(row["blockY"])  # type: ignore[arg-type]
            for port in row["conserving"]:  # type: ignore[union-attr]
                d = math.hypot(x - bx - port["x"], y - by - port["y"])
                if d <= best_d:
                    best, best_d = f"{row['blockId']}.{port['name']}", d
        return best

    @Slot(float, float, result=str)
    def portAt(self, x: float, y: float) -> str:  # noqa: N802
        """The input port ("Block1.u") within reach of (x, y), or ""."""
        best, best_d = "", float(PORT_RADIUS)
        for row in self._blocks.rows():
            bx, by = float(row["blockX"]), float(row["blockY"])  # type: ignore[arg-type]
            for port in row["inputs"]:  # type: ignore[union-attr]
                d = math.hypot(x - bx - port["x"], y - by - port["y"])
                if d <= best_d:
                    best, best_d = f"{row['blockId']}.{port['name']}", d
        return best

    # ---- projects

    def export_state(self) -> DiagramState:
        """The user block code, the diagram and the run settings, for saving."""
        return DiagramState(
            sources=[(d, self._registry.source(d)) for d in self._registry.documents()],
            diagram=self._diagram.to_dict(),
            t_end=self._t_end,
            dt=self._dt,
        )

    def load_state(self, state: DiagramState) -> list[str]:
        """Replace the user blocks, the canvas and the run settings with ``state``.

        Not undoable: the undo stack is cleared. Code with errors, blocks of unknown
        types and wires that break a rule are skipped; the returned warnings say
        which. Must not be called while a diagram runs.
        """
        if self._running:
            raise RuntimeError("cannot load a diagram while it is running")
        warnings: list[str] = []
        self._set_selection(set(), set())
        self._set_loop(set())
        while self._physical_wires.rowCount():
            self._physical_wires.remove(self._physical_wires.rowCount() - 1)
        while self._wires.rowCount():
            self._wires.remove(self._wires.rowCount() - 1)
        while self._blocks.rowCount():
            self._blocks.remove(self._blocks.rowCount() - 1)
        self._geometry.clear()
        self._specs_of.clear()
        self._diagram = Diagram(self._spec)
        self._update_junctions()

        for document in self._registry.documents():
            self._registry.remove_document(document)
        for document, code in state.sources:
            result = self._registry.add_source(code, document)
            if not result.ok:
                first = result.diagnostics[0]
                warnings.append(
                    f"block code {document!r} was not loaded (line {first.line}: {first.message})"
                )
        self._refresh_library()
        documents = self._registry.documents()
        numbers = [int(m.group(1)) for d in documents if (m := _DOCUMENT_ID.match(d))]
        self._next_document = max(numbers, default=0) + 1
        self._set_document(documents[0] if documents else self._new_document_id())
        self._clear_feedback()

        for item in state.diagram.get("blocks", []):
            block_id = item.get("id")
            if self._spec(item.get("spec", "")) is None:
                warnings.append(f"block {block_id} was not loaded: unknown type {item.get('spec')}")
                continue
            try:
                inst = self._diagram.add_block(
                    item["spec"], item["x"], item["y"], item["settings"], block_id=block_id
                )
            except (DiagramError, KeyError, TypeError) as err:
                warnings.append(f"block {block_id} was not loaded: {err}")
                continue
            self._diagram.remove_block(inst.id)
            self._insert_block(inst)
        for item in state.diagram.get("wires", []):
            try:
                (sb, sp), (db, dp) = _split(item.get("src")), _split(item.get("dst"))
            except DiagramError as err:
                warnings.append(f"wire was not loaded: {err}")
                continue
            wire = Wire(sb, sp, db, dp)
            self._insert_wire(wire)
            if wire not in self._diagram.wires:
                warnings.append(f"wire {wire.src} -> {wire.dst} was not loaded")
        for item in state.diagram.get("physical_wires", []):
            try:
                (ab, ap), (bb, bp) = _split(item.get("a")), _split(item.get("b"))
            except (DiagramError, AttributeError) as err:
                warnings.append(f"physical wire was not loaded: {err}")
                continue
            pwire = PhysicalWire(ab, ap, bb, bp)
            self._insert_physical_wire(pwire)
            if pwire not in self._diagram.physical_wires:
                warnings.append(f"physical wire {pwire.a} - {pwire.b} was not loaded")

        self._undo.clear()
        self._undo.setClean()
        self._set_t_end(state.t_end)
        self._set_dt(state.dt)
        self._result = None
        self._set_hint("")
        self._run_details = ""
        self._set_run_status("")
        self.documentReplaced.emit(self._registry.source(self._document))
        self.sourcesChanged.emit()
        return warnings

    def restore_result(self, result: object) -> None:
        """Make ``result`` the last diagram result (a project was loaded)."""
        self._result = result

    # ---- running

    @Slot(QObject)
    def attachWaveforms(self, model: QObject) -> None:  # noqa: N802 - QML API
        """Send every block-diagram result to ``model`` (a WaveformModel)."""
        self._waveforms = model

    @Slot(float, float, result=bool)
    def run(self, t_end: float, dt: float) -> bool:
        """Check the diagram and start running it in the background.

        Returns False (with ``runStatus`` set) if it cannot start. An algebraic loop
        marks its blocks with ``inLoop``.
        """
        if self._running:
            return False
        self._set_loop(set())
        self._run_details = ""
        if not (math.isfinite(dt) and dt > 0):
            self._set_run_status("Not started: the time step must be a positive number")
            return False
        if not (math.isfinite(t_end) and t_end > 0):
            self._set_run_status("Not started: the end time must be a positive number")
            return False
        if not self._blocks.rowCount():
            self._set_run_status("Not started: the diagram is empty")
            return False
        # compile() only orders and checks the blocks: the specs stand in for the block
        # objects, so no user code runs in this process.
        stand_ins = {spec.name: spec for spec in self._registry.specs()}
        try:
            ExecutionEngine(stand_ins).compile(self._diagram)
        except AlgebraicLoopError as err:
            self._set_loop(set(err.blocks))
            self._set_run_status(f"Not started: {err}")
            return False
        except DiagramError as err:
            self._set_run_status(f"Not started: {err}")
            return False
        sources = {doc: self._registry.source(doc) for doc in self._registry.documents()}
        runner = BlockRunner(self._diagram.to_dict(), sources, t_end, dt, timeout=self.timeout)
        self._runner = runner
        self._set_running(True)
        self._set_run_status("Running…")
        threading.Thread(target=self._run_thread, args=(runner,), daemon=True).start()
        return True

    @Slot()
    def stop(self) -> None:
        if self._runner is not None:
            self._runner.cancel()

    @Slot()
    def shutdown(self) -> None:
        """Stop any run. Called when the app quits."""
        self.stop()

    def _run_thread(self, runner: BlockRunner) -> None:
        outcome = runner.run()
        self._runDone.emit((runner, outcome))

    @Slot(object)
    def _on_run_done(self, payload: tuple[BlockRunner, RunOutcome]) -> None:
        runner, outcome = payload
        if runner is not self._runner:
            return
        self._runner = None
        if outcome.ok and outcome.result is not None:
            self._result = outcome.result
            if self._waveforms is not None:
                self._waveforms.set_result(outcome.result)  # type: ignore[attr-defined]
            steps = len(outcome.result.vectors["time"])
            traces = outcome.result.trace_names()
            shown = ", ".join(traces) if traces else "no Scope blocks"
            self._set_run_status(f"Finished: {steps} steps ({shown})")
            self._set_running(False)
            self.resultReady.emit()
            return
        self._run_details = outcome.traceback
        if outcome.status == "cancelled":
            self._set_run_status("Stopped")
        elif outcome.status == "timeout":
            self._set_run_status(f"Failed: {outcome.message}")
        else:
            self._set_run_status(f"Failed: {outcome.message}")
        self._set_running(False)

    # ---- primitive changes (used by the undo commands)
    #
    # The library is not on the undo stack, so an undo or redo step can meet a block
    # type, port or setting that no longer exists. These skip what cannot apply.

    def _block_row_data(self, block_id: str, spec: BlockSpec) -> dict[str, object]:
        geo = block_geometry(spec)
        self._geometry[block_id] = geo
        self._specs_of[block_id] = spec
        return {
            "specName": spec.name,
            "blockWidth": geo.width,
            "blockHeight": geo.height,
            "shape": spec.shape,
            "color": spec.color,
            "inputs": geo.inputs,
            "outputs": geo.outputs,
            "conserving": geo.conserving,
        }

    def _insert_block(self, inst: BlockInstance) -> None:
        spec = self._spec(inst.spec_name)
        if spec is None or self._has_block(inst.id):
            return
        settings = {k: v for k, v in inst.settings.items() if k in spec.settings}
        added = self._diagram.add_block(inst.spec_name, inst.x, inst.y, settings, block_id=inst.id)
        self._blocks.append(
            {
                "blockId": added.id,
                "blockX": added.x,
                "blockY": added.y,
                **self._block_row_data(added.id, spec),
                "selected": False,
                "inLoop": False,
            }
        )
        self._set_loop(set())

    def _delete_block(self, block_id: str) -> None:
        if not self._has_block(block_id):
            return
        for pwire in [w for w in self._diagram.physical_wires if w.touches(block_id)]:
            self._delete_physical_wire(pwire)
        self._diagram.remove_block(block_id)
        self._blocks.remove(self._block_row(block_id))
        self._geometry.pop(block_id, None)
        self._specs_of.pop(block_id, None)
        if block_id in self._selected_blocks:
            self._set_selection(
                self._selected_blocks - {block_id},
                set(self._selected_wires),
                set(self._selected_physical),
            )
        self._set_loop(set())

    def _refresh_block(self, block_id: str) -> None:
        """Re-read a placed block's spec: new size and ports; its wires follow."""
        self._blocks.update(
            self._block_row(block_id),
            **self._block_row_data(block_id, self._diagram.spec(block_id)),
        )
        self._update_wires_of(block_id)
        if block_id in self._selected_blocks:
            self.selectionChanged.emit()

    def _place_block(self, block_id: str, x: float, y: float) -> None:
        if not self._has_block(block_id):
            return
        self._diagram.move_block(block_id, x, y)
        self._blocks.update(self._block_row(block_id), blockX=x, blockY=y)
        self._update_wires_of(block_id)

    def _update_wires_of(self, block_id: str) -> None:
        for i, row in enumerate(self._wires.rows()):
            if row["src"].startswith(block_id + ".") or row["dst"].startswith(block_id + "."):  # type: ignore[union-attr]
                self._wires.update(i, **self._wire_geometry(self._wire_at_row(i)))
        for i in range(self._physical_wires.rowCount()):
            pwire = self._physical_wire_at_row(i)
            if pwire.touches(block_id):
                self._physical_wires.update(i, **self._physical_wire_geometry(pwire))
        self._update_junctions()

    def _insert_physical_wire(self, wire: PhysicalWire) -> None:
        try:
            added = self._diagram.add_physical_wire(
                wire.a_block, wire.a_port, wire.b_block, wire.b_port
            )
        except DiagramError:
            return  # a block or port is gone, or the two ports are wired already
        self._physical_wires.append(
            {"a": added.a, "b": added.b, "selected": False, **self._physical_wire_geometry(added)}
        )
        self._update_junctions()

    def _delete_physical_wire(self, wire: PhysicalWire) -> None:
        if wire not in self._diagram.physical_wires:
            return
        self._diagram.remove_physical_wire(wire)
        for i in range(self._physical_wires.rowCount()):
            if self._physical_wire_at_row(i) == wire:
                self._physical_wires.remove(i)
                break
        if wire in self._selected_physical:
            self._set_selection(
                set(self._selected_blocks),
                set(self._selected_wires),
                self._selected_physical - {wire},
            )
        self._update_junctions()

    def _physical_wire_at_row(self, i: int) -> PhysicalWire:
        row = self._physical_wires.row(i)
        (ab, ap), (bb, bp) = _split(str(row["a"])), _split(str(row["b"]))
        return PhysicalWire(ab, ap, bb, bp)

    def _conserving_port(self, block_id: str, port: str) -> dict[str, object]:
        return next(p for p in self._geometry[block_id].conserving if p["name"] == port)

    def _physical_wire_geometry(self, wire: PhysicalWire) -> dict[str, object]:
        ends = []
        for block_id, port in wire.ends:
            inst = self._diagram.block(block_id)
            p = self._conserving_port(block_id, port)
            ends.append((inst.x + float(p["x"]), inst.y + float(p["y"]), str(p["side"]), p))  # type: ignore[arg-type]
        (x1, y1, s1, p1), (x2, y2, s2, p2) = ends
        domain = p1["domain"] if p1["domain"] != ANY_DOMAIN else p2["domain"]
        return {
            "domain": domain,
            "color": domain_color(str(domain)),
            "points": physical_route(x1, y1, s1, x2, y2, s2),
        }

    def _update_junctions(self) -> None:
        """A dot on every port with two or more physical wires."""
        count: dict[tuple[str, str], int] = {}
        for wire in self._diagram.physical_wires:
            for end in wire.ends:
                count[end] = count.get(end, 0) + 1
        junctions: list[dict[str, object]] = []
        for (block_id, port), n in count.items():
            if n < 2 or block_id not in self._geometry:
                continue
            inst = self._diagram.block(block_id)
            p = self._conserving_port(block_id, port)
            first = next(w for w in self._diagram.physical_wires if (block_id, port) in w.ends)
            domain = self._physical_wire_geometry(first)["domain"]  # a Solver port takes it
            junctions.append(
                {
                    "port": f"{block_id}.{port}",
                    "x": inst.x + float(p["x"]),  # type: ignore[arg-type]
                    "y": inst.y + float(p["y"]),  # type: ignore[arg-type]
                    "color": domain_color(str(domain)),
                }
            )
        if junctions != self._junctions:
            self._junctions = junctions
            self.junctionsChanged.emit()

    def _insert_wire(self, wire: Wire) -> None:
        try:
            added = self._diagram.add_wire(
                wire.src_block, wire.src_port, wire.dst_block, wire.dst_port
            )
        except DiagramError:
            return  # a block or port is gone, or the input is wired again
        self._wires.append(
            {"src": added.src, "dst": added.dst, "selected": False, **self._wire_geometry(added)}
        )
        self._set_loop(set())

    def _delete_wire(self, wire: Wire) -> None:
        if wire not in self._diagram.wires:
            return
        self._diagram.remove_wire(wire)
        for i, row in enumerate(self._wires.rows()):
            if row["src"] == wire.src and row["dst"] == wire.dst:
                self._wires.remove(i)
                break
        if wire in self._selected_wires:
            self._set_selection(set(self._selected_blocks), self._selected_wires - {wire})
        self._set_loop(set())

    def _apply_settings(self, block_id: str, settings: dict) -> None:
        if not self._has_block(block_id):
            return
        spec = self._diagram.spec(block_id)
        self._diagram.set_settings(
            block_id, {k: v for k, v in settings.items() if k in spec.settings}
        )
        if block_id in self._selected_blocks:
            self.selectionChanged.emit()

    def _has_block(self, block_id: str) -> bool:
        return any(b.id == block_id for b in self._diagram.blocks)

    def _wire_valid(self, wire: Wire) -> bool:
        """Both ends of ``wire`` still exist in the current library."""
        try:
            src = self._diagram.spec(wire.src_block)
            dst = self._diagram.spec(wire.dst_block)
        except DiagramError:
            return False
        return wire.src_port in src.outputs and wire.dst_port in dst.inputs

    # ---- helpers

    def _push(self, command: QUndoCommand) -> None:
        self._undo.push(command)

    def _copy(self, inst: BlockInstance) -> BlockInstance:
        return BlockInstance(inst.id, inst.spec_name, inst.x, inst.y, dict(inst.settings))

    def _block_row(self, block_id: str) -> int:
        for i, row in enumerate(self._blocks.rows()):
            if row["blockId"] == block_id:
                return i
        raise KeyError(block_id)

    def _wire_at_row(self, i: int) -> Wire:
        row = self._wires.row(i)
        (sb, sp), (db, dp) = _split(str(row["src"])), _split(str(row["dst"]))
        return Wire(sb, sp, db, dp)

    def _wire_geometry(self, wire: Wire) -> dict[str, float]:
        src = self._diagram.block(wire.src_block)
        dst = self._diagram.block(wire.dst_block)
        sp = next(p for p in self._geometry[src.id].outputs if p["name"] == wire.src_port)
        dp = next(p for p in self._geometry[dst.id].inputs if p["name"] == wire.dst_port)
        x1, y1 = src.x + float(sp["x"]), src.y + float(sp["y"])  # type: ignore[arg-type]
        x2, y2 = dst.x + float(dp["x"]), dst.y + float(dp["y"])  # type: ignore[arg-type]
        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "bend": wire_bend(x1, x2)}

    def _selected_order(self) -> list[str]:
        """Selected block ids in diagram order (stable for undo)."""
        return [b.id for b in self._diagram.blocks if b.id in self._selected_blocks]

    def _set_selection(
        self, blocks: set[str], wires: set[Wire], physical: set[PhysicalWire] | None = None
    ) -> None:
        physical = set() if physical is None else physical
        blocks = {b for b in blocks if any(r["blockId"] == b for r in self._blocks.rows())}
        if (
            blocks == self._selected_blocks
            and wires == self._selected_wires
            and physical == self._selected_physical
        ):
            return
        self._selected_blocks, self._selected_wires = blocks, wires
        self._selected_physical = physical
        for i, row in enumerate(self._blocks.rows()):
            self._blocks.update(i, selected=row["blockId"] in blocks)
        selected = {(w.src, w.dst) for w in wires}
        for i, row in enumerate(self._wires.rows()):
            self._wires.update(i, selected=(row["src"], row["dst"]) in selected)
        for i in range(self._physical_wires.rowCount()):
            self._physical_wires.update(i, selected=self._physical_wire_at_row(i) in physical)
        self.selectionChanged.emit()

    def _set_loop(self, blocks: set[str]) -> None:
        if blocks == self._loop_blocks:
            return
        self._loop_blocks = blocks
        for i, row in enumerate(self._blocks.rows()):
            self._blocks.update(i, inLoop=row["blockId"] in blocks)

    def _set_hint(self, text: str) -> None:
        if text != self._hint:
            self._hint = text
            self.hintChanged.emit()

    def _set_running(self, running: bool) -> None:
        if running != self._running:
            self._running = running
            self.runningChanged.emit()

    def _set_run_status(self, text: str) -> None:
        self._run_status = text
        self.runStatusChanged.emit()

    def _new_document_id(self) -> str:
        document = f"function{self._next_document}"
        self._next_document += 1
        return document

    def _describe(self, result: SourceResult) -> str:
        parts: list[str] = []
        if result.specs:
            parts.append("Library: " + ", ".join(s.name for s in result.specs))
        if result.removed_blocks:
            parts.append("removed blocks: " + ", ".join(result.removed_blocks))
        if result.removed_ports:
            parts.append("removed ports: " + "; ".join(result.removed_ports))
        return " — ".join(parts) if parts else "No blocks defined"

    def _refresh_library(self) -> None:
        library = [
            spec_to_dict(s, self._registry.is_builtin(s.name)) for s in self._registry.specs()
        ]
        library += [spec_to_dict(p.block, True) for p in self._physical.specs()]
        if library != self._library:
            self._library = library
            self.libraryChanged.emit()

    def _set_diagnostics(self, result: SourceResult) -> None:
        diagnostics = [d.to_dict() for d in result.diagnostics]
        if diagnostics != self._diagnostics:
            self._diagnostics = diagnostics
            self.diagnosticsChanged.emit()

    def _set_removed_ports(self, ports: list[str]) -> None:
        if ports != self._removed_ports:
            self._removed_ports = ports
            self.removedPortsChanged.emit()

    def _set_status(self, text: str) -> None:
        if text != self._status_text:
            self._status_text = text
            self.statusTextChanged.emit()

    def _set_document(self, document: str) -> None:
        if document != self._document:
            self._document = document
            self.documentChanged.emit()

    def _clear_feedback(self) -> None:
        self._set_diagnostics(SourceResult())
        self._set_removed_ports([])
        self._set_status("")


def _split(text: str) -> tuple[str, str]:
    if not isinstance(text, str) or text.count(".") != 1:
        raise DiagramError(f"a port must look like 'Block1.port', not {text!r}")
    block_id, port = text.split(".")
    return block_id, port
