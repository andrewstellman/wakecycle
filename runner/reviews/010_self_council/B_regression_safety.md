# Panel B — regression-safety (instr 010, FR-75 per-job retry)

**Charge:** FR-6 no-double-dispatch on requeue; `max_attempts:1`/absent = current
behavior (back-compat); the `stall_retries` seam folded cleanly (no FR-74
regression); FR-72 launch + FR-74 reclaim + FR-76 done_check untouched; shell +
subagent parity; no real sleeps (ARUNNER_NOW seam).

## What I checked

1. **FR-6 no-double-dispatch.** A requeued job is `queued`; `_dispatch` dispatches
   a `queued` run exactly once (the `queue/ → claimed/` move + claim-lock holds),
   then flips it to `claimed` in the same pass. `test_no_double_dispatch_on_requeue`
   proves the redispatch tick emits exactly one dispatch entry for the run and
   leaves exactly one claim lock. The restored `queue/` token is immediately
   consumed by the redispatch (no stray token), or — during backoff — sits as a
   normal queued-run token. **No double-dispatch.**

2. **Back-compat (`max_attempts:1`/absent).** `_retry_policy` defaults to
   `(1, 0)`; with `max_attempts == 1`, the FIRST failure has `attempts == 1 >= 1`
   → `_maybe_retry` returns False → terminal, exactly as before FR-75.
   `test_max_attempts_1_is_no_retry_backcompat` covers both `1` and absent; the
   no-cap mutation (always requeue) bites it. The full suite (494 prior tests)
   stays green — every existing FR-74/FR-76/FR-72 test uses default plans, so the
   new `_maybe_retry` calls all return False and behavior is byte-identical.

3. **`stall_retries` folded.** `max_attempts` is the unified retry budget; the
   engine no longer reads `stall_retries`. It is still accepted + validated at
   `--check` (`test_stall_retries_must_be_non_negative` still passes) for plan
   back-compat. Because `stall_retries` NEVER had runtime effect (FR-74 always
   abandoned at the default 0), nothing regresses — this is a clean supersede,
   not a behavior change. The FR-74 reclaim caller now consults `_maybe_retry`
   first, but with default `max_attempts=1` it abandons exactly as FR-74 did
   (`test_reclaimed_stall_is_abandoned_and_frees_slot`, `..._does_not_resurrect...`,
   `test_pool2_two_stalled_with_queue_drains_not_halt` all green).

4. **FR-72 / FR-74 / FR-76 untouched.** FR-72 launch advisory + hard cap
   (`auth_or_launch_failed`) is NOT a retryable terminal → never requeued. The
   FR-74 reclaim guard (output-fresh) is unchanged — `_maybe_retry` is consulted
   only AFTER the output-stale + reclaim-window predicate already fired. FR-76
   done_check eval order is preserved (the backoff check is added BEFORE it, only
   for `queued` runs). Their full test sets pass.

5. **Shell + subagent parity.** The terminal-sentinel reap (both modes) and the
   dead-shell-PID path (shell — the canonical gen-007 "child runner exited 1"
   transient abort) are both wired. The stall-reclaim retry covers single-prompt
   shell + subagent. `test_stall_reclaimed_job_is_requeued_when_under_cap` is an
   agent job; the dead-PID path is exercised by the existing shell tests staying
   green under default no-retry.

6. **No real sleeps.** Backoff is `retry_not_before` compared to `_now()`
   (ARUNNER_NOW-overridable); the tests drive it purely by advancing
   `ARUNNER_NOW`. No `time.sleep` anywhere. NFR-3 preserved (stdlib only — no new
   import; reuses `glob`/`subprocess`/`json`/`Path`).

## Findings

- **B-N1 (NOTED, ratified):** **Multi-step job-level retry is out of scope.**
  `_maybe_retry` returns False for a multistep entry, and the
  `_advance_multistep` reclaim/FAILED paths do NOT call it. So a `mode:pipeline`
  job with `max_attempts>1` is validated-but-inert — exactly the forward-seam
  pattern FR-74 used for `stall_retries`. Resume-vs-restart-from-step semantics
  is a genuine separate design question (which I am not authorized to settle here)
  and the instruction's pins are all single-prompt. Documented in FR-75 / UC-18 /
  the constant. No silent surprise: the scope limit is stated in the requirement
  and the output.
- **B-N2 (NOTED, ratified):** a retried job's EARLIER-attempt FR-65 token usage is
  not summed into the final result (the failed attempt's sentinel is removed on
  requeue). The job's result records its final attempt; the SUMMARY is written only
  on `done`, so there is no double-count or corruption — only a minor under-count of
  a flaky job's wasted-attempt tokens. Acceptable; recorded.

## Verdict: **SHIP.** No regression to FR-6/FR-72/FR-74/FR-76, clean back-compat,
clean `stall_retries` supersede, no real sleeps, NFR-3 intact. The two NOTED items
are ratified scope limits, both documented.
