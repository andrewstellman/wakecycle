# Panelist A — Checker Parity + CLI + Independence (instr 041)

Reviewer A, independent/adversarial. Repo `arunner` @ `/Users/andrewstellman/Documents/wakecycle`, git `main`, uncommitted working tree. Authoritative design: `docs/ACCEPTANCE_TESTS.md`.

## 1. Durable-artifact grading reaches the SAME verdict as the meta path where both apply — PASS

`check()` (`tests/integration/checker.py`) now branches on `has_meta = os.path.isfile(meta_path)`. The core verdict keys read from durable disk regardless of meta:

- `done` ← `harness_status.json` (line 144)
- `counts` ← `status["counts"]` (subset match, 162-167)
- `run_states` ← `status["runs"][run]["state"]` (169-174)
- `paused` ← `status["paused"]` (157-160)
- `summary_present` ← `SUMMARY.md`/`summary.json` on disk (232-253)
- `results_for_terminal` ← `results/` dir (282-294)

These never read meta, so durable and meta paths are identical for them. The meta-dependent keys fall back correctly:

- `stopped` → `meta.stopped` if meta else **STOP file on disk** (`os.path.isfile(run_dir/"STOP")`, 150-155)
- `stop_readonly` → `meta.pre_stop_status` if meta else **agent-provided `_before_snapshot.json`** (or `expected["before_snapshot"]`), 261-280
- `verdict_present` → meta `tick_trace` if meta else **`journal.ndjson` verdict lines** via `_read_journal` (307-316)
- `final_done` → `meta.final_done` if meta else **`status["done"]`** (317-321)

The runner suite still uses meta where present (`has_meta` gate prefers meta). RAN `python3 -m pytest tests/test_integration_scenarios.py tests/test_acceptance_checker.py -q` → **14 passed**. The 257 deterministic scenarios run as `subTest`s inside `test_each_scenario_passes` (4 pytest methods, all green; scenarios discovered from `tests/integration/scenarios/`). The new durable tests (`DurableGrading`, `StopReadonlySnapshot`, `CheckerCLI`) all pass. Both paths green together.

## 2. Meta-only keys are FLAGGED, not silently passed — PASS

- `max_inflight_*`: on a meta-less run-dir emits `"max_inflight_*: requires the runner's per-tick trace (_check_meta.json) — not gradeable from durable artifacts alone"` (180-183). Verified directly AND via `test_meta_only_key_is_flagged_not_silently_passed` (run: pass).
- `min/max_next_cadence`: meta-less → `"no next_tick_minutes recorded in the tick trace"` (204-205). Verified.
- `byte_identical_results`: meta-less → flagged as `"... not in pre-CANCEL snapshot"` even when the result file is present (223-224). Verified both file-missing and file-present-no-snapshot cases.
- continuation `violations`: meta-less, expected non-empty (e.g. `silent_abandonment`) → fails with `"continuation violations: expected ['silent_abandonment'], got []"` (303-305) because the host-stop/eval-now signals live in meta. Does NOT silently pass.

Only `max_inflight_*` emits a bespoke "requires the runner" string; the other three fail safely via their own natural guards (missing trace / missing snapshot / detector mismatch). All four are flagged, none silently pass. The durable-vs-snapshot-vs-meta split is documented in the module docstring (lines 14-33) and matches the code branches.

## 3. CLI correctness — PASS

Built a durable run-dir myself and ran `python3 tests/integration/checker.py <run-dir> <expected.json>`:

- Pass → `CHECK PASSED: <run-dir>`, **exit 0**
- Fail → `CHECK FAILED (2):` + failure lines (`done`, `runs[run-02].state`), **exit 1**
- One arg (bad args) → usage to stderr, **exit 2**
- Nonexistent run-dir → `checker: not a run-dir: ...`, **exit 2**

`main()` (342-365) returns 2/1/0; `if __name__` wraps `sys.exit(main())`. Correct.

## 4. Stdlib-independence intact — PASS

RAN `python3 -m pytest tests/test_checker_independence.py -q` → **2 passed**. AST scan confirms checker imports stdlib only. `_read_journal` (325-339) uses only `os`/`json`; `main` does a local `import sys` (stdlib). No repo imports added; no relative imports.

## 5. Load-bearing pin bites — PASS

Snapshotted `checker.py` via `shutil.copy2` to `/tmp/checker_pristine_041.py`. No-op'd the `run_states` check (`if got != st: pass`), purged `tests/**/__pycache__`, ran `test_durable_grading_detects_wrong_run_state` → **FAILED** (`AssertionError: False is not true : []`). Restored from the pristine snapshot via `shutil.copy2`, re-purged pycache, re-ran `test_acceptance_checker.py` + `test_checker_independence.py` → **12 passed**. `git diff HEAD --stat` back to `120 insertions(+), 15 deletions(-)` — clean restore.

## Adversarial notes (non-blocking)

- The `max_inflight_*` branch appends the flag but then still runs the (empty) trace loop; harmless — `worst` stays 0 and no second failure is appended that could mask the flag. Cosmetic only.
- "Same verdict where both apply" holds because the durable fallbacks read the same ground-truth state the meta mirrors (STOP file, journal verdicts, status.done). No divergence found.

## VERDICT

VERDICT: SHIP
