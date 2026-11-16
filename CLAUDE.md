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
- v0.5.0 complete on the `phase-5` branch (not yet tagged or pushed): the Grid Network
  Designer, a graphical feeder editor backed by GridModel and solved by OpenDSS, with
  Level 1 (topology) and Level 2 (electrical, one snapshot) feasibility checks; .qsp
  format version 5 (grid_designs), sample examples/ieee13_design.qsp.
  Spikes S1-S6 and tasks D1-D9 done. Every v0.1-v0.4 and netlist-schematic test passes.
- Roadmap change (decided for v0.5): GIS import moves from v0.5 to v0.6.
  v0.6 GIS import + catalogs, v0.7 feeder planning (scenarios, before/after),
  v0.8+ advanced studies (QSTS, DER/EV impact, hosting capacity, faults, protection).

## What v0.5 adds (only these - nothing else)
- S1-S6 Spikes: DONE (see "Spike results (v0.5)").
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
+                 diagram/{model.py,layout.py,commands.py,hittest.py}
                  engines/base.py engines/opendss/{engine.py,extract.py,writer.py,units.py}
bridge/           (v0.4 files) + grid_diagram_controller.py
ui/qml/           (v0.4 files) + grid/{GridDiagramEditor.qml,GridComponentLibrary.qml,
+                 GridProperties.qml,GridResultsOverlay.qml,GridFeasibilityPanel.qml,
+                 NewGridDesignDialog.qml} + grid/symbols/ (one SVG per model class)
tests/core/grid/  + test_topology.py test_sequence.py test_design_io.py
+                 diagram/{test_model.py,test_layout.py,test_commands.py,test_hittest.py}
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

## Spike results (v0.5) - S1-S6 DONE
Measured on i7-1185G7, Windows 11, Python 3.12.2, PySide6 6.8.3, dss-python 0.15.7.
Numbers, tables and code: scratch/phase5/spike<N>_*/README.md. Below: decisions only.
- S1 Canvas (done): IEEE 123 and 8x copies hold 60 fps. Branches as ShapePaths
  (GeometryRenderer + MSAA 4x) plus glyph Items; a separate "live" ShapePath for what is
  dragged; glyph SVG Images with power-of-two sourceSize, async loading. Hit-testing in
  core/grid/diagram with a spatial index: nearest glyph within 6 px/zoom wins (kind only
  breaks ties). Reuse from SchematicCanvas (copy, model-free): pan/zoom content Item,
  settledZoom pen width, grid layer, rubber band.
- S2 Undo (done): delta commands (store only what changes; Qt-free core commands wrapped
  1:1 in a QUndoCommand). 1000 random commands on IEEE 123 undo/redo exactly; ~0.3 KB and
  microseconds per command (snapshots: 7-60 ms and 161-658 MB per 1000 steps - rejected).
  Rules for D4: apply = do and redo; revert assumes the exact post-apply state; values are
  captured on first apply; remove keeps the objects and their dict positions and
  re-inserts there (key order is part of "exact"); every command reports affected() ids.
  Merge: MoveGlyph (merge_id 1) on (glyph id, gesture id), a no-op drag becomes obsolete;
  SetProperty (merge_id 2) only with an explicit merge key; others never merge. Remove bus
  and library Regulator are ONE compound core command, not a QUndoStack macro. No undo
  limit needed. Open for D3: Switch ids are line.<name> (resolve against both dicts);
  decide whether LineCode/RegControl get glyphs (rule 21).
- S3 Layout (done): our own layered tree from topology.py (rule 25). auto_layout =
  "packed-mid" when no bus has coordinates; otherwise scale file coordinates (median
  branch 6 units, snapped), separate coincident buses (IEEE 123 150r/9r/25r/160r; the bus
  nearer the source keeps the spot) and place_missing. D4 uses place_missing for one new
  bus. 0 crossings/overlaps on IEEE 13/34/123. Not in v0.5: orthogonal routing, label
  collision avoidance, loop crossing reduction.
- S4 Engine checks (done):
  Ratings: NormAmps/EmergAmps are typed; IEEE files set none (OpenDSS defaults 400/600 A).
  linecode= resets ratings, so the writer emits normamps/emergamps AFTER linecode=.
  Voltage bases: source kV + 3-phase winding kV (LL), then a 1-phase wye winding
  (kV*sqrt(3)) only if no base is within 1 % - never let near-duplicates in.
  Sequence: sequence.py uses self=(2s1+s0)/3, mutual=(s0-s1)/3 for n>=2 and z1 only for
  n=1; reference tests/fixtures/grid/sequence_reference.json (made by
  scratch/phase5/make_sequence_reference.py; never edit by hand).
  Loading: line = max |I| at terminal 1 / NormAmps; transformer = max over windings of
  |S| at terminal k / kVA (NOT OpenDSS's %, which uses NormHkVA = 1.1 kVA).
  Compile silently defaults missing kW/kV/impedance/kVA/kvar/Pmpp and converges with
  phase mismatches, islands and cross-zone lines: validate.py must catch all of them.
  Compare solutions made the same way (same-solve, as in G3).
- S5 Design file (done): plain JSON from a dataclass-to-dict converter (json + typing
  hints, no new dependency). IEEE 13/34/123 + diagram: load(save(m)) == m, every float
  bit-identical (shortest repr), same key order, same bytes on re-save; 26 / 64 / 133 KB
  at indent 1 (write indent 1: one line per value for git); save <= 45 ms, load <= 26 ms
  on IEEE 123 (UI thread is fine). Encoding: GridModel class dicts -> arrays in dict
  order (key = name); base_frequency only at the top level; Terminal {"bus","nodes"};
  matrices flat row-major (loader checks phases^2); complex [re, im]; NaN/inf refused at
  save; write <name>.qgrid.tmp then rename. Reading: never raises, NetworkDiagnostic at the
  element id; unknown field (any level) -> warning, ignored; missing field with a dataclass
  default -> default; missing without default (base_frequency, names, kV, glyph x/y) ->
  error; wrong type / duplicate name / bad matrix -> error; int read as float allowed;
  null only for X | None. Any error -> nothing loaded. "version" is one integer bumped
  only for incompatible changes (new fields with defaults stay version 1); newer version
  -> error, nothing loaded; older -> migrate step by step. Open for D3: keep or clear
  Bus.x/y in a design (rule 20); bus glyph key = bus name vs "bus.671" (rule 21);
  LineCode/RegControl glyphs; lines and switches share "line.<name>", so names must be
  unique across both; run check_consistency() after load.
- S6 Edit-run loop (done): warm child (spawn + duplex Pipe + daemon) with a NEW
  OpenDSSEngine per run: 35 / 54 ms per run on IEEE 13 / 123 (cold child ~0.75 s).
  Send the whole pickled model (no incremental protocol); keep validation in the child.
  Spawn when a design is created or opened (only if OpenDSSEngine.available()), stop when
  the last design closes and at quit (STOP, join 2 s, terminate). One request in flight,
  only to an idle child, replies carry the run id. A failed run (ERROR, TIMEOUT,
  CANCELLED) ends the child, no retry; next Run spawns a new one; a dead idle child is
  replaced before sending. State lives only in the GridController-owned instance. D5 moves
  the spawn/Pipe/daemon/terminate code into one function in core/blocks/process.py with the
  warm class next to it; GridRunner keeps the one-shot path for .dss files.

## G5 benchmarks (v0.4, done)
tests/core/grid/test_benchmarks.py compares every node with the published IEEE PES
solution in tests/fixtures/grid/<feeder>/reference/published.json (made from the report
by scratch/phase4/make_published_reference.py; never edit by hand). Tolerance 0.5 % on
|V| pu, 0.5 deg; IEEE 13/34/123 all pass (max 0.141 % / 0.10 deg; dss-python 0.15.7,
backend 0.14.5, OpenDSS SVN 3723).
- RegControl settles 1-2 taps from the published ones, so the benchmark sets the
  published taps and solves with ControlMode OFF; OpenDSS's own controlled solution is
  tested against opendss_reference.json.
- Pseudo-nodes XFXFM1 (13), XF10 (34), XF1 (123) are skipped by name; any other
  unmatched node fails. Buses with only delta windings (IEEE 123 610) compare LL.
- Regulator nodes: RG10 -> 814r, RG11 -> 852r, RG1 -> 150r, RG2 -> 9r, RG3 -> 25r,
  RG4 -> 160r.

## Spike results (v0.4, done)
Details: scratch/phase4/ READMEs. Feeders: tests/fixtures/grid/ (electricdss-tst 3b20839,
BSD-3). Reference: tests/fixtures/grid/<feeder>/opendss_reference.json
(scratch/phase4/make_reference.py; never edit by hand).
- Solve: IEEE 13 solves inside `compile`; IEEE 34 and 123 need an explicit Solve
  (123: MaxControlIter=30). OpenDSS power sign is negative = supplied by the source;
  GridResult flips it. A second Solve moves a solution by up to 5e-6 pu: the G3 round
  trip compares solutions made the same way.
- Contexts: separate DSS.NewContext() engines do not affect each other. Every context
  sets AllowChangeDir = False (compile otherwise changes the process cwd) and
  AllowForms = False; pass absolute paths.
- Child process: spawn + Pipe + daemon works, bit-identical to in-process; start-up
  ~0.5 s dominates (work 10-20 ms), so a busy indicator is enough.
- pytest on Windows prints "Windows fatal exception: code 0xe0465043" on first
  `import dss` (Free Pascal, handled by the engine). Do not try to "fix" it.
- PyInstaller: use our hook scratch/phase4/spike3_pyinstaller/hooks/
  hook-dss_python_backend.py (hidden imports _dss_capi and _cffi_backend, no debug build).
- API: every IEEE 13/34/123 element reads back through the API. Read through
  ActiveDSSElement.Properties("<name>").Val (float()): Vsource R1, X1, R0, X0, MVAsc3,
  MVAsc1, Isc3, Isc1, BaseFreq; LineCode BaseFreq, Kron; Line BaseFreq; Transformer
  %NoLoadLoss, %IMag, %LoadLoss, Bank, BaseFreq; RegControl PTPhase; Load VLowpu.
  Transformer taps and load Vminpu/Vmaxpu are typed.
- Units: codes 0 none, 1 mi, 2 kft, 3 km, 4 m, 5 ft, 6 in, 7 cm, 8 mm. Line matrices are
  per LINE unit: units.py converts from the line's own Units. R1/X1/matrices set after
  Units reset it to none: writer.py emits Units= after the impedances.
- Switch: keep length (default 0.001), units and impedance (IEEE 13 r1 = r0 = 1e-4).
  Vsource bus2 reads back as "sourcebus.0.0.0".
- Regulator taps are 1.0 before the solve; store the solved taps (rule "Grid model").
- IEEE 123 buses 300_open and 94_open have no coordinates.
- save circuit writes one file per class with quoted names (New "Load.l1" ...).
- Locale: a decimal-comma locale changes nothing in OpenDSS; keep
  setlocale(LC_NUMERIC, "C") before compiling anyway.

## Spike results (v0.3, done)
Details: scratch/phase3/ READMEs. Combined-run facts that the code relies on:
- Set breakpoints one at a time from the tick (ngSpice_SetBkpt(next tick)), never all up
  front. SetBkpt is required (shortening delta in GetSyncData gives ~1 % error).
  ~55 us per tick; smallest supported Ts 10 us.
- SendData is called only if a SendInitData callback is also passed to ngSpice_Init.
- End of run: make the .tran run 2*Ts past the last tick and send
  `stop when time > t_stop + Ts/2` before `run` (ngspice_sync.run_ticks); otherwise
  ngspice aborts or loops just short of tstop.
- Redo: GetSyncData(location=1, redostep=1) marks a rejected step. Run the tick only from
  SendData (accepted points); GetVSRCData only returns the held value. Never return
  nonzero from GetSyncData (the redo steps over the tick).
- Child process: spawn + Pipe + daemon works on Windows (~0.35 s start-up).
- Mapping check: mass-spring-damper as C = m, R = 1/b, L = 1/k matches analytic x, v to
  0.0005 %.
- Locale: on Windows ngspice.dll links its C runtime statically (setlocale cannot reach
  it); on Linux/macOS it shares libc, so rule 4's setlocale matters there.
- PWM (M4): use the `Crossing` primitive; the CoSimulator sets a pair of breakpoints at
  edge +- 1e-4*Ts from each tick, so edges land within 20 ns. The edge is still made by
  the comparator inside ngspice.
