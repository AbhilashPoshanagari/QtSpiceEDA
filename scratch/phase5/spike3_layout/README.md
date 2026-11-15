# S3 spike: auto-layout (v0.5 Grid Network Designer)

Question: can our own code place buses that have no coordinates well enough to edit?

Answer: **yes**. A layered tree layout places IEEE 13, 34 and 123 with every coordinate
removed in under 1 ms, with **0 branch crossings and 0 glyph overlaps** on all three.
1057 buses (8 copies of IEEE 123) take under 10 ms. The mixed case keeps the file
coordinates and places only the missing buses next to a neighbour. On IEEE 123 it places
300_open and 94_open and separates the 4 buses that share a position with another bus,
without adding a crossing or an overlap.
**Recommendation:** `packed-mid` for whole feeders, `place_missing` for the mixed case
and for buses added later. Both together are under 500 lines of Python on dicts and a deque, with
no new dependency.

Scratch only: nothing in app/, bridge/, core/ or ui/ was changed or imports this.

## Files

| file | what |
|---|---|
| `layout.py` | Qt-free. `Graph`/`Edge`, `tree_layout(graph, variant)`, `scale_coordinates`, `place_missing(graph, fixed)`, and the geometry helpers `seg_cross` and `seg_hits_box` |
| `feeders.py` | IEEE feeders through `tests/core/grid/helpers.solve_feeder` (OpenDSS in-process, spike only) -> `Graph`; `replicate`; metrics (`measure`) |
| `render.py` | QPainter on an offscreen QImage -> PNG (PySide6 is already a dependency, so matplotlib was not needed) |
| `run_all.py` | every measurement -> `results.json` and the PNGs |
| `test_spike3.py` | `pytest -q scratch/phase5/spike3_layout`: a small graph with open switch, transformer and an isolated bus (all variants), determinism, IEEE 13/34/123 auto with no crossing or overlap, IEEE 123 mixed |

Run with `python scratch/phase5/spike3_layout/run_all.py` (needs `.[grid]`).

## What was built

**Graph.** One edge per line, switch and transformer winding pair (winding 1 to winding
k), plus the number of shunt glyphs per bus (source, loads, capacitors, PV). Parallel
branches between the same two buses count as one connection, for example the three
single-phase regulators 650-rg60. A branch is *closed* when it is enabled and, for a
switch, closed. In the product this graph comes from core/grid/topology.py (rule 25).

**Tree layout** (`tree_layout`).
1. Breadth-first search from the source bus over closed branches only, so an open
   switch is a non-tree edge. Buses that are reached only through an open branch are
   then hung off the bus that reaches them, and the search goes on over closed branches
   from there. Parts with no branch to the source at all are laid out as their own
   trees, stacked below.
2. Column = parent column + 1. A transformer edge adds one extra column (+2).
3. Children are ordered deepest subtree first, then by size, then by name. The result
   is deterministic and does not depend on the order of the input.
4. Rows: the four variants differ only in this step.
   - `leaf-trunk`: one row per leaf in subtree leaf order. A bus sits on its first
     child's row, so the longest path is one straight row.
   - `leaf-mid`: one row per leaf. A bus sits midway between its first and last child.
   - `packed`: like leaf-trunk, but each child subtree moves up to the highest rows
     that are free in the columns it uses (per-column top/bottom contours, as in
     Reingold-Tilford).
   - `packed-mid`: packed, with a bus midway between its first and last child.
   A non-first child behind a transformer is pushed down until the edge, which skips
   a column, clears its siblings in the skipped column.
5. x is the sum of column widths. A column is as wide as its longest bus bar plus a gap
   of 3. A bus bar is max(2, 1.2 x number of shunts) long, because shunts sit centred
   in one row 2 units below their bus. Rows are 4 units apart. Every position is on
   whole grid units.

**Mixed** (`place_missing`).
1. File coordinates are scaled so that the median branch is 6 units long, then snapped
   to whole units (`scale_coordinates`).
2. Buses that share a position: the bus nearest the source (in hops) keeps it, and the
   others count as missing (IEEE 123: 150r/150, 9r/9, 25r/25, 160r/160; this is the S1
   finding).
3. Missing buses are placed in breadth-first order from the placed ones, so each bus
   has a placed neighbour when its turn comes. Candidates are the grid points within
   12 units of that neighbour, tried nearest first to a wanted point: 6 units from the
   neighbour, away from the neighbour's other branches. Score, in this order: glyph
   overlaps (1 unit clearance), new crossings, branches through a glyph, branches
   shorter than 3 units, distance to the wanted point. The first candidate with no
   penalty wins.
4. A part with no branch to any placed bus gets `tree_layout`, placed below.

**Metrics** (`feeders.measure`). Branches are straight segments between bus centres,
which is the S1 drawing. Measured:
- *crossings*: pairs of segments with no common bus that intersect or overlap
  collinearly;
- *glyph overlaps*: pairs of buses whose bus bars or shunt boxes intersect;
- *through a glyph*: (segment, bus) pairs where a branch passes through another bus's
  bar or shunt;
- branch length (mean and max, in units) and the size of the bounding box.

The "original" layout is the file coordinates put through `scale_coordinates`. In IEEE
123 the two buses without coordinates are left out of it.

## Results

Intel i7-1185G7 (Intel64 Family 6 Model 140), Windows 11, Python 3.12.2, PySide6
6.8.3, dss-python 0.15.7. Times are the median of 50 runs (8x: 10; mixed: 3), layout
only, with the GridModel already built. `results.json` has every number.

### All coordinates removed

| feeder | layout | time ms | crossings | overlaps | through | branch mean / max | size (units) |
|---|---|---|---|---|---|---|---|
| IEEE 13 (16 buses) | file coordinates | - | 0 | 0 | 0 | 5.2 / 9 | 28 x 27 |
| | leaf-trunk | 0.09 | 0 | 0 | 0 | 9.8 / 21 | 54 x 23 |
| | leaf-mid | 0.09 | 0 | 0 | 0 | 8.0 / 12 | 54 x 23 |
| | packed | 0.18 | 0 | 0 | 0 | 9.8 / 21 | 54 x 23 |
| | **packed-mid** | 0.27 | 0 | 0 | 0 | 8.0 / 12 | 54 x 23 |
| IEEE 34 (37) | file coordinates | - | 0 | **1** (858/834) | 0 | 6.2 / 18 | 97 x 42 |
| | leaf-trunk | 0.16 | 0 | 0 | 0 | 10.6 / 32 | 165 x 35 |
| | leaf-mid | 0.18 | 0 | 0 | 0 | 8.3 / 20 | 165 x 35 |
| | packed | 0.21 | 0 | 0 | 0 | 8.3 / 23 | 165 x 15 |
| | **packed-mid** | 0.27 | 0 | 0 | 0 | 8.1 / 20 | 165 x 24 |
| IEEE 123 (132) | file coordinates (130) | - | **4** | **5** | **11** | 6.8 / 21 | 119 x 86 |
| | leaf-trunk | 0.59 | 0 | 0 | 4 | 16.9 / 164 | 148 x 167 |
| | leaf-mid | 0.57 | 0 | 0 | 0 | 8.1 / 34 | 148 x 167 |
| | packed | 0.75 | 0 | 0 | 4 | 12.5 / 108 | 148 x 111 |
| | **packed-mid** | 0.91 | 0 | 0 | 1 | 7.2 / 24 | 148 x 122 |
| 8 x IEEE 123 (1057) | leaf-trunk | 6.0 | 0 | 0 | 35 | - / 1176 | |
| | leaf-mid | 8.5 | 0 | 0 | 0 | - / 588 | |
| | packed | 7.5 | 0 | 0 | 35 | - / 784 | |
| | packed-mid | 9.5 | 0 | 0 | 8 | - / 220 | |

The 8x graph has one artificial root with 8 feeders under it. That fan is the long
edge, so its max length only shows the trend.

- **No variant crosses or overlaps.** A layered tree with disjoint subtree row ranges
  cannot cross itself, and the column widths are derived from the bus bar lengths.
- **The `-trunk` variants draw long, steep fan edges.** On IEEE 123 the main trunk runs
  along the top, and later laterals end up 100-160 units below their parent, connected
  by near-vertical lines (see the second panel of `ieee123_original_vs_auto.png`).
  Those lines are also what passes through glyphs (4). The `-mid` variants halve the
  fan. packed-mid has the shortest branches on every feeder (max 24 on IEEE 123,
  against 21 in the file) and is a third smaller than leaf-mid.
- packed-mid's remaining "through" on IEEE 123 is one fan edge that grazes a
  sibling's load box. At 8x there are 8, all at the artificial root.
- Auto layouts are wider and flatter than the hand-drawn ones (IEEE 34: 165 x 24
  against 97 x 42), because depth is drawn left to right. That is acceptable for
  editing: S1 pans and zooms 1056 buses at 60 fps.
- `measure` is O(n^2) (52 ms on IEEE 123). It is for the spike, not for the app.

Pictures (left to right: file coordinates, leaf-trunk, packed, packed-mid; red circle
= crossing, orange box = overlapping glyphs):
`ieee13_original_vs_auto.png`, `ieee34_original_vs_auto.png`,
`ieee123_original_vs_auto.png`.

### Mixed: keep coordinates, place the missing buses

| case | buses placed | time ms | crossings | overlaps | through | branch mean / max |
|---|---|---|---|---|---|---|
| IEEE 123 as is (file) | - | - | 4 | 5 | 11 | 6.8 / 21 |
| IEEE 123 as is, place_missing | 6: 300_open, 94_open + 150r, 9r, 25r, 160r | 107 | **0** | 1 | 3 | 6.9 / 21 |
| IEEE 34, 30 % of the coordinates removed (seed 1) | 11 | 69 | 0 | 0 | 0 | 6.5 / 18 |
| IEEE 123, 30 % removed (seed 1) | 44 | 1478 | 2 | 1 | 2 | 8.0 / 25 |

- IEEE 123 as is: all 4 crossings in the file come from the coincident regulator
  buses. Moving the second bus of each pair removes them. 300_open lands 4 units above
  300 and 94_open 6 units below 54 (`ieee123_mixed.png`, magenta). The overlap left over
  (15/34) and the 3 "through" are in the file's own coordinates, which the mixed case
  keeps by design.
- With 30 % of the coordinates removed the result is still readable
  (`ieee34_mixed30.png`, `ieee123_mixed30.png`). On IEEE 123 two of the 44 placed buses
  could not find a spot without a crossing within 12 units. They take the least bad
  one.
- Cost is about 10-35 ms per placed bus: a pure-Python scan of up to 624 candidates
  against every box and segment. That is fine for the cases v0.5 has (2-6 buses on
  import, 1 per user edit). For more, use the S1 spatial index to look up only nearby
  boxes and segments.

## Recommendation for D3 (core/grid/diagram/layout.py)

1. `auto_layout(model, topology, fixed: dict[bus, (x, y)])`:
   - If no bus has coordinates (a new design, or a file without BusCoords), use
     `packed-mid`.
   - If some buses have coordinates, scale them (median branch 6 units, snapped), move
     coincident buses apart (the bus nearest the source stays), and place the rest with
     `place_missing`.
   - If fewer than about half the buses have coordinates, `packed-mid` for everything
     is the better picture. Threshold to be decided in D3; this spike only measured 70 %.
2. The spanning tree, source reachability, open-switch handling and hop distance come
   from topology.py (rule 25). layout.py only orders and places.
3. Keep the geometry constants together (row 4, column gap 3, bus bar max(2, 1.2 x
   shunts), shunt row 2 below the bus) and share them with the S1 canvas glyph sizes.
4. In D4, adding a bus or a shunt element uses `place_missing` for that one bus. It is
   cheap at one bus and never moves anything the user placed.
5. Not done here, and not needed for the v0.5 feeders: orthogonal routing of branches,
   label collision avoidance (the IEEE 34 pictures show crowded labels at 840/862),
   and crossing reduction for meshed (looped) designs. Loops close through non-tree
   edges, which are drawn straight and can cross.
