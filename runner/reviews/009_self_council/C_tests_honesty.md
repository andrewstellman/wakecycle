# Panel C — tests / honesty (instr 009, FR-76)

**Verdict: SHIP.**

## Charter
the stop/restart pin AND the partial-redone pin both mutation-bite; FR-76 + US/UC
added (next-free, no reuse); §9 honest.

## Findings

1. **Both load-bearing pins mutation-bite (executed in-tree, restored):**
   - `test_stop_restart_skips_done_dispatches_remainder` — MUTATION: replace the
     pre-dispatch `if _done_check_satisfied(entry):` skip with `if False:` → all N
     dispatch, nothing skipped → **FAIL** (`tests/...:512 AssertionError`). Restored → OK.
   - `test_partial_target_not_satisfied_is_redone_not_skipped` — MUTATION: invert to
     `if not _done_check_satisfied(entry):` → the unsatisfied target is wrongly
     skipped → **FAIL** (`tests/...:534 AssertionError`). Restored → OK.
   Both demonstrated on Python 3.14.6; the working tree was restored from a side
   copy via `/bin/cp -f` and the full set re-confirmed green (no mutation residue —
   grep clean).

2. **Coverage is honest and complete to the instruction's test list:**
   - both done_check shapes — `test_done_check_command_exit0_skips_nonzero_dispatches`
     (exit 0 skips / non-0 dispatches) + artifact via the stop/restart pin +
     `test_artifact_glob_shape_matches_file` (a trailing `**` matches files).
   - run-dir independence — `test_resume_from_fresh_run_dir_skips_done`.
   - FR-6 compose — `test_inflight_job_with_done_check_not_double_dispatched`.
   - `--check` shape validation — `test_done_check_requires_exactly_one_shape`
     (neither/both/each-single) + `test_done_check_bad_member_shapes` (empty
     string, empty argv, non-string argv, unknown key) + `test_clean_plan_with_all_
     new_knobs` extended with done_check.
   - disk-truth hygiene (A-F1) — queue/ + claimed/ empty after a skip.

3. **Numbering honest — next-free, no reuse.** FR-76 (FR-75 is the reserved retry
   seam, untouched), **US-21** (after US-20), **UC-17** (after UC-16). No existing
   FR/US/UC number changed.

4. **§9 row honest.** The new VERIFIED row names the real functions
   (`_done_check_satisfied`, `_synthesize_done_skip`, `_check_done_check`), the real
   test names, and the two mutation bites. Marked VERIFIED on the same basis as
   006/007/008 (the instruction's tests pass + mutation-verify); the orchestrator's
   independent re-verify remains the external gate.

5. **Honest scope note recorded** (Panel A): per-job done_check (not per-step);
   pipeline jobs gate as a whole. The `command` shape is exit-code only by design
   (no output parsing), consistent with `_eval_shell_gate`.

Counts: baseline **486 passed, 1 skipped** (main `3175c40`) → final **494 passed,
1 skipped ×3**, Python **3.14.6**. The +8 are all FR-76; the 1 skip is instr 008's
installed-metadata pin (unrelated, skips when arunner isn't pip-installed).
