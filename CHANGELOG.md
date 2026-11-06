# Changelog

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
