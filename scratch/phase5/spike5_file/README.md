# S5 spike: design file format (v0.5 Grid Network Designer)

Question: can a `.qgrid` design (GridModel + DiagramModel) be plain JSON written by a
plain dataclass-to-dict converter, read back *exactly*, fast enough for IEEE 123, with
rules that let later versions add fields without breaking old files?

Answer: **yes**. IEEE 13, 34 and 123 with a full diagram load back equal
(`load(save(m)) == m`), with **every float bit-identical** (1659 + 1316 floats on IEEE
123), the same dict key order, and the same bytes when saved again. A random 20 000-float
stress test (random bit patterns: subnormals, -0.0, 1e308, ...) also survives bit for bit.
IEEE 123 is 133 KB (indent 1); save and load each take under 45 ms. No new dependency
(json, dataclasses, typing).

Scratch only: nothing in app/, bridge/, core/ or ui/ was changed or imports this.
OpenDSS runs only in a child process (rule 5), to extract the models.

Measured on Windows 11 Enterprise 10.0.26100, 11th Gen Intel Core i7-1185G7, Python
3.12.2, dss-python 0.15.7. 20 repeats per timing, median; three runs of `bench.py`.

## Files

| file | what |
|---|---|
| `designfile.py` | Qt-free. `BusGlyph`, `ElementGlyph`, `DiagramModel`; `to_plain` (generic converter), `document`/`dumps`/`save`; `loads`/`load` (never raise) with the version and field rules; `float_mismatches` (bit-level compare incl. key order), `count_floats` |
| `feeders.py` | IEEE 13/34/123 extracted after one solve in a `ChildProcess` (as in S6); `diagram_from_coordinates` (S3 `scale_coordinates` + `place_missing`) |
| `bench.py` | sizes, exactness and timings -> `results.json` (last run), about 15 s |
| `test_spike5.py` | `pytest -q scratch/phase5/spike5_file`: 51 tests, about 4 s |

## What was built

**Writing.** `to_plain` turns any model value into JSON types: a dataclass becomes an
object with one key per dataclass field (ClassVars such as `cls` are not fields and
are not written); a tuple or list becomes an array; `complex` becomes `[re, im]`; a float
is written with Python's shortest round-trip repr. On top of that, two changes for
GridModel only:
- each class dict (`buses`, `lines`, ...) is written as an **array in dict order**, and
  the dict key is the element's `name`. Order is part of an exact model (S2: undo puts
  elements back where they were), and other tools (jq -S, editors) do not keep the key
  order of a JSON object;
- `base_frequency` is written **once, at the top level**, not again inside
  `grid_model`, so the two copies cannot disagree.

Matrices stay the model's flat row-major lists (`phases * phases` values); the loader
checks the length. A Terminal is `{"bus": "671", "nodes": [1, 2, 3]}`, not the string
"671.1.2.3", so reading needs no parsing. NaN and +-inf are not JSON: `save` refuses
them with an error Diagnostic and writes nothing. `save` writes `<name>.qgrid.tmp` and
renames it over the file, so a failed save never leaves half a design.

**Reading.** `loads` walks the JSON with the dataclass type hints
(`typing.get_type_hints`): float, int, bool, str, `X | None`, `tuple[T, ...]`, `list[T]`,
`dict[str, T]`, complex and nested dataclasses. An int where a float is expected is read
as a float (`50` -> `50.0`, for hand-edited files); anything else of the wrong JSON
type is an error. It never raises: every problem is a `NetworkDiagnostic` (the grid
Diagnostic class, rule 6) at the element id ("linecode.mtx601") or "file", with a code.

**Diagram.** One BusGlyph per bus (file coordinates scaled to a median branch of 6
units, S3; IEEE 123 300_open and 94_open placed by `place_missing`), one ElementGlyph per
source, line, switch, transformer, load, capacitor and PV system (branches at the
middle of their two buses, shunts below their bus). LineCodes and RegControls get no
glyph here (see "Open for D3").

## Results

Sizes are exact (they do not change between runs). Times: lowest - highest median of
the three runs (the machine was busy during the later two; IEEE 123 varied 2x).

| | IEEE 13 | IEEE 34 | IEEE 123 |
|---|---:|---:|---:|
| buses / element glyphs | 16 / 35 | 37 / 111 | 132 / 230 |
| floats in GridModel / DiagramModel | 440 / 188 | 932 / 555 | 1659 / 1316 |
| load(save(m)) == m, both models, compact / indent 1 / indent 2 | yes | yes | yes |
| floats or key order differing | 0 | 0 | 0 |
| same bytes when saved again | yes | yes | yes |
| Diagnostics on load | 0 | 0 | 0 |
| **file, indent 1 (proposed)** | **26.0 KB** | **64.1 KB** | **133.3 KB** |
| file, compact / indent 2 | 18.7 / 32.8 KB | 45.8 / 81.0 KB | 94.2 / 169.3 KB |
| of which diagram (indent 1) | 5.3 KB | 15.6 KB | 36.1 KB |
| gzip of indent 1 (for comparison only) | 2.9 KB | 5.0 KB | 8.9 KB |
| pickle (for comparison only) | 14.3 KB | 36.5 KB | 78.8 KB |
| to_plain (ms) | 0.6 | 1.6 | 3.9 - 9.7 |
| json.dumps, indent 1 (ms) | 1.1 - 1.2 | 2.8 - 3.3 | 6.5 - 18.7 |
| **save: to_plain + dumps + write + rename (ms)** | **7.6 - 8.6** | **10 - 13** | **17 - 43** |
| json.loads (ms) | 0.2 | 0.5 - 1.5 | 1.0 - 3.1 |
| loads: json.loads + type-checked rebuild (ms) | 1.5 - 2.6 | 3.8 - 13 | 21 - 24 |
| **load: read + loads (ms)** | **1.9 - 2.5** | **6.0 - 13** | **24 - 26** |

Notes on the numbers:
- Writing the file and renaming it takes about 5-25 ms on this machine (antivirus
  scan of a new file), more than serialising. Save runs on the UI thread in the product;
  under 50 ms for IEEE 123 needs no worker thread.
- The type-checked rebuild is 10-20x `json.loads`. It is not optimised (it calls
  `typing.get_origin` for every value); a decoder cached per class would cut it, but
  24 ms for IEEE 123 does not need it.
- Indent 1 puts every matrix element on its own line. It is 40 % larger than compact,
  but a design under git gives one changed line per changed value. gzip would make IEEE
  123 9 KB; not worth a binary file at these sizes.

## Schema (version 1)

```json
{
  "format": "qtspice-grid-design",
  "version": 1,
  "base_frequency": 60.0,
  "grid_model": {
    "name": "ieee13nodeckt",
    "voltage_bases": [115.0, 4.16, 0.48],
    "buses":        [{"name": "sourcebus", "kv_base_ln": 66.39528095680697, "x": 200.0, "y": 400.0}],
    "sources":      [{"name": "source", "bus1": {"bus": "sourcebus", "nodes": []}, "bus2": {"bus": "sourcebus", "nodes": [0, 0, 0]}, "kv": 115.0, "...": "..."}],
    "linecodes":    [{"name": "mtx601", "phases": 3,
                      "r_matrix": [0.2153051181102362, "... 9 values, row-major, ohm/km"],
                      "x_matrix": ["... 9 values"], "c_matrix": ["... 9 values, nF/km"],
                      "normal_amps": 400.0, "emergency_amps": 600.0, "generated": false}],
    "lines":        [{"name": "650632", "bus1": {"bus": "rg60", "nodes": [1, 2, 3]},
                      "bus2": {"bus": "632", "nodes": [1, 2, 3]}, "phases": 3,
                      "length_m": 609.6, "linecode": "mtx601", "enabled": true}],
    "switches": [], "transformers": [{"name": "sub", "windings": [{"terminal": {"bus": "sourcebus", "nodes": []}, "...": "..."}], "...": "..."}],
    "regulators": [], "loads": [], "capacitors": [], "pvsystems": []
  },
  "diagram": {
    "bus_glyphs":     {"sourcebus":   {"x": 12.0, "y": -24.0, "orientation": 0, "length": 2.0}},
    "element_glyphs": {"line.650632": {"x": 12.0, "y": -16.5, "rotation": 0,
                                       "label_dx": 0.0, "label_dy": -1.0}}
  }
}
```

- Top level: exactly `format`, `version`, `base_frequency`, `grid_model`, `diagram`, in
  that order. `base_frequency` is required (Hz, > 0, no default: never assume 60 Hz).
- `grid_model`: every GridModel field except `base_frequency`; each class dict is an
  array of element objects in model order, one key per dataclass field, in canonical
  units (CLAUDE.md "Grid model"). Element names are unique per array.
- `diagram`: `bus_glyphs` keyed by bus name, `element_glyphs` keyed by element id.
  Glyph order is not meaningful.
- Never stored: results, the DSS script, undo history, the GridModel ClassVars.

## Rules for reading this and later versions

| case | result |
|---|---|
| not UTF-8, not JSON, not an object, `format` is not `qtspice-grid-design` | error Diagnostic, nothing loaded |
| `version` missing, not an int (also `1.0`, `"1"`, `true`), or < 1 | error, nothing loaded |
| `version` > the newest this build reads | error "made by a newer QtSpice", **nothing loaded** |
| `version` < newest (none yet) | migrate the JSON step by step to the newest, then read |
| unknown field, at any level (top, grid_model, element, glyph) | **warning** `unknown_field`, ignored; the rest loads |
| missing field that has a dataclass default (e.g. `enabled`, `rotation`, a whole class array) | **the default**, no Diagnostic |
| missing field without a default (`base_frequency`, `grid_model.name`, an element's `name`/`kv`, a glyph's `x`) | error, nothing loaded |
| wrong JSON type (also `1.0` for an int field), NaN/Infinity in the file, matrix not phases x phases, duplicate name | error, nothing loaded |
| int for a float field | read as float, no Diagnostic |
| `null` | allowed only where the type is `X | None` (D2 makes user-entered fields optional for drafts) |

Any error loads nothing: a partly loaded design would break rule 21 (one glyph per
element). Warnings load. `version` is a single integer that changes only for an
incompatible change: adding a field with a default (or a new class array) keeps
version 1, because an older build reads such a file with a warning per new field. A
save always writes every field and the newest version.

## Open for D3 (not decided by this spike)

- **Bus.x / Bus.y in GridModel** duplicate the diagram's bus positions for a design
  opened from a .dss file (the file coordinates are kept as they were read). Either
  keep them as "coordinates from the file" or clear them when a design is made; rule
  20 puts positions in the DiagramModel only, and Export .dss writes BusCoords from the
  diagram.
- **Glyph keys.** CLAUDE.md "Diagram model" keys bus glyphs by bus name ("671"), rule 21
  says element ids ("bus.671"). The spike uses the bus name.
- **LineCode and RegControl glyphs** (S2 open point, rule 21): the spike gives them
  none.
- **Switch ids are `line.<name>`** like lines, so element_glyphs needs a line and a
  switch never to share a name: D2's "unique per class" must cover lines and switches
  together.
- `check_consistency()` (rule 21) is not run by the loader here; D3 runs it after
  reading and reports a glyph without an element as an error.
