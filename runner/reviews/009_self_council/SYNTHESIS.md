# Self-Council SYNTHESIS — instr 009 (FR-76 target-state done-check + idempotent resume)

**Mandatory 3-panel self-Council. Verdict: UNANIMOUS SHIP** (after one round-1 FIX).

Branch `fr76-done-check` off `main` (`3175c40`). Lifecycle/state-machine work, so
three panels per the instruction.

| Panel | Charter | Verdict |
|---|---|---|
| **A — state-machine/correctness** | eval-before-dispatch; completed/skip terminal; run-dir-independent resume; declared gate not output-parsing; FR-41 extension | **SHIP** after **A-F1** |
| **B — regression-safety** | gen-007 pin holds; partial redone not skipped; FR-6 no-double-dispatch; doneness-from-status intact; mode parity; no FR-74/FR-72 perturbation | **SHIP** |
| **C — tests/honesty** | both pins mutation-bite; FR-76 + US-21 + UC-17 next-free; §9 honest | **SHIP** |

## Round-1 FIX (applied before SHIP)

- **A-F1 — disk-truth hygiene.** A done-skipped job left its `queue/job-NNNNN.json`
  behind (never claimed → never moved), unlike a reaped `completed` job (queue/ +
  claimed/ empty, only the result sentinel). Inert — nothing rescans `queue/`,
  counts come from the `runs` dict — but inconsistent. **Fixed:** `_synthesize_done_skip`
  now unlinks the queue file. **Pinned:** an added assertion in
  `test_stop_restart_skips_done_dispatches_remainder` (queue/ + claimed/ empty
  after a skip).

## Ratified scope decisions

1. **Per-job, not per-step.** done_check gates a whole job (a pipeline job skips
   entirely when its final artifact exists). Per-step done_check is out of scope.
2. **`command` shape is exit-code only** (no stdout read), mirroring
   `_eval_shell_gate` — keeping the "engine never parses worker output" invariant
   literally true; done_check is the ONE declared exception (a predicate, not parsing).
3. **Once-per-run-dir eval** (the `done_checked` guard) bounds the `command`-shape
   subprocess cost to one probe per job on (re-)entry; a fresh run-dir re-derives.

## What shipped

Plan-declared per-job `done_check` (artifact-exists | exit-0 command) evaluated
BEFORE dispatch in `_dispatch`; a satisfied queued job → terminal `completed`
(skipped, `SKIPPED:done`) via the idempotent `_synthesize_failure` sentinel,
never claiming a slot. Re-running the same plan = resume derived from TARGET STATE
(run-dir-independent). Composes with FR-6 (only `queued` gated); a partial target
is redone; extends FR-41 to a pre-dispatch gate; stdlib (NFR-3). Schema (both
copies, identical) + `--check` validation + STATE_MACHINE (both copies, identical)
+ REQUIREMENTS FR-76/US-21/UC-17/§9.

## Evidence

- Two load-bearing pins mutation-bitten (stop/restart; partial-redone), restored.
- Full suite **486 → 494 passed, 1 skipped ×3**, Python **3.14.6**.
- Schema + STATE_MACHINE copies `diff`-clean; schema valid JSON.

**UNANIMOUS SHIP.**
