# Grid fixtures (v0.4)

IEEE 13, 34 and 123 bus test feeders, copied unchanged from the DSS-Extensions
`electricdss-tst` repository (BSD-3, see `LICENSE` in this folder), commit
3b208397160213cae4a9e2d0a7d1aa3528ce26e1, folder `Version8/Distrib/IEEETestCases/`.
Byte-exact (CR+LF): never convert (see `.gitattributes`).

| Path | From |
|---|---|
| `IEEELineCodes.DSS` | `IEEETestCases/IEEELineCodes.DSS` (the feeders' own `IEEELineCodes.DSS` is `redirect ../IEEELineCodes.DSS`) |
| `ieee13/` | `13Bus/` (all files) |
| `ieee34/` | `34Bus/` (all files) |
| `ieee123/` | `123Bus/`: master, loads, regulators, switches, linecodes, bus coordinates, `Run_IEEE123Bus.DSS`. The load-shape, PV, wind and storage scripts were left out (QSTS is out of scope for v0.4) |
| `LICENSE` | `License.txt` |

`<feeder>/opendss_reference.json` is ours: the OpenDSS snapshot solution of the
original files, written by `scratch/phase4/make_reference.py` (versions and the
commands used are in its `about` block). Tests compare against it; never edit by hand.
How each feeder is run is in `scratch/phase4/common.py` (`CASES`): IEEE 13 is
`compile`d as is; IEEE 34 is `compile ieee34Mod1.dss` + `solve`; IEEE 123 is the first
script of `Run_IEEE123Bus.DSS` without the meter, show and plot commands.

`bad/` is ours too: small hand-written files with one deliberate mistake each
(a misspelled redirect, an unknown linecode), for the G1 error tests. They contain
no reference values.

`small/` is ours as well: tiny hand-written circuits for the G2 extractor tests
(`fuse_pv.dss`: a Fuse, which the model does not support, and a PVSystem). The values
the tests check are the ones written in the file itself; no reference results.

`<feeder>/reference/` holds the published IEEE PES test feeder package of that feeder
(report `.doc`, data `.xls`, `.tcw`), copied unchanged from `feeder13.zip`,
`feeder34.zip` and `feeder123.zip` at https://cmte.ieee.org/pes-testfeeders/resources/
(IEEE PES Test Feeder Working Group). `reference/published.json` is ours: the voltage
profile and regulator taps read from the report by
`scratch/phase4/make_published_reference.py` (antiword; the report's SHA-256 is in its
`about` block). The G5 benchmark (`tests/core/grid/test_benchmarks.py`) reads it; never
edit by hand.
