"""Drive the real QtSpice window through docs/manual_tests/v0.5_designer.md (D6).

Mouse and keys go through QTest on the visible window (real platform plugin), so each
step exercises the QML handlers, not only the slots. Prints one PASS/FAIL per step.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
import traceback
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

from PySide6.QtCore import QObject, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QSurfaceFormat  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import app.__main__ as appmain  # noqa: E402
from tests.core.grid.helpers import IEEE13, IEEE123  # noqa: E402
from tests.ui.mouse import drag, find_item  # noqa: E402

QQuickStyle.setStyle("Fusion")
fmt = QSurfaceFormat.defaultFormat()
fmt.setSamples(4)
QSurfaceFormat.setDefaultFormat(fmt)
app = QGuiApplication([sys.argv[0]])
engine = appmain.create_engine()
win = engine.rootObjects()[0]
win.setProperty("width", 1366)
win.setProperty("height", 768)
warnings: list[str] = []
engine.warnings.connect(lambda items: warnings.extend(w.toString() for w in items))
d = win.findChild(QObject, "gridDesigner")
grid = engine.singletonInstance("QtSpice", "GridController")
results: list[tuple[str, bool, str]] = []


def wait(ms: int = 60) -> None:
    QTest.qWait(ms)


def wait_until(cond, timeout=60.0) -> bool:
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        QTest.qWait(30)
    return cond()


def step(name: str, fn) -> None:
    only = os.environ.get("STEPS")
    if only and name.split()[0] not in only.split(","):
        return
    try:
        detail = fn() or ""
        results.append((name, True, str(detail)))
    except Exception as err:  # noqa: BLE001
        results.append((name, False, f"{err!r}\n{traceback.format_exc(limit=3)}"))
    print(
        ("PASS " if results[-1][1] else "FAIL ") + name + "  " + results[-1][2].splitlines()[0]
        if results[-1][2]
        else ("PASS " if results[-1][1] else "FAIL ") + name,
        flush=True,
    )


def shot(name: str) -> None:
    wait(150)
    win.grabWindow().save(str(OUT / f"{name}.png"))


def editor():
    return find_item(win, "gridDiagramEditor")


def canvas():
    return find_item(win, "designerCanvas")


def at(x: float, y: float) -> QPoint:
    """Window position of diagram point (x, y)."""
    e = editor()
    z, u = e.property("zoom"), e.property("unit")
    vx = e.property("panX") + x * u * z
    vy = e.property("panY") + y * u * z
    p = canvas().mapToScene(QPointF(vx, vy))
    return QPoint(round(p.x()), round(p.y()))


def center_of(name: str) -> QPoint:
    it = find_item(win, name)
    p = it.mapToScene(QPointF(it.width() / 2, it.height() / 2))
    return QPoint(round(p.x()), round(p.y()))


def empty() -> QPoint:
    """An empty canvas point (bottom right corner of the canvas)."""
    c = canvas()
    p = c.mapToScene(QPointF(c.width() - 12, c.height() - 12))
    return QPoint(round(p.x()), round(p.y()))


def click(p: QPoint, mods=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.mouseMove(win, p)
    QTest.mouseClick(win, Qt.MouseButton.LeftButton, mods, p)
    wait()


def key(k, mods=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.keyClick(win, k, mods)
    wait()


def type_into(name: str, text: str) -> None:
    f = win.findChild(QObject, name)
    if f is None:
        f = find_item(win, name)
    f.forceActiveFocus()
    f.setProperty("text", "")
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    for ch in text:
        for t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QGuiApplication.sendEvent(win, QKeyEvent(t, 0, Qt.KeyboardModifier.NoModifier, ch))
    QTest.keyClick(win, Qt.Key.Key_Return)
    wait(80)


def glyph(gid: str) -> tuple[float, float]:
    g = (
        d.design().diagram.bus_glyphs[gid[4:]]
        if gid.startswith("bus.")
        else d.design().diagram.element_glyphs[gid]
    )
    return g.x, g.y


def sel() -> list[str]:
    return list(d.property("selection"))


def check(cond: bool, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg)


wait(500)


# 1 --------------------------------------------------------------- start
def s1():
    check(win.findChild(QObject, "designerTab") is not None)
    click(center_of("designerTab"))
    check(win.findChild(QObject, "leftTabs").property("currentIndex") == 4)
    check(not d.property("hasDesign"))
    check(not d.property("canRun"))
    return f"Designer tab shown, empty state; Run off: {d.property('runDisabledReason')!r}"


step("1 Designer tab and empty state", s1)


def s2():
    dlg = win.findChild(QObject, "newGridDesignDialog")
    dlg.open()
    wait(200)
    ok = dlg.property("valid")
    check(not ok, "valid with empty fields")
    type_into("newDesignName", "demo")
    check(not dlg.property("valid"), "valid without a frequency")
    type_into("newDesignFrequency", "60")
    win.findChild(QObject, "newDesignWithSource").setProperty("checked", True)
    wait()
    check(not dlg.property("valid"), "valid without source values")
    for name, v in (
        ("newDesignKv", "12.47"),
        ("newDesignR1", "0.1"),
        ("newDesignX1", "1.0"),
        ("newDesignR0", "0.1"),
        ("newDesignX0", "1.0"),
    ):
        type_into(name, v)
    check(dlg.property("valid"), "still invalid")
    shot("02_new_design_dialog")
    dlg.accept()
    wait(300)
    check(d.property("hasDesign") and d.property("designName") == "demo", d.property("statusText"))
    check(d.property("busCount") == 1 and d.property("elementCount") == 1)
    check(d.property("baseFrequency") == 60.0)
    return "demo, 60 Hz, sourcebus + source"


step("2 New Grid Design dialog (frequency required)", s2)


def s3():
    from PySide6.QtCore import Q_ARG, QMetaObject

    QMetaObject.invokeMethod(win, "setBottomState", Q_ARG("QVariant", "collapsed"))
    wait(300)
    e = editor()
    e.setProperty("zoom", 1.0)
    e.setProperty("settledZoom", 1.0)
    e.setProperty("panX", 300.0)
    e.setProperty("panY", 60.0)
    wait(200)
    click(center_of("libraryItem_bus"))
    check(editor().property("tool") == "place")
    p = at(0, 8)
    e = editor()
    print(
        "DEBUG at",
        p,
        "zoom",
        e.property("zoom"),
        e.property("panX"),
        e.property("panY"),
        "canvas",
        canvas().mapToScene(QPointF(0, 0)),
        canvas().width(),
        canvas().height(),
        "tool",
        e.property("tool"),
    )
    shot("03_before_place")
    click(p)
    print("DEBUG after", e.property("tool"), d.property("statusText"))
    check(d.property("busCount") == 2, d.property("statusText"))
    check("bus.b1" in sel())
    x, y = glyph("bus.b1")
    check(x == round(x) and y == round(y), "not snapped")
    return f"b1 at {(x, y)} (snapped), tool back to select: {editor().property('tool')}"


step("3 Click-to-place a bus (grid snap)", s3)


def s4():
    start = center_of("libraryItem_load")
    bx, by = glyph("bus.b1")
    end = at(bx + 0.3, by)
    drag(win, start, end, steps=25)
    wait(200)
    ld = [i for i in d.design().grid.loads]
    check(ld == ["ld1"], f"loads {ld}; {d.property('statusText')}")
    check(d.design().grid.loads["ld1"].terminal.bus == "b1")
    return "dragged Load from the library onto b1 -> load.ld1 on b1"


def s5():
    start = center_of("libraryItem_bus")
    drag(win, start, at(10, 8), steps=25)
    wait(200)
    check(d.property("busCount") == 3, d.property("statusText"))
    n = len(d.design().grid.capacitors)
    drag(win, center_of("libraryItem_capacitor"), at(30, 30), steps=20)  # empty space
    wait(200)
    check(len(d.design().grid.capacitors) == n, "capacitor added off a bus")
    return f"bus b2 by drag; capacitor off a bus refused: {d.property('statusText')!r}"


def s6():
    click(center_of("designerTool_line"))
    check(editor().property("tool") == "branch")
    sx, sy = glyph("bus.sourcebus")
    click(at(sx, sy))
    check(editor().property("branchFrom") == "sourcebus")
    shot("06_branch_tool_from_a")
    bx, by = glyph("bus.b1")
    click(at(bx, by))
    check("l1" in d.design().grid.lines, d.property("statusText"))
    ln = d.design().grid.lines["l1"]
    check((ln.bus1.bus, ln.bus2.bus) == ("sourcebus", "b1"))
    key(Qt.Key.Key_Escape)
    check(editor().property("tool") == "select")
    return "line l1 sourcebus -> b1; Esc leaves the tool"


step("4 Branch tool: click bus A, then bus B", s6)
step("5 Drag a load from the library onto a bus", s4)
step("6 Drag a bus onto the canvas; a shunt off a bus is refused", s5)


def s7():
    click(center_of("designerTool_switch"))
    bx, by = glyph("bus.b1")
    click(at(bx, by))
    b2 = glyph("bus.b2")
    click(at(*b2))
    wait(200)
    pop = win.findChild(QObject, "designerValuesPopup")
    check(pop.property("visible"), "no values popup for the switch")
    shot("07_switch_values_popup")
    type_into("designerValue_r_ohm", "0.0001")  # Enter = Add
    wait(200)
    check("sw1" in d.design().grid.switches, d.property("statusText"))
    key(Qt.Key.Key_Escape)
    return "switch asks for R (no default), then sw1 b1 -> b2"


step("7 Switch asks for its required R", s7)


def s8():
    click(center_of("libraryItem_linecode"))
    wait(100)
    lcs = list(d.design().grid.linecodes)
    check(len(lcs) == 1 and sel() == [f"linecode.{lcs[0]}"], f"{lcs} {sel()}")
    # draft values are errors with inline text
    wait(200)
    rows = {r["field"]: r for r in d.property("propertySheet")}
    check("not set" in rows["r_matrix"]["diagnostic"], rows["r_matrix"])
    shot("08_linecode_inline_diagnostics")
    r3 = "0.3 0.1 0.1 0.1 0.3 0.1 0.1 0.1 0.3"
    x3 = "0.6 0.2 0.2 0.2 0.6 0.2 0.2 0.2 0.6"
    c3 = "3 -1 -1 -1 3 -1 -1 -1 3"
    for f, v in (("r_matrix", r3), ("x_matrix", x3), ("c_matrix", c3)):
        type_into("gridProperty_" + f, v)
    wait(200)
    lc = d.design().grid.linecodes[lcs[0]]
    check(lc.r_matrix[0] == 0.3 and lc.c_matrix[1] == -1.0, str(lc))
    rows = {r["field"]: r for r in d.property("propertySheet")}
    check(rows["r_matrix"]["diagnostic"] == "")
    return f"{lcs[0]}: matrices typed in the properties panel; inline errors gone"


step("8 Line code and the properties panel (setProperty, inline diagnostics)", s8)


def s9():
    wait(200)
    check(d.property("errorCount") > 0)
    check(not d.property("canRun"))
    reason = d.property("runDisabledReason")
    check("error" in reason, reason)
    sev = {r["glyphId"]: r["severity"] for r in d.property("elementGlyphs").rows()}
    check(sev.get("load.ld1") == "error", sev)
    check(find_item(win, "designerElement_load.ld1") is not None)
    counts = find_item(win, "designerErrors").property("text")
    shot("09_badges_and_status")
    return f"badge on load.ld1; status bar {counts!r}; Run tooltip {reason!r}"


step("9 Badges, status bar counts, Run disabled with reason", s9)


def s10():
    lx, ly = glyph("load.ld1")
    click(at(lx, ly))
    check(sel() == ["load.ld1"], sel())
    type_into("gridProperty_kw", "900")
    type_into("gridProperty_kvar", "300")
    click(at(*[(a + b) / 2 for a, b in zip(glyph("bus.sourcebus"), glyph("bus.b1"), strict=True)]))
    check(sel() == ["line.l1"], sel())
    combo = find_item(win, "gridProperty_linecode")
    combo.forceActiveFocus()
    QTest.keyClick(win, Qt.Key.Key_Down)
    wait(100)
    type_into("gridProperty_length_m", "500")
    ln = d.design().grid.lines["l1"]
    check(ln.linecode != "" and ln.length_m == 500.0, str(ln))
    # b2 has nothing: a warning or error? finish the design so it runs
    wait(200)
    errs = [r for r in d.property("diagnosticRows") if r["severity"] == "error"]
    return f"remaining errors: {[(e['location'], e['message']) for e in errs]}"


step("10 Fill the load and the line from the panel", s10)


def s11():
    # select by click / Ctrl+click / rubber band
    click(at(*glyph("bus.b1")))
    check(sel() == ["bus.b1"], sel())
    click(at(*glyph("load.ld1")), Qt.KeyboardModifier.ControlModifier)
    check(sel() == ["bus.b1", "load.ld1"], sel())
    click(empty())
    check(sel() == [], sel())
    x0, y0, x1, y1 = d.contentBounds()
    drag(win, at(x0 - 1, y0 - 1), at(x1 + 1, y1 + 1))
    n = len(sel())
    check(n == d.property("busCount") + d.property("elementGlyphs").property("count"), sel())
    shot("11_rubber_band")
    return f"click, Ctrl+click, empty click, rubber band selects all {n}"


step("11 Select: click, Ctrl+click, rubber band", s11)


def s12():
    click(empty())
    bx, by = glyph("bus.b2")
    click(at(bx, by))
    undo_before = d.property("undoText")
    drag(win, at(bx, by), at(bx + 3.4, by + 2.2), steps=30)
    nx, ny = glyph("bus.b2")
    check((nx, ny) == (bx + 3, by + 2), f"{(bx, by)} -> {(nx, ny)}")
    check(d.property("undoText") == "Move bus.b2", d.property("undoText"))
    menu_text = win.findChild(QObject, "designUndoMenu").property("text")
    check("Move bus.b2" in menu_text, menu_text)
    key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    check(glyph("bus.b2") == (bx, by) and d.property("undoText") == undo_before)
    key(Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    check(glyph("bus.b2") == (bx + 3, by + 2))
    return f"30-event drag = one step {menu_text!r}; Ctrl+Z / Ctrl+Y"


step("12 Move by drag (snapped, one undo step), Ctrl+Z / Ctrl+Y", s12)


def s13():
    click(at(*glyph("bus.b2")))
    canvas().forceActiveFocus()
    key(Qt.Key.Key_R)
    check(d.design().diagram.bus_glyphs["b2"].orientation == 90)
    key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    click(at(*glyph("bus.b2")))
    n_sw = len(d.design().grid.switches)
    key(Qt.Key.Key_Delete)
    check("b2" not in d.design().grid.buses and len(d.design().grid.switches) == n_sw - 1)
    check(
        d.property("undoText").startswith("Remove") or "Delete" in d.property("undoText"),
        d.property("undoText"),
    )
    key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    check("b2" in d.design().grid.buses and len(d.design().grid.switches) == n_sw)
    return "R rotates, Del removes b2 with its switch, Ctrl+Z restores both"


step("13 Rotate (R), Delete (Del), undo", s13)


def s14():
    click(at(*glyph("bus.b1")))
    canvas().forceActiveFocus()
    key(Qt.Key.Key_F2)
    check(win.findChild(QObject, "designerRenamePopup").property("visible"))
    type_into("designerRenameField", "main")
    wait(150)
    check("main" in d.design().grid.buses and sel() == ["bus.main"], sel())
    check(d.design().grid.lines["l1"].bus2.bus == "main")
    return "F2 rename b1 -> main; the line and selection follow"


step("14 Rename (F2)", s14)


def s15():
    x, y = glyph("line.sw1")
    p = at(x, y)
    QTest.mouseDClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    wait(150)
    check(not d.design().grid.switches["sw1"].closed, "not opened")
    shot("15_switch_open")
    QTest.mouseDClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    wait(150)
    check(d.design().grid.switches["sw1"].closed, "not closed")
    return "double-click opens sw1 (dashed red, open symbol), again closes it"


step("15 Open/close a switch (double-click)", s15)


def s16():
    e = editor()
    z0 = e.property("zoom")
    p = at(0, 4)
    from tests.ui.mouse import wheel  # type: ignore[attr-defined]

    wheel(win, p, 120)
    check(e.property("zoom") > z0)
    px0 = e.property("panX")
    drag(win, at(0, 4), at(5, 4), button=Qt.MouseButton.MiddleButton)
    check(e.property("panX") > px0)
    px1 = e.property("panX")
    canvas().forceActiveFocus()
    QTest.keyPress(win, Qt.Key.Key_Space)
    drag(win, QPoint(600, 400), QPoint(650, 420))
    QTest.keyRelease(win, Qt.Key.Key_Space)
    check(e.property("panX") > px1 + 30, "space-drag did not pan")
    click(center_of("designerFit"))
    return f"wheel zoom, middle-drag pan, Space+drag pan, Fit (zoom {e.property('zoom'):.2f})"


step("16 Pan and zoom (wheel, middle drag, Space+drag, Fit)", s16)


def s17():
    # b2 needs a voltage... it is fed by sw1 so it has a zone. Run
    wait(200)
    errs = [
        (r["location"], r["message"])
        for r in d.property("diagnosticRows")
        if r["severity"] == "error"
    ]
    check(errs == [], errs)
    check(d.property("canRun"), d.property("runDisabledReason"))
    find_item(win, "designerRun")
    click(center_of("designerRun"))
    check(wait_until(lambda: d.property("hasResult") or not d.property("busy"), 60))
    check(d.property("hasResult"), d.property("statusText"))
    s = d.property("summary")
    shot("17_after_run")
    return f"{d.property('statusText')}; losses {s['lossesKw']:.3f} kW"


step("17 Run Power Flow (enabled once errors are 0)", s17)


def s18(tmp=OUT):
    path = tmp / "demo.qgrid"
    if path.exists():
        path.unlink()
    check(d.save(str(path)) and path.is_file())
    check(not d.property("modified"))
    dss = tmp / "demo.dss"
    check(d.exportDss(str(dss)) and "New Circuit.demo" in dss.read_text(encoding="utf-8"))
    text = dss.read_text(encoding="utf-8")
    g = d.design().diagram.bus_glyphs["main"]
    check(f"SetBusXY bus=main x={g.x:g} y={-g.y:g}" in text, "no SetBusXY for main")
    before = d.design()
    import copy

    before = copy.deepcopy(before)
    check(d.load(str(path)))
    check(d.design() == before)
    return f"saved {path.name}, exported {dss.name}, reopened equal"


step("18 Save Design / Export .dss / Open Design (slots of the dialogs)", s18)


def s19():
    t = time.time()
    check(d.openDssAsDesign(str(IEEE123)))
    check(wait_until(lambda: not d.property("busy"), 60))
    check(d.property("busCount") > 100, d.property("statusText"))
    wait(400)
    shot("19_ieee123_fit")
    e = editor()
    b = d.contentBounds()
    z, u = e.property("zoom"), e.property("unit")
    w, h = (b[2] - b[0]) * u * z, (b[3] - b[1]) * u * z
    cw, ch = canvas().width(), canvas().height()
    print("FIT", b, z, w, h, cw, ch, e.property("panX"), e.property("panY"))
    check(
        max(w / (cw - 60), h / (ch - 60)) > 0.95, f"fit uses {w:.0f}x{h:.0f} of {cw:.0f}x{ch:.0f}"
    )
    return f"{d.property('statusText')} in {time.time() - t:.1f} s"


step("19 Open .dss as Design: IEEE 123", s19)

fps_detail = {}


class Driver(QObject):
    """Runs ``step(k)`` once per real frame (queued from frameSwapped, as S1's bench)."""

    def __init__(self, fn):
        super().__init__()
        self.fn, self.times, self.k, self.slot = fn, [], 0, []

    def on_frame(self):
        self.times.append(time.perf_counter())
        t = time.perf_counter()
        self.fn(self.k)
        self.slot.append((time.perf_counter() - t) * 1000)
        self.k += 1


def run_frames(fn, seconds):
    drv = Driver(fn)
    win.frameSwapped.connect(drv.on_frame, Qt.ConnectionType.QueuedConnection)
    fn(0)
    canvas().update()
    QTest.qWait(int(seconds * 1000))
    win.frameSwapped.disconnect(drv.on_frame)
    ts = drv.times
    dt = [b - a for a, b in zip(ts, ts[1:], strict=False)]
    return {
        "fps": round((len(ts) - 1) / (ts[-1] - ts[0]), 1),
        "p95_ms": round(sorted(dt)[int(0.95 * len(dt))] * 1000, 1),
        "slot_ms": round(statistics.median(drv.slot), 2),
        "slot_max_ms": round(max(drv.slot), 2),
        "frames": len(ts),
    }


def input_frames(make_event, seconds, interval_ms=5):
    """Send input events every ``interval_ms`` (precise timer, like a 200 Hz mouse)
    and count the frames the window shows meanwhile."""
    from PySide6.QtCore import QTimer

    frames: list[float] = []
    cb = lambda: frames.append(time.perf_counter())  # noqa: E731
    win.frameSwapped.connect(cb)
    k = [0]
    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(interval_ms)

    def tick():
        make_event(k[0])
        k[0] += 1

    timer.timeout.connect(tick)
    timer.start()
    QTest.qWait(int(seconds * 1000))
    timer.stop()
    win.frameSwapped.disconnect(cb)
    dt = [b - a for a, b in zip(frames, frames[1:], strict=False)]
    return {
        "fps": round((len(frames) - 1) / (frames[-1] - frames[0]), 1),
        "p95_ms": round(sorted(dt)[int(0.95 * len(dt))] * 1000, 1),
        "events": k[0],
        "frames": len(frames),
    }


def _mouse(type_, pos, button, buttons):
    from PySide6.QtGui import QMouseEvent

    ev = QMouseEvent(
        type_,
        QPointF(pos),
        QPointF(win.mapToGlobal(pos)),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QGuiApplication.sendEvent(win, ev)


def s20():
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QWheelEvent

    e = editor()
    z0, px0, py0 = e.property("zoom"), e.property("panX"), e.property("panY")
    c = canvas().mapToScene(QPointF(canvas().width() / 2, canvas().height() / 2))
    centre = QPoint(round(c.x()), round(c.y()))
    mid = Qt.MouseButton.MiddleButton
    _mouse(QEvent.Type.MouseButtonPress, centre, mid, mid)

    def pan(k):
        a = k * 2 * math.pi / 150
        p = QPoint(round(centre.x() + 150 * math.cos(a)), round(centre.y() + 150 * math.sin(a)))
        _mouse(QEvent.Type.MouseMove, p, Qt.MouseButton.NoButton, mid)

    fps_detail["pan"] = input_frames(pan, 2.5)
    _mouse(QEvent.Type.MouseButtonRelease, centre, mid, Qt.MouseButton.NoButton)

    def zoom(k):
        delta = 120 if (k // 10) % 2 == 0 else -120
        gpos = win.mapToGlobal(centre)
        ev = QWheelEvent(
            QPointF(centre),
            QPointF(gpos),
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QGuiApplication.sendEvent(win, ev)

    fps_detail["zoom"] = input_frames(zoom, 2.5, interval_ms=8)
    e.setProperty("zoom", z0)
    e.setProperty("panX", px0)
    e.setProperty("panY", py0)
    wait(300)
    pf, zf = fps_detail["pan"]["fps"], fps_detail["zoom"]["fps"]
    check(pf > 50 and zf > 50, fps_detail)
    return f"middle-drag pan {fps_detail['pan']}; wheel zoom {fps_detail['zoom']}"


step("20 IEEE 123 pan/zoom smoothness (S1: ~60 fps)", s20)


def s20base():
    # the same input with every glyph hidden: what the window itself allows here
    hidden = []
    for nm in ("designerElementRepeater", "designerBusRepeater"):
        par = find_item(win, nm).parentItem()
        for c in par.childItems():
            if c.objectName().startswith(("designerElement_", "designerBus_")):
                c.setProperty("visible", False)
                hidden.append(c)
    sh = find_item(win, "designerBranches")
    sh.setProperty("visible", False)
    try:
        return s20()
    finally:
        for c in hidden:
            c.setProperty("visible", True)
        sh.setProperty("visible", True)


step("20b Same input, glyphs hidden (harness baseline)", s20base)


def s21():
    from PySide6.QtCore import QEvent

    e = editor()
    rows = d.property("busGlyphs").rows()
    # a bus near the middle of the view
    cx, cy = canvas().width() / 2, canvas().height() / 2
    z, u = e.property("zoom"), e.property("unit")
    best = min(
        rows,
        key=lambda r: (
            (e.property("panX") + r["x"] * u * z - cx) ** 2
            + (e.property("panY") + r["y"] * u * z - cy) ** 2
        ),
    )
    start = at(best["x"], best["y"])
    left = Qt.MouseButton.LeftButton
    d.clearSelection()
    _mouse(QEvent.Type.MouseButtonPress, start, left, left)
    wait(50)
    t = []

    def move(k):
        a = k * 2 * math.pi / 150
        p = QPoint(round(start.x() + 80 * math.sin(a)), round(start.y() + 60 * (1 - math.cos(a))))
        t0 = time.perf_counter()
        _mouse(QEvent.Type.MouseMove, p, Qt.MouseButton.NoButton, left)
        t.append((time.perf_counter() - t0) * 1000)

    r = input_frames(move, 3.0)
    _mouse(QEvent.Type.MouseButtonRelease, start, left, Qt.MouseButton.NoButton)
    r["handler_ms_median"] = round(statistics.median(t), 2)
    fps_detail["drag"] = r
    undo = d.property("undoText")
    d.undo()
    check(r["fps"] > 50, r)
    return f"mouse drag of {best['glyphId']}: {r}; one undo step {undo!r}"


step("21 IEEE 123 drag of a bus with the mouse", s21)


def s22():
    original = IEEE13.read_bytes()
    d.set_design(d.design())  # mark clean so no discard dialog
    check(grid.openFile(str(IEEE13)))
    check(wait_until(lambda: grid.model() is not None and not grid.property("running"), 60))
    win.findChild(QObject, "leftTabs").setProperty("currentIndex", 3)
    wait(300)
    btn = find_item(win, "gridEditAsDesign")
    check(btn.property("visible"))
    shot("22_grid_view_edit_as_design")
    click(center_of("gridEditAsDesign"))
    wait(300)
    check(win.findChild(QObject, "leftTabs").property("currentIndex") == 4)
    check(d.property("designName") == "ieee13nodeckt" and d.property("filePath") == "")
    check(IEEE13.read_bytes() == original)
    shot("22_ieee13_design")
    return "Edit as Design: a copy in the Designer tab; the .dss file is unchanged"


step("22 Edit as Design from the read-only grid view", s22)


def s23():
    win.findChild(QObject, "leftTabs").setProperty("currentIndex", 4)
    wait(100)
    run = win.findChild(QObject, "runButton")
    check(run.property("enabled") == d.property("canRun"))
    menu = win.findChild(QObject, "runPowerFlowMenu")
    check(menu.property("enabled") == d.property("canRun"))
    return "toolbar Run and Grid > Run Power Flow follow designer.canRun"


step("23 Main toolbar Run and Grid menu follow the designer", s23)

summary = {
    "steps": [{"name": n, "pass": ok, "detail": det} for n, ok, det in results],
    "fps": fps_detail,
    "warnings": [w for w in warnings if "does not support customization" not in w],
}
(OUT / "walk.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
print("WARNINGS:", summary["warnings"])
win.setProperty("quitting", True)
d.shutdown()
for n in ("Simulator", "DiagramController", "GridController"):
    engine.singletonInstance("QtSpice", n).shutdown()
win.close()
os._exit(0 if all(ok for _, ok, _ in results) else 1)
