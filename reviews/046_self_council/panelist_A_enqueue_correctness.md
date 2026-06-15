# Panelist A — enqueue correctness (FR-57)

Charter: stage-and-absorb race-free; append-only numbering; `--check` pre-gate; placeholders
unresolved; positional rebuild intact after absorb.

## Round 1 — FIX-REQUIRED
PASS: `add` writes ONLY `<run-dir>/incoming/` and never the live files
(`test_add_never_touches_live_files` — plan.json/harness_status.json byte-identical after an add);
the absorb runs inside `tick()`, under the caller's `.tick.lock`. Append-only `idx = len(entries)+1`
— the PIN bites (a renumber to `idx = added+1` clobbered run-01's task_id `a`→`c`, the silent
job-drop the spec names). `--check` rejects a bad add (`command:"notarray"` → FAILED, rc 1) and
`incoming/` is never even created. Placeholders stored verbatim. Positional rebuild intact
(`len(entries)==len(runs)`), no `done` write, add-to-a-done-run re-activates.

**BLOCKING:** `_absorb_incoming` ran at the start of `tick()` BEFORE the STOP read-only gate, so a
STOP tick with a pending `incoming/` add appended to plan.json (3→4), scaffolded run-04, and consumed
the staged file — `plan changed=YES, status changed=YES`. Violates FR-10/FR-35 (a STOP tick must
mutate nothing, consume nothing). Fix: gate the absorb behind `not (run_dir/"STOP").exists()`.

## Round 2 — SHIP (after the fix)
`tick.py` now gates: `if not (run_dir / "STOP").exists(): _absorb_incoming(run_dir)` at the top of
`tick()`. Live repro: STOP tick left **3 entries + the staged file preserved**; clearing STOP and
re-ticking absorbed to **4**. The regression test `test_stop_tick_does_not_absorb` (PIN) passes and
bites (reverting the gate → `"STOP tick absorbed (mutated plan.json) -- not read-only"`). Full module
10 passed; the original PASS items (append-only, never-touches-live-files, --check, placeholders,
positional, no-done-write, reactivate) all hold. No new STOP-window hazard (the top gate reads STOP
once; the later `stop = ...` recomputes; the absorb only runs when STOP was absent at the gate).

VERDICT: SHIP
