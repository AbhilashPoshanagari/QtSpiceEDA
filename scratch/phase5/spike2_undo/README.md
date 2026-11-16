# S2 spike: undo command pattern (v0.5 Grid Network Designer)

Question: which command pattern gives exact undo for edits that change GridModel and
DiagramModel together?

Answer: **delta commands** (each command stores only what it changes), written as
Qt-free core commands and wrapped 1:1 in a `QUndoCommand`. Merge rules: a drag merges on
glyph id + gesture id. A compound delete is **one compound command**, not a
`beginMacro` macro. On IEEE 123, 1000 random commands undo to a state equal (`==` and the
same dict key order) to the start and redo to the end, for deltas and for both kinds of
snapshot. Deltas are about 2000x faster than pickle snapshots and use about 500x less
memory. Snapshots also lose object identity on every undo, so the bridge could not
update its models row by row.

Scratch only: nothing in app/, bridge/, core/ or ui/ was changed or imports this.

## Files

| file | what |
|---|---|
| `state.py` | `DesignState` = the real v0.4 `GridModel` + a toy `DiagramModel` (`BusGlyph`, `ElementGlyph`, presentation only); `check_consistency` (rule 21 + dangling references); `order` (key order of every dict); `load_ieee123` (in-process OpenDSS, spike only); `mini_state` (hand-made 6-bus feeder for tests without dss) |
| `commands.py` | Qt-free protocol `Command` (`description`, `merge_id`, `apply(state)`, `revert(state)`, `merge(other)`, `affected()`); delta commands `AddElement`, `RemoveElement`, `MoveGlyph`, `SetProperty`, `CompoundCommand`; factories `add_load`, `delete_bus`; `SnapshotCommand(inner, "deepcopy" or "pickle")` |
| `qt_commands.py` | `UndoCommand(QUndoCommand)`: the 1:1 wrapper; `push_macro` (the macro alternative) |
| `randomseq.py` | seeded random user actions built from the current state: add load 25 %, drag 35 %, set property 33 %, delete bus 7 % |
| `bench.py` | the measurements -> `results.json` |
| `test_spike2.py` | `pytest -q scratch/phase5/spike2_undo` (25 tests, about 3 min; the IEEE 123 ones need dss-python) |

## What was built

- **Protocol** (no Qt import). `apply` must work as both the first "do" and every later
  "redo". `revert` undoes exactly one `apply` and may assume the state is exactly the
  one `apply` left (stack discipline). `merge(other)` absorbs a command that was already
  applied directly after this one. `affected()` returns the element ids whose rows changed.
  The bridge needs that to update its glyph/property models row by row (proposed
  addition for D4).
- **AddElement**: inserts the element and its glyph at the end of their dicts; revert
  deletes both. `add_load` copies kv/kW/kvar/model/phases from an existing load (rule 24)
  and takes the lowest free name `ld<n>`.
- **RemoveElement**: pops the element and its glyph and remembers both dict positions.
  Revert puts the **same objects** back at those positions. Dict order matters:
  `==` on dataclasses ignores it, but it sets the order of the written DSS script and of
  the .qgrid. The tests check it separately (`order()`).
- **MoveGlyph** (presentation only): stores old and new (x, y). `merge_id` = 1. It
  merges only with a MoveGlyph of the **same glyph and the same gesture id** (one mouse
  press). Without the gesture id, two drags of the same glyph in a row would merge into
  one undo step (tested: two drags = two steps). A merged drag that ends where it
  started is `setObsolete(True)`, and QUndoStack drops it (tested).
- **SetProperty**: dotted path with list indices (`"kw"`, `"windings.1.tap"`); stores the
  old value on the first apply. It never merges by default. With a `merge_key` (e.g. one
  spin-box editing session), consecutive sets of the same field merge (tested).
- **delete_bus** -> `CompoundCommand`: regulators of removed transformers first, then
  every branch/shunt with a terminal at the bus, then the bus. Children apply in order
  and revert in reverse. In D4 the connected set must come from topology.py (rule 25);
  the spike scans the model.
- **SnapshotCommand** wraps any command. The first apply runs the inner command between
  two snapshots of (GridModel, DiagramModel). Later applies restore "after", reverts
  restore "before". A restore always makes a fresh copy, or later edits would change the
  stored snapshot.

Two findings about ids. A Switch's id is `line.<name>` (OpenDSS class), the same prefix as
Line, so resolving an id needs both dicts (`state.resolve`). LineCode and RegControl have
no glyph in the toy; rule 21 says "every GridModel element", so D3 has to decide whether
library entries and controls get glyphs.

## Results

Windows 11, Intel i7-1185G7, Python 3.12.2, PySide6 6.8.3, dss-python 0.15.7 (only to load
the feeder). IEEE 123 as extracted: 132 buses, 118 lines, 8 switches, 8 transformers,
7 regulators, 91 loads, 4 capacitors, 12 linecodes; 132 bus glyphs + 230 element glyphs.
Whole state: 76 KB pickled, about 0.4 MB as Python objects (tracemalloc). pickle dumps
2.9 ms, loads 1.9 ms, deepcopy 8.1 ms. All numbers are from `results.json`.

### Exactness

| test | delta | deepcopy snapshot | pickle snapshot |
|---|---|---|---|
| 1000 random commands on IEEE 123, undo all == start, redo all == end, key order equal, consistency after every apply/revert | pass (seeds 1, 7, 2026) | pass (2026) | pass (2026) |
| same on the 6-bus mini feeder (no dss) | pass | pass | pass |
| each command apply -> revert -> apply | pass | pass | pass |
| 1000 user actions through one QUndoStack, drags of 1-200 mouse-move events; `setIndex(0)` == start, `setIndex(count)` == end | pass (1000 actions, 36 821 pushes -> 1000 steps) | - | pass (50 actions) |

Seed 2026 makes 254 add load, 308 set property, 367 drags and 71 bus deletions. IEEE 123
ends at 61 buses, 31 lines and 208 loads.

### Time and memory, 1000 commands, IEEE 123 (core, no Qt)

| | delta | deepcopy snapshot | pickle snapshot |
|---|---|---|---|
| apply, total | 3.5 ms | 65.7 s | 7.9 s |
| apply, median / p95 / max per command | 1.6 us / 22 us / 55 us | 59 ms / 103 ms / 756 ms | 7.2 ms / 12.5 ms / 15.8 ms |
| median per kind: add load / set / drag / delete bus | 1.9 / 1.8 / 0.6 / 25 us | about 59 ms each | about 7.2 ms each |
| undo all 1000 | 6.9 ms | 31.6 s | 3.9 s |
| redo all 1000 | 2.6 ms | 36.0 s | 4.2 s |
| memory held by the 1000 commands | 0.32 MB (315 B/command) | 658 MB (658 KB/command) | 161 MB (161 KB/command) |

DEEPCOPY_GC_LINE

### Qt: QUndoStack

QT_TABLE

## Decision

- **Deltas.** A command stores old/new values, or the removed objects and their dict
  positions. Exact by construction, microseconds per command, about 0.3 KB per step
  (1000 steps: 0.3 MB). Snapshots are also exact, but cost 7 ms (pickle) to 60 ms
  (deepcopy) per command on IEEE 123. With 200 mouse-move events in one drag, that is
  about 1.7 s per drag for pickle. They hold 161-658 MB for 1000 steps. They replace
  every object on undo, so the bridge could only reset all of its models, and QML would
  rebuild every delegate (S1: rebuilding all paths is 0.7-4 ms per edit even without
  that). Snapshots also grow with the feeder; deltas grow with the edit.
- **Delta rules for D4:** (1) `apply` is both do and redo; `revert` assumes the exact
  post-apply state. (2) Remove keeps the object and both dict positions and re-inserts
  at those positions (order is part of "exact"). (3) Values are captured on the first
  apply, not at construction, so a command can be built before the stack calls `redo()`.
  (4) Every command reports `affected()` ids for the bridge. (5) Tests compare `==`,
  key order and `check_consistency()` after every apply and every revert.
- **Merge rules:** `merge_id` 1 = MoveGlyph, merging on (glyph id, gesture id). The
  bridge's drag slot gets a new gesture id on press. A merged no-op becomes obsolete.
  `merge_id` 2 = SetProperty, only with an explicit merge key (one editing session of one
  field); a plain property edit is always its own step. All other commands: -1 (never).
  QUndoStack only tries to merge with the top command, so merging needs no extra checks.
- **Compound command, not a macro.** Both give one undo step and exact undo/redo, at
  about the same speed (table above). The compound command is a core object: the
  designer proof test (rebuild IEEE 13 with edit commands, undo all, redo all) runs on core
  commands without Qt, and the bridge keeps a 1:1 wrapper (rule 22: "one QUndoCommand
  per command"). A macro exists only on the QUndoStack, so the core tests could not
  cover it. It would also need the bridge to know a command's children, and a
  half-pushed macro (an exception in the third child) leaves half an edit on the stack.
  Library "Regulator" (transformer + RegControl) and "remove bus" are compound
  commands.
- **Not needed:** an undo limit. 1000 delta steps on IEEE 123 hold about 0.3 MB of Python
  objects (plus the QUndoCommand wrappers, QUNDO_MEM).
