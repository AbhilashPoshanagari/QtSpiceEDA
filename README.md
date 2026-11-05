# QtSpice 0.1

QtSpice is a small desktop EDA application written in Python with Qt Quick. You can:

- type, paste, open or drag in a SPICE netlist (`.cir`, `.net`, `.sp`) and see syntax
  and connectivity errors on the exact line;
- simulate it with ngspice (`.op`, `.dc`, `.ac`, `.tran`) in the background;
- look at the results in a waveform viewer with zoom/pan, two cursors with delta, a
  log frequency axis for AC, and CSV export;
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

`examples/` holds three projects. Open them with **File → Open…**:

- **`rc_filter.qsp`**: an RC low-pass, R = 1k and C = 1u. **Run** does the `.tran`: the
  step response reaches 63 % at 1 ms. To get the Bode plot, choose `.ac` in the Analysis
  panel and run again: it is −3 dB at 159 Hz. Put cursors A and B on the curve to read
  the values.
- **`diode_rectifier.qsp`**: a 5 V, 50 Hz half-wave rectifier with RC smoothing. Plot
  `V(in)` and `V(out)`: the output sits about one diode drop below the peak, with about
  0.4 V of ripple.
- **`first_order_feedback.qsp`**: open the **Blocks** tab. A step goes into a loop of
  Sum (`+-`), Gain (1/τ = 2) and Integrator, with the output fed back to the Sum. **Run**
  plots `r` and `y` in the Waveforms tab. `y` follows 1 − e^(−(t−0.5)/τ), with τ = 0.5 s.

`examples/rc_lowpass.cir` is the same RC filter as a plain netlist.

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
- `.param k=v ...` (top level only)
- `.op`, `.dc src start stop step [src2 start2 stop2 step2]`,
  `.ac dec|oct|lin points fstart fstop`, `.tran tstep tstop [tstart [tmax]] [uic]`
- `.end`: anything after it is ignored

Other element letters (A, B, J, K, T, …) and other cards (`.lib`, `.options`, `.ic`,
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

## Known limitations (v0.1)

- **No schematic capture.** Circuits are netlists only.
- **Analyses:** `.op`, `.dc`, `.ac` and `.tran` only, one per run. No noise, `.tf`,
  `.sens`, `.four` or `.meas`, and no `.control` scripts.
- **Netlist syntax:**
  - No `.lib`.
  - No `.param` inside `.subckt`.
  - No nested `.subckt`.
  - Element types other than those in the table above are skipped.
  - The first line is always the title. A netlist that starts with an element loses
    that element, and the error then points at another line.
- **Checks happen partly at run time.** Some errors are only found by ngspice when it
  runs, for example voltage sources in parallel, duplicate `.model` or `.subckt` names,
  or a node with no DC path to ground. These errors appear in the Console and the
  status line, not on the netlist line.
- **Block diagrams:**
  - Fixed-step forward Euler only (no variable-step solver).
  - No physical/acausal blocks.
  - Scope records scalar values only.
  - Each run has a fixed **10 s** timeout that can't be changed from the UI.
  - There is no limit on End time / Step, and a run with a very large number of steps
    may run out of memory or hit the timeout.
- **Projects:**
  - A project keeps one netlist and one diagram in the editors.
  - Saving `name.qsp` manages the folder `name_results/` next to it, and removes `.npz`
    files in it that are not results of this project. Don't keep other files there.
  - The samples live in the app folder. Use **Save As…** to keep your own copy.
- **Simulator:** QtSpice has no SPICE engine of its own. Accuracy, convergence and
  device models are ngspice's, and only one simulation runs at a time.
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
