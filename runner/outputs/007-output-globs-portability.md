# Output for 007-output-globs-portability.md
**Status:** completed

FR-73 `output_globs` portability defect (Python 3.10–3.12 floor) fixed on the
existing `fr74-continue-past-stall` branch, on top of 006 (`5695c9f`), in worktree
`~/Documents/arunner-fr74`. One focused commit, **local only — not pushed/merged**.
Single-panel self-Council: **SHIP**. **Demonstrated failing→passing on Python
3.12.13** (the 3.10–3.12 band); full suite **483 → 485 passed ×3** on Python 3.14.6.

## Pre-flight
- Worktree `~/Documents/arunner-fr74` present, on `fr74-continue-past-stall`, HEAD
  `5695c9f` (006's two commits `68a3cc3` + `5695c9f` present). Confirmed before any edit.

## The defect (before / after)

**Before:** `_newest_output_mtime`'s `output_globs` branch used
`for p in root.glob(pat)` (`pathlib.Path.glob`). Pre-3.13, `Path.glob('x/**')`
matches **directories only**; 3.13 changed it to also match files. So the
*documented* example `output_globs: ["quality/**"]` matched **no files** on arunner's
supported floor (3.10–3.12) → `_newest_output_mtime` → `None` → `_output_age_secs`
→ `None` → FR-74's reclaim guard reads "output unmeasurable → HOLD" → a genuinely
hung worker with `output_globs` set is **never reclaimed at `stall_reclaim_minutes`**,
silently falling back to the FR-72 720-min hard cap — defeating FR-74's fast reclaim
for its primary (QPB-style) use case.

**After:** the branch uses stdlib `glob.iglob(os.path.join(str(root), pat),
recursive=True)`, keeping only `os.path.isfile(fp)`. `recursive=True` matches files
under a trailing `**` on **3.10+** by documented, version-stable behavior — correct
**by construction**, not by a green suite on the dev interpreter.

## Portable approach chosen + why

- **`glob.iglob(..., recursive=True)`** (the instruction's "Preferred" `glob` route),
  with one deliberate refinement: **`iglob` (lazy iterator), not `glob` (eager
  list)**. `glob.glob` materializes the entire recursive match set before the
  `file_cap` check, which would weaken the bound for a pathological repo; the
  original `Path.glob` was a generator. `iglob` yields one path at a time, so the
  existing `seen > file_cap → return` guard caps work **without** first building a
  full list — preserving the lazy-bound property the instruction said to keep.
- Handles every `output_globs` shape: `**`, non-`**` (`*.txt`), and multi-glob lists.
- **VCS parity:** verified on 3.12 that `iglob('**', recursive=True)` does not
  traverse leading-dot dirs (`glob` skips dotpaths unless the pattern names the dot),
  so a `**` glob naturally excludes `.git`/`.hg`/`.svn` — agreeing with the no-globs
  branch's explicit `_OUTAGE_SKIP_DIRS` pruning.
- **stdlib only (NFR-3):** `glob` + `os` are stdlib; no new engine-path dependency.

## Files created / changed
| Path | Lines | Note |
|---|---|---|
| `arunner/engine/tick.py` | +11 / −3 | `import glob`; `output_globs` branch `Path.glob` → `glob.iglob(..., recursive=True)` + `os.path.isfile`/`os.stat`; portability docstring. No-globs `os.walk` path, `file_cap`, memoization untouched. |
| `tests/test_run_robustness.py` | +29 | `test_out_age_globs_scope_the_scan` annotated as the FR-73 portability pin; added `test_out_age_globs_non_doublestar_pattern` (`*.txt`) + `test_out_age_globs_multi_glob_list` (union newest). |
| `docs/REQUIREMENTS.md` | +1 (in-row) | FR-73 §9 row notes `output_globs` is now Python-3.10+ portable (records the fix). US-19/20, UC-15/16, FR-74 row unchanged. |
| `runner/reviews/007_self_council/SYNTHESIS.md` | new | single-panel self-Council, SHIP. |

## Commits made
- **`895da5e`** — *FR-73: make output_globs scan Python-3.10+ portable (instr 007)* —
  engine + tests + §9 note + council artifact. On `fr74-continue-past-stall`,
  **local only — not pushed, not merged** (operator lands `fr74` after the
  orchestrator's 3.10 re-verify).

`git log --oneline -3`:
```
895da5e FR-73: make output_globs scan Python-3.10+ portable (instr 007)
5695c9f FR-73: gate OUT-AGE scan to in-flight runs (self-Council B-F1) + 006 council artifacts
68a3cc3 FR-74 continue-past-stall + FR-73 OUT-AGE output-activity (instr 006)
```

## Acceptance criteria — pass/fail per item
- `test_out_age_globs_scope_the_scan` PASSES on the floor band — **PASS** (verified on
  3.12.13; FAILS there with the reverted `Path.glob` form → `int(None)` TypeError).
- Annotated as the portability pin (docstring naming the pre-3.13 bug) — **PASS**.
- Coverage that the fix is not `**`-specific: non-`**` + multi-glob — **PASS**
  (`test_out_age_globs_non_doublestar_pattern`, `test_out_age_globs_multi_glob_list`).
- Version-stable by construction (`glob` `recursive=True`) — **PASS** (+ shown
  failing→passing on 3.12).
- `file_cap` bound preserved (no unbounded walk) — **PASS** (`iglob` lazy;
  `test_outage_scan_is_bounded` green).
- Per-render memoization preserved — **PASS** (`_output_age_secs` memo untouched).
- VCS-dir pruning preserved — **PASS** (`**` skips dotpaths; `test_vcs_dirs_pruned` green).
- No-globs path unchanged — **PASS** (`test_out_age_newest_mtime_correct` green).
- Display-only invariant intact — **PASS** (`test_out_age_is_display_only_not_lifecycle` green).
- FR-74 reclaim guard still consumes the OUT-AGE data signal — **PASS** (both 006
  load-bearing pins green).
- stdlib only (NFR-3) — **PASS**.

## Council (required) — single-panel self-Council: **SHIP**
`runner/reviews/007_self_council/SYNTHESIS.md`. Charter (a) the glob scan now matches
files for `**`/non-`**`/multi-glob on 3.10+ and stays stdlib + bounded (`file_cap`) +
memoized — SHIP; (b) no regression to the no-globs path, the FR-74 guard, or the
display-only invariant — SHIP. One self-initiated hardening applied before the
verdict (`glob.glob` → `glob.iglob` to keep the bound lazy). Evidence beyond the
dev interpreter: reproduced the defect on **Python 3.12.13**, showed the buggy
`Path.glob` form fails 2 data-layer tests there, and the `iglob` fix makes all 7
pass — real failing→passing in the affected band.

## Tests
- **Floor band (Python 3.12.13):** `OutAgeDataLayer` 7/7 OK with the fix; 2 FAIL with
  the reverted `Path.glob` form (demonstrated via a temporary revert, then restored
  via `/bin/cp -f` from a side copy; impl SHA re-checked).
- **Dev (Python 3.14.6):** full suite `python3 -m pytest tests/` → **485 passed ×3**
  (baseline 483 from 006 + 2 new portability tests). The bug does not manifest on
  3.14 (≥3.13), so this run alone is NOT the portability evidence — see the 3.12 run
  and the by-construction argument.
- **Hard gate (orchestrator):** independent re-verify on **Python 3.10.12** (the exact
  floor) — the check the original 006 Council lacked.

## §9 rows flipped
None added/flipped (instruction: no new FR). Appended a one-line portability note to
the **existing** FR-73 §9 row (records the fix + names the three pinning tests +
"demonstrated failing→passing on Python 3.12"). US-19/20, UC-15/16, and the FR-74
row left unchanged.

## Notable observations
- **`iglob` over `glob`** is the one judgment call beyond the instruction's letter —
  same matching, but lazy, so the `file_cap` bound is genuinely preserved rather than
  applied after an eager full-list materialization. Justified in the output + council.
- **`**` excludes dotpaths by default** — a happy alignment: the globs branch needs no
  explicit VCS pruning to match the no-globs branch, because `glob`/`iglob` skip
  leading-dot dirs unless the pattern names the dot. An operator can still opt a dot
  dir in explicitly (e.g. `[".git/**"]`) — same contract as before.
- **Local 3.12 availability** let me convert the instruction's "your run can't prove
  this" caveat into actual failing→passing evidence on the affected version band,
  short of the 3.10.12 hard gate.
- **Process:** committed AFTER the council this tick (no mutation-test `git checkout`
  was used on uncommitted work); the one temporary impl revert for the 3.12
  failure-demo was done on a committed-free copy and restored before staging.

## Next action expected from orchestrator
Independent re-verify on **Python 3.10.12** (the floor) — the hard gate. On a green
floor run, the operator lands `fr74-continue-past-stall` → `main` and deletes the
branch + worktree; the run-robustness hard checkpoint then fully clears. Next in the
single-trunk 1.1.0 line (per `docs/PLANNED_run_robustness.md`): version
source-of-truth → FR-76 → FR-75 → FR-77 → doc-sync; tag `v1.1.0` when complete.
