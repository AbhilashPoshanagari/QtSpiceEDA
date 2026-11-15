# S4 spike: engine checks for designs (v0.5 Grid Network Designer)

Questions: how OpenDSS handles ratings, derived voltage bases, sequence impedances,
loading quantities and incomplete designs, so D2 (draft model), D7 (feasibility) and
validation can be specified from measured behaviour.

Measured on Windows 11, Python 3.12.2, dss-python 0.15.7, "DSS C-API Library version
0.14.5 revision 87d85c2... based on OpenDSS SVN 3723 [FPC 3.2.2] (64-bit build)".
Property names come from the dss/I*.py docstrings of that package.

Scratch only: nothing in app/, bridge/, core/ or ui/ was changed or imports this.
The one file outside scratch/ is the generated fixture
`tests/fixtures/grid/sequence_reference.json`.

## Files

| file | what |
|---|---|
| `common.py` | new DSS contexts (AllowChangeDir/AllowForms off), feeders in their reference state (`tests/core/grid/helpers.AFTER_COMPILE`), `Properties()` helper |
| `spike4a_amps.py` | a) NormAmps/EmergAmps: typed vs Properties, feeder values, `save circuit`, property order |
| `spike4b_voltage_bases.py` | b) writer.py with derived VoltageBases, per-bus kVBase and solution against the original |
| `../make_sequence_reference.py` | c) writes `tests/fixtures/grid/sequence_reference.json` (inputs + OpenDSS matrices) |
| `sequence.py` | c) textbook sequence -> phase conversion (prototype of core/grid/sequence.py) |
| `spike4c_sequence.py` | c) textbook vs fixture |
| `spike4d_loading.py` | d) loading API calls, comparison with PDElements.AllPctNorm, signs |
| `spike4e_compile_errors.py` | e) what OpenDSS reports for incomplete or wrong designs; writer with a None field |
| `run_all.py` | all of it -> `results.json` (about 3 s) |
| `test_spike4.py` | `pytest -q scratch/phase5/spike4_engine`: pins every claim below (11 tests) |

OpenDSS runs in-process in these scripts (spike only; the app keeps rule 5).

## a) NormAmps / EmergAmps

**Typed, both classes.** `Lines.NormAmps`, `Lines.EmergAmps`, `LineCodes.NormAmps`,
`LineCodes.EmergAmps` (and `CktElement.NormalAmps` / `EmergAmps` for any PD element)
are typed in 0.15.7. On every line and linecode of the three feeders the typed value
equals `Properties("normamps"/"emergamps").Val` exactly. No Properties() fallback needed.

**Values.** No feeder file sets a rating. Every element has the OpenDSS defaults
**400 A / 600 A**:

| feeder | linecodes | lines (incl. switches) | values |
|---|---|---|---|
| IEEE 13 | 36 | 12 | all 400 / 600 |
| IEEE 34 | 29 | 32 | all 400 / 600 |
| IEEE 123 | 29 | 126 | all 400 / 600 |

So the IEEE loading % in v0.4 (e.g. IEEE 13 line 650632 at 147.9 %) is measured against
a default, not a rating from the file.

**Was it set in the file?** `ActiveDSSElement.ToJSON(0)` (API extension, no "Full"
flag) lists only the properties that were set. 0 of the 94 feeder linecodes list
`NormAmps`. A linecode given `normamps=250` lists it, and one without it does not. For
lines the test does not help: `linecode=` copies the code's rating, so it shows as set
(11/12, 32/32, 118/126; the rest are switches without a linecode). That makes it possible
to tell "no rating" from "400 A" on linecodes read from a file.

**save circuit.** Yes, ratings survive it. Saved, recompiled and read back, all values
are the same on all three feeders. This holds for the defaults and for non-default
values typed on a linecode, on a line, on a line with no linecode, and set through
the API (`Lines.NormAmps = 123`).

**Property order (new).** `linecode=` resets the line's ratings to the linecode's:
`normamps=111 ... linecode=lc` ends with 250 (the linecode's), while
`linecode=lc normamps=111` keeps 111. This is the same kind of issue as Units in v0.4 S4.
The writer must emit a line's `normamps=`/`emergamps=` **after** `linecode=`.

## b) Derived voltage bases

Method: feeder -> `OpenDSSEngine.extract_model()` -> `voltage_bases` replaced by a
derived set -> `OpenDSSEngine.load_model()` (writer.py, then `Set VoltageBases=[...]`
and `CalcVoltageBases`) -> solve. Compared bus by bus (`ActiveBus.kVBase`) with the
original file, and node by node with `opendss_reference.json`. "Same solve" means the
original file solved the same way (Tolerance 1e-10, like the G3 round trip).

"The sorted set of source kV and every transformer winding kV (LL)" can be read in
more than one way, because a 1-phase wye winding stores LN kV (regulators: 2.4, 14.376,
2.402). Variants measured:

| variant | IEEE 13 | IEEE 34 | IEEE 123 |
|---|---|---|---|
| file | 115, 4.16, 0.48 | 69, 24.9, 4.16, 0.48 | 4.16, 0.48 |
| as_stored (1-ph kV as stored) | 0.48, 2.4, 4.16, 115 | 4.16, 14.376, 24.9, 69 | 0.48, 2.402, 4.16 |
| ll (1-ph wye x sqrt 3) | 0.48, 4.1569, 4.16, 115 | 4.16, 24.89996, 24.9, 69 | 0.48, 4.16, **4.16039** |
| three_phase_only | 0.48, 4.16, 115 | 4.16, 24.9, 69 | 0.48, 4.16 |
| ll_3ph_first (1-ph x sqrt 3 only if no value within 1 %) | 0.48, 4.16, 115 | 4.16, 24.9, 69 | 0.48, 4.16 |

Buses with a different kVBase from the file, and the largest pu difference:

| variant | IEEE 13 (16 buses) | IEEE 34 (37) | IEEE 123 (132) |
|---|---|---|---|
| as_stored | 0, 2.2e-9 | 0, 7.5e-12 | 0, 2.6e-9 |
| ll | 0, 2.2e-9 | 0, 7.5e-12 | **130, 9.6e-5** |
| ll_merged (keep the larger of close values) | 0 | 0 | **131, 9.6e-5** |
| three_phase_only | 0, 2.2e-9 | 0, 7.5e-12 | 0, 2.6e-9 |
| ll_3ph_first | 0, 2.2e-9 | 0, 7.5e-12 | 0, 2.6e-9 |

(pu difference = largest difference from the original solved the same way.)

- **Same kVBase on every bus: yes**, for as_stored, three_phase_only and ll_3ph_first on
  all three feeders. The bus count is the same as the file's, and the 0.48 kV base that
  IEEE 34 lists but no bus uses is not needed.
- **Trap:** on IEEE 123, converting 1-phase regulator windings to LL gives 4.16039.
  `CalcVoltageBases` picks 4.16039 instead of 4.16 for 130 buses. The solution in volts
  is identical (largest relative difference from the reference 1.37e-5, the same as the
  file's), but pu moves by up to 1e-4. On IEEE 13 the near-duplicate 4.1569 happens to be
  below 4.16 and is never picked. Whether a duplicate is harmless depends on the feeder,
  so near-duplicates must not reach the base list.
- **Within 1e-6 pu of opendss_reference.json: no, and the bases are not the reason.** The
  largest differences are 1.37e-5 (IEEE 13), 4.97e-6 (IEEE 34) and 9.97e-6 (IEEE 123).
  The original file itself, solved again at Tolerance 1e-10, differs from its reference by
  the same amount (1.37e-5 / 4.97e-6 / 9.97e-6): the reference is a default-tolerance
  solve (v0.4 S2). Against the original solved the same way, the design is within
  **2.6e-9 pu**, and losses are within 1.2e-4 relative. As in G3, the D2 test must compare
  against the original solved the same way, or allow 1e-4 against the reference file.
  Regulator taps are the same as in the original in every run.

## c) Sequence impedances

`make_sequence_reference.py`: the linecodes of the three feeders are all given as
matrices. Their `LineCodes.R1/X1/...` read back as the OpenDSS defaults (0.058, 0.1206,
...), not values from the file. So one realistic input set per linecode was taken from its
own matrix (zs = mean diagonal, zm = mean off-diagonal, z1 = zs - zm, z0 = zs + 2 zm), in
its own unit (mi, kft, none). That gives **26 distinct input sets, 78 cases** (1, 2 and 3
phases). Each case is defined three ways: as a `Linecode` from r1 x1 r0 x0 c1 c0 + units,
as a `Line` with the same values and no linecode, and as a `Line` that uses that linecode.
All are read back after one Solve.

**Important:** the matrices of a `Line` are updated only by a solve. Read before a
solve, `Lines.Rmatrix` still shows the default r1/x1 matrices, even though `Lines.R1`
already reads the new value. (`Yprim` does not trigger the update.) Linecodes read back
correctly without a solve. Units are kept as given in every case.

Textbook: self = (2 s1 + s0) / 3, mutual = (s0 - s1) / 3, for r, x and c.
Largest difference (all matrix entries, all cases):

| defined as | n = 1 | n = 2 | n = 3 |
|---|---|---|---|
| Linecode (LineCodes.*matrix) | 5.7e-14 abs / 3.8e-16 rel | same | same |
| Line with r1 ... (Lines.*matrix) | **0.67 abs / 84 % rel** | 5.7e-14 / 3.8e-16 | same |
| Line using the seq. linecode | **0.67 / 84 %** | 5.7e-14 / 3.8e-16 | same |

**What OpenDSS does for 1 and 2 phases:**
- 2 phases: the textbook rule (the same formula as 3 phases, a 2 x 2 matrix).
- 1 phase, **LineCode**: `LineCodes.Rmatrix` reports the textbook (2 z1 + z0) / 3.
- 1 phase, **Line** (given r1 ... directly, *or* using a 1-phase sequence linecode):
  the line uses **z1 only** (self = r1 + j x1, c = c1), and matches that to 2e-16.
  Confirmed with Yprim: 1/Y11 = 0.2 + j0.4 = z1, not (2 z1 + z0) / 3 = 0.333 + j0.667.
  So for a 1-phase line, the linecode's own reported matrix is **not** what the solve uses.

Consequences for D2:
- `sequence.py` needs the phase count and must follow the Line behaviour: textbook for
  n >= 2, z1 only for n = 1. The fixture contains both, so this is tested.
- The writer writes linecodes as matrices, so a design is never affected by this. A .dss
  file with a 1-phase sequence linecode is read correctly by extract.py, because it reads
  `Lines.*matrix` after the solve. The linecode's own matrix (textbook) then differs from
  the line's, so `_linecode_for` creates a `_<line>` code. That is correct, but worth
  knowing.
- No conversion of units was needed here: inputs and matrices share the linecode's unit.
  D2 tests convert the fixture with units.py (rule 17).

## d) Loading: API calls and signs

| quantity | call |
|---|---|
| per-phase current, terminal 1 | `CktElement.Currents` (complex, into each conductor of each terminal; terminal k = entries [k*NumConductors, (k+1)*NumConductors), the first NumPhases are the phases) |
| line rating | `Lines.NormAmps` (= `CktElement.NormalAmps`) |
| per-winding terminal power | `CktElement.Powers` (kW + j kvar into each conductor), summed over the conductors of terminal k (transformer: NumConductors = phases + 1) |
| transformer rating | `Transformers.Wdg = 1; Transformers.kVA` |
| OpenDSS's own % (cross-check) | `PDElements.AllPctNorm()` (first terminal, max conductor current / NormalAmps) |

- **Line loading** = max |I phase, terminal 1| / NormAmps. It equals `AllPctNorm` to
  **2.8e-14 %** on all 12 + 32 + 126 lines.
- **Transformer:** OpenDSS's `NormalAmps` come from `NormHkVA`, which defaults to
  **1.1 x kVA** (5000 -> 5500 kVA, checked on all 21 transformers). So `AllPctNorm` and
  the v0.4 `BranchResult.loading_pct` of a transformer are current / (1.1 x rated
  current), **not** the CLAUDE.md |S| / kVA. IEEE 13 values:

  | transformer | max_k \|S_k\| / kVA | max I1 / rated I1 | OpenDSS AllPctNorm |
  |---|---|---|---|
  | sub (5000 kVA) | 79.3 % | 88.8 % | 80.7 % |
  | xfm1 (500 kVA) | 100.9 % | 118.0 % | 107.3 % |
  | reg1 (1666 kVA, 1 ph) | 85.7 % | 85.6 % | 77.8 % |

  The |S| and current definitions differ by the voltage (pu) and by unbalance (|S| is the
  sum over phases, current is the worst phase). D7 must compute transformer loading
  itself from `Powers` and kVA, and must not use `NormalAmps`/`loading_pct`.
- `Transformers.WdgCurrents` returns 2 complex values per phase per winding (12 for 3
  phases, 2 windings; 4 for 1 phase). That is both ends of each winding, not one value per
  terminal. Use `Currents` / `Powers` slices instead.

**Signs** (checked against CLAUDE.md "Grid model": all agree):
- Line: P into terminal 1 > 0 and P into terminal 2 < 0 on every line that carries
  power (all of IEEE 13/34; IEEE 123: all except 4 switches with zero flow: l32, l51,
  sw7, sw8). I1 + I2 ~ 0 (charging current only): both terminals use "into".
  So "branch power positive into terminal 1" is the OpenDSS sign as is.
- Transformer: winding 1 P > 0, winding 2 P < 0 (sub: +3567.05 / -3567.02 kW).
- Source: `Vsource.source` Powers = -3567.05 kW = `Circuit.TotalPower`: negative =
  supplied. GridResult flips it (already done in engine.py).
- Load: +1155 kW / +660 kvar (load.671), positive = consumed.
- Capacitor: Q = -593.5 kvar into the terminal (delivers vars).

## e) What compile reports for incomplete designs

13 small designs (12.47 kV source -> line -> load, one defect each), run one command at
a time (as `load_model`) and as one `compile`:

| defect | OpenDSS reports | what it assumes | solve |
|---|---|---|---|
| line names a missing linecode | **error (#401)** `Line.l1.LineCode: LineCode object "nosuch" not found.` (compile adds `[file: ..., line: 5]`) | command by command: the line stays, with default r1 = 0.058 ... | converges, min 0.744 pu |
| load without kW (and kvar) | nothing | kW = 10, PF 0.88 (kvar 5.4) | converges |
| load without kW, with kvar | nothing | kW = 10, kvar 200 (PF 0.05) | converges |
| load without kV | nothing | kV = 12.47 | converges |
| linecode with no impedance | nothing | r1 0.058, x1 0.1206, ..., normamps 400 | converges |
| line with no linecode, no impedance | nothing | r1 0.058, x1 0.1206, c1 3.4 | converges, 0.744 pu |
| line phases=1 with a 3-phase linecode | nothing | **linecode= sets phases = 3** (written order: phases first) | converges |
| transformer without kVA / kV | nothing | 1000 kVA, 12.47 / 12.47 kV, %R 0.2, Xhl 7 | converges, 0.31 pu |
| capacitor without kvar | nothing | 1200 kvar | converges |
| PV without Pmpp / kVA | nothing | Pmpp 500, kVA 500, irradiance 1 | converges |
| load on node 3 of a bus that has only node 1 | nothing | node b2.3 created, 0 V, load draws 0 | converges |
| island (load on an unconnected bus) | nothing | nodes at 0 V | converges |
| line joining 12.47 kV and 0.48 kV zones | nothing | - | converges, 0.65 pu |

After a failed `compile` the circuit exists anyway (`ActiveCircuit.Name` = "d"), because
it was defined before the bad line.

**Conclusion: OpenDSS catches only a missing linecode.** Every other draft gap gets a
silent default, and every structural error (phases, islands, zones) converges without a
message. All of them must be caught by our validation (validate.py on topology.py)
before the writer runs. The writer must never be the place where a gap is noticed.

**writer.py today:** `write_dss` on a model with `Load.kw = None` **raises**
`TypeError: float() argument must be a string or a real number, not 'NoneType'`
(`num()` handles only NaN/inf as `_BadValue`). An empty linecode matrix is already
reported as a Diagnostic. D2 must turn None into an error Diagnostic (rule 6), and
`write()` returns (None, diagnostics).

## Decisions proposed for D2 / D7

1. Ratings: read typed. The writer emits line `normamps`/`emergamps` after `linecode=`.
   For extracted linecodes, `ToJSON(0)` without `NormAmps` -> `norm_amps = None` ("no
   rating" info), so IEEE feeders are not judged against 400 A. Needs your approval,
   because it changes what the v0.4 view shows.
2. Voltage bases: ll_3ph_first (source + 3-phase windings as LL, a 1-phase winding
   x sqrt 3 for wye only when no base is within 1 %). It matches the file on all three
   feeders and keeps single-phase-only zones.
3. sequence.py: textbook for n >= 2, z1 only for n = 1 (OpenDSS Line behaviour). Tested
   against sequence_reference.json "line" matrices.
4. Transformer loading from `Powers` slices / kVA in analysis.py. Line loading from
   `Currents` terminal 1 / norm_amps (equal to AllPctNorm).
5. Validation must catch every defect in the table above except the missing linecode
   (which OpenDSS also reports) before writing.
