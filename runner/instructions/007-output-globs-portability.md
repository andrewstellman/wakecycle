# Instruction 007 — FR-73 `output_globs` portability fix (Python 3.10 floor)

## What this is
Orchestrator independent verification (Cowork, on **Python 3.10.12** — arunner's supported floor: "user-level Python 3.10+") found that instruction 006 (on `fr74-continue-past-stall`) has a **Python-version portability defect the self-Council missed** — it ran only on Python **3.14.6**.

`_newest_output_mtime` (`arunner/engine/tick.py`, the `for p in root.glob(pat)` scan, ~line 1799 — re-confirm the line on the branch) uses `pathlib.Path.glob`. **Python 3.13 changed `Path.glob` so a trailing `**` matches files; on 3.10–3.12 it matches directories only.** So an `output_globs` pattern like `["quality/**"]` — the *documented* example for QPB-style workers — matches **no files** on the floor → `_newest_output_mtime` returns `None` → `_output_age_secs` returns `None`. Per FR-74's "unmeasurable output HOLDS," a genuinely-hung worker that has `output_globs` set is then **never reclaimed at `stall_reclaim_minutes`** — it falls back to the 720-min hard cap, silently defeating FR-74's fast reclaim for the feature's primary use case.

**Evidence (orchestrator, Python 3.10.12, full suite on `fr74`):** `1 failed, 482 passed, 32 subtests passed`. The single failure is `tests/test_run_robustness.py::OutAgeDataLayer::test_out_age_globs_scope_the_scan` (`int(None)` → `TypeError`, line 381). The core fix is sound — both load-bearing pins (drain + output-fresh) PASS on the floor; **only the optional `output_globs` scan is broken.** Confirmed directly on 3.10.12: `Path.glob("quality/**")` → `['quality']` (dir only), while `Path.glob("quality/**/*")` and `glob.glob(..., recursive=True)` both match the file.

## Prerequisite / worktree (do NOT land)
This fix goes on the **existing** `fr74-continue-past-stall` branch, on top of 006's commits (`68a3cc3` + `5695c9f`), in the worktree `~/Documents/arunner-fr74`.

**Pre-flight:** confirm that worktree exists and is on `fr74-continue-past-stall` (`git -C ~/Documents/arunner-fr74 rev-parse --abbrev-ref HEAD` → `fr74-continue-past-stall`, HEAD `5695c9f`). If the worktree is missing, recreate it: `git worktree add ~/Documents/arunner-fr74 fr74-continue-past-stall`. **Do NOT merge or land** — the operator lands `fr74` only after the orchestrator's floor re-verify.

## The fix
Make the `output_globs` scan in `_newest_output_mtime` match **files** for any pattern, portably on Python **3.10+**:

- **Preferred:** use the stdlib `glob` module instead of `Path.glob`. For each pattern `pat`: `glob.glob(os.path.join(str(root), pat), recursive=True)`, keeping only files (`os.path.isfile`) when taking the newest mtime. `glob.glob(..., recursive=True)` matches files under a trailing `**` on 3.10+ (verified), is version-stable, and also handles non-`**` patterns (`["*.json"]`) and multi-glob lists.
- (Equivalent alternative if you prefer `pathlib`: normalize a pattern whose final segment is exactly `**` to `**/*`. The `glob.glob` route is cleaner and covers every shape — pick one and justify it in the output.)
- **Preserve everything else:** the existing `file_cap` bound (no unbounded walk), the per-render memoization, VCS-dir pruning, the **no-globs path** (already correct on the floor — `test_out_age_newest_mtime_correct` passes), `test_outage_scan_is_bounded`, and the **display-only invariant** (OUT-AGE never a lifecycle input). NFR-3: `glob`/`os` are stdlib — no new dependency.

## Tests
- The existing `test_out_age_globs_scope_the_scan` must now **PASS on the floor** (it currently fails on 3.10–3.12). Annotate it as the portability pin (docstring: "FR-73: a trailing `**` must match files on the Python 3.10+ floor; pre-3.13 `Path.glob('x/**')` matched directories only — verified bug 2026-06-24, instr 007").
- Add coverage that the fix is not `**`-specific: a non-`**` pattern (e.g. `output_globs: ["*.txt"]`) and a multi-glob list both resolve to the correct newest mtime.
- **Important — your run can't prove this fix.** The test already PASSES on your Python 3.14.6 (the bug only manifests pre-3.13), so a green suite on your interpreter is NOT evidence the portability is fixed. Make the impl version-stable **by construction** (the documented `glob.glob(recursive=True)` behavior). Run `python3 -m pytest tests/ -q` ×3 and **report your Python version**; the **orchestrator independently re-verifies on Python 3.10.12 (the floor) — that run is the hard gate** (the check the original Council lacked).

## Council
Single-reviewer self-Council (small, deterministic portability change) with an explicit charter: (a) the glob scan now matches files for `**`, non-`**`, and multi-glob `output_globs` on Python 3.10+, and stays stdlib + bounded (`file_cap`) + memoized; (b) **no regression** to the no-globs path, to FR-74's reclaim guard consuming the OUT-AGE signal, or to the display-only invariant. Write the verdict to `runner/reviews/007_self_council/` (single panel is fine for this scope). Iterate to SHIP.

## §9 / requirements
No new FR (this fixes FR-73's implementation). Add a one-line note to the FR-73 §9 row that `output_globs` is now Python-3.10+ portable (records the fix); leave US-19/20 and UC-15/16 unchanged.

## Commit / output
Focused commit on `fr74-continue-past-stall` (do **NOT** push/merge — operator lands `fr74` after the orchestrator's 3.10 re-verify). Output → `outputs/007-output-globs-portability.md`: the before/after, the portable approach chosen + why, the test deltas, full-suite count + **your Python version**, the single-panel verdict, and `git log --oneline -3`. The orchestrator then re-verifies on Python 3.10.12; only then does the operator land `fr74` → `main`, and the hard checkpoint fully clears.
