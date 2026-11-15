# S6 spike: edit -> run loop (v0.5 Grid Network Designer)

Question: how long does one designer Run take from an edit to a GridResult, where does
the time go, and is a new child process per run (today's GridRunner pattern) good
enough, or does the designer need a warm child that stays alive between runs?

Measured on Windows 11 Enterprise 10.0.26100, 11th Gen Intel Core i7-1185G7 (32 GB),
Python 3.12.2, dss-python 0.15.7 (same engine string as v0.4 S1), numpy 2.5.4.
10 runs per case, median unless stated. Numbers in `results.json` (last run); two
earlier clean runs agreed within about 10 %. A third run, during background load on
the machine, was 2-4x slower in every stage, parent-side pickling included, and is
not used.

Scratch only: nothing in app/, bridge/, core/ or ui/ was changed or imports this.
OpenDSS runs only in child processes, also in the benchmark (rule 5).

## Files

| file | what |
|---|---|
| `child.py` | child side: `cold_main` (one run per process, one message) and `warm_main` (serves runs until STOP or EOF); `run_once` does the stages below with `perf_counter` stamps |
| `warm.py` | `WarmGridChild`: parent side of the warm child (spawn + duplex Pipe + daemon, statuses and `ChildOutcome` from `core/blocks/process.py`) |
| `feeders.py` | the GridModels sent to the child: IEEE 13 and IEEE 123 (with `AFTER_COMPILE`) extracted after one solve, in a `ChildProcess`; `edited(model, i)` scales the first load's kW by 1 + 0.01 i |
| `bench.py` | all measurements -> `results.json` (about 30 s) |
| `test_spike6.py` | `pytest -q scratch/phase5/spike6_runloop`: pins the claims below (9 tests, about 15 s) |

## The loop that was measured

Parent: edit the GridModel (`dataclasses.replace`, stands in for a D4 command) ->
`pickle.dumps` -> hand over to the child -> child: unpickle -> `write_dss` (writer.py)
-> write `run_<n>.dss` into a temporary folder -> `OpenDSSEngine.load_native` (clear +
compile) -> `engine.validation()` (extract + validate.py, what `run` does first) ->
`engine.run(config)` (STATIC controls, MaxControlIter 30) -> pickle the GridResult ->
parent: unpickle -> overlay input (min pu per bus, loading % per branch; the QML
repaint itself is not measured, S1 has it).

Cold = `core/blocks/process.ChildProcess(cold_main, ...)` per run: a new process and a
new engine each time. Warm = one `WarmGridChild`; "new engine" makes a new
OpenDSSEngine (= new DSS context) per run inside the warm child, "reuse engine" keeps
one engine (one context) for every run (`load_native` starts with `clear`).

Every run solves a different model (edit i), so nothing can be served from the run
before. Edit 0 is the unedited model.

## Results (ms, median of 10)

| stage | IEEE 13 cold | warm, new engine | warm, reuse engine | IEEE 123 cold | warm, new engine | warm, reuse engine |
|---|---:|---:|---:|---:|---:|---:|
| edit + pickle model (parent) | 0.4 | 0.3 | 0.4 | 1.3 | 0.8 | 1.3 |
| to child (cold: spawn + Python start; warm: pipe) | 306 | 0.2 | 0.4 | 315 | 0.4 | 0.3 |
| import numpy + core.grid | 171 | - | - | 194 | - | - |
| import dss | 173 | - | - | 166 | - | - |
| DSS context (`NewContext`) | 39 | 17 | 0 | 42 | 16 | 0 |
| unpickle model | 0.3 | 0.3 | 0.3 | 1.2 | 0.9 | 1.0 |
| write_dss | 1.1 | 0.6 | 1.1 | 3.4 | 2.2 | 2.9 |
| write the script file | 3.7 | 1.7 | 2.5 | 3.5 | 3.2 | 3.3 |
| compile (clear + compile) | 7.4 | 5.1 | 15.1 | 10.2 | 7.6 | 14.4 |
| validation (extract + validate.py) | 3.6 | 1.8 | 3.2 | 13.4 | 8.6 | 8.2 |
| solve + read result | 2.0 | 1.1 | 2.0 | 8.1 | 6.9 | 6.2 |
| pickle result | 0.2 | 0.1 | 0.3 | 1.0 | 0.7 | 0.7 |
| back to parent (cold: + child exit and join; warm new: + engine close) | 17 | 5.5 | 0.4 | 19 | 4.3 | 0.6 |
| unpickle result + overlay input | 0.3 | 0.2 | 0.2 | 1.0 | 0.7 | 0.9 |
| **total, median** | **733** | **35** | **28** | **777** | **54** | **42** |
| total, min - max | 700 - 814 | 30 - 50 | 20 - 39 | 716 - 888 | 49 - 64 | 34 - 57 |

Sizes: IEEE 13 model pickle 10 KB, script 8 KB, result pickle 5 KB; IEEE 123 52 KB,
34 KB, 39 KB. Pickling both ways and the pipe cost about 4 ms together on IEEE 123:
sending the whole GridModel on every run is fine, no incremental protocol is needed.

**Where the time goes.** Cold: 690-720 ms of the 733/777 ms is the process itself
(spawn and Python start 306-315, numpy + core.grid 171-194, `import dss` 166-173, the
first DSS context 39-42), the same 0.5-0.56 s start-up v0.4 S2/S6 found plus our own
imports. The work (write, compile, validate, solve) is 18 ms (IEEE 13) / 39 ms
(IEEE 123). Warm: the start-up is gone; what is left is the work plus a new DSS context
(16-17 ms) or, with a reused context, a slower `clear` + compile (14-15 ms instead of
5-8 ms: clearing the previous circuit is part of the compile stage).

**First run of a warm child** = a cold run: 706-900 ms from `start()` to the first
result (ready, i.e. `import dss` done, 624-800 ms after spawn). Spawning the child
when a design is opened hides this unless Run is pressed within about 0.7 s.

**Results.** All warm runs are bit for bit equal to the cold runs of the same edit
(node voltages, losses, regulator taps), with a new engine and with a reused engine:
no state carries over between runs in one process. Edit 0 against the solve of the
original file: 4.3e-6 pu (IEEE 13), 9.8e-6 pu (IEEE 123), which is the known
second-solve effect at the default Solution.Tolerance 1e-4 (v0.4 S2), not the loop.

**load_model instead of a file.** `OpenDSSEngine.load_model` (the script command by
command, no file) takes 7.2 ms (IEEE 13) / 12.8 ms (IEEE 123) in the reused engine,
against 17.6 / 17.7 ms for write file + `clear` + compile in the same engine. Not
worth a different path: the run's DSS script is kept under
`name_results/grid/<design>/` anyway (project file rules), and the file is what
`compile` reports errors against.

**Validation in the child** costs 1.8-13 ms and repeats what the designer's live
validation (D5) has already done on the same model. Keep it: it checks what OpenDSS
actually compiled, and it is cheap.

**Memory.** Idle working set of the warm child after 11 runs: 72-74 MB with a new
engine per run, 54-56 MB with one reused engine (tasklist).

## Shutdown and recovery (warm child)

How `WarmGridChild` (warm.py) behaves; every row is a test in `test_spike6.py`, the
times are bench.py on IEEE 123.

| event | what happens | measured |
|---|---|---|
| `stop()` (designer closed, app quits) | sends STOP; the child closes its engine and returns; join 2 s, then terminate if still alive; pipe closed. Idempotent (second call returns None) | 57-106 ms, exit code 0 |
| parent goes away without STOP | the child's `recv` gets EOF and it returns (exit code 0); `daemon=True` also ends it when the parent exits normally | test: pipe closed -> exit 0 |
| Stop during a run (`cancel()` from another thread) | terminate at once; the run returns CANCELLED; the child is discarded | returns 15 ms after cancel |
| crash during a run (`os._exit(3)`) | the run returns ERROR "grid runner process exited with code 3"; discarded | - |
| killed while idle | the next `run` sees a dead process and spawns a new child before sending | next run 1.1 s |
| timeout (child does not answer) | TIMEOUT after the run's timeout; terminated and discarded | 0.5 s timeout -> returned in 0.56 s |
| next run after any of these | a new child (`restarts` + 1), i.e. a cold run | 0.88-1.13 s |

Rules the warm child follows:
- A failed run (ERROR, TIMEOUT, CANCELLED) always ends the child: after a Stop or a
  timeout it may be anywhere inside OpenDSS, so it is never reused. Nothing is retried
  automatically; the failed run reports its own outcome and the next Run respawns.
- One request in flight at a time; replies carry the request's run id.
- On Windows a `send` of a large request on the pipe returns only when the child has
  read it (found while writing the tests: a busy child blocks the sender). The parent
  therefore sends only to an idle child, and if the child is found dead during the
  send the request never reached it, so it is sent once to a new child.
- No global mutable state: the parent's state is the `WarmGridChild` instance (owned by
  one controller), the child's is the locals of `warm_main` (test checks the module
  globals of child.py and warm.py).

## Recommendation

**A warm child, with a new OpenDSSEngine (new DSS context) per run.**

- It cuts a designer Run from 0.73-0.78 s to 35-54 ms on IEEE 13/123, under the
  ~100 ms at which a result feels immediate; the cold start-up (about 0.7 s) is paid
  once per design, and again only after a failed run.
- It keeps the rule 5 pattern: spawn context, a Pipe (duplex instead of one-way, the
  only change), a daemon Process, terminate on Stop, the helper's statuses. OpenDSS
  stays out of the UI process.
- A new engine per run costs 8-12 ms more than reusing one context, but every run
  starts from a fresh context (rule 18: one engine, one context), so no setting of one
  design can reach the next; results are the same bit for bit either way.
- Spawn the child when a design is created or opened (only if
  `OpenDSSEngine.available()`), stop it when the last design closes and at app quit.
  Without dss-python nothing is spawned and Run stays disabled.
- The process helper must not be copied (rule 5, "reuse, don't copy"): the task that
  builds the run path (D5) should move the spawn + Pipe + daemon + terminate code of
  `ChildProcess` into one function in `core/blocks/process.py` and add the warm class
  there next to `ChildProcess`; GridRunner keeps the one-shot API for .dss files
  (open, G6 view), the designer's `GridController.runModel` uses the warm child.

Not needed for v0.5: incremental model updates (the whole model and result pickled
both ways cost about 4 ms on IEEE 123), a pool of children, progress reporting during
a warm run, reusing the DSS context.
