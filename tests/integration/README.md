# Integration scenarios (FR-51)

The deterministic, ticker-driven regression net. Every later v0.1.0 increment
runs against these, so they pin **current** behaviour. Two disciplines keep
the suite trustworthy:

1. **Ticker-driven, never the agent loop.** Scenarios run via
   `bin/ticker.py --once` in a loop (`runner.py`), so a run is reproducible and
   CI-able and the flaky Class-C path never enters the regression net.
2. **Independent verdict.** The pass/fail check (`checker.py`) is plain Python
   that **imports the standard library only** — never the `wakecycle` package.
   The harness never grades its own homework. `test_checker_independence.py`
   enforces this mechanically (an AST scan that fails if the checker ever
   imports the harness).

## Scenario format

One folder per scenario under `scenarios/<name>/`, each with a single
`scenario.json`:

```json
{
  "description": "human-readable",
  "plan": { ... a normal wakecycle plan ... },
  "control": { "write_stop_after_tick": 2 },     // optional
  "expected": {
    "done": true,                                 // run reached done
    "stopped": false,                             // a STOP file halted it
    "counts": { "completed": 3, "failed": 0 },    // subset of harness_status counts
    "run_states": { "run-01": "completed" },      // per-run final state
    "max_inflight_le": 2,                          // pool never exceeded (staggering)
    "stop_readonly": true                          // the STOP tick changed nothing
  }
}
```

**Placeholders the runner substitutes** in the plan before `--init`:
`{STUB}` → `stub_worker.py`, `{SCENARIO_DIR}` → the scenario folder. The engine
then substitutes its own `{HEARTBEAT_PATH}/{TASK_ID}/{RUN_DIR}/{TARGET_REPO}/{HARNESS_BIN}`
block at dispatch (FR-21a: no model-transcribed paths).

## The controllable stub (`stub_worker.py`)

Heartbeats `STARTING` + `IN_PROGRESS × N`, then — if `--hold-file F` is given —
holds until `F` exists before its terminal line. That hold is what the control
iterations (PAUSE/stall/CANCEL) act against. Stdlib-only raw JSON appends (also
exercises the no-helper worker contract). Honours `WAKECYCLE_NOW` (the clock
seam).

## How a scenario is graded

`runner.run_scenario(scenario_dir, work_dir)` drives the ticks, records
`_check_meta.json` (the per-tick count trace + the pre-STOP snapshot + the
stopped flag) into the run-dir, then kills any held workers. `checker.check(run_dir, expected)`
reads `harness_status.json`, `results/`, heartbeats, claim locks, and the meta,
and returns a list of failure strings (empty == PASS). `test_integration_scenarios.py`
wires it, and proves the checker can actually fail (a deliberately-wrong
`expected` must produce failures).

## Dogfood

Because a scenario IS a wakecycle plan, this suite doubles as a worked example
of Wakecycle orchestrating a batch — the dogfood. (Dogfooding *measures* which
wake-up modes survive real use; it never *validates* a §9 evidence row — those
need a recorded matrix run, NFR-12.)
