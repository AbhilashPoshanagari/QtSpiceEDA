# Feeder files of `ieee13_powerflow.qsp`

The IEEE 13 bus test feeder, as distributed with OpenDSS. These files are byte-exact
copies of `tests/fixtures/grid/` and must stay that way (CR+LF, see `.gitattributes`):

| File | From |
|---|---|
| `ieee13/IEEE13Nodeckt.dss` | the feeder; ends in `Solve` and loads the bus coordinates |
| `ieee13/IEEELineCodes.DSS` | `redirect ../IEEELineCodes.DSS` |
| `ieee13/IEEE13Node_BusXY.csv` | bus coordinates for the single-line view |
| `IEEELineCodes.DSS` | the shared IEEE line codes |
| `LICENSE` | BSD-3 license of the files |

Source: DSS-Extensions `electricdss-tst`, commit
3b208397160213cae4a9e2d0a7d1aa3528ce26e1, folder `Version8/Distrib/IEEETestCases/`.
Copyright EPRI and the DSS-Extensions contributors; see `LICENSE`.

QtSpice does not read these files itself: OpenDSS compiles them, and QtSpice reads
the circuit back through the OpenDSS API.
