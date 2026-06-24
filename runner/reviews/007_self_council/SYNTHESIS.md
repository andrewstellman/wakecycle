# Self-Council — instr 007: FR-73 `output_globs` Python-3.10+ portability fix

**Scope:** single-panel self-Council (small, deterministic portability change), as
the instruction specifies. Branch `fr74-continue-past-stall`, on top of 006
(`5695c9f`). **Verdict: SHIP** (round 1, with one self-initiated hardening applied
before the verdict — `glob.glob` → `glob.iglob`).

## Charter (from instr 007)

(a) The `output_globs` scan now matches **files** for `**`, non-`**`, and
multi-glob patterns on Python **3.10+**, and stays **stdlib + bounded (`file_cap`)
+ memoized**.
(b) **No regression** to the no-globs path, to FR-74's reclaim guard consuming the
OUT-AGE data signal, or to the display-only invariant.

## The defect (independently reproduced, not taken on faith)

Pre-3.13 `Path.glob('x/**')` matches **directories only**; 3.13 changed it to also
match files. The documented `output_globs` example `["quality/**"]` therefore found
**no files** on the 3.10–3.12 floor → `_newest_output_mtime` returns `None` →
`_output_age_secs` returns `None` → FR-74's guard reads "output unmeasurable →
HOLD" → a genuinely-hung worker with `output_globs` set is **never reclaimed at
`stall_reclaim_minutes`**, falling back to the 720-min hard cap. This silently
defeats FR-74's fast reclaim for its primary (QPB-style) use case.

Reproduced directly on **Python 3.12.13** (in the 3.10–3.12 band):
- `Path.glob('quality/**')` → `['quality']` (dir only, no file).
- `glob.glob('quality/**', recursive=True)` filtered to files → `['quality/fresh.txt']`.

## (a) The fix matches files, portably, and stays bounded/stdlib/memoized — SHIP

- **Impl:** the `globs` branch now uses stdlib `glob.iglob(os.path.join(str(root),
  pat), recursive=True)`, keeping only `os.path.isfile(fp)`. `glob` with
  `recursive=True` matches files under a trailing `**` on **3.10+** by documented,
  version-stable behavior — the fix is correct **by construction**, not by a green
  suite on the dev interpreter.
- **`**`, non-`**`, multi-glob all covered:** `test_out_age_globs_scope_the_scan`
  (`["quality/**"]`), `test_out_age_globs_non_doublestar_pattern` (`["*.txt"]`,
  top-level only), `test_out_age_globs_multi_glob_list` (`["quality/**",
  "reports/**"]`, newest across the union). All three PASS on **3.12** and **3.14**.
- **Bounded:** I chose `iglob` (lazy iterator) over `glob` (eager list) on purpose
  — `iglob` yields one path at a time, so the existing `seen > file_cap → return`
  guard still caps work without first materializing a full match list. The original
  `Path.glob` was also a generator, so this **preserves** the lazy-bound property
  rather than weakening it. `test_outage_scan_is_bounded` (cap 0 → None) still
  passes.
- **Stdlib (NFR-3):** `glob` and `os` are both stdlib. No new runtime dependency on
  the engine path.
- **Memoized:** untouched — `_output_age_secs` still keys the memo on
  `(str(root), tuple(globs))` and only calls `_newest_output_mtime` on a miss.

## (b) No regression — SHIP

- **No-globs path:** literally unchanged (the `else: os.walk(...)` branch with
  `_OUTAGE_SKIP_DIRS` pruning). `test_out_age_newest_mtime_correct` and
  `test_vcs_dirs_pruned` (both exercise the no-globs path) still pass.
- **VCS pruning parity in the globs branch:** the no-globs branch prunes
  `.git`/`.hg`/`.svn`; the globs branch never pruned them explicitly. Verified on
  3.12 that `iglob('**', recursive=True)` **does not traverse leading-dot
  directories** (glob skips dotpaths unless the pattern names the dot), so a `**`
  glob naturally excludes `.git` — the two branches agree. (A pattern that
  *explicitly* names a dot dir, e.g. `[".git/**"]`, would include it — but that is
  the operator opting in, identical to the prior contract.)
- **FR-74 reclaim guard:** still consumes the **data-layer mtime** via
  `_output_age_secs` (not the rendered column). The fix only makes that data signal
  *correct on the floor* — the guard's logic is untouched. The two load-bearing
  006 pins (`test_pool2_two_stalled_with_queue_drains_not_halt`,
  `test_stalled_but_output_fresh_is_NOT_reclaimed`) still pass.
- **Display-only invariant:** untouched —
  `test_out_age_is_display_only_not_lifecycle` still passes; OUT-AGE remains a
  column, never a lifecycle input.

## Honesty note on evidence

The instruction warns that a green suite on the dev interpreter (3.14, where the
bug does **not** manifest) is **not** evidence of the portability fix. Beyond
making the impl version-stable by construction, I went further and **ran the bug
on the floor band directly:** Python 3.12.13 was available locally, so I (1)
reproduced the `Path.glob` dir-only defect, (2) showed the buggy `Path.glob` form
**fails 2 of the data-layer tests** on 3.12 (`int(None)` TypeError), and (3) showed
the `iglob` fix makes all 7 **pass** on 3.12. This is real failing→passing evidence
in the affected version band. The hard gate remains the orchestrator's independent
re-verify on **3.10.12** (the exact floor).

## Tests

- Floor band **3.12.13**: `OutAgeDataLayer` 7/7 OK with the fix; 2 FAIL with the
  reverted `Path.glob` form (demonstrated, then restored).
- Dev **3.14.6**: full suite `python3 -m pytest tests/` → **485 passed ×3**
  (483 baseline from 006 + 2 new portability tests).

**Verdict: SHIP.** Both charter clauses satisfied; failing→passing demonstrated on
the floor band; no regression to the no-globs path, the FR-74 guard, or the
display-only invariant.
