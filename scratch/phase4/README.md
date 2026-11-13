# v0.4 spikes (scratch only)

Throw-away code for the Phase 4 week-1 spikes (S1-S6): DSS-Python / OpenDSS. Nothing
in `app/`, `bridge/` or `core/` imports it. Results are written up in the
"Spike results (v0.4)" section of `CLAUDE.md`.

Needs the grid extra: `pip install -e ".[dev,grid]"` (dss-python 0.15.x).

| File | What it shows |
|---|---|
| `common.py` | Feeder cases (IEEE 13/34/123 from `tests/fixtures/grid/`), `new_context()` (own DSS context, `AllowChangeDir=False`), compile + solve, reading the solution, reference-file helpers |
| `make_reference.py` | Writes `tests/fixtures/grid/<feeder>/opendss_reference.json`: the OpenDSS result of the original files. Re-run only on purpose |
| `spike1_install.py` | S1: versions; IEEE 13 converged, iterations, total power, V at bus 671 |
| `spike2_contexts.py` | S2: two contexts (IEEE 13 + IEEE 34) do not affect each other; `compile` moves the process cwd unless `AllowChangeDir=False`; IEEE 13 in a spawn + Pipe + daemon child (BlockRunner pattern); a child terminated during IEEE 123 solves |
| `spike3_pyinstaller/` | S3: `app.py` (10 lines: compile IEEE 13, print 671.1), `build.py` (one-file builds: plain, `--collect-binaries`, `--hidden-import _cffi_backend`, our hook; runs each exe and lists the bundled native libraries), `hooks/hook-dss_python_backend.py` |
| `spike4_api.py` | S4: every element of a feeder read back through the API; per value, typed interface or `ActiveDSSElement.Properties()`; unit codes, switch, taps before/after the solve, bus coordinates, voltage bases (`python scratch/phase4/spike4_api.py ieee34`) |

Run from the repository root:

```bash
pytest -q -rs -s scratch/phase4
python scratch/phase4/spike1_install.py
python scratch/phase4/spike2_contexts.py
python scratch/phase4/spike3_pyinstaller/build.py          # all variants, ~1 min each
python scratch/phase4/spike4_api.py
```

The CI job `spikes-grid` runs all of the above on Windows, macOS and Linux.
