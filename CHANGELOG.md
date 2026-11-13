# Changelog

## 0.4.0 (unreleased)

QtSpice 0.4 adds distribution-grid analysis. You can open a feeder written for OpenDSS
(`.dss`), run a snapshot power flow, and see node voltages, branch flows, losses,
voltage and loading violations, a voltage profile and a read-only single-line view.
OpenDSS, through DSS-Python, compiles and solves the feeder; QtSpice has no
power-flow solver or DSS parser of its own. The grid features are an optional extra
(`pip install -e ".[grid]"`): without it the app starts and everything else works.
Projects from 0.1, 0.2 and 0.3 still open; they are saved as format version 4. See
[README.md](README.md) for the units, the sign conventions and the benchmarks.

### Spikes (S1-S6)
- DSS-Python 0.15.7 (OpenDSS engine 0.14.5) installs from pip wheels on Windows,
  macOS and Linux. IEEE 13, 34 and 123 test feeders (electricdss-tst, BSD-3) with the
  OpenDSS reference result of each, and a CI job that repeats the spikes on all three
  OSes (S1).
- Independent DSS contexts; `compile` changes the working directory unless
  AllowChangeDir is off; OpenDSS in a spawned child process, and a terminated child
  (S2).
- PyInstaller: the engine needs the `_cffi_backend` hidden import; a hook bundles it
  without the debug build (S3).
- Every element of the three feeders can be read back through the API: unit codes,
  switches, transformer windings, regulator taps, bus coordinates (S4).
- A German locale doesn't change how OpenDSS reads or writes numbers (S5).
- Timing: under 20 ms of OpenDSS work for IEEE 123; the child's start-up (about
  0.5 s) is what the UI waits for (S6).

### Engine and model (G1-G3)
- `OpenDSSEngine`: loads a `.dss` file in its own DSS context and runs a snapshot
  power flow, returning a `GridResult` (node voltages, branch results, totals; source
  power positive when supplied). Grid runs take place in the GridRunner child process,
  which shares a process helper with the BlockRunner (G1).
- Engine-independent grid model: Source, Bus, LineCode, Line, Switch, Transformer with
  windings, Regulator, Load, Capacitor, PVSystem. The extractor builds it from the
  compiled circuit in canonical units (m, ohm/km, nF/km, kV, kW). Unsupported
  elements are reported as warnings, never dropped (G2).
- Writer: grid model to DSS script. The round trip `.dss` -> model -> script -> solve
  matches the original solve within 1e-6 pu on every node and 0.1 % on losses, for
  IEEE 13, 34 and 123 (G3).

### Checks and analysis (G4, G5)
- Topology and electrical checks on a grid model, reported as diagnostics with stable
  codes: unknown or unreachable buses, buses cut off by an open switch, phase and
  line-code mismatches, winding and element kV against the bus base, missing voltage
  bases, no source (G4).
- Violations (under-voltage, over-voltage, overloads), the voltage profile along the
  feeder and losses by element class, computed outside the engine from the limits
  (G5).
- Benchmarks against the published IEEE PES results: IEEE 13 (35 nodes), 34 (92) and
  123 (271 values) are within 0.141 % on |V| and 0.10 degrees, against a tolerance of
  0.5 % and 0.5 degrees (G5).

### UI (G6)
- **File → Open Grid (.dss)…** and a **Grid** tab: Run and Stop, a summary of totals
  and losses, sortable Node voltages, Branches and Violations tables with
  violations highlighted.
- The voltage profile in the waveform window, with the limits as flat traces.
- A read-only single-line view drawn from the bus coordinates and coloured by voltage;
  click a bus for its phase voltages.
- OpenDSS messages and the feeder checks in the Console.

### Projects, sample and release (G7)
- `.qsp` format version 4: an optional grid section with the `.dss` file (relative to
  the project when inside its folder), the power-flow settings and the voltage and
  loading limits. Versions 1-3 still open. Grid results are not saved.
- Opening a project opens its feeder in the Grid tab. The feeder is kept when it
  can't be opened (no DSS-Python, a missing file), so saving doesn't drop it.
- New sample `ieee13_powerflow.qsp`, with the IEEE 13 files and their license in
  `examples/grid/`, tested against the OpenDSS reference result.
- The packaged apps bundle OpenDSS (the hook from S3), and `--smoke-test` also solves
  the bundled IEEE 13 feeder.
- Fixed: the voltage limits shown in the UI didn't follow a change of settings.
- Fixed: packaged builds were missing the physical component library (since 0.3), so
  the block diagram didn't start in the packaged app.

### Known limitations
See *Known limitations (v0.4)* in the README. In short: snapshot power flow only (no
QSTS, load shapes, fault studies, hosting capacity, EV impact, harmonics or
dynamics). Feeders are solved only by OpenDSS. There is no GIS import, and no
conductor or transformer catalogs. The grid view is read-only, and grid models can't
be coupled with circuits, physical networks or Python controllers. There is no
settings dialog for the power-flow settings and limits in 0.4.

## 0.3.0 (unreleased)

QtSpice 0.3 adds Simscape-style physical modelling to block diagrams. You can wire
electrical, rotational, translational and thermal parts together and drive them from
signal blocks in one combined run. Every physical network is turned into a circuit
and solved by ngspice; QtSpice has no equation solver of its own. Projects from 0.1
and 0.2 still open, and they are saved as format version 3. See
[README.md](README.md) for the conventions and the library.

### Foundation (F1-F6)
- Domain registry for electrical, rotational, translational and thermal: across and
  through quantities, SI units and display units, and the only place where they map
  to voltage and current (F1).
- Signal and conserving port kinds in `BlockSpec` (F2).
- `PhysicalComponent` API with SPICE primitives and `@physical_component` (F3).
- Undirected physical wires between conserving ports of the same domain. Wires
  between different domains are refused, with the reason (F4).
- `NetworkExtractor` finds the physical networks of a diagram. It checks that each
  has exactly one Solver Configuration and a reference for every domain it uses
  (F5).
- `NetworkMapper` turns a network into a `Circuit` and an ngspice deck (F6).

### Combined run (C1-C5)
- ngspice sync mode: `external` sources fed from `GetVSRCData` / `GetISRCData`, a
  breakpoint at every tick, halt and resume (C1).
- `CoSimulator` with a fixed controller tick Ts. It ticks only on accepted ngspice
  points, so rejected steps never reach the controller. Source values are held
  between ticks, with exactly the held value even when the initial value is nonzero
  (C2).
- Each physical network is one stateful engine block whose outputs are the sensor
  values of the previous tick (C3).
- Combined runs take place in the BlockRunner child process, with Stop and a
  timeout (C4).
- A combined run gives one `SimulationResult`. Physical vectors are named
  `<component>.<port>.<quantity>` in SI (C5).

### Library (B1-B5)
- Electrical: R, C, L, DC/sine/pulse voltage and current sources, diode, controlled
  switch and reference (B1).
- Rotational: inertia, damper, spring, torque and speed sources and reference (B2).
- Translational: mass, damper, spring, force and velocity sources and reference (B3).
- Thermal: thermal mass, conduction, convection, heat flow and temperature sources
  and reference (absolute zero) (B4).
- Signal-controlled sources, sensors and the Solver Configuration block (B5).

### UI (U1-U4)
- Round, domain-coloured conserving ports. Physical wires are undirected and drawn
  with junction dots, and a refused drop shows the reason (U1).
- Library groups Physical > Electrical, Rotational, Translational, Thermal, Sensors
  and sources. Settings have unit captions and take SPICE numbers such as `10m` and
  `2.2u` (U2).
- One Run button for mixed diagrams. The Ts, network and mapping checks name the
  failing blocks first. The canvas step is the Solver Configuration's Ts (U3).
- Unit labels on the Y axis, an rpm/°C display toggle, and physical vectors grouped
  by component in Add Trace (U4).

### Mid level (M1-M7)
- Cross-domain parts: DC motor, gearbox, ideal transformer, battery with a
  state-of-charge table, half-bridge and H-bridge, and a resistor and MOSFET with a
  thermal port (M1).
- Circuit block: a v0.2 schematic or netlist with port flags becomes a physical block
  with electrical ports (core API) (M2).
- Bridges are `averaged` or `switching`, chosen by the `model` parameter (M3).
- PWM Generator whose edges are made inside ngspice and land within 20 ns. New
  DiscretePI and DiscretePID blocks with output limits and anti-windup (M4).
- User-defined physical components, written with the library's own API in a
  Components editor. The code is read without being run, and it runs only in the
  block-runner process (M5).
- Start values of storage parts become ngspice initial conditions. The Solver
  Configuration has a start-from-steady-state option (M6).
- Parameter sweep: one block parameter over a list of values, with the runs overlaid
  and labelled in the waveform window (M7).

### Projects and samples (O1, O2)
- `.qsp` format version 3: physical wires, checked Solver Configuration settings, user
  component code, the sweep and the rpm/°C setting. Versions 1 and 2 still open (O1).
- Fixed: a project with physical wires could not be reopened. Version 2 files saved by
  0.3 development builds open again (O1).
- New samples: `dc_motor_speed.qsp` (PI, H-bridge and DC motor),
  `heatsink_warmup.qsp` (MOSFET with a thermal port on a heatsink) and
  `battery_discharge.qsp`. Each is tested against its analytic answer (O2).

### Known limitations
See *Known limitations (v0.3)* in the README. In short: there is no equation language
or solver of QtSpice's own, and no hydraulic, gas, thermal-liquid or magnetic
domains. There is no 3D multibody, no real-time or hardware-in-the-loop, no code
generation, and no linearization of mixed models. Controllers have one fixed rate,
and Ts can't go below 10 µs. Physical blocks can't be placed in the schematic
editor.

## 0.2.0 (unreleased)

QtSpice 0.2 adds a schematic editor, vendor model libraries and LTspice-format
symbols, one-level circuit symbols, and a new waveform window with LTspice-style
panes, cursors and expressions. Projects from 0.1 still open; they are saved as format
version 2. See [README.md](README.md) for how to use the new features.

### Libraries (L1-L3, L8)
- `.lib file` and `.lib file section` with `.lib section` … `.endl`, and `.param`
  inside `.subckt` (L1).
- A library index lists every model and subcircuit in the library folders; encrypted
  model files are flagged and never decrypted (L2).
- ngspice LTspice compatibility: `set ngbehavior=<mode>` is sent before each circuit
  load (`ltpsa` by default) and stored in the project (L3).
- Library Browser with search paths and **Use with symbol…** (L8).

### Symbols (L4-L7)
- `.asy` reader: Windows-1252, CR+LF or LF; pin order from `SpiceOrder`; unknown
  keywords are warnings (L4).
- Symbol library with ordered, read-only search paths (built-in, project, user,
  LTspice folder) and a cache (L5).
- QtSpice's own built-in `.asy` symbols (L6).
- One placed symbol becomes one SPICE line; `Prefix X` is a subcircuit and its
  `ModelFile` an `.include` (L7).

### Schematic (S1-S11)
- Schematic model with instances, wires, labels, ground and port flags and texts (S1).
- Connectivity by union-find with T-joins, labels, ground and `N001` net numbering (S2).
- A whole schematic is netlisted to the same `Circuit` and deck as the netlist editor,
  through the existing parser, checks and builder (S3).
- One schematic stored in the `.qsp` (format version 2) (S4).
- Canvas with grid, pan, zoom, fit and rubber band; symbols drawn from their `.asy`
  shapes; tested with 300 parts and 600 wires (S5).
- Part picker with search and preview; click to place repeatedly (S6).
- Wire tool with orthogonal segments snapped to the grid and pins; label and ground
  tools (S7).
- Rotate, mirror, delete, drag (wires follow), move (wires detach), copy/paste, undo
  and redo (S8).
- Attribute dialog on double-click, directive tool, label rename (S9).
- LTspice keyboard shortcuts with a Help panel (F1); Run and Check from the toolbar
  (S10).
- Probing after a run: wire → `V(net)`, part → current, pin → pin current, net to net
  → `V(a,b)`, Alt+click → power (S11).

### Circuit symbols (H1-H3)
- Port flags with a Ports panel; a ports dialog for netlists (H1).
- **Make Symbol** writes `<name>.sub` and `<name>.asy` into the project folder; the
  `.sub` is checked as a subcircuit (H2).
- Circuit symbols are placed from the part picker and saved with the project (H3).

### Waveform window (W1-W10)
- One plot layout per analysis type, saved in the project, with its own undo stack
  (W1).
- Stacked panes sharing one X axis; Add pane above/below, Delete pane, and a Plot
  settings dialog (W2).
- Drag-box zoom (Esc cancels), Zoom back, Auto-range, undoable zoom history, status
  bar with 1/Δx (W3).
- Trace names above each pane: drag moves, Ctrl+drop copies, drag out removes,
  double-click shows only that trace (W4).
- Log X / log Y with decade ticks and minor grid; AC Bode pane (W5).
- Two cursors attached from trace names, dragged or moved with the arrow keys, with a
  readout of both cursors, the difference and 1/Δx (W6).
- Expression engine with LTspice syntax, parsed with `ast` against a whitelist and
  evaluated on NumPy arrays, real or complex (W7).
- Add Trace dialog with a wildcard vector filter, click-to-insert, Plot into pane, and
  editing a trace's expression (W8).
- Plot definitions file (`.func` / `.param`) with diagnostics, default
  `.func dB(x) 20*log10(Mag(x))`, saved in the project (W9).
- Ctrl+click a trace name for average and RMS over the zoomed X range; Bode key and
  light plot theme (W10).

### Fixes (O1, O4, O5)
- A warning when line 1 of a netlist looks like an element, with an "Insert title
  line" quick-fix; an emptied editor shows "Empty netlist" instead of errors (O1).
- Safe results folder: a save deletes only the result files that the project's own
  .qsp lists, never other files in `name_results/`, and keeps the folder while
  anything else is in it (O4).
- One schematic per project: drawing over another netlist asks Replace or Keep, and a
  schematic-owned netlist is saved into the project (O5).

### Samples and docs
- New samples: `rc_filter_schematic.qsp`, `opamp_amp/opamp_amp.qsp` (project symbol
  plus its own `.lib`), `hierarchy/rc2.qsp` (one circuit symbol used twice),
  `rlc_bandpass.qsp` and `diode_log.qsp`.
- `buck_cpm/buck_cpm.qsp`: a 1 MHz peak current mode synchronous buck (5 V to 3.3 V,
  type II loop with slope compensation, 2 A load step) as a schematic with its own ngspice-ready model
  library, and the same converter as a block diagram of Python blocks.
- README covers the schematic editor and shortcuts, probing, library and symbol search
  paths, the waveform window and expression syntax.
- A release check opens, saves and reopens the v0.1.0 sample projects.

### Not in this release
- O2 (ngspice run errors mapped to a netlist line or part) and O3 (block-run timeout
  setting and step guard) are not done. Block runs still have a fixed 10 s timeout.

### Known limitations
See [README.md](README.md#known-limitations-v02).

## 0.1.0 (2026-11-05)

The first release of QtSpice: a desktop app for editing and checking SPICE netlists,
simulating them with ngspice, viewing waveforms, and building and running block
diagrams from Python functions. See [README.md](README.md) for install, use and
known limitations.

### Netlist input (F1)
- Netlist editor with line numbers and syntax highlighting.
- Type or paste a netlist, open it from a file, or drag a `.cir`, `.net` or `.sp`
  file onto the editor.
- `.include` is resolved relative to the netlist's folder. Include loops and missing
  files are reported on the `.include` line.

### Netlist checking (F2)
- The parser never fails on bad input. Every problem becomes an error or warning on
  its own line and column, marked in the editor.
- Supported syntax:
  - `*` comments, and `;` and `$` inline comments.
  - `+` continuation lines.
  - SPICE numbers with scale suffixes, including `meg` and `mil`.
  - Elements R C L V I D Q M E F G H X.
  - Cards `.model`, `.subckt`/`.ends`, `.param`, `.include`, `.op`, `.dc`, `.ac`,
    `.tran` and `.end`.
- Unsupported elements and cards are warned about and skipped, not rejected.
- Connectivity and reference checks:
  - a ground node exists;
  - every node has at least two connections;
  - element names are unique;
  - every model and subcircuit used is defined;
  - each subcircuit call has the right number of ports;
  - there is an analysis card.
- A clean SPICE deck is built from the checked circuit and shown in the Deck tab.

### Simulation (F3)
- `.op`, `.dc`, `.ac` and `.tran` run through the ngspice shared library, loaded
  lazily with ctypes. Without ngspice the app still starts and says why it can't run.
- Simulations run on a background thread with progress and **Stop**. ngspice's output
  appears in the Console tab.
- An Analysis panel with forms for all four analyses can replace the netlist's
  analysis card, or supply one when the netlist has none.
- When a netlist has several analysis cards, the first one runs and the status line
  says how many were not run.

### Waveforms (F4)
- Trace list with show/hide.
- Mouse-wheel zoom, drag to pan, double-click to fit.
- Two cursors with values and delta.
- AC results show magnitude in dB and phase on a log frequency axis.
- CSV export.
- Large results are drawn with min/max-per-pixel decimation.

### Function blocks (F5)
- A Python function or class marked with `@block` becomes a block. Inputs, settings
  and outputs come from its signature. `t` and `dt` are supplied by the engine.
- Stateful blocks are classes with `__init__`, `output()` and `step()`.
- The function editor checks code as you type, without running it (it is read with
  `ast`). The library keeps the last version without errors.
- Built-in library: Constant, Step, Sine, Gain, Sum, Product, Integrator, UnitDelay and
  Scope.

### Block diagrams (F6)
- Block canvas:
  - drag blocks in from the library;
  - wire output ports to input ports;
  - rubber-band select, pan and zoom;
  - edit settings in the Properties panel;
  - undo and redo for every edit.
- Placed blocks follow edits to their function code. Removed ports are reported.
- Fixed-step execution in dependency order. Algebraic loops are refused before the run
  starts.
- Diagrams run in a separate process, with a timeout and **Stop**, so a bad block can't
  hang or crash the app.
- Scope blocks plot into the waveform viewer.

### Projects (F7)
- **File → New, Open…, Open Recent, Save and Save As…**, with a prompt when there are
  unsaved changes.
- A project is one `.qsp` JSON file (the format is described in
  `core/project/schema.json`), with results stored as `.npz` files in a
  `<name>_results/` folder next to it. Damaged or missing result files are reported
  and skipped.
- Opening a project from elsewhere that contains block code asks before the code is
  loaded.

### Samples and docs
- Sample projects in `examples/`: `rc_filter.qsp`, `diode_rectifier.qsp` and
  `first_order_feedback.qsp`, plus `rc_lowpass.cir`.
- README with install, run, the supported netlist syntax, how to write a block, and
  known limitations.

### Packaging
- PyInstaller specs for Windows, macOS and Linux with ngspice bundled, and a
  `--smoke-test` option that checks an installation without opening a window.
- CI runs ruff and pytest on all three operating systems, builds the packages, and
  smoke-tests them on fresh runners.

### Known limitations
See [README.md](README.md#known-limitations-v01). In short:
- no schematic capture;
- one analysis per run;
- no `.lib` or `.control`;
- fixed-step blocks only, with a fixed 10 s run timeout;
- saving manages the `<name>_results/` folder next to the project.
