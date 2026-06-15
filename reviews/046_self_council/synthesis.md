# Instruction 046 self-council synthesis — FR-57 live enqueue + FR-58a cadence + FR-58b SKILL

*Mandatory 3-panel (engine-state hazards). Three fresh-context, role-locked, adversarial
reviewers verifying on disk: reproducing the stage-and-absorb end-to-end, mutation-biting
the load-bearing pins, driving the real adapter on the default-grace path, and checking the
§9 honesty split. Date: 2026-06-15.*

| Panelist | Charter | Verdict |
|----------|---------|---------|
| `panelist_A_enqueue_correctness.md` | stage-and-absorb race-free; append-only; --check pre-gate; placeholders unresolved; positional rebuild | **FIX-REQUIRED → SHIP** (after the STOP fix) |
| `panelist_B_cadence_correctness.md` | interval decoupled from stall/3; first-scan; adapter-synthesis live; --check keepalive>grace; ACTIVITY moves on the DEFAULT path | **SHIP** |
| `panelist_C_honesty_regression.md` | FR-58b stays DESIGNED, graded separately; §9 flips cite real tests; no regression | **SHIP** |

## Outcome: unanimous SHIP (round 2, after one fix)

### Panelist A — enqueue correctness (FIX-REQUIRED → SHIP)
Round 1 confirmed: `add` writes ONLY `incoming/` (`test_add_never_touches_live_files` —
plan/status byte-identical); the absorb runs inside `tick()` under the caller's `.tick.lock`;
append-only `idx = len(entries)+1` (PIN bites — a renumber clobbered run-01's task_id, the exact
swallowed-except job-drop the spec names); the `--check` pre-gate rejects a bad add before
`incoming/` is even created; placeholders stored verbatim; positional rebuild intact, no `done`
write; add-to-a-done-run re-activates. **Blocking defect found:** `_absorb_incoming` ran BEFORE
the STOP read-only gate, so a STOP tick with a pending add mutated plan.json/harness_status.json
and consumed the staged file — violating FR-10/FR-35. **Fix:** gate the absorb behind
`if not (run_dir/"STOP").exists()`; a staged add waits untouched while STOP is present and
absorbs once STOP clears. Round 2 reproduced the fix (STOP tick → 3 entries + file preserved;
un-STOP tick → 4) and confirmed the regression test `test_stop_tick_does_not_absorb` bites and
nothing else regressed → **SHIP**.

### Panelist B — cadence correctness (SHIP)
`_resolve_keepalive_secs` returns the explicit `--keepalive-seconds` else ~45s, floored to 1s —
NOT `min(grace, stall/3)`; both adapters call it (the old `keepalive_interval_secs` survives only
as the cadence test's old-formula assertion). `emit_first` re-scans+emits as ONE event (first-scan
PIN bites). `_adapter_worker_cmd(entry, plan)` synthesizes all three knobs (entry>plan>default),
wired into `_dispatch` — independently confirmed `--launch-grace-minutes 20 --stall-threshold-minutes
60 --keepalive-seconds 30`. `--check` rejects keepalive>grace (plan + per-entry). ACTIVITY moves on
the DEFAULT grace path (`RealDefaultGracePath` grace>0 + the clock-seam `test_label_moves`); the
previously-grace-0 fixtures honestly updated to pass an explicit `--keepalive-seconds`, so the
regression is no longer masked. 310 passed. (One stale grace-0 docstring noted → fixed.)

### Panelist C — honesty & regression (SHIP)
FR-58b §9 row stays PENDING/DESIGNED; the SKILL states verbatim it is "NOT verified by any engine
test, and a green FR-58a engine test does not satisfy it" — no conflation (the FR-50/FR-54 overclaim
pattern avoided). The FR-57/FR-58a §9 flips cite `test_live_enqueue` / `test_activity_cadence`,
which exist and pass (20). `test_positioning_honesty` green; Windows-floor row still PENDING. No
regression (290 → 310 then 311 after the STOP regression test). The FR-56/UC-12 fixtures updated
honestly (explicit `--keepalive-seconds`, no assertion loosened). Diff confined to
tick.py/heartbeat.py/cli.py + REQUIREMENTS/TRACEABILITY/SKILL/BOOTSTRAP + the test files.

## Net
FR-57 lands as stage-and-absorb (`add` → `incoming/`, the tick absorbs under the `.tick.lock`,
append-only, placeholders unresolved, no `done` write, **STOP-gated read-only**). FR-58a makes the
activity-refresh cadence a configurable `--keepalive-seconds` (~45s default, decoupled from stall/3,
first-scan-at-start) and fixes the latent inert-knob bug so grace/stall/keepalive all flow to the
adapter; `--check` rejects keepalive>grace. FR-58b hardens the SKILL/runbook so the rung-1
orchestrator prints `status_table` every tick — per-host DESIGNED, graded separately. One blocking
defect (STOP/absorb ordering) found and fixed; the load-bearing invariants are mutation-pinned.
Suite 290 → 311.
