# Test fixtures (v0.2)

Small files used by the v0.2 tests. Placeholders are hand-written; files marked
*real* are copies of third-party files supplied by the project owner. We never ship
Analog Devices' own `.asy`/`.lib` files here.

## asy/
| File | Purpose |
|------|---------|
| `res.asy` | 2-pin resistor: LINE, WINDOW, SYMATTR, PIN/PINATTR basics. |
| `cap.asy` | 2-pin capacitor. |
| `opamp5.asy` | 5 pins drawn in the order OUT, In+, V+, In-, V- but `SpiceOrder` 1-5 = In+, In-, V+, V-, OUT. Tests that netlist pin order comes from `SpiceOrder`. Also uses CIRCLE, RECTANGLE, ARC, TEXT and `Prefix X` + `ModelFile`. |
| `micro_cp1252.asy` | Windows-1252 with CR+LF; `Value 4.7` followed by byte 0xB5 (micro sign). Do not re-save in an editor. |
| `cmosn.asy`, `cmosp.asy` | *real* (TSMC 180 nm course kit). 4-pin MOSFETs, `Prefix MN`/`MP` (not X), `ModelFile tsmc018.lib`. |

## lib/
| File | Purpose |
|------|---------|
| `opamp.lib` | One `.subckt opamp5` matching `opamp5.asy` pin order. |
| `corner.lib` | `.lib tt` / `ff` / `ss` sections closed by `.endl`. |
| `param_in_subckt.lib` | `.param` lines inside a `.subckt`, one depending on another. |
| `encrypted.lib` | `* Begin:` ... `* End` hex block; must be detected and reported, never decrypted. |
| `tsmc018.lib` | *real* (TSMC 180 nm course kit). BSIM3 `LEVEL=49 VERSION=3.1` `.MODEL CMOSN`/`CMOSP`. |

## schematics/
| File | Purpose |
|------|---------|
| `AC_RC_filter.asc` | *real* LTspice schematic, reference only (`.asc` import is out of scope for v0.2). RC low-pass with built-in `voltage`/`cap`/`res`, `M90` rotation, `Value2 AC 1`, `.1` + 0xB5 value, `.ac` directive. |
| `rc/rc.json`, `rc/rc.cir` | The same RC filter hand-transcribed into a QtSpice schematic (`Schematic.to_dict()` form, built-in symbols) and its hand-written netlist; S3 checks they give the same Circuit and the same ngspice result. Two deliberate differences from the .asc: our built-in `res` has its pins at (16,0)/(16,96) instead of (16,16)/(16,96), so R2 sits at (144,96) and one wire ends at x=240; and the `.ac` card is `dec 100` (the .asc says `dec 100m`). |

## v01/
| File | Purpose |
|------|---------|
| `rc_filter.qsp`, `diode_rectifier.qsp`, `first_order_feedback.qsp` | The v0.1.0 sample projects exactly as released (`git show v0.1.0:examples/<name>.qsp`), format version 1. The release check opens, saves (as version 2) and reopens them. |
