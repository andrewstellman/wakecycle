# Self-Council SYNTHESIS — instr 010 (FR-75 per-job retry policy)

**Verdict: unanimous SHIP** (3-panel self-Council, one round-1 FIX applied).

## Panels
- **A — state-machine / correctness: SHIP.** The retryable-terminal → requeue
  transition is wired at every retryable site (FAILED/ABANDONED reap, dead-shell-
  PID, FR-74 stall-reclaim caller), bounded by a persisted attempt count,
  terminal-reachable after the cap, resume-not-restart (output kept, heartbeat
  rotated, `done_check` re-derived), backoff on the `ARUNNER_NOW` clock. One NOTED
  edge (A-N1): the heartbeat rotation truncates an FR-20 external heartbeat file
  — necessary for retry to function, acceptable (the file IS the heartbeat),
  full per-attempt isolation is a follow-up.
- **B — regression-safety: SHIP.** No FR-6 double-dispatch (requeued job claimed
  once per attempt, single lock); `max_attempts:1`/absent is byte-identical to
  pre-FR-75 (494 prior tests green); clean `stall_retries` supersede (still
  `--check`-accepted, never had runtime effect); FR-72/FR-74/FR-76 untouched; no
  real sleeps; NFR-3 stdlib-only intact. Two NOTED ratified scope limits:
  multistep job-level retry is out of scope (validated-but-inert, like FR-74's
  `stall_retries` seam), and a flaky job's earlier-attempt FR-65 tokens aren't
  summed (no corruption — SUMMARY writes only on `done`).
- **C — tests / honesty: SHIP after C-F1.** The two load-bearing pins
  (`test_retry_then_succeed`, `test_cap_honored...`) mutation-bite; coverage
  honest; FR-75/US-22/UC-18 next-free, no reuse; §9 accurate.

## Round-1 FIX (applied + mutation-verified)
- **C-F1:** the transient-vs-fatal default ("`auth_or_launch_failed` is never
  retried") was asserted but unpinned → added
  `test_auth_or_launch_failed_is_not_retried` (shell job, `max_attempts:3`, no
  heartbeat past launch grace → `auth_or_launch_failed`, `attempts == 1`).
  Mutation (wire `_maybe_retry` into the launch-fail path) bites; restored.

## Ratified scope decisions
1. **Retryable set = {`failed`, stall-reclaimed `abandoned`}.** `completed` and
   `auth_or_launch_failed` are never retried — the default transient-vs-fatal
   split (a runtime failure may be flaky; an auth/launch/pre-flight failure won't
   fix itself on a blind re-run). No per-job classification override this round
   (the instruction made it optional; the natural split covers the gen-007 case).
2. **End-state by cause:** an exhausted `failed` ends `failed`; an exhausted
   stall-reclaim ends `abandoned` (FR-74's honest "gave up waiting; no failure
   observed"). `max_attempts` is the single unified budget; `stall_retries`
   superseded (back-compat-accepted only).
3. **Single-prompt scope.** Multi-step job-level retry (resume-vs-restart-from-
   step) is a documented follow-up; a multistep run is never requeued.
4. **Heartbeat rotation on requeue** is the heartbeat-isolation mechanism (clears
   the stale terminal so the new attempt isn't re-reaped); full per-attempt
   heartbeat files are a follow-up.

## Evidence
- Tests: **494 → 506 passed ×3, 1 skipped** (`python3 -m pytest tests/`),
  Python 3.14.6. +12 (8 RetryPolicy lifecycle + C-F1 + 3 CheckValidation).
- Two load-bearing pins + backoff + done_check-clear + C-F1: all mutation-bit,
  restored (impl committed `c3c80cd` first).
- Schema (both copies) + STATE_MACHINE (both copies) byte-identical (`diff` clean).
- NFR-3 preserved (no new runtime dependency; `glob`/`subprocess`/`json` reused).
