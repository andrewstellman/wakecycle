# Output for 009-fr76-done-check.md
**Status:** completed

FR-76 (target-state done-check + idempotent resume) implemented on a short-lived
branch off `main`, mandatory **3-panel self-Council → unanimous SHIP**, two commits
**local only — not pushed/merged**. Suite **486 → 494 passed ×3, 1 skipped**, Python
**3.14.6**. stdlib-only engine preserved (NFR-3). Schema + STATE_MACHINE copies
byte-identical.

- Worktree `~/Documents/arunner-fr76`, branch **`fr76-done-check`** off `main` (`3175c40`).
- Commits: **`945fab0`** (impl + tests + schema + docs) + **`71a2608`** (self-Council A-F1 + council artifacts).

## The design — done_check shapes + eval point

A plan-declared per-job optional **`done_check`** with EXACTLY ONE shape:
- **`artifact`** — a path/glob relative to the job's `repo` whose existence ⇒ done.
  Evaluated with `glob.iglob(os.path.join(repo, pat), recursive=True)` (the FR-73
  portable glob — a trailing `**` matches files on the 3.10+ floor).
- **`command`** — an argv run with `cwd = the job's repo`; **exit 0 ⇒ done**.
  `subprocess.run` with stdout/stderr → DEVNULL, **exit-code only** — mirrors
  `_eval_shell_gate`, so the engine never parses worker output to infer doneness.

**Eval point:** in `_dispatch`, for each `queued` run, **BEFORE** the pool-slot gate
and the claim. A satisfied job → `_synthesize_done_skip` marks it terminal
`completed` (skipped, not re-run) via the idempotent `_synthesize_failure` sentinel
path, with a `done_skipped` display marker (`SKIPPED:done` — the `_format_table`
activity cell), and **never claims a slot**. Any error/unmeasurable predicate ⇒ NOT
satisfied ⇒ dispatched (a probe must never falsely skip). Evaluated **once per
run-dir** (a persisted `done_checked` guard bounds the command-shape subprocess
cost; a FRESH run-dir has no flag → re-derives).

## Before / after

**Before:** idempotency was tick-level (FR-6, crash-safe within a run-dir) but not
target-state-level — a fresh plan re-run re-dispatched everything, and `resume` was
run-dir-bound (re-tick an existing run-dir). A lost/rotated run-dir lost the resume.

**After:** re-running the same plan = **resume derived from TARGET STATE** — the
engine consults each queued job's `done_check` on (re-)entry, skips the satisfied,
dispatches only the remainder — **independent of run-dir survival** (a new run-dir,
a new machine, a fresh session resume identically). The engine half of the
`PLANNED_run_robustness.md` §7 stop/restart acceptance.

## Compose with FR-6 + the doneness invariant

- **FR-6 preserved:** only `queued` runs are gated, so an in-flight (claimed) job is
  never re-checked → no double-dispatch (pinned by
  `test_inflight_job_with_done_check_not_double_dispatched`).
- **Partial target REDONE, not skipped** (the zero-loss half) — an unsatisfied
  `done_check` dispatches.
- **done_check is the ONE explicit operator-declared exception** to "doneness from
  declared status, never parsed output" (FR-18): a declared predicate/gate, not the
  engine parsing output. Extends the **FR-41** sentinel from in-run doneness to a
  pre-dispatch gate.

## Files created / changed
| Path | Lines | Note |
|---|---|---|
| `arunner/engine/tick.py` | +~95 | `_done_check_of`/`_done_check_satisfied`/`_synthesize_done_skip` (+ `_DONE_CHECK_TIMEOUT_SECONDS`/`_DONE_SKIP_HINT`); the pre-dispatch eval block in `_dispatch`; `done_skipped` marker in `_format_table`; `done_check` in `_COMMON_JOB_KEYS` + `_check_done_check` validator. |
| `schemas/plan.schema.json` (+ plugins copy, **identical**) | +~4 | `done_check` object def (oneOf artifact\|command) + added to all five oneOf job branches. |
| `references/STATE_MACHINE.md` (+ plugins copy, **identical**) | +~15 | the `queued → completed` done-check edge in the diagram + a transition bullet. |
| `docs/REQUIREMENTS.md` | +~6 | FR-76 (§5) + US-21 (§3) + UC-17 (§4) + one VERIFIED §9 row. |
| `tests/test_run_robustness.py` | +~150 | `DoneCheckResume` (6 tests) + 2 `CheckValidation` tests; two pins mutation-verified. |
| `runner/reviews/009_self_council/{A,B,C,SYNTHESIS}.md` | new | 3-panel self-Council (committed). |

## Commits made
- **`945fab0`** — *FR-76: target-state done-check + idempotent resume (instr 009)*.
- **`71a2608`** — *FR-76: done-skip disk-truth hygiene (self-Council A-F1) + 009 council artifacts*.

Both on `fr76-done-check`, **local only — not pushed, not merged**.

`git log --oneline -4`:
```
71a2608 FR-76: done-skip disk-truth hygiene (self-Council A-F1) + 009 council artifacts
945fab0 FR-76: target-state done-check + idempotent resume (instr 009)
3175c40 runner: commit 008 record (instr 008 + output)
91f9b75 FR-34 hardening: single version source + dynamic pyproject + bump 1.1.0 (instr 008)
```

## Acceptance criteria — pass/fail per item
- **gen-007 stop/restart pin** — a (re-)run dispatches only the N−K remainder; the K
  done are NOT re-dispatched; the remainder not lost — **PASS**
  (`test_stop_restart_skips_done_dispatches_remainder`, N=8/K=3; mutation-verified:
  remove pre-dispatch eval ⇒ all N re-dispatch ⇒ bite).
- **Partial-target redone** — an unsatisfied done_check is dispatched, not skipped —
  **PASS** (`test_partial_target_not_satisfied_is_redone_not_skipped`;
  mutation-verified: skip-on-unsatisfied ⇒ bite).
- **Both done_check shapes** — artifact-exists + check-command (exit 0 / non-0) —
  **PASS** (`test_done_check_command_exit0_skips_nonzero_dispatches`,
  `test_artifact_glob_shape_matches_file`).
- **Run-dir independence** — same plan re-`--init`'d into a FRESH run-dir skips the
  done — **PASS** (`test_resume_from_fresh_run_dir_skips_done`).
- **FR-6 compose** — an in-flight job is not double-dispatched even with done_check —
  **PASS** (`test_inflight_job_with_done_check_not_double_dispatched`).
- **`--check` validation** — done_check shape enforced (exactly one of
  artifact/command; non-empty) — **PASS** (`test_done_check_requires_exactly_one_shape`,
  `test_done_check_bad_member_shapes`).
- **Schema + STATE_MACHINE copies identical** — **PASS** (`diff -q` clean both).
- **stdlib-only (NFR-3)** — `glob`/`subprocess`, no new dependency — **PASS**.

## Council (required) — 3-panel self-Council: **UNANIMOUS SHIP**
`runner/reviews/009_self_council/SYNTHESIS.md` (+ A/B/C).
- **A (state-machine/correctness):** SHIP after **A-F1** — done-skip left the
  never-claimed `queue/` job file behind (inert but inconsistent with a reaped
  `completed`); fixed `_synthesize_done_skip` to unlink it, pinned in the
  stop/restart test. Eval-before-dispatch, terminal transition, run-dir-independent
  resume, declared-gate-not-parsing, FR-41 extension all correct.
- **B (regression-safety):** SHIP — both pins hold; partial redone; FR-6 preserved
  (only queued gated); doneness invariant intact; all-mode parity; FR-74/FR-72
  untouched; schema/STATE_MACHINE identical; bounded cost (once-per-run-dir guard;
  subprocess timeout 30s).
- **C (tests/honesty):** SHIP — both pins mutation-bite (executed + restored, no
  residue); FR-76 + US-21 + UC-17 next-free, no reuse; §9 names the real
  functions/tests/bites.
- **Ratified scope:** per-job (not per-step) done_check; command shape exit-code
  only; once-per-run-dir eval.
- **Process:** committed the impl (`945fab0`) BEFORE the Council; the A-F1 FIX is its
  own commit (`71a2608`). Mutations restored via `/bin/cp -f` from a side copy
  (not `git checkout`); grep-confirmed no mutation residue before commit.

## Tests
Baseline **486 passed, 1 skipped** (main `3175c40`, via `git stash`) → final
**494 passed, 1 skipped ×3** on the post-A-F1 tree, `python3 -m pytest tests/ -q`,
Python **3.14.6**, `__pycache__` purged before the baseline measure. The +8 are all
FR-76 (6 `DoneCheckResume` + 2 `CheckValidation`); the 1 skip is instr 008's
installed-metadata pin (unrelated — skips when arunner isn't pip-installed).

## §9 rows flipped
One new **VERIFIED** row added (FR-76 / US-21 / UC-17 — done-check + idempotent
resume, both load-bearing pins mutation-verified). No existing row changed; no
number reuse.

## STATE_MACHINE delta
Added the `queued ──[done_check pre-satisfied]──▶ completed (skipped — FR-76)` edge
to the diagram (evaluated before the pool-slot dispatch) and a transition bullet
covering the artifact/command shapes, the FR-6 queued-only gating, partial-redone,
the declared-gate-not-parsing invariant, and the once-per-run-dir eval. Synced to
the plugins copy (identical).

## Notable observations
- **Reuses the synthesized-sentinel path** (`_synthesize_failure` → COMPLETED) rather
  than inventing a new terminal, so idempotency/comeback-safety is inherited (a
  terminal run is skipped by `_advance`; `_dispatch` only dispatches `queued`).
- **Portable artifact glob** — `glob.iglob(..., recursive=True)` (the instr-007/008
  lesson) so a `["out/**"]`-style artifact matches files on the 3.10+ floor.
- **A-F1 hygiene** — the one real issue the Council surfaced: disk-truth consistency
  (queue/ file removal) for a never-claimed skip. Cosmetic but now pinned.

## Next action expected from orchestrator
Independent verification, then operator merges `fr76-done-check` → `main` and deletes
the branch + worktree. Next single-trunk 1.1.0 step (per
`docs/PLANNED_run_robustness.md` §8): **FR-75** (per-job retry policy — consumes the
`stall_retries` seam) → FR-77 (supervised-bounded model, host-capability probe first)
→ doc-sync; tag `v1.1.0` when the line completes.
