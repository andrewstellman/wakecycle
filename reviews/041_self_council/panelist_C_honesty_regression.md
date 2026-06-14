# Panelist C — Honesty & Regression (instr 041)

Charter: gradeable-vs-reported honesty, "cheap not free", no overclaim, no regression, sound new test.
Work is uncommitted; reviewed via `git diff HEAD` + untracked files.

## 1. Disk-gradeable vs agent-reported is honest — PASS
`checker.py` docstring (ACCEPTANCE LAYER block) splits three tiers and they match the code:
- **disk-gradeable alone**: `done` (harness_status), `stopped` (falls back to `os.path.isfile(run_dir/STOP)` when no meta), `counts`/`run_states`/`paused`/`summary_present`/`results_for_terminal`, and continuation `verdict_present`/`final_done` from `journal.ndjson` (`_read_journal`) + status.
- **needs before-snapshot**: `stop_readonly` (UC-3) reads `_before_snapshot.json` / `expected["before_snapshot"]` and compares; flags when absent.
- **needs runner meta**: `max_inflight_*` now appends an explicit "requires the runner's per-tick trace — not gradeable from durable artifacts alone" failure when `has_meta` is False; `byte_identical`/cadence/violations remain meta-backed.
Matches `docs/ACCEPTANCE_TESTS.md` §"Disk-gradeable vs. agent-reported" (line 39-41) and §Grading (line 12-20). Meta-only keys are flagged, not silently passed — verified by `test_meta_only_key_is_flagged_not_silently_passed`.

## 2. "Cheap, not free" stated — PASS
`docs/ACCEPTANCE_TESTS.md:10`: "worker dispatch is stubs, so **zero worker API spend**. The in-agent cases still spend the agent's own tick tokens plus a trivial subagent call per job — **cheap, not free**. The ticker cases are genuinely free." The stub plan worker prompt also says "no real work, no API spend beyond this turn." Honest; not hidden as free.

## 3. No overclaim — PASS
Design doc status (line 52-58) frames 041 as the FOUNDATION: items 1-3 (checker+CLI, stub plans, demonstrate ONE in-agent test) built; items 4-5 (AGENTS.md, real runs) explicitly ahead. Cursor/Copilot stay DESIGNED (line 49); a macOS Claude pass "does **not** clear the §9 Windows floor row" (line 50). No per-UC-suite-done, no Windows/cross-platform, no per-agent result claimed. `tests/test_positioning_honesty.py` — **7 passed**, incl. `test_floor_windows_row_stays_pending`. Diff touches only `checker.py` + new untracked test/plan files; no §9/README/SDLC edit that could flip a row.

## 4. No regression — PASS
Full suite **267 passed** (257 + 10). `git diff HEAD -- tests/integration/checker.py` is additive: 120 insertions / 15 deletions, all changed lines convert a meta-only read into "meta if present, else durable fallback" (done/stopped/stop_readonly/verdicts/final_done) or add `_read_journal` + `main()` CLI. Existing meta-based grading path unchanged when `has_meta` is True. `test_continuation.py` + `test_checker_independence.py` + `test_acceptance_checker.py` — **33 passed**.

## 5. New test sound, not vacuous — PASS
`tests/test_acceptance_checker.py` real assertions: durable pass with NO meta; PIN `test_durable_grading_detects_wrong_run_state` (run-02 actually failed → must appear in failures); wrong done+counts; verdict-present from durable journal (positive + negative "never emitted → fails"); meta-only key flagged; UC-3 snapshot match/mismatch/missing; CLI exit-0 pass and exit-1 with `run-02` in stdout. Panelist A independently ran the mutation bite (`shutil.copy2` snapshot, no-op'd `run_states`, pycache purge, PIN FAILED, clean restore) — confirms non-vacuous.

VERDICT: SHIP
