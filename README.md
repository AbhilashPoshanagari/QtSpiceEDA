# QtSpice 0.4

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
- model **physical systems** in the same diagram (new in 0.3): electrical,
  rotational, translational and thermal parts such as motors, bridges, batteries and
  heatsinks, joined by physical wires, solved by ngspice and driven by signal blocks
  such as a PI controller in one combined run;
- open a **distribution feeder** written for OpenDSS (`.dss`) and run a snapshot
  power flow (new in 0.4, optional `grid` extra): node voltages, branch flows,
  losses, voltage and loading violations, a voltage profile and a single-line view;
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

For distribution-grid power flow (0.4), also install the optional `grid` extra. It
adds [DSS-Python](https://github.com/dss-extensions/dss_python) 0.15.x (BSD-3), which
brings the OpenDSS engine as a pip wheel on Windows, macOS and Linux (no compiler
needed):

```bash
pip install -e ".[dev,grid]"
```

Without it the app starts and everything else works; the **Grid** tab and the
**Open Grid (.dss)…** menu entry are hidden.

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

- **`ieee13_powerflow.qsp`** (new in 0.4, needs the `grid` extra): the IEEE 13 bus
  test feeder (files in `examples/grid/`, BSD-3). Opening the project opens the feeder
  in the **Grid** tab and draws its single-line view. **Run** solves a snapshot power
  flow in about half a second: 3567 kW and 1736 kvar from the source, 112.4 kW of
  losses, node 671.1 at 0.9828 pu. With the default limits (0.95–1.05 pu, 100 %
  loading) the Violations table lists the regulator outputs rg60.1 and rg60.3 at
  1.056 pu and four overloads: three lines (650632 at 148 %) and transformer xfm1
  (107 %). The voltage profile
  opens in the waveform window.
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

## Physical modelling (new in 0.3)

A block diagram can also hold **physical blocks**: resistors, motors, inertias, springs,
thermal masses and so on, joined by **physical wires**. Physical blocks don't pass
signals from an output to an input. They share a node, and each node has an *across*
quantity (voltage, speed, temperature) and a *through* quantity that is conserved at
the node (current, torque, force, heat flow). QtSpice has no equation solver of its
own: every physical network is turned into a SPICE circuit and solved by ngspice.

### Domains

| Domain        | Across            | Through     | Storage (C)    | R from        | L from         | Reference |
|---------------|-------------------|-------------|----------------|---------------|----------------|-----------|
| electrical    | voltage V         | current A   | capacitor      | resistor      | inductor       | 0 V       |
| rotational    | speed w [rad/s]   | torque [N·m]| inertia J      | damper 1/b    | spring 1/k     | 0 rad/s   |
| translational | speed v [m/s]     | force F [N] | mass m         | damper 1/b    | spring 1/k     | 0 m/s     |
| thermal       | temperature T [K] | heat Q [W]  | thermal mass   | resistance R [K/W] | –         | 0 K       |

Ports are round and coloured by domain. A wire between ports of two different domains
is refused, and the status line says why. The Solver Configuration's grey port joins
a network of any domain.

### Conventions

- **Units.** Everything is SI inside: rad/s, K, m/s, N·m. rpm, °C and mm are for
  display only. The **rpm / °C** button in the waveform window shows rad/s as rpm and K
  as °C, and the project saves the toggle. Expressions always see the SI values.
- **Signs.** The through quantity is positive flowing **into port A and out of port B**
  of a part (into `p` and out of `n` for electrical parts, as in SPICE). A source
  delivers its through quantity into the network at A. A sensor measures A relative to
  B, so put B on the reference to read an absolute value.
- **References.** Every domain a network uses needs its reference block: Electrical
  Reference, Mechanical Rotational / Translational Reference or Thermal Reference.
  Mass, inertia and thermal mass are one-port parts, tied to the reference inside.
- **Thermal reference** is absolute zero. An ambient temperature is a Temperature
  Source between the body and the Thermal Reference, e.g. 298.15 K for 25 °C.
- **Position and angle** come from integrating speed inside the network (Position and
  Angle sensors).
- **Domains couple only through controlled sources.** In a DC motor, for example,
  E = k·w and τ = k·i.
- **Solver Configuration.** Each physical network needs exactly one, wired to any
  node. Its `Ts` is the controller tick and must equal the diagram's time step: the
  canvas step follows it. The other settings are `reltol`, `abstol`, `max_step`
  (0 = Ts) and `steady_state`. The run is refused, and the failing blocks are named,
  if a network has no Solver Configuration, two of them, or a missing reference.
- **Start values.** Storage parts start from their start values (`w0`, `v0`, `T0`,
  capacitor `v0`, inductor `i0`, spring `tau0`/`F0`). Tick `steady_state` in the Solver
  Configuration to run an operating point first instead.

### The combined run

A diagram with physical blocks has one **Run** button, like any other diagram. The
run is split this way:

- Every physical network runs in ngspice as one stateful block of the diagram. The
  signal blocks (Gain, Sum, PI, your own `@block` code) run in Python with the fixed
  step `Ts`.
- At each tick k·Ts, QtSpice reads the sensors, runs the signal blocks once, and sets
  the signal-controlled sources. ngspice gets a breakpoint at every tick.
- **One-sample delay.** The sensor values the controller sees at tick k are those of
  the accepted ngspice point at tick k. A value the controller sets is held until the
  next tick (zero-order hold). The physical network therefore reacts to a controller
  output one tick later. In a feedback loop this delay is part of the loop: pick `Ts`
  small compared with the time constants you control.
- Steps that ngspice rejects and redoes never reach the controller.
- PWM edges are made inside ngspice by the PWM Generator, not by the Python
  controller, so they are not rounded to the tick.
- The result is one set of waveforms. Signal vectors carry their Scope labels, and
  physical vectors are named `<component>.<port>.<quantity>` in SI, e.g.
  `motor.A.w`. **Add Trace** groups them by component, and the Y axis shows the unit.
- Ts may be as small as 10 µs. At 100 µs a combined run takes about 0.55 s per
  simulated second on a current PC; at 10 µs, about 5.4 s.
- The run takes place in the block-runner child process, with **Stop** and a timeout.
  **Sweep…** next to Run repeats it for a list of values of one block parameter and
  overlays the runs.

### The library

The block library has these groups under **Physical**:

- **Electrical:** Resistor, Capacitor, Inductor, Diode, Controlled Switch, DC / sine /
  pulse voltage and current sources, Electrical Reference.
- **Rotational:** Inertia, Rotational Damper, Rotational Spring, Torque Source, Angular
  Velocity Source, Mechanical Rotational Reference.
- **Translational:** Mass, Translational Damper, Translational Spring, Force Source,
  Velocity Source, Mechanical Translational Reference.
- **Thermal:** Thermal Mass, Conduction, Convection, Heat Flow Source, Temperature
  Source, Thermal Reference.
- **Sensors and sources:**
  - signal-controlled sources (voltage, current, torque, angular velocity, force,
    velocity, heat flow, temperature), driven by a signal wire;
  - sensors with a signal output (voltage, current, angular velocity, angle, torque,
    velocity, position, force, temperature, heat flow);
  - Solver Configuration;
  - PWM Generator.
- **Composite:** DC Motor, Gearbox, Ideal Transformer, Battery (state-of-charge
  table), Half-Bridge and H-Bridge (`model` = `switching` or `averaged`), Thermal
  Resistor and Thermal MOSFET (their power loss flows into a thermal port).
- **User:** your own components (see below).

The **Signal** group adds **DiscretePI** and **DiscretePID** for controllers, with
output limits and anti-windup (`clamping` or `back-calculation`). Numeric settings
of physical blocks take SPICE suffixes (`10m`, `2.2u`), and the Properties panel
shows each setting's unit.

### Writing your own component

Open the **Components** tab and write a class with `@physical_component`. It uses
the same API as the built-in library, and it appears under **Physical > User** as
soon as it checks clean:

```python
T0 = 298.15


@physical_component
class TCThermalResistor(PhysicalComponent):
    """Thermal resistance R0 * (1 + alpha * (Tm - T0)) between A and B, where Tm is
    the mean of the two port temperatures."""

    ports = (Port("A", THERMAL), Port("B", THERMAL))
    parameters = (
        Parameter("R0", 1.0, "K/W", "resistance at T0"),
        Parameter("alpha", 0.004, "1/K", "temperature coefficient"),
    )

    def network(self, ports, params):
        th = get_domain(THERMAL)
        r0 = th.resistance_from(float(params["R0"]))
        alpha = float(params["alpha"])
        if r0 <= 0:
            raise ValueError("R0 must be positive")
        mean = f"({th.across_expr(ports.A)} + {th.across_expr(ports.B)}) / 2"
        r = f"{r0!r} * (1 + {alpha!r} * ({mean} - {T0!r}))"
        heat = f"{th.across_expr(ports.A, ports.B)} / ({r})"
        return [Behavioural("R", ports.A, ports.B, "i", heat)]
```

- `ports`: conserving ports `Port(name, DOMAIN)` and signal ports `SignalInput` /
  `SignalOutput`. `parameters`: `Parameter(name, default, unit, description)`.
- `network(self, ports, params)` returns primitives such as `Resistor`, `Capacitor`,
  `Inductor`, `VoltageSource`, `CurrentSource`, `Behavioural`, `ExternalSource` and
  `Sensor`, between the port nodes and the `internal` nodes you declare.
- Ask the domain for the mapping (`get_domain(THERMAL).across_expr(...)`,
  `.resistance_from(...)`, `.to_current(...)`) instead of writing SPICE voltages and
  currents yourself.
- An error raised in `network` (a bad parameter, say) is reported on the block when
  the run starts.
- The editor only reads the code: it checks the declarations and the calls in
  `network`, and never runs it. The code runs in the block-runner process when you
  press Run. As with `@block` code, a project from somewhere else asks before it
  loads component code.

## Distribution grids (new in 0.4)

QtSpice 0.4 opens distribution feeders written for
[OpenDSS](https://www.epri.com/pages/sa/opendss) and runs a **snapshot power flow**:
one steady-state solution at the loads given in the file. It needs the `grid` extra
(see *Install*). Open a feeder with **File → Open Grid (.dss)…** or the **Open .dss…**
button on the **Grid** tab, then press **Run**. Try `examples/ieee13_powerflow.qsp`.

- **Results:** node voltages (kV, pu, angle), branch currents, powers and loading,
  and total source power and losses (also per element class). The tables sort by any
  column.
- **Violations** are computed by QtSpice from the limits, not by OpenDSS: by default
  a node voltage outside 0.95–1.05 pu, or a branch loaded above 100 % of its normal
  rating. Rows with a violation are highlighted.
- **Voltage profile:** pu against distance from the source, per phase, with the two
  limits as flat traces, in the waveform window (cursors and zoom work as usual).
- **Single-line view:** drawn from the bus coordinates in the file and coloured by the
  lowest pu voltage of each bus; open switches are drawn open. It is read-only. Buses
  without coordinates are counted and left out. Click a bus to see its phase voltages.
- **Console:** what OpenDSS reported, QtSpice's own checks of the feeder (unknown
  buses, buses not reached from a source, phase and kV mismatches, missing voltage
  bases) and the run summary.
- **Project:** the `.dss` file (relative to the project when it is inside the project
  folder), the power-flow settings and the limits are saved in the `.qsp` (format
  version 4). Results are not saved; **Run** again (about half a second).

### How `.dss` files are read

QtSpice does not parse DSS text itself. OpenDSS compiles the file (with its
`redirect`s, `like=`, line codes, RPN values and so on) and solves it, and QtSpice
reads every element back through the OpenDSS API into its own grid model. Each run
uses its own OpenDSS context in a separate process, so the app never blocks and
**Stop** always works.

The model knows the Vsource, buses, line codes, lines, switches, transformers (with
their windings), regulators (RegControl), loads, capacitors and PV systems. Any other
element in the file (a fuse, a storage element, ...) is still solved by OpenDSS, but
the Console reports it as a warning, `unsupported element <class>.<name>`: nothing is
dropped silently. The base frequency and the voltage bases always come from the file.

### Units and sign conventions

| Quantity            | Unit in the grid model                               |
|---------------------|------------------------------------------------------|
| length              | m                                                    |
| series impedance    | ohm/km, full phase matrices                          |
| shunt capacitance   | nF/km, full phase matrices                           |
| voltage             | kV (line-to-line for 3-phase and delta; as given for 1-phase wye) |
| power               | kW, kvar, kVA                                        |
| transformer R, X    | % on the winding / element base                      |
| node voltage result | complex V, line-to-neutral; pu = abs(V) / line-to-neutral base |

- Names are lower case. Elements are `<class>.<name>` (`line.650632`), nodes are
  `<bus>.<node>` (`671.1`).
- Branch currents are per phase at terminal 1. Branch power is positive flowing into
  terminal 1.
- Source power is positive when it is supplied to the grid. (OpenDSS reports it as
  negative; QtSpice flips the sign.)

### Benchmarks

The IEEE PES test feeders match the published IEEE results node by node, within the
test tolerance of 0.5 % on |V| (pu) and 0.5° on the angle
(`tests/core/grid/test_benchmarks.py`; DSS-Python 0.15.7, OpenDSS engine 0.14.5):

| Feeder   | Values compared | Largest error in |V| | Largest angle error |
|----------|-----------------|-----------------------|---------------------|
| IEEE 13  | 35 nodes        | 0.135 %               | 0.06°               |
| IEEE 34  | 92 nodes        | 0.141 %               | 0.06°               |
| IEEE 123 | 271 values      | 0.136 %               | 0.10°               |

For this comparison the regulator taps are set to the published ones, with controls
off. OpenDSS's own RegControl settles one or two tap steps away from the published
taps, which moves voltages by up to 2.2 %. The controlled OpenDSS solution is tested
separately, against the OpenDSS result of the original files.

## Known limitations (v0.4)

The grid side of 0.4 analyses snapshot power flow only. These are not supported (some
are planned for later versions):

- **No power-flow solver of QtSpice's own**, and no DSS-language parser. Feeders are
  solved only by OpenDSS, through DSS-Python. There is no second grid engine
  (pandapower, the Windows COM OpenDSS, OpenDSSDirect.py).
- **Snapshot power flow only.** No QSTS, load shapes or yearly runs, no fault studies,
  hosting capacity, EV impact, harmonics or dynamics.
- **No GIS or ArcGIS import**, no data normalisation or electrical enrichment, no
  conductor or transformer catalogs, and no writing results back to GIS.
- **Read-only grid view.** Feeders can't be drawn or edited in the UI: edit the `.dss`
  file and open it again.
- **No coupling** of grid models with ngspice circuits, physical networks or Python
  controllers.
- **No settings dialog.** The power-flow settings and limits are stored in the
  project, but 0.4 has no dialog to change them (edit the `grid` section of the
  `.qsp`).
- **Grid results are not saved** in the project.
- **Tests on Windows:** pytest may print "Windows fatal exception: code 0xe0465043"
  when it first imports `dss`. It is an exception that the OpenDSS engine raises and
  handles itself; the tests pass.

Everything listed for v0.2 and v0.3 below still applies. Projects are saved as format
version 4, which 0.3 doesn't open.

## Known limitations (v0.3)

Physical modelling in 0.3 is kept small on purpose. These are not supported:

- **No equation solver or equation language of QtSpice's own.** Every physical
  network is mapped to a SPICE circuit and solved by ngspice. Python never integrates
  physical equations, and there is no Simscape-like language for components. Your own
  components are built from SPICE primitives (see *Writing your own component*).
- **Four domains only:** electrical, rotational, translational and thermal. No
  hydraulic, gas, thermal-liquid or magnetic domains.
- **No 3D multibody** mechanics. Translational and rotational motion are each
  one-dimensional.
- **No real-time runs, hardware-in-the-loop or code generation.**
- **No linearization or frequency analysis of mixed models.** A combined run is a
  transient run. (A plain netlist or schematic still has `.ac`.)
- **One controller rate.** All signal blocks run at the one fixed tick Ts. There are
  no variable-step controllers and no multi-rate diagrams.
- **Ts is at least 10 µs.**
- **Physical blocks live in block diagrams only.** They can't be placed in the
  schematic editor.
- **Circuit blocks (M2)** are made in code (`core/physical/circuit_block.py`). The
  app has no menu to make one, and a project doesn't store them.
- **One-sample delay.** The controller always sees the sensors of the current tick
  and acts on the next one (see *The combined run*).
- **Speed and timeout.** A combined run at Ts = 10 µs is about 5 times slower than
  real time. Combined runs share the block diagrams' fixed **10 s** timeout, so at
  Ts = 10 µs only about 1.5 s can be simulated. The three samples each run in a few
  seconds.

Everything listed for v0.2 below still applies, except where 0.3 replaced it: block
diagrams may now hold physical blocks, and projects are saved as format version 3.
A project saved by 0.3 doesn't open in 0.2.

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
  - Physical blocks: see *Physical modelling* and *Known limitations (v0.3)*.
  - A Scope records scalar values only.
  - Each run has a fixed **10 s** timeout that can't be changed from the UI.
  - There is no limit on End time / Step, and a run with a very large number of steps
    may run out of memory or hit the timeout.
- **Projects:**
  - Saving `name.qsp` writes its results to `name_results/` next to it, as
    `netlist.npz` and `diagram.npz`, and deletes only result files that the project
    itself listed. Other files in the folder are left alone, but a file with one of
    those two names is overwritten.
  - Projects saved by 0.2 (format version 2) don't open in 0.1, and projects saved by
    0.3 (format version 3) don't open in 0.2. Projects from 0.1 and 0.2 open in 0.3 and
    are saved as version 3. (0.4 opens versions 1-3 and saves version 4.)
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
ngspice are marked `@pytest.mark.ngspice` and are skipped when it is missing. Tests
that need DSS-Python are marked `@pytest.mark.opendss` and are skipped without it
(`pytest -q -m opendss` runs only those). See
[CHANGELOG.md](CHANGELOG.md) for the release notes.
