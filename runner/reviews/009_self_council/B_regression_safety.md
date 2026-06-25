# Panel B — regression-safety (instr 009, FR-76)

**Verdict: SHIP.**

## Charter
the gen-007 stop/restart pin holds; a partial target is **redone not skipped**;
FR-6 no-double-dispatch preserved; the "doneness from declared status" invariant
intact (done_check is the narrow, explicit declared exception); shell + subagent
parity; no perturbation to FR-74's reclaim or FR-72's launch path.

## Findings

1. **gen-007 stop/restart pin holds.** `test_stop_restart_skips_done_dispatches_
   remainder` (N=8, K=3 done) → exactly the 5 remainder dispatched, the 3 done
   skipped (never dispatched, synthesized COMPLETED sentinel), none lost.
   Mutation-verified: neuter the pre-dispatch eval → all 8 dispatch → bite.

2. **Partial target REDONE, not skipped.** `test_partial_target_not_satisfied_is_
   redone_not_skipped` → an unsatisfied done_check dispatches (claimed), not
   skipped. Mutation-verified: skip-on-unsatisfied → bite. This is the load-bearing
   "zero loss" half of the acceptance.

3. **FR-6 no-double-dispatch preserved.** done_check is evaluated ONLY for runs
   whose `state == "queued"` (the loop's first guard). A claimed/running/stalled
   job is never re-checked, so an artifact appearing mid-flight cannot flip a live
   run to done_skipped or spawn a second dispatch. `test_inflight_job_with_done_
   check_not_double_dispatched` pins it (artifact created mid-flight + worker kept
   alive → stays claimed/running, not done_skipped). The once-per-run-dir
   `done_checked` guard adds no queued-return path (claimed→…→terminal is one-way).

4. **Doneness-from-declared-status invariant intact.** The only new doneness source
   is the operator's explicit `done_check` predicate — not engine output parsing.
   `OUT-AGE` stays display-only; `_terminal_status_of` is untouched. The invariant
   text in PLANNED §9 and REQUIREMENTS now names done_check as the one exception.

5. **Shell + subagent + all-mode parity.** The eval sits BEFORE the mode branch in
   `_dispatch`, so it applies uniformly to agent/command/log/shell/pipeline jobs.
   No mode-specific path was touched.

6. **No perturbation to FR-74 / FR-72.** `_advance` (reclaim) and the launch-grace
   path are untouched; the full FR-73/74 suite (both load-bearing pins included)
   stays green. The done_check block adds only a pre-claim branch for queued runs.
   Full suite **486 → 494 passed, 1 skipped** (the +8 are all FR-76; the 1 skip is
   instr 008's installed-metadata pin, unrelated).

7. **Schema/STATE_MACHINE copies byte-identical** (`diff -q` clean for both
   `plan.schema.json` and `STATE_MACHINE.md`); the schema is valid JSON; `--check`
   accepts the new key and validates its shape (`_check_done_check`), in lockstep
   with the schema oneOf branches (done_check added to all five).

8. **Bounded cost (noted, accepted).** A `command`-shape done_check spawns a
   subprocess (timeout 30s, mirroring auth_check/gate). Evaluated at most ONCE per
   job per run-dir (the `done_checked` guard), so steady-state cost is one probe
   per job on (re-)entry — not per tick. The `artifact` shape is a cheap bounded
   glob. No new runtime dependency (stdlib `glob`/`subprocess`); NFR-3 intact.

No regressions found.
