# QtSpice (Python) - project memory

## What we are building
A desktop EDA application in Python. v0.1 has exactly these features:
- F1 Netlist input: type, paste, open or drag in .cir / .net / .sp files
- F2 Netlist checking: syntax and connectivity errors shown on the exact line
- F3 Simulation: .op .dc .ac .tran through the ngspice shared library, in the background
- F4 Waveforms: trace list, zoom/pan, two cursors with delta, log axis for AC, CSV export
- F5 Function blocks: a Python function with @block becomes a block whose ports
  match its signature
- F6 Block diagrams: place, wire and run blocks with a fixed time step; Scope blocks
  plot into the waveform viewer
- F7 Projects: save/load one .qsp JSON file plus .npz result files

OUT OF SCOPE for v0.1 - do not build: schematic capture, noise/TF analyses,
variable-step block solver, physical (Simscape-like) blocks, code generation,
plug-ins, our own SPICE engine.

## Stack
Python 3.12, PySide6 6.8 (Qt Quick / QML, QtGraphs), NumPy,
ngspice shared library via ctypes, graphlib, multiprocessing,
pytest, pytest-qt, ruff, PyInstaller.

## Architecture rules (always follow)
1. Four layers: ui/qml -> bridge -> core -> external. A layer only calls the one below.
2. core/ never imports PySide6 or any Qt module.
3. QML never calls core. It binds only to four bridge objects: Simulator,
   WaveformModel, DiagramController, ProjectManager. Register them with
   @QmlElement / @QmlSingleton, not setContextProperty.
4. Only core/sim/ngspice_adapter.py knows the ngspice API. Load libngspice lazily with
   ctypes. If it is missing, available() returns False and the app still starts.
5. ngspice runs on a QThread (SimulationWorker). User block code runs in a separate
   process (BlockRunner) with a timeout and cancel.
6. NetlistParser never raises on bad input. It returns ParseResult(circuit, diagnostics).
7. ngspice results and block results use the same SimulationResult class.

## Folder layout
app/__main__.py                  entry point: QGuiApplication + Main.qml
core/netlist/   parser.py model.py validate.py builder.py
core/sim/       backend.py ngspice_adapter.py result.py
core/blocks/    decorator.py spec.py registry.py diagram.py engine.py runner.py library/
core/project/   io.py schema.json
bridge/         simulator.py sim_worker.py waveform_model.py
                diagram_controller.py project_manager.py highlighter.py
ui/qml/         Main.qml NetlistEditor.qml AnalysisPanel.qml WaveformView.qml
                DeckView.qml ConsoleView.qml BlockCanvas.qml Block.qml Port.qml
                Wire.qml BlockLibrary.qml FunctionEditor.qml PropertiesPanel.qml
tests/core/ tests/ui/ tests/netlists/
third_party/ngspice/             sharedspice.h + shared library per OS

## ngspice in this checkout
- Header: third_party/ngspice/include/sharedspice.h
- Windows library: third_party/ngspice/lib/ngspice.dll (64-bit, loads with ctypes)
- third_party/ngspice/README.md describes an older C++/QLibrary version of this app;
  ignore its C++ paths. The Python adapter searches lib/ and bin/ under
  third_party/ngspice/, then the system path.

## Commands
pip install -e ".[dev]"
python -m app
pytest -q
ruff check . && ruff format --check .

## Conventions
- Type hints everywhere; dataclasses for data; no global mutable state.
- Every core module has tests under tests/core/ mirroring its path.
- Tests that need ngspice are marked @pytest.mark.ngspice and skip if it is missing.
- When adding or removing a source file, update [tool.pyside6-project] files in
  pyproject.toml (Qt Creator uses it to open the project).
- One component per commit; message format "<area>: <what>".
- Before saying a task is done: ruff and pytest must both pass.
- For ngspice API details, read third_party/ngspice/include/sharedspice.h; do not guess.
- If a requirement is unclear, ask instead of inventing behaviour.

## Spike results (phase-2 setup; ngspice-46, PySide6 6.8.3, Windows 11)
Spike code lives under scratch/ and is never imported by app code. Fixtures are in
tests/fixtures/ (see its README); they are byte-exact (`-text` in .gitattributes).

A. Compat mode (scratch/spike_a_compat/compat_spike.py; one fresh process per case)
- `ngSpice_Command("set ngbehavior=ltpsa")` before `ngSpice_Circ` WORKS. ngspice
  prints "Compatibility modes selected: ps lt a". A .spiceinit is not needed.
- The setting is read on every circuit load: sent after an earlier circuit it still
  applies, and `unset ngbehavior` switches it off for the next load. So the
  compatibility-mode setting can be applied per run without restarting ngspice.
- A .spiceinit with the same line in the working folder also works, but only at
  ngSpice_Init (once per process). Do not use it.
- Needs the mode: PSpice `if()` and LTspice `uplim()` in B-sources ("no such function"
  without it). Same in every mode: `R={expr}`, a UTF-8 micro sign in values, and
  tsmc018.lib (LEVEL=49 VERSION=3.1, BSIM3) runs with only W/Pd/Ps warnings.
  Fails in every mode: PSpice `VSWITCH` model and `E ... TABLE {V(a)} = (..)`;
  these must become clear "not supported by ngspice" diagnostics.
- After a "no such function" load error ngspice prints "cannot recover and awaits to
  be reset", but the next `ngSpice_Circ` in the same process loads and runs fine.

B. Symbols in QML (scratch/spike_b_symbols/run.py)
- One `Shape` + `ShapePath { scale: Qt.size(u, u) }` + `PathSvg` per symbol draws
  LINE/RECTANGLE/CIRCLE/ARC cleanly (CurveRenderer); pins are Rectangle dots on the
  16-unit grid. The real cmosn.asy draws correctly; the cp1252 file decodes to 4.7u
  with the micro sign.
- ARC -> SVG: centre and radii from the bbox, project the start/end ray points onto
  the ellipse, sweep-flag 0 (counter-clockwise on screen, y down).
- Offscreen grabs need QT_QPA_FONTDIR=C:/Windows/Fonts or every Text shows as boxes.

C. Panes, 3 x 1 M points (scratch/spike_c_panes/run.py --mode ...; 1200x800 window)
- raw (all 1 M points in each LineSeries): unusable - 13 s to show, ~0 fps zooming.
- decimated (v0.1 minmax_buckets on every zoom step): 22 fps. The cost is
  minmax_buckets over the full 1 M window (~12 ms/pane); QPointF building is ~2 ms.
- pyramid (min/max levels precomputed in blocks of 4^k, O(pixels) per fill): 32 fps.
- axis-only baseline (fill once, then only move the axes): 60 fps, so the remaining
  cost is the per-frame refill plus the QtGraphs geometry rebuild, not drawing.
- Plan for the W tasks: build a min/max pyramid per trace once per result; while a zoom
  animates, move the axes only and refill when it settles (or at most every ~50 ms).
- One cursor line across all panes = one overlay Rectangle. It works because the panes
  share X and the same fixed plot-area offsets (60 px left, as in WaveformView.qml).
- QML gotcha: never name a property `data` on an Item; it shadows Item.data and all
  children vanish.

D. Drag and drop (scratch/spike_d_dnd/run.py --selftest; passes offscreen and on screen)
- `Drag.dragType: Drag.Internal` + `Drag.keys` + one `DropArea { keys }` per pane, with
  `ParentChange` to the root while dragging so the label can leave its pane.
- Ctrl = copy: set `Drag.proposedAction` from `mouse.modifiers` in the MouseArea's
  onReleased, then call `Drag.drop()`; the DropArea reads `ev.proposedAction`
  (DragEvent has no modifiers). QTest.mouseMove sends no modifiers, so tests pass
  Ctrl on press/release.
- QML gotcha: a property named like an outer id (`property int pane: pane.index`)
  shadows the id and silently reads itself; use distinct names.
