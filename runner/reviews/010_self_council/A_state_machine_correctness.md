# Panel A — state-machine / correctness (instr 010, FR-75 per-job retry)

**Charge:** the retryable-terminal → requeue transition; attempt accounting
persisted (crash-safe across a tick); terminal-`failed` reachable after the cap;
resume-not-restart; FR-76 compose; backoff timing.

## What I checked

1. **Retryable-terminal → requeue edge.** `_maybe_retry` is wired at every site a
   run reaches a retryable terminal in `_advance`:
   - the FAILED/ABANDONED heartbeat reap (`terminal in ("FAILED","ABANDONED")` →
     `final == "failed"` → `_maybe_retry` before `r["state"]=final`);
   - the dead-shell-PID failure (`_synthesize_failure(...,"failed")` then
     `_maybe_retry`);
   - the FR-74 stall-reclaim caller (`_maybe_retry` BEFORE `_reclaim_stalled`).
   `auth_or_launch_failed` and `completed` are deliberately NOT wired → never
   retried (the transient-vs-fatal default). **Correct:** a launch/auth failure
   won't fix itself on a blind re-run; a COMPLETED is done.

2. **Attempt accounting + cap.** `attempts` is incremented exactly once per fresh
   single-prompt claim in `_dispatch` (after `r["started"]=True`), persisted in
   the run record, so it survives across ticks (the tick is the unit of
   atomicity — `harness_status.json` is written atomically at tick end, like all
   run state). `_maybe_retry` requeues only while `attempts < max_attempts`;
   `>=` is the cap. **Terminal-`failed` is reachable after the cap** — verified by
   `test_cap_honored_terminal_failed_after_max_attempts` (exactly 2 attempts then
   `failed`, bounded loop never infinite) and the no-cap mutation (always requeue)
   bites it. Off-by-one or no-cap → bite.

3. **End-state by cause.** The stall-reclaim path calls `_maybe_retry` BEFORE
   `_reclaim_stalled`, so an exhausted stall-reclaim falls through to
   `_reclaim_stalled` → `abandoned` (FR-74's honest "gave up waiting"); an
   exhausted FAILED reap stays `failed`. Verified:
   `test_stall_reclaimed_abandoned_when_cap_exhausted` (ends `abandoned`, not
   `failed`) + `test_cap_honored...` (ends `failed`). The two retryable paths
   converge on "no result sentinel after a requeue" by different routes — the
   FAILED path writes-then-removes the sentinel (`_move_to_results` then
   `_requeue_for_retry`), the stall path never writes one (requeue precedes
   reclaim). Both correct.

4. **Resume-not-restart + heartbeat isolation.** `_requeue_for_retry` keeps the
   worker's OUTPUT (target repo) untouched and rotates (clears) the WATCHED
   heartbeat. The heartbeat clear is **load-bearing for correctness**: without it
   the stale FAILED line sits in the tail and `_terminal_status_of` (first
   terminal in the tail wins) would re-reap the new attempt instantly. It also
   restores the `queue/` claim token so the re-dispatch re-claims cleanly
   (`queue/ → claimed/ + .lock`) — disk-truth matches a fresh queued run
   (`test_no_double_dispatch_on_requeue` checks the single lock).

5. **FR-76 compose.** `_requeue_for_retry` pops `done_checked`, so the retry
   re-derives `done_check` on the next `_dispatch`. A retry whose target is now
   satisfied is skipped (`_synthesize_done_skip` → `completed`/`done_skipped`)
   with NO new dispatch — `test_retry_skipped_when_done_check_now_satisfied`
   asserts `attempts == 1` (no wasted attempt); the don't-clear-`done_checked`
   mutation bites.

6. **Backoff timing.** `retry_not_before = now + retry_backoff_seconds` (on the
   `ARUNNER_NOW` clock, no real sleeps); `_dispatch` skips a queued run while
   `now < retry_not_before` (held `queued`, holding NO slot), cleared on the next
   live dispatch. `test_backoff_delays_redispatch` (held `queued` at +1m, redispatched
   past +10m); the ignore-backoff mutation bites.

## Finding (A-N1, NOTED — ratified, not a blocker)

The heartbeat rotation truncates the **watched** heartbeat file. For a job using
an **FR-20 `heartbeat_path`** (an external status file the job already writes),
that truncates the operator's file. This is necessary for retry to function (the
re-reap hazard above), and the external file IS the engine's heartbeat (the
worker rewrites it each run), so it is acceptable. Full per-attempt heartbeat-file
isolation (a distinct heartbeat per attempt) is a documented follow-up. No
default-path job is affected (the common case is the engine-owned run-dir
heartbeat). Recorded; no code change.

## Verdict: **SHIP.** The retryable-terminal → requeue transition is correct,
bounded, persisted, and composes with FR-6/FR-74/FR-76. The heartbeat-rotation
edge is a documented, ratified scope decision.
