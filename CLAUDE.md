# QtSpice (Python) - project memory

## Status
- v0.1.0 released: netlist editor, ngspice simulation, waveform viewer, Python function
  blocks, block diagrams, .qsp projects.
- v0.2.0 released: LTspice-style improvements only (.lib/.asy reading, schematic editor,
  circuit symbols, multi-pane waveform window with LTspice expressions).
- v0.3.0 released: Simscape-style physical modelling (F, C, B, U, M, O tasks).
- v0.4.0 released: distribution-grid power flow with OpenDSS (S1-S6, G1-G7): engine,
  GridModel, writer + round trip, validation, IEEE 13/34/123 benchmarks, read-only grid
  view, .qsp format 4.
- Schematic from netlist released (N0-N4): library folder, netlist -> schematic.
- We are now building v0.5 on the `phase-5` branch: the Grid Network Designer, a
  graphical feeder editor backed by GridModel and solved by OpenDSS, with Level 1
  (topology) and Level 2 (electrical, one snapshot) feasibility checks.
  Every v0.1-v0.4 and netlist-schematic test must keep passing.
- Roadmap change (decided for v0.5): GIS import moves from v0.5 to v0.6.
  v0.6 GIS import + catalogs, v0.7 feeder planning (scenarios, before/after),
  v0.8+ advanced studies (QSTS, DER/EV impact, hosting capacity, faults, protection).

## What v0.5 adds (only these - nothing else)
- S1-S6 Spikes (week 1, scratch/phase5/ only): canvas performance on IEEE 123, undo
  command pattern, auto-layout for buses without coordinates, draft models and engine
  checks (NormAmps, voltage bases, sequence impedances), design-file format, edit-run
  latency.
- D1 Topology: core/grid/topology.py builds the electrical graph from a GridModel
  (buses, branches, open switches, transformers), finds islands, source reachability,
  voltage zones and per-bus available phases. G4 validate.py is moved onto it with the
  same diagnostics.
- D2 Model for designs: GridModel accepts incomplete (draft) elements; LineCode/Line get
  NormAmps/EmergAmps; voltage bases are derived for designs; sequence -> phase matrices in
  core/grid/sequence.py. The writer refuses an incomplete model with Diagnostics.
- D3 Diagram model and design file: core/grid/diagram/{model.py,layout.py} (presentation
  only) and core/grid/design_io.py (.qgrid JSON = GridModel + DiagramModel, versioned).
- D4 Edit commands: core/grid/diagram/commands.py - add, remove, connect, disconnect,
  set property, move, rotate, rename, open/close switch, compound. Exact revert.
- D5 Bridge: bridge/grid_diagram_controller.py (GridDiagramController), one QUndoStack,
  glyph and property models, live validation, Run through GridController/GridRunner.
- D6 Editor UI: GridDiagramEditor.qml, GridComponentLibrary.qml, GridProperties.qml;
  New Design, Open .dss as Design, Save/Save As, Export .dss.
- D7 Feasibility: line and transformer loading, voltage violations, losses, voltage drop,
  FeasibilityReport (Level 1 + Level 2), computed in core/grid/analysis.py.
- D8 Results overlay: GridResultsOverlay.qml (bus voltage / branch loading colours),
  feasibility panel, click-to-select, Console diagnostics.
- D9 Project and release: .qsp format version 5, sample design project, docs, v0.5.0.

OUT OF SCOPE for v0.5 - do not build, even if it looks easy:
GIS or ArcGIS import, data normalisation, electrical enrichment, conductor/transformer
catalogs or "standard" equipment libraries (v0.6); scenarios, load-growth studies,
before/after comparison, design alternatives, "can I add X" automation (v0.7); QSTS /
load shapes / yearly runs, fault studies, protection, hosting capacity, EV impact,
harmonics, dynamics (v0.8+); new equipment classes (fuse, recloser, breaker, generator,
storage, EV charger) - the library offers only the v0.4 model classes; advice or
auto-fix of violations ("upsize the conductor"); copy/paste between designs; more than
one diagram per GridModel; display units other than the canonical ones; editing in the
G6 read-only grid view; our own power-flow solver or a DSS-language parser; coupling
grid models with ngspice, physical networks or Python controllers; a second grid engine.
Everything that was out of scope in v0.2, v0.3 and v0.4 stays out.
If a task seems to need one of these, stop and ask.

## Schematic from netlist (released, kept)
- Library folder: `library/` from source (git-ignored), Documents/QtSpice/library in the
  packaged app; SchematicController.defaultLibraryFolder; `.include`/`.lib` search order:
  absolute, next to the netlist, default folder, library folders
  (`parse(text, base_dir, search_dirs)`).
- core/schematic/fromnetlist.py converts an error-free netlist; re-netlisting the result
  must give the same Circuit, or nothing is shown. SchematicController.generateFromNetlist
  is one undo step. Tests isolate the default folder (tests/conftest.py); never read the
  real one.

## Stack
Python 3.12, PySide6 6.8 (Qt Quick / QML, QtGraphs), NumPy, ngspice shared library
via ctypes, graphlib, multiprocessing, pytest, pytest-qt, ruff, PyInstaller.
DSS-Python (package `dss-python`, pin 0.15.x, BSD-3) as the optional extra
`pip install -e ".[grid]"`. The app must start and all non-grid features must work
without it. The designer must open, edit, validate and save designs without it; only
Run needs it.
v0.5 adds NO new dependency (no networkx, graphviz, igraph or layout library; the
topology graph and auto-layout are our own small code on graphlib/dicts).
Do not add PySpice as a dependency (GPLv3). Reading its source for reference is fine;
never copy code from it. Do not add OpenDSSDirect.py, the Windows COM OpenDSS or
pandapower.

## Architecture rules (always follow)
1. Four layers: ui/qml -> bridge -> core -> external. A layer only calls the one below.
2. core/ never imports PySide6 or any Qt module.
3. QML binds only to bridge objects registered with @QmlElement / @QmlSingleton:
   Simulator, WaveformModel, DiagramController, ProjectManager, SchematicController,
   WaveformController, GridController (v0.4), and NEW in v0.5: GridDiagramController.
   Never setContextProperty.
4. Only core/sim/ngspice_adapter.py knows the ngspice API. Load libngspice lazily with
   ctypes; if missing, available() returns False and the app still starts.
   Call setlocale(LC_NUMERIC, "C") before loading any circuit.
5. Plain netlist and schematic runs: ngspice on a QThread (SimulationWorker).
   Block diagrams, including combined physical runs: in the BlockRunner child process,
   which loads NgspiceAdapter itself. User code never runs in the UI process.
   (v0.4) grid runs: in the GridRunner child process (same spawn + Pipe + daemon pattern
   as BlockRunner; reuse its process helper, don't copy it), which creates its own
   OpenDSSEngine. OpenDSS never runs in the UI process.
6. Parsers and checkers never raise on bad input; they return results plus
   Diagnostic(line or block, severity, message). Grid code uses the same Diagnostic
   class, with the element id ("line.650632", "bus.671") as the location. If a stable
   diagnostic code is needed, add an optional field to the existing class - never a
   second Diagnostic class.
7. All time-domain and sweep results use the same SimulationResult class.
   (v0.4) grid power flow returns GridResult (node voltages, branch results, totals).
   Anything shown in the waveform window is converted to a SimulationResult first
   (GridResult.to_simulation_result()); the waveform window never reads GridResult.
8. A schematic is only another way to make a Circuit (v0.2 rule, unchanged).
9. Waveform expressions: Python ast + whitelist, never eval/exec (v0.2 rule, unchanged).
10. Every user edit goes through that editor's own QUndoStack.
11. Physical networks are ALWAYS solved by mapping them to a Circuit with
    NetworkMapper and running ngspice. Never write a second solver, and never integrate
    physical equations in Python. (v0.3 rule, unchanged. OpenDSS is not a solver for
    physical networks: never map a physical network to OpenDSS or a grid to ngspice.)
12. The across/through -> voltage/current mapping lives ONLY in
    core/physical/domains.py. Other files ask the Domain registry; they never hard-code
    "C = J" or similar.
13. Built-in physical components are written with the same PhysicalComponent API
    that users get in M5. No private shortcuts.
14. A physical network is one stateful block in the v0.1 engine: its outputs are
    the sensor values from the previous tick. Do not change the algebraic-loop rule.
15. (v0.4) Grid models are solved ONLY by OpenDSS. Only core/grid/engines/opendss/ imports
    `dss`; everything else uses the GridEngine protocol in core/grid/engines/base.py.
    Load `dss` lazily; if missing, OpenDSSEngine.available() returns False. Never write
    power-flow equations or iterations in Python.
16. (v0.4) OpenDSS is the .dss parser. Never parse DSS text ourselves (redirect, ~, like=,
    RPN values, linecodes). Compile the file in OpenDSS, then read elements back through
    the API in extract.py. Writing DSS text (writer.py) is allowed.
17. (v0.4) GridModel uses the canonical units below. Unit conversion happens ONLY in
    core/grid/engines/opendss/units.py. Other files never multiply by 0.3048 or 1609.344.
18. (v0.4) Each OpenDSSEngine owns its own DSS context (DSS.NewContext()). Never use the
    global DSS instance (no global mutable state, rule "Conventions").
19. (v0.4) Violations (voltage limits, overloads) are computed in core/grid/analysis.py
    from PowerFlowConfig limits, never by the engine and never in QML.
20. NEW v0.5: GridModel is the single source of electrical truth, connectivity included
    (Terminals). DiagramModel holds presentation only: positions, rotation, bus-bar
    length, label offsets. It never stores which bus an element connects to; every line
    drawn on the canvas is derived from GridModel terminals at render time.
21. NEW v0.5: DiagramModel refers to elements only by element id ("load.l1", "bus.671").
    Every GridModel element and bus has exactly one glyph and every glyph names an
    existing element (core/grid/diagram/model.py check_consistency; tested after every
    command).
22. NEW v0.5: Every designer edit is a core command in core/grid/diagram/commands.py that
    changes GridModel and DiagramModel together and reverts exactly (apply then revert
    gives a model equal to the original). The bridge wraps each command in one
    QUndoCommand on GridDiagramController's QUndoStack. QML never changes a model
    directly; it calls GridDiagramController slots.
23. NEW v0.5: The designer never calls OpenDSS. Run goes GridDiagramController ->
    GridController.runModel(GridModel) -> GridRunner child, which writes the DSS script
    with writer.py and solves in its own OpenDSSEngine. Rules 15-16 are unchanged.
24. NEW v0.5: No invented electrical values. New elements get structural defaults only (bus,
    phases from the bus, kV from the voltage zone). Impedances, ratings, loads, kvar and
    kW are typed by the user or copied from an element/linecode already in the model.
    A missing value is an error Diagnostic and blocks Run. No catalogs before v0.6.
25. NEW v0.5: The grid graph lives ONLY in core/grid/topology.py (islands, source
    reachability, voltage zones, per-bus phases, loops). validate.py, commands.py,
    layout.py, analysis.py and the bridge ask it; no other file walks the grid graph.
26. NEW v0.5: Sequence -> phase impedance conversion (r1/x1/r0/x0/c1/c0 to full matrices)
    lives ONLY in core/grid/sequence.py, tested against OpenDSS's own matrices from a
    fixture. Rule 17 (unit conversion only in units.py) is unchanged; the designer
    shows and edits canonical units only.
27. NEW v0.5: Feasibility in v0.5 is Level 1 (topology) + Level 2 (electrical) for ONE
    snapshot. Loading %, voltage-limit and loss checks are computed in
    core/grid/analysis.py from PowerFlowConfig limits (rule 19). Every FeasibilityReport
    and its UI panel states that a snapshot does not prove feasibility for other loads,
    contingencies or protection.
28. NEW v0.5: The G6 grid view stays read-only. The designer is a separate editor; "Edit as
    Design" copies the opened feeder into a new design and never writes the original
    .dss file.

## Grid model (v0.4, follow exactly)
| Quantity            | Canonical unit in GridModel                         |
| length              | m                                                   |
| series impedance    | ohm/km, full phase matrices, row-major              |
| shunt capacitance   | nF/km, full phase matrices                          |
| voltage             | kV (LL for 3-phase and delta; as given for 1-ph wye)|
| power               | kW, kvar, kVA                                       |
| transformer R, X    | % on the winding / element base                     |
| node voltage result | complex V, line-to-neutral; pu = abs(V) / LN base   |
- Names are lower case. Element ids are "<class>.<name>" (line.650632); node names are
  "<bus>.<node>" (671.1). A Terminal is a bus plus explicit node numbers.
- Model classes: Source (OpenDSS Vsource), Bus, LineCode, Line, Switch, Transformer
  with Winding list, Regulator, Load, Capacitor, PVSystem, GridModel. All in
  core/grid/model.py for v0.4.
- Switch is its own class. The writer emits it as `Line ... Switch=yes` and keeps its
  impedance (IEEE 13 uses r1 = 1e-4); open state is written with `Open`.
- Regulator is a control that names a Transformer and winding (OpenDSS RegControl),
  never a standalone branch.
- Regulator taps are stored in the model after the solve, so a round trip with controls
  on converges to the same state.
- Base frequency and voltage bases always come from the .dss file (for a design: see
  "Grid model changes for designs"). Never assume 60 Hz.
- Elements the model does not support yet are reported as a warning Diagnostic
  ("unsupported element ...") by the extractor - never dropped silently.
- Branch currents are per phase at terminal 1; branch power is positive into terminal 1;
  source power is reported as positive when supplied to the grid.
- Round trip: .dss -> GridModel -> DSS script -> solve must match the original solve
  within 1e-6 pu on every node and 0.1 % on total losses. This is the test that proves
  the model is complete.

## Grid model changes for designs (v0.5, follow exactly)
- Draft elements: fields that a user must enter (linecode impedances, load kW/kvar or
  PF, transformer kVA/kV/%R/X, capacitor kvar, PV Pmpp/kVA) may be None in a GridModel.
  validate.py reports each as an error ("load.l3: kW not set"); writer.write() returns
  (None, diagnostics) for an incomplete model and never raises; Run is disabled.
  Models extracted from a .dss file are always complete.
- LineCode and Line get norm_amps and emerg_amps (A). extract.py reads them through the
  API, writer.py writes them; the G3 round trip must still pass on IEEE 13/34/123.
- Voltage bases for a design: base frequency is entered in the New Design dialog
  (required, no default - never assume 60 Hz); voltage bases are the sorted set of
  source kV and every transformer winding kV (LL), derived in topology.py, written by
  the writer as Set VoltageBases=[...] then CalcVoltageBases. A design opened from a
  .dss file keeps the file's bases.
- Voltage zone of a bus = the winding (or source) kV reached from the source without
  crossing another transformer. A line connecting two zones is an error Diagnostic.
- Phases: an element's nodes must be available at its bus (topology.py phase
  availability from the source); a mismatch is an error Diagnostic ("line.l7 needs
  node 3 at bus.12, which has 1.2").
- Element names: lower case, unique per class, [a-z0-9_]+; new elements get
  "<class-prefix><n>" (l1, ld1, tx1, c1, pv1, sw1, b1) with the lowest free n.
- Loading limits (PowerFlowConfig, user-settable; these are check thresholds, not
  equipment data): line loading = max phase current / norm_amps, transformer loading =
  |S| at the winding terminal / kVA; default limit 100 %, "warn" from 90 % of the limit.
  A line without norm_amps gets an info Diagnostic ("no rating"), never a guessed value.
- Removing a bus removes every element connected to it, as one compound command.
- Regulator stays a control on a Transformer winding (rule unchanged): the library
  "Regulator" drops a single-phase transformer + RegControl pair as one compound
  command.

## Diagram model (v0.5, follow exactly)
- DiagramModel: bus_glyphs[bus name] = BusGlyph(x, y, orientation 0/90, length);
  element_glyphs[element id] = ElementGlyph(x, y, rotation 0/90/180/270, label_dx,
  label_dy). Canvas units are diagram units (1 = one grid step), not metres.
- Branches (lines, switches, transformers) are drawn between their bus glyphs; a glyph
  position for a branch is only its symbol/label anchor. Shunts (load, capacitor, PV,
  source) are drawn at their glyph and connected by a stub to their bus.
- Bus coordinates from the .dss file (G6) become bus glyph positions, scaled to fit;
  buses without coordinates (IEEE 123 300_open, 94_open) get positions from
  layout.auto_layout (layered tree from the source, our own code). Bus coordinates are
  written back by the writer only on Export .dss.
- Moving a glyph is presentation only: it never changes GridModel, the DSS output (except
  exported BusCoords) or any result.

## Design file (v0.5)
- `<name>.qgrid`: UTF-8 JSON {"format": "qtspice-grid-design", "version": 1,
  "grid_model": {...}, "diagram": {...}, "base_frequency": ...}. Written and read only by
  core/grid/design_io.py; read never raises (returns model + Diagnostics). Load -> save
  -> load gives equal dataclasses. Results are never stored in a .qgrid.

## Physical domains (v0.3, follow exactly)
| Domain        | Across -> V  | Through -> A | C from         | R from   | L from   | Reference |
| electrical    | V            | A            | capacitor      | resistor | inductor | 0 V       |
| rotational    | w [rad/s]    | tau [N*m]    | inertia J      | 1/b      | 1/k      | 0 rad/s   |
| translational | v [m/s]      | F [N]        | mass m         | 1/b      | 1/k      | 0 m/s     |
| thermal       | T [K]        | Q [W]        | thermal mass   | R [K/W]  | -        | 0 K       |
- SI units internally. Display units (rpm, degC, mm) are display only.
- Through variable is positive flowing into port A and out of port B of a component.
- Mass, inertia and thermal mass are one-port elements connected to the reference.
- Thermal reference is absolute zero; ambient is a Temperature Source (e.g. 298.15 K).
- Position and angle are made by integrating velocity inside the network.
- Domain coupling only through controlled sources (e.g. DC motor: E = k*w, tau = k*i).
- Ports of different domains must never be connected; F4 refuses such wires.
- Each physical network needs exactly one Solver Configuration block and a reference
  block for every domain it uses (F5 checks this).

## Combined run (v0.3, follow exactly)
- Converter inputs become ngspice sources declared `external`; values come from the
  GetVSRCData / GetISRCData callbacks registered with ngSpice_Init_Sync.
- A breakpoint is set at every tick k*Ts (ngSpice_SetBkpt). At each accepted tick:
  read sensors -> run signal blocks once -> set external source values.
- Source values are held until the next tick (zero-order hold, one sample of delay).
- Values from steps that ngspice rejects and redoes must never reach the controller
  (use the GetSyncData redo information).
- Physical vectors are named <component>.<port>.<quantity>, e.g. motor.shaft.w, in SI.
- PWM edges are generated inside ngspice (M4), not by the Python controller.

## Folder layout (v0.5 additions marked +)
app/__main__.py
core/netlist/     parser.py model.py validate.py builder.py libindex.py
core/sim/         backend.py ngspice_adapter.py result.py
core/blocks/      decorator.py spec.py registry.py diagram.py engine.py runner.py library/
core/project/     io.py schema.json
core/symbols/     asy.py library.py builtin/
core/schematic/   model.py connectivity.py netlister.py fromnetlist.py
core/hierarchy/   symbolgen.py
core/waveform/    layout.py expr.py plotdefs.py
core/physical/    (v0.3 files)
core/grid/        model.py validate.py config.py result.py analysis.py runner.py
+                 topology.py sequence.py design_io.py
+                 diagram/{model.py,layout.py,commands.py}
                  engines/base.py engines/opendss/{engine.py,extract.py,writer.py,units.py}
bridge/           (v0.4 files) + grid_diagram_controller.py
ui/qml/           (v0.4 files) + grid/{GridDiagramEditor.qml,GridComponentLibrary.qml,
+                 GridProperties.qml,GridResultsOverlay.qml,GridFeasibilityPanel.qml,
+                 NewGridDesignDialog.qml} + grid/symbols/ (one SVG per model class)
tests/core/grid/  + test_topology.py test_sequence.py test_design_io.py
+                 diagram/{test_model.py,test_layout.py,test_commands.py}
+ tests/bridge/test_grid_diagram_controller.py
tests/fixtures/grid/{ieee13,ieee34,ieee123}/ + sequence_reference.json (S4 script)
library/          README.md (user's .lib/.asy, git-ignored)
examples/         (v0.4 files) + ieee13_design.qsp (+ ieee13_design.qgrid)
third_party/ngspice/

## Project file
- `.qsp` format version 5 adds a "grid_designs" list: path of each .qgrid (relative to
  the project), the active design, and per-design PowerFlowConfig and limit settings
  (voltage band, line and transformer loading limit %). The v4 "grid" section (a .dss
  file) is kept unchanged. Versions 1-4 must still open; a v4 project saved by v0.5
  becomes v5 only when it gets a design.
- Results stay in `name_results/` (designs under `name_results/grid/<design>/`, including
  the DSS script written for each run); only the project's own listed files are deleted.

## Commands
pip install -e ".[dev]"
pip install -e ".[dev,grid]"    # with OpenDSS
python -m app
python -m app --smoke-test      # includes a designer check from v0.5 (D9)
pytest -q
pytest -q -m "ngspice"          # tests that need libngspice
pytest -q -m "opendss"          # tests that need dss-python
pytest -q tests/core/grid tests/bridge/test_grid_diagram_controller.py   # v0.5 work
ruff check . && ruff format --check .

## Conventions
- Type hints everywhere; dataclasses for data; no global mutable state.
- Every core module has tests under tests/core/ mirroring its path.
- Physical components need a test against an analytic answer (time constant,
  natural frequency, steady state), not just "it runs".
- Grid code needs a test against a reference result (IEEE feeder report or the
  OpenDSS result of the original .dss), not just "it converges". Reference values come
  from files in tests/fixtures/grid/; never type them from memory.
- NEW v0.5: the designer's proof test: rebuild IEEE 13 from an empty design using ONLY
  edit commands (values read from the extracted GridModel of the fixture), run it, and
  match opendss_reference.json within 1e-6 pu on every node and 0.1 % on losses. Undo
  all commands -> empty design equal to the start; redo all -> equal to the rebuilt one.
- NEW: every command test checks check_consistency() and topology after apply and after
  revert.
- NEW: bridge tests use pytest-qt and the real QUndoStack; QML is not unit-tested, but
  every QML action must map to one tested controller slot.
- Tests that need ngspice are marked @pytest.mark.ngspice and skip if it is missing.
- Tests that need dss-python are marked @pytest.mark.opendss and skip if it is missing.
- One task ID (e.g. D1, D4) per commit; message "<ID> <area>: <what>".
- Commit attribution: every commit names Abhilash Poshanagari
  <abhi.sree762@gmail.com> as author and adds the trailer
  "Co-Authored-By: Abhilash Poshanagari <abhi.sree762@gmail.com>".
- Before saying a task is done: ruff and pytest must both pass.
- For ngspice API details read third_party/ngspice/sharedspice.h; do not guess.
- For OpenDSS API details read the installed dss-python package (dss/I*.py docstrings)
  and the spike notes below; do not guess property names.
- If a requirement is unclear, ask instead of inventing behaviour.

## Kept from earlier versions (do not change unless a task says so)
- LTspice compatibility rules from v0.2 (symbols, probing, waveform window, expression
  syntax: ^ is XOR, log() is natural log, V(a,b) = V(a) - V(b)).
- Python signal blocks use fixed-step forward Euler.
- First netlist line is the title. One plain simulation runs at a time.
- Windows pytest may print "Windows fatal exception: access violation" when a
  block-runner child starts. Tests pass; the app does not enable faulthandler.
  Do not try to "fix" this.

## Spike results (v0.5)
Week-1 spikes S1-S6 in scratch/phase5/ (code, tests, README). Fill this section with
MEASURED results only (machine, versions, numbers, decision) when each spike is done;
until then, every item below is an open question, not a fact.
- S1 Canvas: measured (i7-1185G7, Intel Iris Xe via D3D11, threaded loop, 60 Hz, PySide6
  6.8.3; scratch/phase5/spike1_canvas/README.md). IEEE 123 (132 buses) and 8 copies (1056):
  both variants hold 60 fps pan/zoom/drag; render-thread time per frame p95 1.5 ms (1x),
  4.9-5.8 ms (8x). First frame 0.5 s (1x), 0.95 s (b) / 1.31 s (a) at 8x. (a) Repeater item
  per glyph: 10 534 items at 8x, 56.8 fps pan/zoom, 274 MB; (b) branches as ShapePaths +
  glyph Items: 6 631 items, 59.5 fps, 257 MB, but each edit rebuilds all path strings
  (0.7 ms -> 4.0 ms per drag step 1x -> 8x). GeometryRenderer re-tessellates in 0.04-0.14 ms per
  edit; CurveRenderer 0.2-1.8 ms (max 6.2). GeometryRenderer is aliased: 4x window MSAA fixes
  it at 60 fps, +50 MB. Hit-test in Python: spatial index 6-34 us, linear scan 0.6-5.5 ms;
  click -> next frame 17-23 ms. Reusable from SchematicCanvas (inline, model-free, copy):
  pan/zoom with one transformed content Item, settledZoom pen width, grid layer, rubber-band
  rectangle. Tied to the schematic model: hit_test, snapPoint, selection/move, SymbolItem.
  Decision: variant (b), GeometryRenderer + MSAA 4x, a separate "live" ShapePath for what is
  dragged; glyph SVG Images with power-of-two sourceSize and asynchronous loading;
  hit-testing in core/grid/diagram with a spatial index, nearest glyph within 6 px/zoom
  wins (kind only breaks ties). IEEE 123 buses 150r/9r share coordinates with 150/9: layout
  (S3) must separate coincident buses.
- S2 Undo: core command + QUndoCommand wrapper; mergeWith for drags (one undo step per
  drag); macro for compound delete; memory for 1000 steps. Decision: TBD.
- S3 Layout: measured (i7-1185G7, Python 3.12.2, PySide6 6.8.3; scratch/phase5/spike3_layout/
  README.md). Our own layered tree (BFS from the source over closed branches, open switches
  are non-tree edges, transformer = extra column, rows from subtree leaf order; dicts + deque
  only). All coordinates removed: every variant 0 crossings, 0 glyph overlaps on IEEE 13/34/123;
  0.09 / 0.27 / 0.9 ms, 8x IEEE 123 (1057 buses) < 10 ms. "Trunk" rows (bus on its first
  child's row) give long steep fan edges (IEEE 123 max branch 164 units against 21 in the file);
  "packed-mid" (per-column contour packing, bus midway between first and last child) max 24,
  1 fan edge grazing a load glyph. Mixed (keep coordinates, place the rest next to the nearest
  placed neighbour at the free grid point with fewest overlaps/crossings): IEEE 123 places
  300_open, 94_open and the coincident 150r/9r/25r/160r (the bus nearer the source keeps the
  spot); the file's 4 crossings -> 0, no overlap added; 107 ms (pure-Python candidate scan,
  10-35 ms per placed bus). File coordinates scaled so the median branch is 6 units, snapped.
  Decision: D3 auto_layout = packed-mid when no bus has coordinates, else scale + separate
  coincident buses + place_missing; topology.py supplies the tree (rule 25); D4 uses
  place_missing for one new bus. Not needed for v0.5: orthogonal routing, label collision
  avoidance, crossing reduction for loops.
- S4 Engine checks: Lines/LineCodes NormAmps and EmergAmps typed or Properties();
  writer design with derived VoltageBases + CalcVoltageBases solves like the original;
  sequence -> phase matrices vs OpenDSS for linecodes given r1/x1/r0/x0/c1/c0 (make
  tests/fixtures/grid/sequence_reference.json with scratch/phase5/make_sequence_reference.py;
  never edit by hand). Results: TBD.
- S5 Design file: .qgrid JSON size and save/load time for IEEE 123; equality after
  load/save/load. Results: TBD.
- S6 Edit-run loop: edit -> GridModel to GridRunner -> writer -> compile -> GridResult ->
  overlay, IEEE 13 and 123; decide whether a warm child process is needed. Decision: TBD.

## G5 benchmarks (v0.4)
tests/core/grid/test_benchmarks.py compares every node with the published IEEE PES
solution: tests/fixtures/grid/<feeder>/reference/published.json, made from the report
(.doc, from feederNN.zip at cmte.ieee.org/pes-testfeeders/resources) by
scratch/phase4/make_published_reference.py with antiword (Git for Windows ships it).
Never edit published.json by hand. Tolerance 0.5 % on |V| pu, 0.5 deg on angle.
- Engine used: dss-python 0.15.7, dss-python-backend 0.14.5, "DSS C-API Library
  version 0.14.5 revision 87d85c2622c8281b92255335bc7c09b11191b21d based on OpenDSS SVN
  3723 [FPC 3.2.2] (64-bit build) MVMULT INCREMENTAL_Y CONTEXT_API PM 20240329033747";
  Python 3.12.2, Windows 11.
- Result: IEEE 13 35 nodes, max 0.135 % / 0.06 deg; IEEE 34 92 nodes, 0.141 % /
  0.06 deg; IEEE 123 271 values, 0.136 % / 0.10 deg. All within tolerance.
- Regulator taps: OpenDSS RegControl settles 1-2 steps away from the published taps
  on all three feeders (e.g. IEEE 13 RG60 +9/+6/+9 against +10/+8/+11), which puts
  up to 2.2 % between the two solutions. The benchmark therefore sets the published
  taps (from published.json) and solves with ControlMode OFF. OpenDSS's own
  controlled solution stays tested against opendss_reference.json.
- Published transformer pseudo-nodes XFXFM1 (13), XF10 (34), XF1 (123) have no
  OpenDSS bus; they are skipped by name. Any other unmatched node fails.
- IEEE 123 bus 610 (secondary of the delta-delta XFM1, ungrounded) has no defined
  line-to-neutral voltage; the report repeats bus 61's values there. Such buses
  (only delta windings, no other element) are compared line-to-line.
- Published regulator output nodes map to OpenDSS by the regulator transformer:
  RG10 -> 814r, RG11 -> 852r, RG1 -> 150r, RG2 -> 9r, RG3 -> 25r, RG4 -> 160r.

## Spike results (v0.4)
Week-1 spikes S1-S6 in scratch/phase4/ (code, tests, README). Measured on Windows 11,
Python 3.12.2; the CI `spikes-grid` job repeats them on Windows, macOS and Linux (job
log). Feeders: tests/fixtures/grid/ (electricdss-tst commit 3b20839, BSD-3). Reference
results: tests/fixtures/grid/<feeder>/opendss_reference.json, the OpenDSS result of the
original files (scratch/phase4/make_reference.py; never edit by hand).
- Versions (S1): dss-python 0.15.7 pulls dss-python-backend 0.14.5 and cffi. Engine
  string: "DSS C-API Library version 0.14.5 revision 87d85c2... based on OpenDSS SVN
  3723 [FPC 3.2.2] (64-bit build) MVMULT INCREMENTAL_Y CONTEXT_API ...". pip wheels on
  all three OSes; no compiler needed.
- IEEE 13 (S1): `compile` of IEEE13Nodeckt.dss solves itself (the file ends in Solve):
  converged in 11 iterations, 3 control iterations; voltage bases [115, 4.16, 0.48];
  base frequency 60 Hz read from the file (`get DefaultBaseFrequency`). Total power
  -3567.2 kW / -1736.6 kvar (OpenDSS sign: negative = supplied by the source; our
  GridResult must flip it); losses 112.4 kW. 671.1 = 0.98280 pu. IEEE 34 and 123 need an
  explicit Solve (34: 25 iterations, 6 control; 123 with its Run-file RegControl
  settings and MaxControlIter=30: 45 iterations, 15 control).
- Contexts (S2): two DSS.NewContext() engines (13 + 34) solved interleaved do not
  affect each other: changing a load in one leaves the other bit-identical.
- `compile` CHANGES THE PROCESS WORKING DIRECTORY to the folder of the compiled file
  unless the context has AllowChangeDir = False. Redirects and BusCoords still resolve
  relative to the compiled file with it off. OpenDSSEngine must set AllowChangeDir =
  False (and AllowForms = False) on every new context, and pass absolute paths.
- A second Solve of an already solved circuit moves it by up to 5e-6 pu (IEEE 34;
  default Solution.Tolerance 1e-4). The G3 round trip (1e-6 pu) must compare
  solutions made the same way (same number of solves), or tighten the tolerance.
- Child process (S2): spawn + one-way Pipe + daemon Process (BlockRunner pattern) works;
  results are bit-identical to in-process. Start-up about 0.55 s until the child has
  imported dss and made a context (of that, `import dss` about 0.29 s); the IEEE 13 work
  is then about 10 ms. A child terminated during IEEE 123 solves exits with -15 within
  about 10 ms; the parent never hung, and a new child works afterwards.
  core/blocks/runner.py has no separate process helper today (spawn/Pipe/terminate is
  inside BlockRunner): G1 has to factor one out to "reuse, don't copy".
- pytest on Windows prints "Windows fatal exception: code 0xe0465043" when `dss` is first
  imported. It is a Free Pascal exception ("FPC") that the engine raises and handles
  itself while loading; only faulthandler prints it. Tests pass, exit code 0. Same
  rule as the ngspice message: do not try to "fix" it.
- PyInstaller (S3, 6.22, --onefile): no hook exists in pyinstaller-hooks-contrib.
  Without options the build succeeds and the DLLs are found, but the exe fails with
  "No module named '_cffi_backend'" (the compiled _dss_capi module imports it from C).
  `--collect-binaries dss_python_backend` alone fails the same way.
  `--hidden-import _cffi_backend` alone works (38.9 MB, also bundles the debug engine
  _dss_capid / dss_capid). Chosen: our hook scratch/phase4/spike3_pyinstaller/hooks/
  hook-dss_python_backend.py (collect_dynamic_libs minus the debug build, hidden imports
  _dss_capi and _cffi_backend, excludes _dss_capid): 30.2 MB, prints the reference
  671.1 voltage. Windows bundle: _cffi_backend.pyd, dss_python_backend/_dss_capi.pyd,
  dss_capi.dll, libklusolvex.dll, libwinpthread-1.dll. macOS/Linux file lists: CI
  `spikes-grid` job log. G7 adds the hook to packaging/.
- API coverage (S4): every element of IEEE 13, 34 and 123 is read back through the API
  (no unsupported classes in these feeders). Typed interfaces give buses
  (ActiveCktElement.BusNames), phases, enabled, open state (IsOpen(terminal, 0)),
  linecode/line matrices, units, lengths, IsSwitch, transformer windings (Wdg = i, then
  kV, kVA, R (= %r), IsDelta, Tap, Min/MaxTap, NumTaps, Rneut, Xneut), Xhl/Xht/Xlt,
  all RegControl settings, load kV/kW/kvar/PF/Model/IsDelta/Vminpu/Vmaxpu/Status/ZIPV,
  capacitor kV/kvar/IsDelta/NumSteps/States, bus x/y/Coorddefined, kVBase and
  Settings.VoltageBases.
  Values that need ActiveDSSElement.Properties("<name>").Val (scalar strings, read with
  float()): Vsource R1, X1, R0, X0, MVAsc3, MVAsc1, Isc3, Isc1, BaseFreq;
  LineCode BaseFreq, Kron; Line BaseFreq; Transformer %NoLoadLoss, %IMag, %LoadLoss,
  Bank, BaseFreq; RegControl PTPhase; Load VLowpu.
  Correction to the pre-spike notes: transformer taps (Transformers.Tap per winding) and
  load Vminpu/Vmaxpu ARE typed in 0.15.7.
- Units (S4): unit codes 0 none, 1 mi, 2 kft, 3 km, 4 m, 5 ft, 6 in, 7 cm, 8 mm. IEEE 13
  local linecodes mtx601-607 are mi (1); the shared IEEELineCodes.DSS ones are kft (2)
  or none (0); all IEEE 13 lines are in ft (5). Lines.Rmatrix/Xmatrix/Cmatrix are
  reported per LINE unit, already converted from the linecode (650632: 0.3465 ohm/mi ->
  6.5625e-05 ohm/ft). units.py must convert from the line's own Units code.
- Property order matters: a line given `length=1.5 units=km r1=0.25` ends with Units 0
  (none), because R1/X1/matrices set after Units reset it; `r1=0.25 length=1.5
  units=km` keeps 3 (km). The total impedance is the same. writer.py must emit Units=
  after the impedances (or write everything in one consistent unit).
- Switch (S4): line.671692 has IsSwitch true, linecode "", Units 0 (none), Length 0.001
  (the OpenDSS default) and r1 = r0 = 1e-4: the writer must keep length, units and
  impedance.
- Vsource bus2 reads back as "sourcebus.0.0.0" (grounded) through BusNames.
- Regulator taps (S4): before the solve all taps are 1.0 (no feeder sets them). After:
  IEEE 13 reg1 = 1.05625, reg2 = 1.0375, reg3 = 1.05625 (winding 2); IEEE 34 and 123 in
  their reference files. IEEE 13 solves inside `compile`, so its "before" state was
  rebuilt with ControlMode=OFF and Taps=[1 1].
- Bus coordinates (S4): all buses of IEEE 13 and 34 have them; IEEE 123 has two buses
  without (300_open, 94_open, the open-switch buses). The single-line view (G6) must
  handle buses with no coordinates.
- save circuit (S5) writes one file per class with quoted names (New "Load.l1" ...).
- Locale (S5, Windows): with de_DE (decimal comma), with or without
  setlocale(LC_NUMERIC, "C"), DSS-Python reads "1.5" correctly (typed, Properties(),
  `?`), `save circuit` writes "kW=1.5" with no decimal commas, and IEEE 13 matches its
  reference exactly. The engine (Free Pascal) does its own number formatting.
  macOS/Linux: CI `spikes-grid` job log. Keep setlocale(LC_NUMERIC, "C") before
  compiling anyway (rule 4 style, cheap).
- Timing (S6, Windows, median of 10, new context each run; IEEE 13 solves in compile):
  IEEE 13 (41 nodes) compile 7.5 ms; IEEE 34 (95) compile 7.8 + solve 1.5 ms;
  IEEE 123 (278) compile 8.9 + solve 6.4 ms. Reading all node voltages < 0.5 ms;
  reading every element back (S4) 2.6 / 4.8 / 7.3 ms. In the child process: start-up
  0.49-0.55 s, then 11 / 12 / 19 ms of work; total about 0.5-0.56 s. The child's
  start-up, not OpenDSS, is what the UI waits for: a busy indicator is enough, no
  progress reporting needed for these feeders.

## Spike results (v0.3, kept)
Week-1 spikes in scratch/phase3/ (code, tests, README). Measured on Windows 11, ngspice 46,
Python 3.12; the CI `spikes` job repeats them on Windows (ngspice 44.2), macOS and Linux.
- Timing, 1 s simulated, RC with an external source, one breakpoint set per tick from the
  controller ("rolling"): Ts = 100 us 0.55 s (55 us/tick); 50 us 1.0 s; 20 us 2.7 s;
  10 us 5.4 s (54 us/tick). The same RC with a built-in PWL source and no callbacks takes
  0.06 s at 100 us and 0.65 s at 10 us. About 5 accepted points per tick, because ngspice
  restarts with small steps after every breakpoint. Error against 1 - exp(-t/RC) is
  below 4e-5 V.
- Set breakpoints one at a time from the tick (ngSpice_SetBkpt(next tick)), never all up
  front: setting all of them up front grows faster than linear (10 us: 13.6 s; 5 us: 95 s
  against 12 s).
- SetBkpt is required. Landing on the ticks only by shortening delta in GetSyncData
  costs 11 us/tick but gives about 1 % error at the source edge, because ngspice does not
  restart integration there.
- Smallest workable Ts: 10 us works (5.4x slower than real time on this PC). 5 us also
  runs (12 s for 1 s), but it is below the v0.3 limit.
- SendData is called only if a SendInitData callback is also passed to ngSpice_Init (a
  NULL SendInitData gives no SendData calls at all).
- Ending a run: with GetSyncData registered, ngspice often fails to finish at the deck's
  tstop. It either aborts with "Timestep too small" or loops forever about 127 ulp short
  of tstop, and a delta returned from GetSyncData does not help. Fix: make the .tran run
  2*Ts past the last tick and send `stop when time > t_stop + Ts/2` before `run`
  (ngspice_sync.run_ticks).
- How a redo is reported: GetSyncData(location=0) comes before each step;
  GetSyncData(location=1, redostep=0) means the step was accepted, and SendData for that
  point follows immediately. A rejected step gives GetSyncData(location=1, redostep=1),
  with time already reset to the last accepted point (to 1 ulp) and delta cut.
  GetVSRCData is called for every trial point, rejected ones included.
- Keeping rejected steps away from the controller: run the tick only from SendData (the
  accepted points; its times equal the final time vector exactly). GetVSRCData only
  returns the held value. ngspice's own rejections came right after its breakpoints,
  never on a tick step. When a redo on a tick step was forced, a controller driven by
  GetVSRCData ran on the rejected step, and the SendData-driven one did not.
- Never return nonzero from GetSyncData. It makes ngspice redo the step, but the redo
  steps over the tick breakpoint, so that tick runs late.
- Child process: spawn + Pipe + daemon Process (as in BlockRunner) works on Windows, with
  results identical to an in-process run and about 0.35 s of start-up. A second child
  works, and a terminated child exits with -15 without hanging the parent. The known
  "Windows fatal exception: access violation" message appears under pytest only. macOS
  and Linux: see the CI `spikes` job log.
- Mapping: a mass-spring-damper as C = m, R = 1/b, L = 1/k, with F as a current source and
  x = integral of v (G source into a 1 F capacitor), matches the analytic x(t) and v(t)
  to within 0.0005 %.
- Locale: on Windows a de_DE locale changes nothing, because ngspice.dll links its C
  runtime statically and Python's setlocale cannot reach it. On Linux and macOS ngspice
  shares the process libc: see the CI `spikes` job log for the result without the fix.
  Note: core/sim/ngspice_adapter.py does not call setlocale(LC_NUMERIC, "C") today,
  although rule 4 requires it.
- M4 PWM edges (scratch/phase3/spike6*_pwm.py): a comparator of a held duty and a
  `pulse` sawtooth switches exactly at the carrier corners (ngspice breakpoints) but
  steps over the duty crossing: up to 4.5 us late at 20 kHz. Smooth comparators, an RC
  after it, a curvature "precursor" and the S switch do not help; no XSPICE code models
  are shipped. Capping the step costs: 1 us -> 390 ns error, 500 ns -> 177 ns, at
  15-25x the points. Chosen: the `Crossing` primitive; the CoSimulator sets a pair of
  breakpoints at edge +- 1e-4*Ts from each tick (one on the edge alone is not enough:
  the comparator is undecided there and ngspice's next step is long). Edges then land
  within 20 ns. The edge is still made by the comparator inside ngspice.
