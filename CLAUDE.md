# QtSpice (Python) - project memory

## What we are building
A desktop EDA application in Python. v0.1 has exactly these features:
- F1 Netlist input: type, paste, open or drag in .cir / .net / .sp files
- F2 Netlist checking: syntax and connectivity errors shown on the exact line
- F3 Simulation: .op .dc .ac .tran through the ngspice shared library, in the background
- F4 Waveforms: trace list, zoom/pan, two cursors with delta, log axis for AC, CSV export
- F5 Function blocks: a Python function with @block becomes a block whose ports
  match its signature
- F6 Block diagrams: place, wire and run blocks with a fixed time step; Scope blocks
  plot into the waveform viewer
- F7 Projects: save/load one .qsp JSON file plus .npz result files

OUT OF SCOPE for v0.1 - do not build: schematic capture, noise/TF analyses,
variable-step block solver, physical (Simscape-like) blocks, code generation,
plug-ins, our own SPICE engine.

## Stack
Python 3.12, PySide6 6.8 (Qt Quick / QML, QtGraphs), NumPy,
ngspice shared library via ctypes, graphlib, multiprocessing,
pytest, pytest-qt, ruff, PyInstaller.

## Architecture rules (always follow)
1. Four layers: ui/qml -> bridge -> core -> external. A layer only calls the one below.
2. core/ never imports PySide6 or any Qt module.
3. QML never calls core. It binds only to four bridge objects: Simulator,
   WaveformModel, DiagramController, ProjectManager. Register them with
   @QmlElement / @QmlSingleton, not setContextProperty.
4. Only core/sim/ngspice_adapter.py knows the ngspice API. Load libngspice lazily with
   ctypes. If it is missing, available() returns False and the app still starts.
5. ngspice runs on a QThread (SimulationWorker). User block code runs in a separate
   process (BlockRunner) with a timeout and cancel.
6. NetlistParser never raises on bad input. It returns ParseResult(circuit, diagnostics).
7. ngspice results and block results use the same SimulationResult class.

## Folder layout
app/__main__.py                  entry point: QGuiApplication + Main.qml
core/netlist/   parser.py model.py validate.py builder.py
core/sim/       backend.py ngspice_adapter.py result.py
core/blocks/    decorator.py spec.py registry.py diagram.py engine.py runner.py library/
core/project/   io.py schema.json
bridge/         simulator.py sim_worker.py waveform_model.py
                diagram_controller.py project_manager.py highlighter.py
ui/qml/         Main.qml NetlistEditor.qml AnalysisPanel.qml WaveformView.qml
                DeckView.qml ConsoleView.qml BlockCanvas.qml Block.qml Port.qml
                Wire.qml BlockLibrary.qml FunctionEditor.qml PropertiesPanel.qml
tests/core/ tests/ui/ tests/netlists/
third_party/ngspice/             sharedspice.h + shared library per OS

## ngspice in this checkout
- Header: third_party/ngspice/include/sharedspice.h
- Windows library: third_party/ngspice/lib/ngspice.dll (64-bit, loads with ctypes)
- third_party/ngspice/README.md describes an older C++/QLibrary version of this app;
  ignore its C++ paths. The Python adapter searches lib/ and bin/ under
  third_party/ngspice/, then the system path.

## Commands
pip install -e ".[dev]"
python -m app
pytest -q
ruff check . && ruff format --check .

## Conventions
- Type hints everywhere; dataclasses for data; no global mutable state.
- Every core module has tests under tests/core/ mirroring its path.
- Tests that need ngspice are marked @pytest.mark.ngspice and skip if it is missing.
- One component per commit; message format "<area>: <what>".
- Before saying a task is done: ruff and pytest must both pass.
- For ngspice API details, read third_party/ngspice/include/sharedspice.h; do not guess.
- If a requirement is unclear, ask instead of inventing behaviour.
