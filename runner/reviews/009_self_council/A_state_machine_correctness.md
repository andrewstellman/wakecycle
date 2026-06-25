# Panel A — state-machine / correctness (instr 009, FR-76)

**Verdict: SHIP** (after A-F1, applied).

## Charter
done_check eval before dispatch; the completed/skipped transition is correct +
terminal; run-dir-independent resume; done_check is a **declared gate, not
output-parsing**; the FR-41 extension is sound.

## Findings

1. **Eval point is correct.** `_done_check_satisfied` is called in `_dispatch`,
   inside the `for name in sorted(runs)` loop, **before** the pool-slot gate and
   before the claim (`queue/ → claimed/`). A satisfied job is marked terminal and
   `continue`s without incrementing `inflight` — so it never consumes a slot. The
   remainder dispatches normally. The diagram edge `queued → completed
   [done_check pre-satisfied]` is drawn before the dispatch edge. ✓

2. **The transition is correct + terminal.** `_synthesize_done_skip` sets
   `state="completed"` and writes a synthesized result sentinel with
   `terminal_status="COMPLETED"` via the existing idempotent `_synthesize_failure`
   path (the same writer CANCEL/reclaim use — its name is generic-despite-"failure").
   `completed` is in `_TERMINAL_STATES`, so `_advance` skips it and `_dispatch`
   never reconsiders it (`state != "queued"`). `done` reconciles correctly (all
   terminal). ✓

3. **A-F1 (FIX, applied) — disk-truth hygiene.** A never-claimed job's file still
   sat in `queue/` after the skip (it was never moved to `claimed/`), whereas a
   reaped `completed` job leaves both `queue/` and `claimed/` empty with only the
   result sentinel. Inert (nothing rescans `queue/`; counts come from the `runs`
   dict via `_recount`, not disk) but inconsistent. Fixed: `_synthesize_done_skip`
   now unlinks the `queue/` job file. Pinned by an added assertion in
   `test_stop_restart_skips_done_dispatches_remainder` (queue/ + claimed/ empty).

4. **Run-dir-independent resume is real.** On a FRESH run-dir, `init_run` re-creates
   `queue/` files for all jobs; tick 1 evaluates each queued job's done_check
   against TARGET STATE (the job's `repo` on disk), skips the satisfied, dispatches
   the rest — with no reference to any prior run-dir. Verified by
   `test_resume_from_fresh_run_dir_skips_done` (two distinct run-dirs). ✓

5. **Declared gate, NOT output-parsing.** The `artifact` shape is a filesystem
   existence probe (`glob.iglob`); the `command` shape is **exit-code only**
   (`subprocess.run` with stdout/stderr → DEVNULL, `returncode == 0`), mirroring
   `_eval_shell_gate`. The engine never reads worker stdout to infer completion.
   done_check is the ONE operator-declared exception to doneness-from-status,
   documented in REQUIREMENTS §5/§9, STATE_MACHINE, and the code. ✓

6. **FR-41 extension sound.** done_check extends the in-run sentinel concept to a
   pre-dispatch gate; the synthesized sentinel is the same on-disk record shape. ✓

7. **Conservative on the unknown.** Any error (bad repo, OSError, subprocess
   failure/timeout) → `_done_check_satisfied` returns False → the job is dispatched
   (redone), never falsely skipped. A probe must never lose work. ✓

**Ratified tradeoff:** done_check on a `pipeline` job gates the whole job (the
final artifact ⇒ skip the whole pipeline) — the correct grain for "this target is
already done." Per-step done_check is out of scope (FR-76 is per-job).
