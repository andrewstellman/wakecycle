# Panel C — tests / honesty (instr 010, FR-75 per-job retry)

**Charge:** retry-then-succeed + cap pins mutation-bite; FR-75 + US/UC next-free
(no reuse); §9 honest.

## What I checked

1. **The two load-bearing pins mutation-bite (verified in-tree).**
   - `test_retry_then_succeed` — FAIL attempt 1 with `max_attempts:2` → requeued +
     redispatched → COMPLETED on attempt 2 (`attempts == 2`). **Mutation:**
     `_maybe_retry` always returns False (remove the requeue) → stays `failed`
     after attempt 1 → FAIL. **Bit** (all four retry pins failed under it).
   - `test_cap_honored_terminal_failed_after_max_attempts` — always-fail →
     exactly 2 attempts then terminal `failed`, never infinite. **Mutation:** no
     cap (always requeue) → `attempts` climbs, never terminal → FAIL. **Bit**
     (also bit `test_max_attempts_1_is_no_retry_backcompat`).
   - Plus: ignore-backoff mutation bit `test_backoff_delays_redispatch`; keep-
     `done_checked` mutation bit `test_retry_skipped_when_done_check_now_satisfied`.
   All mutations restored via `git checkout` (the impl was committed FIRST,
   `c3c80cd`, per the instr-006 process lesson — a mutation `git checkout` can't
   revert uncommitted work).

2. **Coverage is honest about what each pin proves.** The 12 new tests cover:
   retry-then-succeed, cap, no-double-dispatch (FR-6), FR-76 compose (no wasted
   attempt), back-compat (`max_attempts:1`/absent), stall-reclaim requeue (the
   `stall_retries` seam live) + exhausted-→-`abandoned`, backoff, the fatal-class
   not-retried pin (C-F1 below), and `--check` validation of both knobs.

3. **FR-75 / US-22 / UC-18 are next-free, no reuse.** `grep` confirms US-22 and
   UC-18 appear only in the new FR-75 prose / UC block (highest prior were
   US-21 / UC-17, FR-76's). FR-75 was the reserved working label in
   `PLANNED_run_robustness.md` and is now assigned at next-free per the
   renumbering discipline.

4. **§9 row is honest.** It marks VERIFIED and names the real engine symbols
   (`_maybe_retry`/`_requeue_for_retry`/`_retry_policy`), the wiring sites, the
   backoff gate, the `--check` checks, the byte-identical schema copies, and the
   load-bearing-pin mutation bites — every claim is backed by a test that exists
   and passes.

## Finding (C-F1, FIX — applied this round)

**The "transient-vs-fatal default" was asserted but unpinned.** FR-75 / UC-18
claim `auth_or_launch_failed` is NEVER retried — an explicit design decision that
nothing tested. **Fix:** added `test_auth_or_launch_failed_is_not_retried` (a
shell job with `max_attempts:3` that emits no heartbeat past launch grace →
`auth_or_launch_failed`, `attempts == 1`, NOT requeued). Mutation-verified: wiring
`_maybe_retry` into the launch-fail path makes the run leave
`auth_or_launch_failed` → the test FAILs → **bit**; restored. The transient-vs-
fatal split is now a load-bearing pin, not just prose.

## Verdict: **SHIP** (after C-F1, applied + mutation-verified). The two
load-bearing pins bite, coverage is honest, numbering is clean, §9 is accurate.
