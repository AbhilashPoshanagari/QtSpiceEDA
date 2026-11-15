# S1 spike: canvas performance (v0.5 Grid Network Designer)

Question: can a QML canvas show and edit IEEE 123 smoothly, and which parts of the
existing schematic editor canvas can we reuse?

Answer: **yes, with plenty of headroom**. Both variants hold 60 fps (display rate)
while panning, zooming and dragging a bus, at 1 copy (132 buses) and at 8 copies (1056
buses). **Recommendation: variant (b)**, with branches as ShapePaths (GeometryRenderer
plus 4x window MSAA) and glyphs as Items. Only the view mechanics of SchematicCanvas
can be reused (pan/zoom, grid layer, rubber band). Its hit-testing, snapping and
selection belong to the schematic model.

Scratch only: nothing in app/, bridge/, core/ or ui/ was changed or imports this.

## Files

| file | what |
|---|---|
| `scene.py` | Qt-free: IEEE 123 through `OpenDSSEngine` + `extract` (in-process, spike only), glyphs in diagram units, `replicate(n)`, `SpatialIndex` hit-test plus a linear scan for comparison |
| `canvas_bridge.py` | `SceneController`: list models (buses, branches, branch symbols, shunts), SVG path strings for (b), slots `clickAt`, `selectRect`, `moveBusTo`, `hitTest` |
| `Canvas.qml` | the test canvas; `variant: "a"` or `"b"` |
| `symbols/*.svg` | one SVG per glyph kind (load, capacitor, PV, source, transformer, switch, open switch) |
| `bench.py` | one measured run; `--variant a\|b --copies N [--curve] [--msaa 4] [--no-vsync] [--shot png] [--interactive]` |
| `run_all.py` | 9 runs (a, b, b-curve x 1/4/8 copies), each in a fresh process -> `results.json` |
| `results_b_msaa4_x{1,8}.json`, `shot_b_msaa4_x{1,8}.png` | the MSAA runs and what they look like |
| `test_spike1.py` | `pytest -q scratch/phase5/spike1_canvas`: glyph counts, index == linear scan, index after a move |

To look at it yourself: `python scratch/phase5/spike1_canvas/bench.py --variant b --copies 1 --interactive`.
Use the wheel to zoom, middle- or right-drag to pan, click to select, Ctrl+click to toggle,
drag a bus to move it, and drag on empty space for a rubber band.

## 1. What the schematic canvas offers (step 1)

Read: `ui/qml/SchematicCanvas.qml`, `SymbolItem.qml`, `WireTool.qml`,
`bridge/schematic_controller.py`, `core/schematic/geometry.py`. For comparison:
`BlockCanvas.qml` and the G6 `grid/GridView.qml`.

| piece | where | separate / reusable? |
|---|---|---|
| Pan/zoom: `zoom`, `panX/panY`, `toSheet`, `zoomAt` (wheel around the pointer), middle/right-drag pan, one `content` Item moved and scaled as a whole | inline in SchematicCanvas.qml (about 40 lines plus the MouseArea branches) | **Model-free, but not a component.** `zoomToFit` needs only a bounds call (`SchematicController.contentBounds`). Copied almost verbatim into the spike. BlockCanvas and GridView each have their own copy (GridView uses Canvas2D `px/py`). |
| `settledZoom` / `penWidth` (pen widths follow the zoom 80 ms after it stops) | inline | Model-free. Copied; the spike also uses it for the SVG raster size. |
| Grid layer (lines in viewport pixels that only move) | inline `Item gridLayer` | **Model-free.** Copied verbatim. |
| Rubber band: rectangle in viewport pixels and the `banding` state | inline | The QML half is model-free. The selection half is `SchematicController.selectRect` -> `core/schematic/geometry.items_in_rect`, which is **tied to the schematic**. |
| Hit-testing | `SchematicController.itemAt` -> `geometry.hit_test`: a linear scan over the `Schematic` lists, with fixed kind priority (flags > texts > wires > instances) | **Tied to the schematic model.** Reusable as a protocol: QML calls one slot with a sheet point and a tolerance of 6 px / zoom. |
| Grid snap | `SchematicController.snapPoint` -> `geometry.snap` (GRID = 16 LTspice units) + `pin_near` | **Tied to the schematic** (LTspice units, pins). The designer snaps to whole diagram units in its own core code. |
| Selection, live move (`beginMove/moveTo/endMove`), copy/paste, undo | SchematicController (`_begin_live` snapshots of `Schematic`, its own QUndoStack) | Tied to the schematic. The *pattern* (live preview, one undo step at release) is S2's subject. |
| Mouse dispatcher (modes place / wire / label / ground / port / text / probe / move / drag / delete), tool bar, shortcuts, popups | inline | Schematic-specific; only the pan and wheel branches are generic. |
| Drawing: wires as one `PathSvg` string from the controller, parts as `SymbolItem` (one Shape + PathSvg per part, CurveRenderer, from `.asy` geometry) | SchematicCanvas / SymbolItem | The wire technique is exactly variant (b). SymbolItem is tied to `.asy` symbols and `SchematicController.symbolGeometry`, so it is not reusable for grid glyphs. |

Conclusion: the view mechanics (pan/zoom, settle, grid layer, rubber-band rectangle)
are generic but inline. For D6, put them into one small designer component (e.g.
`ui/qml/grid/DiagramViewport.qml`) by copying them, and leave the released
SchematicCanvas unchanged. Hit-testing, snapping and selection are new code against
GridModel/DiagramModel (core/grid/diagram + GridDiagramController).

## 2. What was built

- IEEE 123 loads through `OpenDSSEngine.load_native` + `BusCoords` + `run` +
  `extract_model` in 0.20-0.22 s. That gives 132 buses, 118 lines, 8 switches,
  8 transformers (7 of them regulators: 2 windings each, 1 branch), 91 loads,
  4 capacitors and 1 source. Glyphs: 132 bus bars with name labels, 134 branches
  (16 with a switch/transformer SVG symbol at the midpoint), and 96 shunts (stub +
  SVG). Coordinates are divided by 25 (one grid step); y is flipped. The two buses
  without coordinates (300_open, 94_open) go next to their neighbour; real layout is S3.
- Copies: `replicate(n)` puts n copies side by side. 4 copies = 528 buses; **8 copies
  = 1056 buses** (the "about 1100" headroom case). IEEE 123 has 132 buses, so 4 copies
  are 528, not 1100. 1100 is about 4 x its 278 nodes.
- (a) one Item per glyph via Repeater. A branch is a rotated Rectangle, plus an Image
  for switches and transformers.
- (b) branches are one `Shape` with five `ShapePath`s (lines, closed switches, open
  switches dashed, transformers, selected), fed by `PathSvg` strings that the
  controller rebuilds on every change. Buses, shunts and branch symbols are Items
  via Repeater.
- Both: bus labels are `Text` (always on, the worst case). SVG glyphs are `Image`s with
  `sourceSize` set to the next power of two of their on-screen size once the zoom
  settles. Hit-testing is in Python (uniform cell grid, 4 units per cell). The click
  goes QML -> `clickAt` -> model `dataChanged` for the changed rows only.

## 3. Results (step 3)

Machine: laptop, Intel Core i7-1185G7 (4C/8T, 3.0 GHz), 32 GB RAM, Windows 11
Enterprise 10.0.26100. Qt Quick picked the **Intel Iris Xe** (the NVIDIA T500 is
present but unused): D3D11, threaded render loop, 60 Hz display, DPR 1.0. The window
asked for 1600x900 and got 1370x749 (screen size). Python 3.12.2, PySide6 6.8.3,
dss-python 0.15.7. Each run is a fresh process, measured once (`results.json`);
2026-11-15.

How it was measured (bench.py):
- **first frame**: `engine.load(Canvas.qml)` to the first `frameSwapped`. The OpenDSS
  load (about 0.2 s) and Python + Qt start-up (about 0.37 s) come before it and are
  listed separately in the JSON.
- **pan/zoom 5 s**: 2.5 s pan in a 400 px circle, then 2.5 s zoom around the centre
  between 1/8x and 8x of the fit zoom. Each step is set from `frameSwapped`, so one
  step = one real frame. fps = frames / time.
- **SG ms**: render-thread CPU time per frame, from `beforeSynchronizing` to
  `afterRendering` (sync + render recording, without the present/vsync wait). This is
  the headroom figure: at 60 Hz a frame has 16.7 ms.
- **drag 3 s**: the bus with the most branches moves on a 6-unit circle through
  `moveBusTo` (the slot a mouse drag calls), one call per frame. "slot ms" = Python time
  in the slot, model updates and the QML binding updates they trigger included.
  "Shape polish" = `branchShape.ensurePolished()` after each move (re-tessellation,
  which runs on the GUI thread in neither of the other two numbers).
- **settle**: GUI-thread time of `settledZoom` fit -> 4x fit (new pen widths, new SVG
  raster sizes, re-tessellation).
- **hit-test**: 10 000 random points through the index (the first 1000 also through
  the linear scan; 0 mismatches in every run). Then 30 real clicks (`QTest.mouseClick`)
  on a random bus centred at 100 % zoom: "handled" = mouse event delivered and slot
  done; "to frame" = click to the next `frameSwapped`.
- **memory**: process working set (MB) before the QML load, 1 s after the first
  frame, and at the end. GPU memory is not included.

| variant | copies | buses | QQuickItems (content) | first frame s | pan/zoom fps (p95 ms) | SG ms p50/p95/max | drag fps | drag slot ms | Shape polish ms mean (max) | settle ms | click handled / to frame ms | clicks right | WS MB before / after / end |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| a | 1 | 132 | 1322 | 0.50 | 60.1 (21.1) | 0.85/1.49/2.07 | 60.1 | 0.77 | - | 56.0 | 2.2 / 17.4 | 29/30 | 83 / 173 / 188 |
| a | 4 | 528 | 5270 | 0.79 | 60.0 (20.5) | 0.35/3.28/4.95 | 60.0 | 0.89 | - | 5.6 | 5.2 / 19.0 | 30/30 | 84 / 201 / 225 |
| a | 8 | 1056 | 10534 | 1.31 | **56.8 (26.8)** | 0.31/5.81/**13.46** | 59.8 | 1.18 | - | 7.9 | 11.1 / 21.8 | 28/30 | 86 / 241 / 274 |
| b | 1 | 132 | 835 | 0.52 | 59.1 (21.1) | 0.88/1.49/1.82 | 59.9 | 0.69 | 0.04 (0.31) | 56.8 | 2.0 / 17.4 | 29/30 | 83 / 178 / 186 |
| b | 4 | 528 | 3319 | 0.74 | 60.2 (20.3) | 0.34/2.74/7.11 | 60.0 | 1.43 | 0.08 (0.32) | 1.7 | 4.6 / 18.5 | 30/30 | 84 / 204 / 216 |
| b | 8 | 1056 | 6631 | 0.95 | 59.5 (21.4) | 0.38/4.89/10.66 | 60.0 | 4.00 | 0.14 (0.50) | 3.5 | 7.3 / 20.6 | 28/30 | 87 / 235 / 257 |
| b-curve | 1 | 132 | 835 | 0.50 | 59.7 (21.1) | 0.87/1.45/2.35 | 60.0 | 0.57 | 0.20 (0.97) | 52.7 | 1.9 / 18.1 | 29/30 | 83 / 172 / 190 |
| b-curve | 4 | 528 | 3319 | 0.79 | 59.4 (21.0) | 0.38/3.20/10.65 | 59.9 | 1.53 | 0.86 (4.31) | 3.1 | 3.9 / 19.1 | 30/30 | 84 / 196 / 222 |
| b-curve | 8 | 1056 | 6631 | 1.09 | 59.6 (21.4) | 0.41/5.52/8.76 | 59.3 | 3.70 | **1.84 (6.21)** | 6.4 | 7.4 / 22.7 | 28/30 | 86 / 231 / 278 |
| b + 4x MSAA | 1 | 132 | 835 | 0.48 | 60.0 (20.9) | 0.79/1.26/1.82 | 60.1 | 0.61 | n/m | 42.9 | n/m | n/m | - / - / 237 |
| b + 4x MSAA | 8 | 1056 | 6631 | 0.91 | 58.7 (23.0) | 0.40/4.48/7.91 | 60.0 | 3.77 | n/m | 2.5 | n/m | n/m | - / - / 309 |

(n/m = in the JSON but not repeated here. The p95 frame interval of about 20-21 ms
comes from the queued frameSwapped loop and is the same in every row, including the
smallest.)

Hit-test in Python (µs per point; the 10 000 points cover the whole drawing):

| copies | SpatialIndex | linear scan | index build ms |
|---|---|---|---|
| 1 | 6-7 | 555-598 | 1.8-2.2 |
| 4 | 14-19 | 2236-2390 | 7.1-9.0 |
| 8 | 27-34 | 5238-5962 | 21.6-24.8 |

Observations:
- **Pan and zoom never rebuild anything**: moving/scaling one `content` Item keeps the
  median render-thread time under 1 ms at every size. Only (a) x8 drops frames
  (56.8 fps, p95 SG 5.8 ms, max 13.5 ms).
- **Items**: (a) needs 1.6x the QQuickItems of (b) (10 534 against 6 631 at 1056 buses).
  First frame: 1.31 s against 0.95 s. Memory at the end: 274 MB against 257 MB.
- **Editing cost moves to Python in (b)**: every move rebuilds all branch path strings,
  so the drag slot grows with the feeder: 0.7 ms (1x) -> 4.0 ms (8x), against
  0.8 -> 1.2 ms for (a). It is still far inside a frame. In D6 this can be held near
  constant by putting the moving element's branches in their own "live" ShapePath
  during a drag.
- **GeometryRenderer vs CurveRenderer**: re-tessellation with the GeometryRenderer is
  0.04-0.14 ms per move. The CurveRenderer costs 0.2-1.8 ms on average and up to
  6.2 ms per move at 8x, on the GUI thread. The GeometryRenderer draws without
  antialiasing (uneven line widths at fit zoom). Window MSAA 4x fixes that
  (`shot_b_msaa4_x1.png`) and keeps 60 fps, for about +50 MB working set at 8x
  (309 against 257 MB).
- **Settle spike when the SVG raster size changes**: 43-57 ms once, at 1 copy, when
  the 4x zoom crosses a raster-size step (16 -> 32 px). That is the synchronous SVG
  re-rasterisation of the 7 glyph files (the pixmap cache is cold for the new size).
  Without a size step it is 2-8 ms. D6: `asynchronous: true` on the glyph Images, or
  pre-rasterise the few glyph sizes at start-up.
- **Clicks**: from click to the next frame takes one display frame (17-23 ms). Event
  handling incl. the Python slot is 2 ms (1x) to 7-11 ms (8x). That time is mostly
  `dataChanged` + QML bindings, since the hit-test itself is under 0.04 ms.
- **Hit-test correctness**, two findings for D6/S3:
  1. A fixed kind priority (as in the schematic's `hit_test`) picked a bus's own load
     instead of the bus at fit zoom: at 29 % zoom the 6 px tolerance is 1.3 units.
     The spike now uses **nearest glyph within the tolerance, kind only as a
     tie-break**. With that rule, the only misses left are those of point 2.
  2. The regulator buses `150r` and `9r` have **exactly the same coordinates** as
     `150` and `9` in BusCoords.dat, so their glyphs overlap and `150r` can't be
     clicked (the 1-2 misses per run). S3 / layout must separate coincident buses.
- **Uncapped fps is not measurable this way**: with swap interval 0 (`--no-vsync`) the
  frame loop is held at about 15.6 ms (the Windows timer tick), not by rendering. That
  is why render-thread CPU time (SG ms) is the headroom figure. It peaks at 5-6 ms
  p95 at 8x, about a third of a 60 Hz frame.
- The Python linear scan is fine for one click at IEEE 123 size (0.6 ms) but 5.5 ms at
  8x. A spatial index (6-34 µs) is needed for hover and drag-time snapping on large
  designs.

A harness note (not a canvas problem): Python callbacks connected with
`DirectConnection` to render-thread signals (`beforeSynchronizing`) deadlock against
`QTest.mouseClick` and `grabWindow`, which wait for the render thread while holding
the GIL. bench.py connects them only for the pan/zoom and drag phases. The app does
not connect to render-thread signals.

## 4. Recommendation

1. Use **variant (b)**: branches as ShapePaths fed by path strings from
   GridDiagramController (one per style: line, closed switch, open switch, transformer,
   selected, and a "live" path for what is being dragged). Bus bars, shunts and branch
   symbols are Items via Repeater from list models (one row per glyph, `dataChanged`
   per changed row). The GeometryRenderer with window MSAA 4x is the default. Do not
   use the CurveRenderer for the branch Shape (its polish grows to several ms per edit).
   (a) is also fine at IEEE 123 size; (b) is chosen for 1.6x fewer items, a faster
   first frame and lower render-thread peaks at 1000+ buses.
2. Keep pan/zoom as one transformed `content` Item with the 80 ms settle (copied from
   SchematicCanvas into a designer viewport component; SchematicCanvas stays as is).
3. Hit-testing and rubber band in core/grid/diagram with a uniform-cell spatial index,
   nearest glyph wins (kind as tie-break), and tolerance = 6 px / zoom. Expose it
   through one controller slot, as `SchematicController.itemAt` does.
4. SVG glyph Images: power-of-two `sourceSize` from the settled zoom,
   `asynchronous: true`, so that a raster-size step does not stall a frame.
5. The canvas needs no warm-up or tiling at these sizes. Revisit only if designs go
   well beyond about 1000 buses (not planned for v0.5).
