# Panelist B — Security & `--check` validation (FR-56)

**Role:** Reviewer B, independent/adversarial. Surface: operator regexes over unbounded
external output (ReDoS). Work is UNCOMMITTED. Spec authority: `docs/REQUIREMENTS.md`
FR-56 ("Safety (ReDoS) — reduced and disclosed, not eliminated").

All claims below were RUN, not read off the code.

---

## 1. ReDoS bounds are REAL and HONESTLY described — CONFIRMED

**(a) Per-line truncation (~4 KiB) caps INPUT, not backtracking.**
`heartbeat.py:314 _ACTIVITY_LINE_CAP = 4096`; `feed()` line 355 does `ln = ln[:_ACTIVITY_LINE_CAP]`
BEFORE `rx.search`. Ran a `(a+)+$` pattern against a 100 000-char all-`a` line → returned in
0.000s because `search` only saw 4096 chars. The docstring (heartbeat.py:336-340) states plainly
it "does NOT bound catastrophic backtracking, which is a property of the operator-owned pattern …
ReDoS is reduced and disclosed, not eliminated." Honest.

**(b) Per-tick/per-scan total-bytes ceiling (~256 KiB) — CONFIRMED enforced + proven.**
`_ACTIVITY_SCAN_CEILING = 262144` (heartbeat.py:315). `feed()` lines 353-358 accumulate
`scanned += len(ln)` and `break` once `scanned > ceiling`, retaining the last label. I READ
`test_byte_ceiling_stops_and_retains_last` (test file 75-80): `["HIT early"]` then a flood of
`70 × 4096 = 286 720 B > 256 KiB` ending in `"HIT late"` — asserts the label is still `HIT early`
(the late hit is past the ceiling, never seen). I reproduced it directly: ceiling stops the scan,
`HIT first`/`HIT early` retained. Real bound, real test.

**(c) Complexity screen + ≤16 cap — CONFIRMED.** `_ACTIVITY_PATTERN_CAP = 16`,
`_ACTIVITY_PATTERN_MAX_LEN = 500` (tick.py:1423-1424). Screen + cap both bite (see §2/§4).

**Disclosure of the residual single-tick wedge is NOT hidden.** REQUIREMENTS.md:219 and the FR-56
body (line 355) state a determined operator can still wedge ONE tick with a pathological pattern;
it is the same **operator-trusts-own-config / NFR-11** bucket as `worker_cmd`/`command`. Comment at
heartbeat.py:340 repeats it. Plainly stated.

## 2. `--check` rejects every bad case — CONFIRMED (each run individually)

`_check_activity_patterns` (tick.py:1481) results:
- non-list (`"nope"`) → `must be an array of regex strings` ✓
- non-string element (`["ok", 5]`) → `[1]: must be a string` ✓
- **empty element (`[""]`)** → `an empty pattern matches every line and silently defeats the filter` ✓
  (the load-bearing one — `re.compile("")` matches everything)
- >16 (`["x"]*17`) → `at most 16 patterns (got 17)` ✓
- uncompilable (`["(oops"]`) → `not a valid regex (missing ), unterminated subpattern …): '(oops'`
  (names the pattern + the error) ✓
- complexity: `(a+)+`, `(a*)*`, `(ab+c)+` → all rejected `nested unbounded quantifier
  (catastrophic-backtracking shape)` ✓; length cap (501 chars) → `exceeds the 500-char complexity cap` ✓

**Safe patterns NOT false-rejected:** `(x|y)+`, `BUILD (OK|DONE)`, `\d{2,5}`, `Step \d+`
→ all return `[]`. ✓

## 3. Compile RETROFIT (`success_regex`/`failure_regex`) — CONFIRMED

`_check_adapter_entry` tick.py:1535-1541 now runs `_regex_problem` over both doneness regexes
(previously only type-checked). A tail entry with `success_regex: "(a+)+"` →
`entries[0].success_regex: nested unbounded quantifier …`. `failure_regex: "(a*)*"` rejected;
`failure_regex: "(unclosed"` → `not a valid regex`. ✓

## 4. Complexity screen BITES (not vacuous) + end-to-end gate — CONFIRMED

- `_regex_complexity_problem("(a+)+")` returns the problem string directly; `(x|y)+` and
  `BUILD (OK|DONE)` return `None`. ✓
- **End-to-end via the real entrypoint:** `python3 arunner/engine/tick.py --check <plan>` with
  `adapter_activity_patterns: ["(a+)+"]` → `plan FAILED … nested unbounded quantifier`, **exit 1**.
  A safe-pattern plan → `plan OK`, **exit 0**. `check_plan()` on a full wrap plan with `(a+)+`
  returns the problem; with safe patterns returns `[]`. The gate bites end-to-end. ✓

**4b. Injection-safety of list synthesis — CONFIRMED.** `_adapter_worker_cmd` (tick.py:1097-1099)
appends each pattern as its OWN argv element via `["--activity-regex", str(pat)]`; the resulting
argv carries `(a+)+ literal` verbatim as a single token — never concatenated into a shell string
(dispatch is `Popen(argv)`, no shell). The `{PLACEHOLDER}`-rewrite footgun (a token-shaped substring
inside a pattern would be template-substituted) is DOCUMENTED at tick.py:1094-1096 and
REQUIREMENTS.md:355, framed correctly as a footgun, not injection. ✓

## 5. Full suite green — CONFIRMED

`python3 -m pytest -q` → **255 passed** (run twice, stable). Named spot-checks
`test_check_plan.py` + `test_tail_adapter.py` + `test_activity_patterns.py` → 56 passed. The
checker/engine changes break nothing. Integration scenario `sim_tail_log_noise/scenario.json`
carries `adapter_activity_patterns: ["step \\d+", "working"]` through synthesis with doneness
unchanged (display-only firewall). ✓

---

## Adversarial findings (non-blocking)

1. **Complexity heuristic has known gaps** — `(a|aa)+` (alternation-overlap ReDoS) and
   `a{1,100}{1,100}` are NOT caught (return `None`). This is **within spec**: FR-56 §355 explicitly
   calls the screen "a conservative heuristic … the common shape, not all ReDoS," and the actual
   loop protection is the byte ceiling + truncation, with the residual single-tick wedge disclosed
   (NFR-11). The screen catching `(a+)+`/`(a*)*`/`(.*a){20}`/`(\d+)+$`/`(x+x+)+y` while passing
   `(x|y)+`/`BUILD (OK|DONE)`/`\d{2,5}` is the documented contract, not a defect. Not blocking.

2. **One non-reproducing flake observed once.** My FIRST `pytest` invocation of the pair
   `test_byte_ceiling… test_line_truncated…` reported 2 failures (label = the raw x-string instead
   of `(running...)`/`HIT early`). It did NOT reproduce: the same node-ids passed 5× consecutively,
   the single tests pass alone, the whole class passes, and the full suite passed 255 twice after a
   `__pycache__` purge. Direct in-process reproduction of the matcher produced the CORRECT result
   every time. Assessed as a stale-`.pyc`/first-load harness artifact, not a code defect — the
   truncation and ceiling logic is verifiably correct in isolation and under the full suite. Flagged
   for the record; not a blocking ship issue.

---

VERDICT: SHIP
