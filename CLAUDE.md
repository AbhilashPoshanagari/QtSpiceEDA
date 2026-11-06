# QtSpice (Python) - project memory

## Status
- v0.1.0 is released: netlist editor, ngspice simulation (.op .dc .ac .tran),
  waveform viewer, Python function blocks, block diagrams, .qsp projects.
- We are now building v0.2 on the `phase-2` branch. Do not break v0.1 behaviour:
  every v0.1 test must keep passing.

## What v0.2 adds (only these - nothing else)
- Libraries (L1-L8): `.lib file`, `.lib file section` / `.endl`, `.param` inside
  `.subckt`, a library index and browser, ngspice LTspice compatibility mode.
- Symbols (L4-L7): read LTspice `.asy` symbol files; `.asy` is also our own symbol format.
- Schematic (S1-S11): one sheet, LTspice-style editing and probing, netlisted to the
  same `Circuit` object the netlist editor produces.
- Circuit symbol (H1-H3): turn a schematic or netlist into a `.sub` + `.asy` pair and
  place it in another schematic. One level only.
- Waveform window (W1-W10): panes, zoom, drag-and-drop traces, log axes, Bode view,
  cursors, Add Trace dialog with LTspice expressions, `.func` plot definitions,
  average/RMS.
- Fixes (O1-O5): title-line warning, run errors mapped to a line or part, block-run
  timeout setting and step guard, safe results folder, one schematic per project.

OUT OF SCOPE for v0.2 - do not build, even if it looks easy:
buses, multiple sheets, `.asc` import, graphical symbol editor, auto-routing,
nested `.subckt`, multi-level hierarchy, parameters passed into symbols,
loading `.dll` files of any kind, decrypting encrypted models, separate plot windows,
XY/Nyquist plots, reading LTspice `.plt` files, FFT, `.meas`, automatic units,
Python functions in the calculator, noise/.tf/.sens/.four/.control, more than one
analysis per run, variable-step block solver, physical blocks, vector Scope values,
two simulations at once, more than one netlist/diagram/schematic per project.
If a task seems to need one of these, stop and ask.

## Stack
Python 3.12, PySide6 6.8 (Qt Quick / QML, QtGraphs), NumPy, ngspice shared library
via ctypes, graphlib, multiprocessing, pytest, pytest-qt, ruff, PyInstaller.

## Architecture rules (always follow)
1. Four layers: ui/qml -> bridge -> core -> external. A layer only calls the one below.
2. core/ never imports PySide6 or any Qt module.
3. QML binds only to bridge objects registered with @QmlElement / @QmlSingleton:
   Simulator, WaveformModel, DiagramController, ProjectManager (v0.1) and
   SchematicController, WaveformController (new in v0.2). Never setContextProperty.
4. Only core/sim/ngspice_adapter.py knows the ngspice API. Load libngspice lazily with
   ctypes; if missing, available() returns False and the app still starts.
5. ngspice runs on a QThread (SimulationWorker). User block code runs in a separate
   process (BlockRunner).
6. Parsers never raise on bad input. They return a result object plus a list of
   Diagnostic(line, col, severity, message). This applies to the netlist parser,
   AsyParser, the library index and the plot definitions file.
7. ngspice results and block results use the same SimulationResult class.
8. A schematic is only another way to make a `Circuit`. Schematic netlisting goes
   through the existing NetlistBuilder and Simulator. Do not add a second simulation path.
9. Waveform expressions are parsed with Python `ast` against a whitelist and evaluated
   on NumPy arrays. Never call eval() or exec() on user text.
10. Every user edit in an editor (netlist, schematic, diagram, plot layout) goes
    through that editor's own QUndoStack.

## LTspice compatibility rules (follow exactly)
Reference: https://analogdevicesinc.github.io/ltspice-reference/ai_ref/WAVEFORM-VIEWER-GUIDE.html
and https://dev-docs.kicad.org/en/import-formats/ltspice/index.html

Symbols (.asy):
- Plain text, read as Windows-1252 (the micro sign is byte 0xB5), CR+LF or LF.
- Keywords: Version, SymbolType (CELL/BLOCK), LINE, RECTANGLE, CIRCLE, ARC, TEXT,
  WINDOW, SYMATTR, PIN, PINATTR. Unknown keywords give a warning, not an error.
- Pin order in the netlist comes from PINATTR SpiceOrder, never from drawing order.
- SYMATTR Prefix X means a subcircuit; ModelFile is added as `.include`.
- We never ship Analog Devices' own .asy/.lib files. Users may add their LTspice
  install folder as a read-only search path.

Probing (schematic, after a run):
- click wire -> V(net); click two-terminal part body -> its current;
  click a pin -> current into that pin; drag net to net -> V(a,b);
  Alt+click part -> power V*I. Pointer icon shows which.

Waveform window:
- Panes share one X axis; each pane autoscales its own Y.
- Drag a trace name to another pane = move; Ctrl+drop = copy; double-click trace = show only it.
- Drag a box to zoom; Esc cancels; zoom back / auto-range; zoom history is undoable.
- Click a trace name to attach cursor 1; right-click for cursor 2 or both;
  arrow keys move one data point.
- Ctrl+click a trace name -> average and RMS over the zoomed X range
  (RMS only for voltage/current traces).
- Expression syntax uses LTspice names:
  operators  & | ^  (Boolean AND, OR, XOR - ^ is NOT power)   > < >= <=   + - * / **   !
  functions  sin cos tan asin acos atan atan2 sinh cosh tanh ln log log10 exp sqrt pow
             abs ceil floor round int if limit min max hypot d Re Im Ph Mag conj
  constants  E pi K Q        axis names  time, omega / w
  V(a,b) means V(a) - V(b). In LTspice, log() is the natural log (same as ln).
- Plot definitions file: `.func` and `.param` lines, same syntax as SPICE.
  Ship a default with `.func dB(x) 20*log10(Mag(x))`.
- One plot layout per analysis type (.tran, .ac, .dc), stored in the .qsp project.

ngspice:
- Send `set ngbehavior=ltpsa` before loading a circuit (setting, on by default).
  Compatibility is not 100%: LTspice-only devices (e.g. A-devices) must produce a
  clear "not supported by ngspice" diagnostic, not a crash.
- Detect encrypted model files (e.g. `* Begin:` ... `* End` hex blocks) and report them;
  never try to decrypt.

## Folder layout (v0.2 additions marked +)
app/__main__.py
core/netlist/     parser.py model.py validate.py builder.py  + libindex.py
core/sim/         backend.py ngspice_adapter.py result.py
core/blocks/      decorator.py spec.py registry.py diagram.py engine.py runner.py library/
core/project/     io.py schema.json
+ core/symbols/   asy.py library.py builtin/*.asy
+ core/schematic/ model.py connectivity.py netlister.py
+ core/hierarchy/ symbolgen.py
+ core/waveform/  layout.py expr.py plotdefs.py
bridge/           simulator.py sim_worker.py waveform_model.py diagram_controller.py
                  project_manager.py highlighter.py
                  + schematic_controller.py + waveform_controller.py
ui/qml/           (v0.1 files) + SchematicCanvas.qml SymbolItem.qml PartPicker.qml
                  WireTool.qml AttributeDialog.qml LibraryBrowser.qml PlotPane.qml
                  PlotConfigDialog.qml TraceLabel.qml CursorReadout.qml
                  AddTraceDialog.qml
tests/core/ tests/ui/ tests/netlists/  + tests/fixtures/ (asy, lib, schematics)
third_party/ngspice/

## Project file
- One `.qsp` JSON file with a `version` field (bump to 2; v1 files must still open).
- v0.2 adds: one schematic, generated symbol paths, plot layouts per analysis type,
  the plot-definitions text, compatibility-mode setting, block-run timeout.
- Results live in `name_results/` next to `name.qsp`. The project keeps a list of its
  own result files and deletes only those.

## Commands
pip install -e ".[dev]"
python -m app
pytest -q
ruff check . && ruff format --check .

## Conventions
- Type hints everywhere; dataclasses for data; no global mutable state.
- Every core module has tests under tests/core/ mirroring its path.
- Tests that need ngspice are marked @pytest.mark.ngspice and skip if it is missing.
- One task ID (e.g. S7, W4) per commit; message format "<ID> <area>: <what>".
- Before saying a task is done: ruff and pytest must both pass.
- For ngspice API details read third_party/ngspice/sharedspice.h; do not guess.
- For LTspice behaviour follow the rules above; if they don't cover a case, ask.
- If a requirement is unclear, ask instead of inventing behaviour.

## Known v0.1 behaviour to keep (unless a v0.2 task changes it)
- The first netlist line is the title (O1 only adds a warning).
- Block diagrams use fixed-step forward Euler.
- One simulation runs at a time.
- On Windows, pytest may print "Windows fatal exception: access violation" while a
  block-runner child process starts. Tests pass; the app does not enable faulthandler.
  Do not try to "fix" this.

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
