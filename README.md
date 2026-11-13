# QtSpice 0.2

QtSpice is a small desktop EDA application written in Python with Qt Quick. You can:

- draw a circuit as a **schematic** with LTspice-style keys, run it, and click wires,
  parts and pins to plot voltages, currents and power;
- use vendor model **libraries** (`.lib`, `.lib file section`) and LTspice-format
  **symbols** (`.asy`), with a library browser and your own search folders;
- turn a schematic or netlist into a **circuit symbol** (`.sub` + `.asy`) and place it
  in another schematic;
- type, paste, open or drag in a SPICE netlist (`.cir`, `.net`, `.sp`) and see syntax
  and connectivity errors on the exact line;
- simulate it with ngspice (`.op`, `.dc`, `.ac`, `.tran`) in the background;
- look at the results in a waveform window with stacked panes, box zoom with
  history, drag-and-drop traces, log axes, a Bode view, two cursors, average/RMS, and
  LTspice-style trace expressions (`V(out)/V(in)`, `dB(...)`);
- write a Python function with `@block`, so it becomes a block with matching ports;
- place, wire and run blocks in a block diagram with a fixed time step, with Scope
  blocks plotting into the same waveform viewer;
- save everything in one `.qsp` project file, with the results in `.npz` files next
  to it.

## Install

You need Python 3.12 and the ngspice **shared library** (`libngspice`, not just the
`ngspice` program).

```bash
python -m venv .venv
```

Activate it (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate`
elsewhere), then:

```bash
pip install -e ".[dev]"
```

Then get ngspice:

| OS      | How to install ngspice |
|---------|------------------------|
| Windows | Put the 64-bit `ngspice.dll` (and the DLLs it needs) in `third_party/ngspice/lib/`. |
| macOS   | `brew install libngspice` |
| Linux   | `sudo apt install libngspice0` (or your distribution's package) |

To use a library somewhere else, set `QTSPICE_NGSPICE_LIB` to the library file. If
QtSpice can't find ngspice, the app still starts: you can edit and check netlists and
run block diagrams, but **Run** on a netlist explains that ngspice is missing.

To check an installation without opening a window:

```bash
python -m app --smoke-test
```

## Run

```bash
python -m app
```

or the `qtspice` command that `pip install` adds. Packaged builds (PyInstaller, with
ngspice bundled) are made with `pyinstaller packaging/qtspice-<windows|macos|linux>.spec`.

### Try the samples

`examples/` holds these projects. Open them with **File → Open…**:

- **`dc_motor_speed.qsp`** (new in 0.3): speed control of a DC motor. Open the
  **Blocks** tab. A DiscretePI controller (Ts = 100 µs, output limits ±1, clamping
  anti-windup) sets the duty cycle of an averaged H-bridge on a 24 V supply, which
  drives the DC motor (R = 1 Ω, k = 0.05, J = 1e-4 kg·m²). The set speed is
  150 rad/s (1432 rpm). At 0.15 s a load torque of 0.01 N·m comes on. **Run** for
  0.3 s: the speed rises without overshoot. In steady state the duty cycle is 0.6569
  before the load step and 0.6611 after it, from V(a, b) = k·w + (R + 2·R_on)·i. The
  waveform window shows the speed in rpm (the rpm/°C toggle is saved on).
- **`heatsink_warmup.qsp`** (new in 0.3): a MOSFET with a thermal port switches a 1 Ω
  load on a 12 V supply. Its conduction loss, I²·R_on = 6.53 W, heats a 50 J/K
  heatsink, which loses heat by convection (R_th = 1/(h·A) = 2 K/W) to a 25 °C
  ambient. **Run** for 600 s: the temperature rises as a first-order step with
  τ = R_th·C_th = 100 s, to 25 + 13.06 = 38.06 °C.
- **`battery_discharge.qsp`** (new in 0.3): a 0.1 A·h battery with an OCV table and
  R_int = 50 mΩ, discharged at a constant 2 A for 150 s. The state of charge falls
  linearly from 1 to 0.167, and the terminal voltage follows the table minus
  I·R_int = 0.1 V. On the signal side a Product and an Integrator add up the energy
  delivered (`E`, in J).
- **`rc_filter_schematic.qsp`** (new in 0.2): an RC low-pass drawn as a schematic,
  R2 = 10k and C1 = 100n, with an `.ac` directive. Press **Run** on the Schematic tab,
  then click the `OUT` wire: the Bode pane shows −3 dB at 159 Hz.
- **`opamp_amp/opamp_amp.qsp`** (new in 0.2): a non-inverting amplifier with a gain of
  1 + Rf/Rg = 10. The op-amp is the project symbol `opamp5.asy`, whose `ModelFile` pulls
  in `opamp.lib` from the same folder. (`opamp.lib` is a hand-written placeholder model,
  not a vendor file.) A 0.5 V, 1 kHz sine gives ±5 V at `out`.
- **`hierarchy/rc2.qsp`** (new in 0.2): the RC cell in `hierarchy/rclp_cell.qsp`, made
  into the circuit symbol `rclp` with **Tools → Make Symbol from Schematic…** and placed
  twice in a row. The result is a second-order low-pass: −9.5 dB at the cell's corner
  frequency, falling at 40 dB/decade.
- **`buck_cpm/buck_cpm.qsp`** (new in 0.2): a 1 MHz peak current mode synchronous buck
  regulator, 5 V to 3.3 V, with a type II error amplifier and a 2 A load step at 110 µs.
  It is redrawn from an LTspice course example (`SyncBuck_switching_CPM_CL.asc`).
  - **Schematic:** the gate drivers, dead time, CPM modulator, op-amp and power devices
    are QtSpice stand-ins in `buck_cpm.lib`, built from R C L V E G D M X only (no
    vendor models). Their parameters are fixed in the library because 0.2 doesn't pass
    parameters into symbols. The run takes a few seconds and saves 100–160 µs.
  - **Blocks tab:** the same converter as Python blocks (power stage, CPM modulator,
    dead time, type II compensator), run at a 5 ns step. Opening the project asks
    you to trust its block code.
  - The modulator has 0.3 V of slope compensation (`Va = 0.3`); the original has none.
    The ripple is a steady ±0.56 A. Set `Va` to 0 (the `CPM` block, or the
    `CPM_modulator` `.param` line) to see subharmonic oscillation: at a duty cycle of
    0.66 the ripple then alternates from cycle to cycle.
- **`rlc_bandpass.qsp`** (new in 0.2): a series RLC band-pass as a schematic, with a
  saved Bode pane of `V(n003)/V(n001)`. It peaks at 503 Hz with a bandwidth of 318 Hz;
  read it with the two cursors.
- **`diode_log.qsp`** (new in 0.2): a diode `.dc` sweep from 1 nA to 10 mA, with the
  current pane on a log Y axis.
- **`rc_filter.qsp`**: an RC low-pass netlist, R = 1k and C = 1u. **Run** does the
  `.tran`: the step response reaches 63 % at 1 ms. To get the Bode plot, choose `.ac` in
  the Analysis panel and run again: it is −3 dB at 159 Hz.
- **`diode_rectifier.qsp`**: a 5 V, 50 Hz half-wave rectifier with RC smoothing. Plot
  `V(in)` and `V(out)`: the output sits about one diode drop below the peak, with about
  0.4 V of ripple.
- **`first_order_feedback.qsp`**: open the **Blocks** tab. A step goes into a loop of
  Sum (`+-`), Gain (1/τ = 2) and Integrator, with the output fed back to the Sum. **Run**
  plots `r` and `y` in the Waveforms tab. `y` follows 1 − e^(−(t−0.5)/τ), with τ = 0.5 s.

`examples/rc_lowpass.cir` is the same RC filter as a plain netlist.

## Schematic editor

A project has one schematic sheet. It is only another way to write a netlist: **Check**
and **Run** on the Schematic tab netlist it (nets you don't name become `N001`, `N002`,
…) and simulate it exactly like the netlist editor. Problems are listed under the
sheet. If the project already has a netlist when you start drawing, QtSpice asks
whether the schematic should **Replace** it or **Keep** it.

Place parts with **F2** (the part picker: search, preview, click to place repeatedly,
Esc to stop). Put SPICE directives such as `.ac dec 100 1 1meg` or `.lib mylib.lib` on
the sheet with the text tool. Double-click a part to edit its attributes (`Value`,
`Value2`, `SpiceModel`, `SpiceLine`, …).

| Key | Action |
|-----|--------|
| R, C, L, D | Place a resistor, capacitor, inductor, diode |
| G | Ground tool |
| F1 | Shortcut list (also the **?** button) |
| F2 | Part picker |
| F3 | Wire tool (orthogonal segments, snapped to the grid and to pins) |
| F4 | Net label tool |
| F5 | Delete tool: click things to delete them |
| F7 | Move tool: drag with wires detached |
| F8 | Drag tool: drag with wires following |
| F9 / Ctrl+Z | Undo |
| Shift+F9 / Ctrl+Y | Redo |
| Ctrl+R / Ctrl+E | Rotate / mirror the selection or the part being placed |
| Delete | Delete the selection |
| Ctrl+C / Ctrl+V | Copy / paste (click to drop the copy) |
| Esc / right-click | End a wire, then leave the tool |
| Double-click | Edit a part's attributes, a label, a port or a text |
| Wheel / middle-drag | Zoom around the pointer / pan |

A click selects, Ctrl+click adds to the selection, and a drag on empty space is a
rubber band. Every edit can be undone.

### Probing

After a schematic run, the sheet is in probe mode. A badge at the pointer shows what a
click will plot:

| Do this | Plots |
|---------|-------|
| Click a wire | `V(net)` |
| Click the body of a two-terminal part | the current through it |
| Click a pin | the current into that pin |
| Drag from one net to another | `V(a,b)` = `V(a) − V(b)` |
| Alt+click a part | its power, V × I |

Editing the sheet ends probe mode until the next run.

### Circuit symbols (one level)

Put **port flags** on a schematic (port tool; direction in, out or bidir) and choose
**Tools → Make Symbol from Schematic…**. QtSpice writes `<name>.sub` and `<name>.asy`
into the project folder, with inputs on the left, outputs on the right and bidir pins on
top. **Tools → Make Symbol from Netlist…** does the same for a `.subckt` netlist. The
new symbol shows up in the project's part picker, and the project remembers it. A
circuit symbol may not itself contain circuit symbols.

## Libraries and symbols

**Netlist cards** (new in 0.2): `.lib file` includes a whole library, `.lib file
section` includes only the part between `.lib section` and `.endl`, and `.param` may be
used inside `.subckt`. Relative paths are resolved from the netlist's (or project's)
folder.

**Search paths.** **Tools → Library Browser… → Search paths…** sets:

- **symbol folders**, searched for `.asy` files in the order given;
- **library folders**, searched for model files (`.lib .sub .mod .inc .cir .sp .spi`);
- an optional **LTspice folder**: your own LTspice install (its `sym` subfolder is used
  for symbols). QtSpice does not ship Analog Devices' symbols or models.

Symbols are looked up in this order: QtSpice's built-in symbols, the project's own
circuit symbols, your symbol folders, then the LTspice folder. The first match wins.
Every folder is read-only: QtSpice never writes to it. The paths belong to you, not to
the project, and are kept in the app settings.

The **Library Browser** lists every `.model` and `.subckt` in the library folders, with
each subcircuit's pins. **Use with symbol…** pairs a subcircuit with a symbol (it fills
in `SpiceModel` and `ModelFile`), and the part picker then places it. Encrypted model
files are listed as encrypted and never decrypted.

**Symbol files** are LTspice `.asy` files, read as Windows-1252 (so `µ` works). The
netlist pin order comes from each pin's `SpiceOrder`, not from the drawing order. A
symbol with `Prefix X` is a subcircuit, and its `ModelFile` becomes an `.include`.
Unknown keywords give a warning.

**LTspice compatibility.** Before each run QtSpice sends `set ngbehavior=ltpsa` to
ngspice, so LTspice and PSpice functions such as `uplim()` and `if()` work in
B-sources. ngspice is not LTspice: devices and models it doesn't have (for example
A-devices, the PSpice `VSWITCH` model, `E … TABLE`) are reported as "not supported by
ngspice". The mode is stored in the project as `simulation.compat_mode`.

## Waveform window

- **Panes.** Panes are stacked and share one X axis; each pane autoscales its own Y.
  Right-click a pane for Add pane above/below, Delete pane, Zoom back, axes and
  settings (titles, ranges, log axes, pane order). One layout is kept per analysis type
  (`.tran`, `.ac`, `.dc`) and saved in the project.
- **Zoom.** Drag a box to zoom (Esc cancels; X zooms every pane, Y only that pane). The
  wheel zooms X around the pointer, middle-drag pans, and double-click auto-ranges.
  **Zoom back** walks the zoom history, and zooms can be undone. The status bar shows
  the pointer position and, while you drag, the box size with 1/Δx.
- **Traces.** Drag a trace name to another pane to move it, Ctrl+drop to copy it, or
  drop it outside the panes to remove it. Double-click a trace name to show only that
  trace.
- **Log axes and Bode.** Log X and log Y have decade ticks and a minor grid. Values
  ≤ 0 are hidden with a note (or plotted as |y|). An `.ac` pane draws magnitude in dB,
  with the phase on the right axis.
- **Cursors.** Click a trace name to attach cursor 1; right-click it for cursor 2 or
  both. Drag a cursor line, or move the last-used cursor one data point with the arrow
  keys. The readout shows both cursors, their difference and 1/Δx.
- **Average and RMS.** Ctrl+click a trace name for its average and RMS over the zoomed
  X range (RMS only for voltage and current traces).

### Add Trace and expressions

**Add Trace…** lists the result's vectors with a wildcard filter (`*`, `?`). Click a
name to insert it, type an expression, and **Plot** it into the selected pane.
Right-click a trace name to edit its expression. Expressions are checked as you type,
use LTspice syntax and are evaluated on the data; they are never run as Python.

| | |
|---|---|
| Vectors | `V(n)`, `V(a,b)` (= `V(a) − V(b)`), `I(R1)`, `Ic(Q1)` |
| Operators | `+ - * /`, `**` (power), `!` (not), `> < >= <=`, `&` `\|` `^` (Boolean AND, OR, XOR; `^` is **not** power) |
| Functions | `sin cos tan asin acos atan atan2 sinh cosh tanh ln log log10 exp sqrt pow abs ceil floor round int if limit min max hypot d Re Im Ph Mag conj` |
| Constants | `E`, `pi`, `K` (Boltzmann), `Q` (electron charge) |
| Axis names | `time` (`.tran`), `frequency`, `omega` / `w` = 2π·frequency (`.ac`) |

As in LTspice, `log()` is the natural log (same as `ln`). `Ph` is the phase in
degrees, `d(x)` is the derivative along the X axis, and a Boolean is true when its value
is above 0.5. Numbers take SPICE suffixes (`1k`, `2meg`).

**Plot definitions.** **Plot definitions…** in the Add Trace dialog edits `.func` and
`.param` lines (SPICE syntax) that expressions can use. The default is
`.func dB(x) 20*log10(Mag(x))`. The definitions are saved in the project.

## Supported netlist syntax

The first line is always the **title**, as in SPICE. Names and nodes are
case-insensitive. Ground is `0` (or `gnd`).

**Comments and lines**

- `*` at the start of a line comments out the whole line.
- `;` anywhere starts an inline comment. So does `$` at the start of a line or after a
  space.
- A line starting with `+` continues the line before it.

**Numbers** may have a scale suffix: `f p n u m k meg g t mil`. `m` is milli, `meg` is
mega, and `mil` is 25.4u. Letters after the suffix are ignored (`10uF`, `1kohm`). Values
can also be `{expressions}` or `'expressions'` and `.param` names.

**Elements**

| Letter | Element | Form |
|--------|---------|------|
| R C L | resistor, capacitor, inductor | `R1 n+ n- value [params]` |
| V I | voltage / current source | `V1 n+ n- [DC v] [AC mag [phase]] [SIN(...) PULSE(...) PWL(...) EXP(...) SFFM(...)]` |
| D | diode | `D1 a k model [params]` |
| Q | BJT | `Q1 c b e [substrate] model [params]` |
| M | MOSFET | `M1 d g s b model [params]` |
| E G | voltage-controlled V / I source | `E1 n+ n- nc+ nc- gain` |
| F H | current-controlled I / V source | `F1 n+ n- Vsense gain` |
| X | subcircuit instance | `X1 n1 n2 ... subckt [params: k=v]` |

**Dot-cards**

- `.model name type (k=v ...)`
- `.subckt name ports... [params: k=v]` … `.ends [name]` (not nested)
- `.include file`: relative to the netlist's folder, so open the netlist from a file
- `.lib file` and `.lib file section` (with `.lib section` … `.endl` in the library)
- `.param k=v ...`, at the top level or inside a `.subckt`
- `.op`, `.dc src start stop step [src2 start2 stop2 step2]`,
  `.ac dec|oct|lin points fstart fstop`, `.tran tstep tstop [tstart [tmax]] [uic]`
- `.end`: anything after it is ignored

Other element letters (A, B, J, K, T, …) and other cards (`.options`, `.ic`,
`.control` … `.endc`, …) are reported as **warnings** and skipped.

**Checks before a run**

- There is a ground node.
- Every node has at least two connections.
- Element names are unique.
- Every model and subcircuit that is used is defined, and each `X` call has as many
  nodes as the subcircuit has ports.
- There is an analysis.

**One analysis per run.** If the netlist has several analysis cards, **Run** uses the
first one and the status line says how many others were not run. Use the Analysis
panel to pick or set a different analysis. The panel's card replaces the netlist's
cards until you click *Use netlist card*.

## Writing a block

Open the **Blocks** tab and type into the function editor. Every function decorated
with `@block` appears in the block library as soon as the code checks clean. `block`
is already defined in the editor, so you don't import it. Other imports such as `math`
or `numpy` work as usual.

```python
@block
def saturate(u, low=-1.0, high=1.0):
    return min(max(u, low), high)
```

The ports and settings come from the signature:

- A parameter **without a default** is an **input port** (`u`).
- A parameter **with a default** is a **setting**, edited in the Properties panel
  (`low`, `high`). Defaults must be plain constants: numbers, strings, booleans, `None`,
  or lists/tuples of those.
- `t` (time) and `dt` (time step) are passed in by the engine. They are not ports.
- There is one output, `y`, unless you name others. A block with several outputs
  returns a tuple:

```python
@block(shape="circle", outputs=["sum", "diff"])
def add_sub(a, b):
    return a + b, a - b
```

A block that keeps state is a class with `stateful=True`:

- `__init__` takes the settings, which must all have defaults.
- `output()` returns the current output. It is computed before the inputs are known,
  so a stateful block breaks a feedback loop.
- `step(...)` takes the inputs (plus `dt` if needed) and updates the state.

```python
@block(stateful=True)
class LowPass:
    def __init__(self, tau=0.1):
        self.tau, self.y = tau, 0.0

    def output(self):
        return self.y

    def step(self, u, dt):
        self.y += (u - self.y) * dt / self.tau
```

`@block` options: `shape` (`"square"`, `"rectangle"` or `"circle"`), `outputs`,
`stateful`, and `color` (`"#rrggbb"`).

**Built-in blocks:** Constant, Step, Sine, Gain, Sum (with `signs`, e.g. `"+-"`),
Product, Integrator (forward Euler), UnitDelay and Scope. A Scope records its input
under its `label` (or its id if the label is empty). Each Scope needs its own label.

**How a diagram runs:** with a fixed time step from 0 to *End time*. Blocks run in
dependency order. A loop of blocks that has no stateful block in it is an *algebraic
loop* and is refused before the run starts.

The editor only *reads* your code; it never executes it. The code runs in a separate
process when you press **Run**, with a timeout and **Stop**. If you open a project made
somewhere else that contains block code, QtSpice asks before loading the code.

## Known limitations (v0.2)

- **Schematic:** one sheet per project, and one schematic, one netlist and one diagram
  per project. No buses, no multiple sheets, no auto-routing, and no `.asc` (LTspice
  schematic) import.
- **Hierarchy:** one level only. A circuit symbol can't contain circuit symbols,
  `.subckt` can't be nested, and parameters can't be passed into a symbol.
- **Symbols:** there is no graphical symbol editor; generated symbols are boxes with
  pins.
- **Libraries:** no `.dll` files of any kind. Encrypted models are reported, never
  decrypted.
- **Analyses:** only `.op`, `.dc`, `.ac` and `.tran`, one per run, and one simulation
  at a time. No noise, `.tf`, `.sens`, `.four` or `.meas`, and no `.control` scripts.
- **Waveforms:** all plots are in one window (no separate plot windows). No XY or
  Nyquist plots, no FFT, no LTspice `.plt` files, no automatic units, and no Python
  functions in expressions.
- **Netlist syntax:**
  - Element types other than those in the table above are skipped.
  - The first line is always the title. A netlist that starts with an element loses
    that element; QtSpice warns about it and offers to insert a title line.
- **LTspice compatibility** depends on ngspice and is not 100 %: devices that ngspice
  doesn't have are reported, not simulated.
- **Some checks happen at run time.** Some errors are only found by ngspice when it
  runs, for example voltage sources in parallel, duplicate `.model` or `.subckt` names,
  or a node with no DC path to ground. These errors appear in the Console and the
  status line, not on the netlist line or the part.
- **Block diagrams:**
  - Fixed-step forward Euler only (no variable-step solver).
  - No physical/acausal blocks.
  - A Scope records scalar values only.
  - Each run has a fixed **10 s** timeout that can't be changed from the UI.
  - There is no limit on End time / Step, and a run with a very large number of steps
    may run out of memory or hit the timeout.
- **Projects:**
  - Saving `name.qsp` writes its results to `name_results/` next to it, as
    `netlist.npz` and `diagram.npz`, and deletes only result files that the project
    itself listed. Other files in the folder are left alone, but a file with one of
    those two names is overwritten.
  - Projects saved by 0.2 (format version 2) don't open in 0.1. Projects from 0.1 open
    in 0.2 and are saved as version 2.
  - The samples live in the app folder. Use **Save As…** to keep your own copy.
- **Simulator:** QtSpice has no SPICE engine of its own. Accuracy, convergence and
  device models are ngspice's.
- **Tests on Windows:** when pytest runs block-runner tests, it may print "Windows
  fatal exception: access violation" tracebacks while a child process starts. The tests
  pass and the app is not affected (it does not enable `faulthandler`).

## Development

```bash
pytest -q
```

```bash
ruff check . && ruff format --check .
```

Architecture rules and the folder layout are in [CLAUDE.md](CLAUDE.md). Tests that need
ngspice are marked `@pytest.mark.ngspice` and are skipped when it is missing. See
[CHANGELOG.md](CHANGELOG.md) for the release notes.
