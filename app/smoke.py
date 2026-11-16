"""Smoke test of an installation: ``qtspice --smoke-test [NETLIST] [--report FILE]``.

Runs without showing a window (offscreen) and checks, through the same bridge objects
the UI uses:

1. Main.qml loads (the QML files and Qt plugins are present);
2. ngspice loads and simulates NETLIST (default: the bundled RC example); for the RC
   example V(out) at the end of the transient must be 1 - exp(-5);
3. a small block diagram runs (the block-runner child process starts);
4. the project saves and opens again with the same result;
5. (v0.4) OpenDSS loads in the grid-runner child process and solves the bundled
   IEEE 13 feeder (examples/grid/). In a packaged app a missing OpenDSS engine is a
   failure, because the build bundles it; from source without the ``grid`` extra the
   check is skipped.
6. (v0.5) the Grid Network Designer opens the bundled sample design
   (examples/ieee13_design.qgrid), builds a two-bus design with edit commands only
   (source, one line with an IEEE 13 linecode, one IEEE 13 load; every value copied
   from the sample), checks it, writes its DSS script, saves and reopens it, and -
   only when OpenDSS is available, as in check 5 - runs it.

Prints one line per check and writes them to FILE as well (a windowed app on Windows
has no console). The exit code is 0 only if every check passed. Settings go to a
temporary folder, so the user's recent files are not touched.
"""

from __future__ import annotations

import copy
import dataclasses
import math
import os
import sys
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEventLoop, QObject, QSettings, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

RUN_TIMEOUT_MS = 120_000

Log = Callable[[str], None]


def run_smoke_test(netlist: Path, report: str | None, qt_args: list[str]) -> int:
    lines: list[str] = []

    def log(text: str) -> None:
        lines.append(text)
        if sys.stdout is not None:
            print(text, flush=True)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    settings_dir = tempfile.mkdtemp(prefix="qtspice-smoke-")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, settings_dir)
    QQuickStyle.setStyle("Fusion")
    app = QGuiApplication(qt_args)
    app.setApplicationName("QtSpice")
    app.setOrganizationName("QtSpice")
    try:
        ok = _run_checks(netlist, Path(settings_dir), log)
    except Exception:
        log("FAIL: unexpected error\n" + traceback.format_exc())
        ok = False
    log("SMOKE TEST PASSED" if ok else "SMOKE TEST FAILED")
    if report:
        Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def _run_checks(netlist: Path, work: Path, log: Log) -> bool:
    from app.__main__ import create_engine

    log(f"QtSpice smoke test: {sys.executable} (frozen: {getattr(sys, 'frozen', False)})")
    engine = create_engine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(w.toString() for w in items))
    if not engine.rootObjects():
        log("FAIL: Main.qml did not load")
        return False
    log("ok: Main.qml loaded")
    sim = engine.singletonInstance("QtSpice", "Simulator")
    diagram = engine.singletonInstance("QtSpice", "DiagramController")
    projects = engine.singletonInstance("QtSpice", "ProjectManager")
    grid = engine.singletonInstance("QtSpice", "GridController")
    schematic = engine.singletonInstance("QtSpice", "SchematicController")
    designer = engine.rootObjects()[0].findChild(QObject, "gridDesigner")
    try:
        ok = (
            _check_netlist(sim, netlist, log)
            and _check_diagram(diagram, log)
            and _check_project(sim, projects, work, log)
            and _check_schematic(schematic, netlist, log)
            and _check_grid(grid, log)
            and _check_designer(designer, work, log)
        )
    finally:
        if designer is not None:
            designer.shutdown()
        sim.shutdown()
        diagram.shutdown()
        grid.shutdown()
    for warning in warnings:
        log(f"QML warning: {warning}")
    return ok


def _wait(running: Callable[[], bool], done_signal: object) -> None:
    loop = QEventLoop()
    done_signal.connect(loop.quit)  # type: ignore[attr-defined]
    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(lambda: None if running() else loop.quit())
    timer.start()
    QTimer.singleShot(RUN_TIMEOUT_MS, loop.quit)
    loop.exec()
    timer.stop()


def _check_netlist(sim: QObject, netlist: Path, log: Log) -> bool:
    backend = sim._get_backend()  # type: ignore[attr-defined]
    if not backend.available():
        log(f"FAIL: ngspice did not load: {backend.last_error}")
        return False
    log("ok: ngspice loaded")
    text = netlist.read_text(encoding="utf-8")
    sim.setProperty("netlistText", text)
    if not sim.run(text):  # type: ignore[attr-defined]
        log(f"FAIL: {netlist.name} did not start: {sim.property('statusText')}")
        return False
    _wait(lambda: bool(sim.property("running")), sim.resultReady)  # type: ignore[attr-defined]
    result = sim.result  # type: ignore[attr-defined]
    if result is None or sim.property("running"):
        log(f"FAIL: {netlist.name}: {sim.property('statusText') or 'no result'}")
        return False
    points = len(result.vectors[result.axis]) if result.axis else 1
    log(f"ok: {netlist.name}: .{result.analysis}, {points} points, {result.trace_names()}")
    out = next((n for n in result.trace_names() if n.lower() == "v(out)"), None)
    if netlist.name == "rc_lowpass.cir" and result.analysis == "tran":
        if out is None:
            log("FAIL: the RC example has no V(out)")
            return False
        final = float(np.real(result.vectors[out][-1]))
        expected = 1 - math.exp(-5)
        if abs(final - expected) > 0.01:
            log(f"FAIL: V(out) at 5 ms is {final:.4f}, expected {expected:.4f}")
            return False
        log(f"ok: V(out) at 5 ms = {final:.4f} (expected {expected:.4f})")
    return True


def _check_diagram(diagram: QObject, log: Log) -> bool:
    sine = diagram.addBlock("Sine", 0, 0)  # type: ignore[attr-defined]
    scope = diagram.addBlock("Scope", 200, 0)  # type: ignore[attr-defined]
    if not diagram.connectPorts(f"{sine}.y", f"{scope}.u"):  # type: ignore[attr-defined]
        log(f"FAIL: could not wire the test diagram: {diagram.property('hint')}")
        return False
    if not diagram.run(0.01, 0.001):  # type: ignore[attr-defined]
        log(f"FAIL: the block diagram did not start: {diagram.property('runStatus')}")
        return False
    _wait(lambda: bool(diagram.property("running")), diagram.resultReady)  # type: ignore[attr-defined]
    result = diagram.result  # type: ignore[attr-defined]
    if result is None or not result.trace_names():
        log(
            f"FAIL: block diagram: {diagram.property('runStatus')} {diagram.property('runDetails')}"
        )
        return False
    log(f"ok: block diagram: {diagram.property('runStatus')}")
    return True


def _check_project(sim: QObject, projects: QObject, work: Path, log: Log) -> bool:
    path = work / "smoke.qsp"
    before = sim.result  # type: ignore[attr-defined]
    text = sim.property("netlistText")
    if not projects.saveAs(str(path)):  # type: ignore[attr-defined]
        log(f"FAIL: project not saved: {projects.property('errorString')}")
        return False
    projects.newProject()  # type: ignore[attr-defined]
    if not projects.open(str(path)):  # type: ignore[attr-defined]
        log(f"FAIL: project not opened: {projects.property('errorString')}")
        return False
    after = sim.result  # type: ignore[attr-defined]
    same = (
        sim.property("netlistText") == text
        and after is not None
        and list(after.vectors) == list(before.vectors)
        and all(np.array_equal(before.vectors[k], after.vectors[k]) for k in before.vectors)
    )
    if not same:
        log("FAIL: the reopened project differs from the saved one")
        return False
    log(f"ok: project saved and reopened ({path.name})")
    return True


def _check_schematic(schematic: QObject, netlist: Path, log: Log) -> bool:
    """Tools > Schematic from Netlist: the built-in symbols are in the bundle and the
    drawn sheet netlists again (N1-N3)."""
    result = schematic.generateFromNetlist(netlist.read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    if not result["ok"] or not schematic.check():  # type: ignore[attr-defined]
        problems = "; ".join(p["message"] for p in schematic.property("problems"))
        log(f"FAIL: schematic from {netlist.name}: {problems or 'not drawn'}")
        return False
    log(f"ok: schematic drawn from {netlist.name} ({result['placed']} parts)")
    schematic.load_schematic(None)  # type: ignore[attr-defined]
    return True


def _check_grid(grid: QObject, log: Log) -> bool:
    from app.__main__ import EXAMPLE_FEEDER

    if not grid.property("available"):
        if getattr(sys, "frozen", False):
            log("FAIL: OpenDSS (dss-python) is not in the bundle")
            return False
        log("skipped: grid power flow (dss-python is not installed)")
        return True
    if not grid.openFile(str(EXAMPLE_FEEDER)):  # type: ignore[attr-defined]
        log(f"FAIL: {EXAMPLE_FEEDER.name} did not open: {grid.property('statusText')}")
        return False
    _wait(lambda: bool(grid.property("running")), grid.runningChanged)  # type: ignore[attr-defined]
    if not grid.property("canRun"):
        log(f"FAIL: {EXAMPLE_FEEDER.name}: {grid.property('statusText')}")
        return False
    if not grid.run():  # type: ignore[attr-defined]
        log(f"FAIL: the power flow did not start: {grid.property('statusText')}")
        return False
    _wait(lambda: bool(grid.property("running")), grid.runningChanged)  # type: ignore[attr-defined]
    result = grid.result()  # type: ignore[attr-defined]
    if result is None or not result.converged:
        log(f"FAIL: power flow: {grid.property('statusText')}")
        return False
    log(f"ok: grid power flow: {grid.property('statusText')}")
    log(f"ok: IEEE 13 node 671.1 = {result.voltage_pu('671.1'):.5f} pu")
    return True


def _check_designer(designer: QObject | None, work: Path, log: Log) -> bool:
    """A two-bus design made in the designer with edit commands only (every one an
    undo step), from values copied from the IEEE 13 sample design (rule 24: nothing
    invented): the IEEE 13 source impedances at the kV of load 671, line 670671's
    linecode and length, load 671. Checked (no errors), written as a DSS script, saved
    and reopened, and run through GridController when OpenDSS is available."""
    from app.__main__ import EXAMPLE_DESIGN

    if designer is None:
        log("FAIL: the Grid Network Designer is not in Main.qml")
        return False
    if not designer.load(str(EXAMPLE_DESIGN)) or designer.property("errorCount"):  # type: ignore[attr-defined]
        log(f"FAIL: {EXAMPLE_DESIGN.name}: {designer.property('statusText')}")
        return False
    template = designer.design()  # type: ignore[attr-defined]
    log(
        f"ok: designer opened {EXAMPLE_DESIGN.name}: {designer.property('busCount')} buses, "
        f"{designer.property('elementCount')} elements"
    )
    ieee = copy.deepcopy(template.grid)
    src, line, load = ieee.sources["source"], ieee.lines["670671"], ieee.loads["671"]
    code = ieee.linecodes[line.linecode]

    def fields(el: object, skip: set[str]) -> dict[str, object]:
        return {
            f.name: getattr(el, f.name)
            for f in dataclasses.fields(el)  # type: ignore[arg-type]
            if f.name not in skip and getattr(el, f.name) is not None
        }

    nan = math.nan
    source = fields(src, {"name", "bus1", "bus2"}) | {"kv": load.kv}
    steps = [
        ("bus", [], 0.0, 0.0, {"name": "b1"}),
        ("vsource", ["b1"], nan, nan, source),
        ("bus", [], 0.0, 6.0, {"name": "b2"}),
        ("linecode", [], nan, nan, fields(code, {"generated"})),
        ("line", ["b1", "b2"], nan, nan, {"linecode": code.name, "length_m": line.length_m}),
        ("load", ["b2"], nan, nan, fields(load, {"name", "terminal"})),
    ]
    if not designer.newDesign("smoke", ieee.base_frequency):  # type: ignore[attr-defined]
        log(f"FAIL: designer: {designer.property('statusText')}")
        return False
    for kind, terminals, x, y, values in steps:
        if not designer.addElement(kind, terminals, x, y, values):  # type: ignore[attr-defined]
            log(f"FAIL: designer: add {kind}: {designer.property('statusText')}")
            return False
    count = designer.undo_stack().count()  # type: ignore[attr-defined]
    if count != len(steps):
        log(f"FAIL: designer: {count} undo steps for {len(steps)} edits")
        return False
    designer._validate_now()  # type: ignore[attr-defined]  # skip the edit debounce
    errors = [d for d in designer.diagnostics() if d.severity == "error"]  # type: ignore[attr-defined]
    if errors:
        log(f"FAIL: design check: {errors[0].block}: {errors[0].message}")
        return False
    log(
        f"ok: designer: two-bus design from {len(steps)} edit commands, no errors "
        f"(linecode {code.name}, load {load.name} {load.kw:g} kW)"
    )
    script = work / "smoke_design.dss"
    if not designer.exportDss(str(script)):  # type: ignore[attr-defined]
        log(f"FAIL: DSS script not written: {designer.property('statusText')}")
        return False
    text = script.read_text(encoding="utf-8").lower()
    if "new line." not in text or "new load." not in text:
        log("FAIL: the DSS script has no line or load")
        return False
    log(f"ok: designer: DSS script written ({len(text.splitlines())} lines)")
    saved = work / "smoke_design.qgrid"
    built = copy.deepcopy(designer.design())  # type: ignore[attr-defined]
    if not designer.save(str(saved)) or not designer.load(str(saved)):  # type: ignore[attr-defined]
        log(f"FAIL: design not saved and reopened: {designer.property('statusText')}")
        return False
    if designer.design() != built:  # type: ignore[attr-defined]
        log("FAIL: the reopened design differs from the saved one")
        return False
    log(f"ok: designer: design saved and reopened ({saved.name})")
    if not designer.property("available"):
        if getattr(sys, "frozen", False):
            log("FAIL: OpenDSS (dss-python) is not in the bundle")
            return False
        log("skipped: design power flow (dss-python is not installed)")
        return True
    if not designer.runPowerFlow():  # type: ignore[attr-defined]
        log(f"FAIL: design run did not start: {designer.property('statusText')}")
        return False
    _wait(lambda: bool(designer.property("busy")), designer.resultReady)  # type: ignore[attr-defined]
    result = designer.result()  # type: ignore[attr-defined]
    if result is None or not result.converged:
        log(f"FAIL: design power flow: {designer.property('statusText')}")
        return False
    worst = min(result.voltage_pu(f"b2.{n}") for n in (1, 2, 3))
    log(f"ok: design power flow: {designer.property('statusText')}")
    log(f"ok: design bus b2 lowest phase = {worst:.5f} pu")
    return True
