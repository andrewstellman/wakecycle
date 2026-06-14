# Panelist C — Honesty & Regression — FR-56 (activity-pattern extraction)

Iteration: BUILD FR-56 (wrap/tail activity-pattern extraction). Work uncommitted
(`git diff HEAD`). Baseline before iteration: 234 tests; current: **255 passed**.

Adversarial independent review. Findings below quote in-repo evidence.

---

## 1. No doneness leak — the display!=doneness firewall (FR-18 producer/reader boundary). PASS

`git diff HEAD -- arunner/engine/tick.py` contains ONLY:
- worker_cmd **synthesis**: `adapter_activity_patterns` → repeated `--activity-regex`
  argv (both adapters; `_adapter_worker_cmd`), and
- `--check` **validation**: `_check_activity_patterns` + a `success/failure_regex`
  compile retrofit (`_regex_problem`/`_regex_complexity_problem`).

There is NO change to terminal/lifecycle determination in tick.py — the engine
reader still interprets only `status`. The synthesis is argv plumbing; the
validation only adds problem strings to the `--check` report. Nothing in the
diff makes the engine read worker output.

Firewall stated in docs + code:
- `heartbeat.py:308` "DISPLAY-ONLY -- never doneness (the FR-18 producer/reader
  boundary: the adapter chooses `label`; the engine still interprets only
  `status`)."
- REQUIREMENTS.md FR-56 "Scope: display/activity only — NOT doneness … the
  engine *reader* still interprets only `status`."
- Pinned: `test_never_matching_pattern_does_not_affect_doneness` — a
  `_TailWatcher` with a never-matching activity matcher STILL returns
  `"COMPLETED"` via `success_regex`, while `label()` shows `(running...)`. The
  doneness path and the display path are exercised independently in one test.

## 2. Staleness hint — the "looks-live-but-stale" honesty guard. PASS

`_ActivityMatcher.label(now, interval)` (heartbeat.py) appends an age hint when
`(now - self.matched_at) > max(1.0, interval)`:
`"%s (%s ago)" % (self.matched, _age_hint(...))`. `_age_hint` is compact ASCII
(`45s`/`8m`/`2h`). Pinned by `test_staleness_age_hint`: a line matched at t=100
shows bare at t=100 (fresh) and `"Step 7/12 (... ago)"` at t=580 (480s later,
> 10s interval). A pinned-but-stale match cannot masquerade as live.

## 3. §9 flip cites REAL evidence. PASS

- Cited files EXIST: `tests/test_activity_patterns.py`,
  `tests/integration/scenarios/sim_tail_log_noise/scenario.json`. The `EndToEnd`
  class (drives the real wrap+tail subprocess, grace 0) and the
  `sim_tail_log_noise` scenario (now carries `adapter_activity_patterns`,
  synthesized into `--activity-regex`) are both real and named in the row.
- The FR-56 §9 row is now `**VERIFIED**` and cites real in-repo tests; it does
  NOT contain "dogfooding"/"always-on" (`test_no_verified_row_cites_dogfooding_or_alwayson`
  enforces this across all VERIFIED rows and PASSES).
- The cadence/Windows floor row STAYS PENDING (`test_floor_windows_row_stays_pending`
  PASSES).
- `python3 -m pytest tests/test_positioning_honesty.py -q` → **7 passed**, both
  guard tests intact.

## 4. No regression. PASS (with a flakiness note, non-blocking)

- Full suite: **255 passed** on three consecutive full runs (22.5–22.7s each) —
  matches the expected 255.
- Adapter regression: `test_wrap_adapter.py test_tail_adapter.py
  test_heartbeat.py` → **46 passed**.
- `_TailWatcher.poll()` signature change is backward-compatible: `now=None`
  default; all existing callers in `tests/test_tail_adapter.py` call `w.poll()`
  with no args and pass. The matcher is only consulted when
  `self.activity is not None and now is not None`.
- Additive for existing paths: a no-activity job has `activity.patterns == []`,
  so `_Keepalive.maybe_emit` falls through to the original
  `_last_output_line(...) or "(running, no output yet)"`; `label()` returns
  `None` and the watcher's `feed()` early-returns. Behavior is exactly as before
  when no patterns are set.

**Flakiness note (NON-BLOCKING, test-robustness only):** on my very first full
run (under concurrent load from parallel review commands) the two `EndToEnd`
subprocess tests failed; they passed in isolation (3/3) and on three subsequent
clean full runs (255/255 each). These tests use real wall-clock subprocess
timing (grace 0 → 1s keepalive vs. ~1.6s of child work), so under heavy CPU
contention the first IN_PROGRESS keepalive can race the child to completion.
This is a real-output integration test (a legitimate, valuable test), not a
product defect and not a regression in FR-56 code. Recommend (future, not
blocking) bumping `--steps`/`--sleep` to widen the keepalive window. Does not
gate ship.

## 5. Cross-platform / NFR-3 + ASCII (NFR-7). PASS

- Pure stdlib `re`: `grep -rn "import regex|from regex" arunner/` → NONE. No new
  dependency. The complexity screen uses `re._parser.parse` (stdlib internal,
  guarded by try/except).
- `(running...)` placeholder and `_age_hint` output are ASCII; pinned by
  `test_placeholder_and_hint_are_ascii` (`.isascii()` on both the hinted label
  and the placeholder).

## 6. ReDoS residual honesty (NFR-11). PASS — disclosed, not hidden

- Code: `heartbeat.py:337-340` "it does NOT bound catastrophic backtracking … 
  ReDoS is reduced and disclosed, not eliminated." `tick.py:1428-1432`
  "Conservative ReDoS HEURISTIC … catches the common shape, not all ReDoS."
- Spec: REQUIREMENTS.md §219 "the residual risk is an operator-self-inflicted
  single-tick wedge, disclosed, not a crash"; FR-56 §355 "**Safety (ReDoS) —
  reduced and disclosed, not eliminated** … a determined operator can wedge a
  single tick … the residual risk is stated, not hidden." Placed in the same
  NFR-11 operator-trusts-own-config bucket as `worker_cmd`.
- The `{TASK_ID}`-template footgun (a pattern containing a placeholder-shaped
  token) is also disclosed in both the FR-56 spec note and the tick.py comment.

---

## Summary

FR-56 is display-only and the FR-18 firewall holds: tick.py changes are pure
synthesis + `--check` validation with zero change to terminal/lifecycle logic,
and the firewall is stated in docs and pinned by a mutation test. The staleness
age hint exists and is pinned; the §9 flip to VERIFIED cites real, existing
in-repo tests and adds no dogfooding/always-on tokens; the Windows floor row
stays PENDING and the honesty guards pass (7 passed). Full suite is 255 passed
on repeated clean runs; adapter regressions (46) pass; the `poll(now=None)`
change is backward-compatible. Implementation is pure stdlib `re` with ASCII
labels, and the ReDoS residual is explicitly disclosed in spec and code. The
only blemish is a load-sensitive flakiness in the two real-subprocess EndToEnd
timing tests — a test-robustness nit, not a product defect, regression, or
honesty leak.

VERDICT: SHIP
