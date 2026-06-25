# Instruction 009 — FR-76: target-state done-check + idempotent resume

## What this is
arunner's idempotency is **tick-level** (crash-safe within a run-dir, FR-6) but **not target-state-level**: a fresh plan re-run re-dispatches everything, and `resume` is run-dir-bound (it re-ticks an existing run-dir). The `run_playbook` wrapper that rescued the gen-007 run had a property arunner lacks — re-running the wrapper *is* the resume: it re-derives "what's left" from each target's state and runs only the remainder, surviving a lost/rotated run-dir. **FR-76 brings that into the engine:** a plan-declared per-job **`done_check`**, evaluated **before dispatch**, so re-running the same plan = resume derived from **target state**, independent of run-dir survival. This is the engine half of the stop/restart acceptance in `docs/PLANNED_run_robustness.md` §7.

## Prerequisite / branch (single-trunk)
Short-lived branch off `main` (now `3175c40`): `git worktree add ~/Documents/arunner-fr76 -b fr76-done-check main`. Implement, self-Council to SHIP, commit. **Worker does NOT push/merge** — operator lands.

## Reference (read first — spec, not code to paste)
- `docs/PLANNED_run_robustness.md` **§5 (FR-76)** + **§7 (the stop/restart acceptance + its regression pin)**.
- The gen-007 stop/restart story: a 33/58-complete batch stopped on a usage boundary, resumed the next day with one command — zero repeats, zero loss. That is the behavior to pin.
- `arunner/engine/tick.py` (`_dispatch` / the claim+dispatch path, `_advance`, terminal states, the FR-6 claim-lock), `references/STATE_MACHINE.md`, `docs/REQUIREMENTS.md` (FR-6 idempotency, FR-41 sentinel), `SDLC.md`. Re-confirm exact line numbers on `main` first.

## The work
1. **Plan-declared `done_check` (schema, both copies, byte-identical).** A per-job optional `done_check`: either an **artifact-exists predicate** (a path/glob relative to the job's `target_repo` whose existence ⇒ done) or a **check command** (runs; exit 0 ⇒ done). Add to `schemas/plan.schema.json` **and** `plugins/arunner/skills/arunner/schemas/plan.schema.json`. Document the field.
2. **Evaluate `done_check` BEFORE dispatch (engine).** In the dispatch path, before claiming/dispatching a queued job, evaluate its `done_check`; if satisfied, mark the target **`completed` (skipped — not re-run)** with a clear display marker (e.g. a `completed` row noting `done-check pre-satisfied` / `SKIPPED:done`). Re-running the plan thus re-derives the remainder from target state.
3. **Run-dir-independent resume.** Re-running the same plan (a fresh `--init` + tick, or a re-pointed plan) consults each job's `done_check` on (re-)entry: skip the satisfied, dispatch the rest — **without** requiring the original run-dir. The done_check IS the resume signal; a fresh run-dir + the same plan = resume.
4. **Compose with FR-6 + preserve the doneness invariant.** FR-6 claim-lock still holds — never double-dispatch an in-flight job. A **partially-written** target (done_check NOT satisfied) is **redone, not skipped**. The done_check is the **one explicit, operator-declared** exception to "doneness from the declared status, never parsed output": it is an operator-provided predicate/gate, **not** the engine parsing worker output to infer completion — keep that distinction sharp and documented.
5. **Extends FR-41** (the in-run sentinel) from in-run doneness to a **pre-dispatch** gate.

## Tests (red→green, mutation-verified; `jobs`/`mode` format)
- **THE load-bearing pin — gen-007 stop/restart:** a plan of N targets where K already satisfy `done_check` → a (re-)run dispatches only the **N−K** remainder; the K done are **not** re-dispatched; the remainder is not lost. Model the 33/58 → resume-the-25 shape. Mutation: remove the pre-dispatch done_check eval ⇒ all N re-dispatch ⇒ bite.
- **Partial-target redone:** a target whose `done_check` is NOT satisfied is dispatched, not skipped. Mutation: skip-on-unsatisfied ⇒ bite.
- **Both done_check shapes:** artifact-exists predicate + check-command (exit 0 / non-0).
- **Run-dir independence:** the same plan re-`--init`'d into a FRESH run-dir skips the done targets (resume from target state, not the old run-dir).
- **FR-6 compose:** an in-flight (claimed) job is not double-dispatched even with done_check present.
- Full suite `python3 -m pytest tests/ -q` green **×3** (report counts + Python version; purge `__pycache__` before any post-restore re-verify). stdlib-only engine (NFR-3).

## Council — mandatory 3-panel self-Council (`runner/reviews/009_self_council/`, committed)
Lifecycle/state-machine work — three panels:
- **A — state-machine/correctness:** done_check eval before dispatch; the completed/skipped transition is correct + terminal; run-dir-independent resume; the done_check is a **declared gate, not output-parsing**; FR-41 extension sound.
- **B — regression-safety:** the gen-007 stop/restart pin holds; a partial target is **redone not skipped**; FR-6 no-double-dispatch preserved; the "doneness from declared status" invariant intact (done_check is the narrow, explicit declared exception); shell + subagent parity; no perturbation to FR-74's reclaim or FR-72's launch path.
- **C — tests/honesty:** the stop/restart pin AND the partial-redone pin both mutation-bite; FR-76 + US/UC added (next-free, no reuse); §9 honest.
Iterate to unanimous SHIP before reporting.

## §9 / requirements
Add **FR-76** + a US + a UC at **next-free** numbers (after FR-74's US-19/US-20, UC-15/UC-16 — do **not** reuse) + a §9 VERIFIED row in `docs/REQUIREMENTS.md`. Update `references/STATE_MACHINE.md` (both copies, identical) with the pre-dispatch done_check gate + the skipped/completed transition. Note the FR-6 / FR-41 lineage and the "done_check is the one declared exception to doneness-from-status" invariant.

## Commit / output
Focused commits on `fr76-done-check` (do NOT push/merge — operator lands + deletes the branch/worktree). Output → `outputs/009-fr76-done-check.md`: the design (done_check shapes + eval point), before/after, the gen-007 stop/restart pin + its mutation bite, per-test evidence, the 3-panel synthesis, suite counts ×3 + Python version, the FR-76 + US/UC + §9 rows, the `STATE_MACHINE.md` delta, `git log --oneline`.
