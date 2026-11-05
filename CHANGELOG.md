# Changelog

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
