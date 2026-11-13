# QtSpice (Python) - project memory

## Status
- v0.1.0 released: netlist editor, ngspice simulation, waveform viewer, Python function
  blocks, block diagrams, .qsp projects.
- v0.2.0 released: LTspice-style improvements only (.lib/.asy reading, schematic editor,
  circuit symbols, multi-pane waveform window with LTspice expressions).
- We are now building v0.3 on the `phase-3` branch: Simscape-style physical modelling.
  Every v0.1 and v0.2 test must keep passing.

## What v0.3 adds (only these - nothing else)
Basic level:
- F1-F6 Foundation: four physical domains, typed conserving ports, PhysicalComponent API,
  physical wires, network extraction and checks, network -> Circuit mapping.
- C1-C5 Combined run: ngspice external sources + sync, CoSimulator with a fixed
  controller tick Ts, physical network as one stateful block, runs in BlockRunner,
  merged results.
- B1-B5 Library: electrical, rotational, translational, thermal basics; references;
  signal-controlled sources; sensors; Solver Configuration block.
- U1-U4 UI: domain ports and wires, library groups and unit fields, one Run button,
  unit labels in the waveform window.
Mid level:
- M1 cross-domain parts (DC motor, gearbox, transformer, battery, half/H-bridge,
  parts with a thermal port), M2 Circuit block (v0.2 symbol as physical block),
  M3 averaged vs switching bridges, M4 PWM on the physical side + discrete PI/PID,
  M5 user-defined physical components, M6 initial conditions / start from steady
  state, M7 parameter sweep.
Project: O1 .qsp format version 3, O2 samples.

OUT OF SCOPE for v0.3 - do not build, even if it looks easy:
our own DAE/equation solver or a Simscape-like equation language; hydraulic, gas,
thermal-liquid or magnetic domains; 3D multibody; real-time runs, hardware-in-the-loop,
code generation; linearization or frequency analysis of mixed models; controllers with a
variable step or several sample rates; physical blocks inside the v0.2 schematic editor;
Ts below 10 us. Everything that was out of scope in v0.2 stays out.
If a task seems to need one of these, stop and ask.

## Stack
Python 3.12, PySide6 6.8 (Qt Quick / QML, QtGraphs), NumPy, ngspice shared library
via ctypes, graphlib, multiprocessing, pytest, pytest-qt, ruff, PyInstaller.
Do not add PySpice as a dependency (GPLv3). Reading its source for reference is fine;
never copy code from it.

## Architecture rules (always follow)
1. Four layers: ui/qml -> bridge -> core -> external. A layer only calls the one below.
2. core/ never imports PySide6 or any Qt module.
3. QML binds only to bridge objects registered with @QmlElement / @QmlSingleton:
   Simulator, WaveformModel, DiagramController, ProjectManager, SchematicController,
   WaveformController. Never setContextProperty.
4. Only core/sim/ngspice_adapter.py knows the ngspice API. Load libngspice lazily with
   ctypes; if missing, available() returns False and the app still starts.
   Call setlocale(LC_NUMERIC, "C") before loading any circuit.
5. Plain netlist and schematic runs: ngspice on a QThread (SimulationWorker).
   Block diagrams, including combined physical runs: in the BlockRunner child process,
   which loads NgspiceAdapter itself. User code never runs in the UI process.
6. Parsers and checkers never raise on bad input; they return results plus
   Diagnostic(line or block, severity, message).
7. All results use the same SimulationResult class.
8. A schematic is only another way to make a Circuit (v0.2 rule, unchanged).
9. Waveform expressions: Python ast + whitelist, never eval/exec (v0.2 rule, unchanged).
10. Every user edit goes through that editor's own QUndoStack.
11. NEW: Physical networks are ALWAYS solved by mapping them to a Circuit with
    NetworkMapper and running ngspice. Never write a second solver, and never integrate
    physical equations in Python.
12. NEW: The across/through -> voltage/current mapping lives ONLY in
    core/physical/domains.py. Other files ask the Domain registry; they never hard-code
    "C = J" or similar.
13. NEW: Built-in physical components are written with the same PhysicalComponent API
    that users get in M5. No private shortcuts.
14. NEW: A physical network is one stateful block in the v0.1 engine: its outputs are
    the sensor values from the previous tick. Do not change the algebraic-loop rule.

## Physical domains (follow exactly)
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

## Combined run (follow exactly)
- Converter inputs become ngspice sources declared `external`; values come from the
  GetVSRCData / GetISRCData callbacks registered with ngSpice_Init_Sync.
- A breakpoint is set at every tick k*Ts (ngSpice_SetBkpt). At each accepted tick:
  read sensors -> run signal blocks once -> set external source values.
- Source values are held until the next tick (zero-order hold, one sample of delay).
- Values from steps that ngspice rejects and redoes must never reach the controller
  (use the GetSyncData redo information).
- Physical vectors are named <component>.<port>.<quantity>, e.g. motor.shaft.w, in SI.
- PWM edges are generated inside ngspice (M4), not by the Python controller.

## Folder layout (v0.3 additions marked +)
app/__main__.py
core/netlist/     parser.py model.py validate.py builder.py libindex.py
core/sim/         backend.py ngspice_adapter.py result.py
core/blocks/      decorator.py spec.py registry.py diagram.py engine.py runner.py library/
core/project/     io.py schema.json
core/symbols/     asy.py library.py builtin/
core/schematic/   model.py connectivity.py netlister.py
core/hierarchy/   symbolgen.py
core/waveform/    layout.py expr.py plotdefs.py
+ core/physical/  domains.py component.py network.py mapper.py cosim.py
+                 circuit_block.py registry.py user.py library/{electrical,rotational,
+                 translational,thermal,converters,composite}/
bridge/           (v0.2 files; diagram_controller.py extended)
ui/qml/           (v0.2 files) + ComponentEditor.qml; Port/Wire/BlockLibrary/
                  PropertiesPanel extended
tests/core/physical/  tests/fixtures/physical/
samples/          + dc_motor_speed.qsp heatsink_warmup.qsp battery_discharge.qsp
third_party/ngspice/

## Project file
- `.qsp` format version 3 adds physical blocks, physical wires, Solver Configuration
  settings and the display-unit setting. Versions 1 and 2 must still open.
- Results stay in `name_results/`; only the project's own listed files are deleted.

## Commands
pip install -e ".[dev]"
python -m app
pytest -q
pytest -q -m "ngspice"          # tests that need libngspice
ruff check . && ruff format --check .

## Conventions
- Type hints everywhere; dataclasses for data; no global mutable state.
- Every core module has tests under tests/core/ mirroring its path.
- Physical components need a test against an analytic answer (time constant,
  natural frequency, steady state), not just "it runs".
- Tests that need ngspice are marked @pytest.mark.ngspice and skip if it is missing.
- One task ID (e.g. F3, C2, M1) per commit; message "<ID> <area>: <what>".
- Before saying a task is done: ruff and pytest must both pass.
- For ngspice API details read third_party/ngspice/sharedspice.h; do not guess.
- If a requirement is unclear, ask instead of inventing behaviour.

## Kept from earlier versions (do not change unless a task says so)
- LTspice compatibility rules from v0.2 (symbols, probing, waveform window, expression
  syntax: ^ is XOR, log() is natural log, V(a,b) = V(a) - V(b)).
- Python signal blocks use fixed-step forward Euler.
- First netlist line is the title. One plain simulation runs at a time.
- Windows pytest may print "Windows fatal exception: access violation" when a
  block-runner child starts. Tests pass; the app does not enable faulthandler.
  Do not try to "fix" this.

## Spike results
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

Note : wheh you do the commit add 
Authored by and Co-Authored-By: Abhilash Poshanagari <abhi.sree762@gmail.com>