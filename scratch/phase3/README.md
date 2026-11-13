# v0.3 spikes (scratch only)

Throw-away code for the Phase 3 week-1 spikes. Nothing in `app/`, `bridge/` or `core/`
imports it. Results are written up in the "Spike results" section of `CLAUDE.md`.

| File | What it shows |
|---|---|
| `ngspice_sync.py` | Scratch ctypes binding: `ngSpice_Init_Sync`, `GetVSRCData`/`GetISRCData`, `GetSyncData`, `SendData`, `ngSpice_SetBkpt`, `run_ticks` (ends a ticked run with `stop when`) |
| `spike1_external.py` | RC with `Vin in 0 dc 0 external`, 1 V step at 1 ms from a Python tick controller; wall time for 1 s at several Ts |
| `spike2_redo.py` | Sharp edges make ngspice reject steps; logs `GetSyncData`; SendData-gated vs naive controller |
| `spike3_child.py` | Spike 1 in a spawned `multiprocessing` child (like `BlockRunner`), second child, terminated child |
| `spike4_msd.py` | Mass-spring-damper as C = m, R = 1/b, L = 1/k vs the analytic step response |
| `spike5_locale.py` | de_DE locale with and without `setlocale(LC_NUMERIC, "C")` |
| `spike6_pwm.py` ... `spike6e_pwm.py` | M4: 20 kHz PWM edges in plain ngspice (held duty vs sawtooth `pulse`): comparator forms, RC filter, curvature precursor, `S` switch, capped max step |
| `spike6f_pwm_cosim.py` | M4: time points around a PWM edge in a combined run with the `Crossing` breakpoint hint |
| `spike7_pi_loop.py` | M4: peak speed of the DC-motor PI loop for each anti-windup mode (`python -m scratch.phase3.spike7_pi_loop clamping`) |
| `fresh.py` | Runs a function in a fresh spawned process (ngspice is global per process) |

Run from the repository root (needs ngspice, see `third_party/ngspice/README.md`):

```bash
pytest -q -s scratch/phase3
python scratch/phase3/spike1_external.py 100 10      # Ts in us; no args = 100 50 20 10
python scratch/phase3/spike2_redo.py
python scratch/phase3/spike3_child.py
python scratch/phase3/spike4_msd.py
python scratch/phase3/spike5_locale.py
```

The CI job `spikes` runs all of the above on Windows, macOS and Linux.
