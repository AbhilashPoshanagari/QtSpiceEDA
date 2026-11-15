# QtSpice (Python) - project memory

## Status
- v0.1.0 released: netlist editor, ngspice simulation, waveform viewer, Python function
  blocks, block diagrams, .qsp projects.
- v0.2.0 released: LTspice-style improvements only (.lib/.asy reading, schematic editor,
  circuit symbols, multi-pane waveform window with LTspice expressions).
- v0.3.0 released: Simscape-style physical modelling (F, C, B, U, M, O tasks).
- We are now building v0.4 on the `phase-4` branch: distribution-grid power-flow analysis
  with OpenDSS. Every v0.1, v0.2 and v0.3 test must keep passing.

## What v0.4 adds (only these - nothing else)
- S1-S6 Spikes (week 1, scratch/phase4/ only): DSS-Python on all three OSes, engine
  contexts and child process, PyInstaller bundling, API coverage for extraction, locale,
  timing on large feeders.
- G1 Engine: OpenDSSEngine loads a hand-written .dss file, runs a snapshot power flow,
  returns a GridResult. Runs in the GridRunner child process.
- G2 Grid model: engine-independent dataclasses (Source, Bus, LineCode, Line, Switch,
  Transformer + Winding, Regulator, Load, Capacitor, PVSystem, GridModel) and the
  extractor that builds a GridModel from a loaded OpenDSS circuit.
- G3 Writer: GridModel -> DSS script, and the round-trip test.
- G4 Validation: topology and electrical checks on a GridModel, reported as Diagnostics.
- G5 Benchmarks and analysis: IEEE 13/34/123 against reference results; violations,
  losses and voltage profile computed outside the engine.
- G6 UI: open a .dss file, Run, results table, voltage-profile plot in the waveform
  window, read-only single-line view from bus coordinates, diagnostics in the Console.
- G7 Project and release: .qsp format version 4, sample project, docs, v0.4.0.

OUT OF SCOPE for v0.4 - do not build, even if it looks easy:
our own power-flow / load-flow solver or a DSS-language parser; GIS or ArcGIS import,
data normalisation, electrical enrichment, conductor/transformer catalogs (v0.5);
QSTS / load shapes / yearly runs, fault studies, hosting capacity, EV impact, harmonics,
dynamics (later phases); drawing or editing feeders in the UI (the grid view is
read-only); coupling grid models with ngspice, physical networks or Python controllers;
a second grid engine (pandapower, COM OpenDSS, OpenDSSDirect.py); writing results back
to GIS. Everything that was out of scope in v0.2 and v0.3 stays out.
If a task seems to need one of these, stop and ask.

## Next phase (after v0.4): schematic from netlist
Branch `netlist-schematic`. Version number not chosen yet (v0.5 is reserved for GIS
import, see the v0.4 out-of-scope list). Adds only:
- N0 Library folder: one default folder for the user's .lib files and .asy symbols:
  `library/` in the repository when run from source (its files are git-ignored),
  Documents/QtSpice/library in the packaged app. Created at app start, changeable in
  the Search paths dialog (SchematicController.defaultLibraryFolder). `.include <name>` / `.lib <name> [section]` look up an
  absolute path as written, then next to the netlist (unchanged), then the default
  folder and the library folders (`parse(text, base_dir, search_dirs)`).
- N1 Converter: core/schematic/fromnetlist.py, netlist -> Schematic. Only for a netlist
  with no errors (same check as the Check button). Symbols: a library symbol whose
  SpiceModel/Value matches the model or subcircuit, else a built-in symbol by kind and
  model type; elements without a symbol stay as SPICE text with a warning. Pins get a
  stub and a net label (ground flag for 0). Re-netlisting the result must give the
  same Circuit, or nothing is shown.
- N2 Bridge: SchematicController.generateFromNetlist(text), one undo step.
- N3 UI: "Schematic from Netlist" menu item; ask before replacing a non-empty sheet.
- N4 Project and docs: README sections (library folder, Schematic from Netlist, its
  known limits), CHANGELOG, a schematic check in --smoke-test. The .qsp format does
  not change; a freshly drawn sheet does not ask Replace/Keep (done in N2).
Tests isolate the default folder (tests/conftest.py); never read the real one.

## Stack
Python 3.12, PySide6 6.8 (Qt Quick / QML, QtGraphs), NumPy, ngspice shared library
via ctypes, graphlib, multiprocessing, pytest, pytest-qt, ruff, PyInstaller.
NEW: DSS-Python (package `dss-python`, pin 0.15.x, BSD-3) as the optional extra
`pip install -e ".[grid]"`. The app must start and all non-grid features must work
without it.
Do not add PySpice as a dependency (GPLv3). Reading its source for reference is fine;
never copy code from it. Do not add OpenDSSDirect.py, the Windows COM OpenDSS or
pandapower.

## Architecture rules (always follow)
1. Four layers: ui/qml -> bridge -> core -> external. A layer only calls the one below.
2. core/ never imports PySide6 or any Qt module.
3. QML binds only to bridge objects registered with @QmlElement / @QmlSingleton:
   Simulator, WaveformModel, DiagramController, ProjectManager, SchematicController,
   WaveformController, and NEW in v0.4: GridController. Never setContextProperty.
4. Only core/sim/ngspice_adapter.py knows the ngspice API. Load libngspice lazily with
   ctypes; if missing, available() returns False and the app still starts.
   Call setlocale(LC_NUMERIC, "C") before loading any circuit.
5. Plain netlist and schematic runs: ngspice on a QThread (SimulationWorker).
   Block diagrams, including combined physical runs: in the BlockRunner child process,
   which loads NgspiceAdapter itself. User code never runs in the UI process.
   NEW: grid runs: in the GridRunner child process (same spawn + Pipe + daemon pattern
   as BlockRunner; reuse its process helper, don't copy it), which creates its own
   OpenDSSEngine. OpenDSS never runs in the UI process.
6. Parsers and checkers never raise on bad input; they return results plus
   Diagnostic(line or block, severity, message). Grid code uses the same Diagnostic
   class, with the element id ("line.650632", "bus.671") as the location. If a stable
   diagnostic code is needed, add an optional field to the existing class - never a
   second Diagnostic class.
7. All time-domain and sweep results use the same SimulationResult class.
   NEW: grid power flow returns GridResult (node voltages, branch results, totals).
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
15. NEW: Grid models are solved ONLY by OpenDSS. Only core/grid/engines/opendss/ imports
    `dss`; everything else uses the GridEngine protocol in core/grid/engines/base.py.
    Load `dss` lazily; if missing, OpenDSSEngine.available() returns False. Never write
    power-flow equations or iterations in Python.
16. NEW: OpenDSS is the .dss parser. Never parse DSS text ourselves (redirect, ~, like=,
    RPN values, linecodes). Compile the file in OpenDSS, then read elements back through
    the API in extract.py. Writing DSS text (writer.py) is allowed.
17. NEW: GridModel uses the canonical units below. Unit conversion happens ONLY in
    core/grid/engines/opendss/units.py. Other files never multiply by 0.3048 or 1609.344.
18. NEW: Each OpenDSSEngine owns its own DSS context (DSS.NewContext()). Never use the
    global DSS instance (no global mutable state, rule "Conventions").
19. NEW: Violations (voltage limits, overloads) are computed in core/grid/analysis.py
    from PowerFlowConfig limits, never by the engine and never in QML.

## Grid model (follow exactly)
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
- Base frequency and voltage bases always come from the .dss file. Never assume 60 Hz.
- Elements the model does not support yet are reported as a warning Diagnostic
  ("unsupported element ...") by the extractor - never dropped silently.
- Branch currents are per phase at terminal 1; branch power is positive into terminal 1;
  source power is reported as positive when supplied to the grid.
- Round trip: .dss -> GridModel -> DSS script -> solve must match the original solve
  within 1e-6 pu on every node and 0.1 % on total losses. This is the test that proves
  the model is complete.

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

## Folder layout (v0.4 additions marked +)
app/__main__.py
core/netlist/     parser.py model.py validate.py builder.py libindex.py
core/sim/         backend.py ngspice_adapter.py result.py
core/blocks/      decorator.py spec.py registry.py diagram.py engine.py runner.py library/
core/project/     io.py schema.json
core/symbols/     asy.py library.py builtin/
core/schematic/   model.py connectivity.py netlister.py
core/hierarchy/   symbolgen.py
core/waveform/    layout.py expr.py plotdefs.py
core/physical/    domains.py component.py network.py mapper.py cosim.py
                  circuit_block.py registry.py user.py library/{electrical,rotational,
                  translational,thermal,converters,composite}/
+ core/grid/      model.py validate.py config.py result.py analysis.py runner.py
+                 engines/base.py
+                 engines/opendss/{engine.py,extract.py,writer.py,units.py}
bridge/           (v0.3 files) + grid_controller.py
ui/qml/           (v0.3 files) + grid/{GridView.qml,GridResultsTable.qml}
tests/core/physical/  tests/fixtures/physical/
+ tests/core/grid/    tests/fixtures/grid/{ieee13,ieee34,ieee123}/ + LICENSE file
library/          README.md (user's .lib/.asy, git-ignored)
examples/         dc_motor_speed.qsp heatsink_warmup.qsp battery_discharge.qsp
+                 ieee13_powerflow.qsp (+ its .dss files)
third_party/ngspice/

## Project file
- `.qsp` format version 4 adds a grid section: path of the .dss file (relative to the
  project), PowerFlowConfig settings and voltage-limit settings. Versions 1-3 must
  still open.
- Results stay in `name_results/` (grid results under `name_results/grid/`); only the
  project's own listed files are deleted.

## Commands
pip install -e ".[dev]"
pip install -e ".[dev,grid]"    # with OpenDSS
python -m app
pytest -q
pytest -q -m "ngspice"          # tests that need libngspice
pytest -q -m "opendss"          # tests that need dss-python
ruff check . && ruff format --check .

## Conventions
- Type hints everywhere; dataclasses for data; no global mutable state.
- Every core module has tests under tests/core/ mirroring its path.
- Physical components need a test against an analytic answer (time constant,
  natural frequency, steady state), not just "it runs".
- NEW: grid code needs a test against a reference result (IEEE feeder report or the
  OpenDSS result of the original .dss), not just "it converges". Reference values come
  from files in tests/fixtures/grid/; never type them from memory.
- Tests that need ngspice are marked @pytest.mark.ngspice and skip if it is missing.
- NEW: tests that need dss-python are marked @pytest.mark.opendss and skip if it is
  missing.
- One task ID (e.g. G1, G3) per commit; message "<ID> <area>: <what>".
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
