# Instruction 038 self-council synthesis — Build FR-56: activity-pattern extraction (wrap/tail adapters)

*Mandatory 3-panel review (product code + a ReDoS security surface + a display-honesty concern). Three fresh-context, role-locked, adversarial reviewers, each verifying on disk (running the suite, driving the real adapter subprocesses, mutation-biting, exercising each --check case). Date: 2026-06-14.*

| Panelist | Charter | Verdict |
|----------|---------|---------|
| `panelist_A_adapter_correctness.md` | adapter correctness (wrap+tail) | **SHIP** |
| `panelist_B_security_check.md` | security & `--check` validation | **SHIP** |
| `panelist_C_honesty_regression.md` | honesty & regression | **SHIP** |

## Outcome: unanimous SHIP (round 1)

### Panelist A — adapter correctness (SHIP)
The matcher selects the most-recent MATCHING line (not the raw last line / noise) — `test_most_recent_match_wins` (pin) confirms. Wrap builds its OWN `_LogTail` incremental reader fed by the keepalive (genuine new seek/tell state, NOT free-riding `_last_output_line`); tail rides the SAME `new_lines()` pass it already reads for doneness (no second reader). The staleness age hint fires when the matched line is older than one interval; the placeholder is `(running...)`; all runtime label strings are ASCII (NFR-7 — the only non-ASCII is em-dashes in comments/docstrings, never in an emitted label). The display-only firewall holds: the mutation bite (breaking `feed` to record the last line) made both `EndToEnd` noise-exclusion assertions FAIL, restored green. Synthesis emits `--activity-regex` for both adapters. Suite 255 green.

### Panelist B — security & `--check` (SHIP)
The ReDoS bounds are real and honestly described: per-line 4 KiB truncation caps INPUT (a `(a+)+$` on a 100k-`a` line returns in ~0s because search sees only 4096 chars) — the docstring states plainly it does NOT bound backtracking; the per-scan 256 KiB ceiling is enforced and proven (`test_byte_ceiling_stops_and_retains_last`: a 286 KiB flood never reaches the late HIT, last label retained); the operator-self-wedge residual (NFR-11) is disclosed, not hidden. Every `--check` bad case bites (non-list / non-string / empty / >16 / uncompilable / complexity-screen + the success/failure compile retrofit), run individually; safe patterns (`(x|y)+`, `BUILD (OK|DONE)`, `\d{2,5}`) are not false-rejected; the end-to-end `--check` gate exits 1 on `(a+)+`. Patterns are passed as argv elements (no shell); the `{PLACEHOLDER}` footgun is documented. Non-blocking: the heuristic misses `(a|aa)+` / `a{1,100}{1,100}` — the documented "common shape, not all ReDoS" contract, with the byte ceiling as the real loop guard.

### Panelist C — honesty & regression (SHIP)
The display!=doneness firewall holds: `git diff` of `tick.py` is ONLY the worker_cmd synthesis + the `--check` validation — zero change to terminal/lifecycle logic; pinned by `test_never_matching_pattern_does_not_affect_doneness`. The staleness hint prevents the "looks-live-but-stale" regression (pinned). The §9 flip is honest: FR-56 VERIFIED citing real, confirmed-existing tests; no dogfooding/always-on tokens; the Windows floor row stays PENDING; `test_positioning_honesty.py` green with both guards intact. No regression: 255 passed on three clean runs; the adapter tests (46) pass; `poll(now=None)` is backward-compatible; additive for no-pattern jobs. Pure stdlib `re`, no new dependency; ASCII placeholder/hint. The NFR-11 residual is disclosed.

## Net
FR-56 ships: both adapters show the most-recent operator-pattern-matching output line as the ACTIVITY label, filtering a chatty tool's noise — DISPLAY-ONLY, the engine still interprets only `status` (doneness untouched: wrap exit-code-only, tail the FR-41 precedence). Wrap got a new incremental reader; tail rides its existing pass; the shared matcher carries a staleness age hint so a pinned-but-stale status never looks live, with `(running...)` before any match. `--check` validates the patterns (and now compiles the success/failure regexes too) with a nested-unbounded-quantifier complexity screen; the ReDoS bounds (per-line truncation + per-scan byte ceiling + complexity screen + ≤16 cap) are reduced-and-disclosed, not eliminated. §9 FR-56 → VERIFIED on real test evidence; the cadence/Windows floor row stays PENDING. Suite 234 → 255.

**Non-blocking notes addressed:** the `EndToEnd` real-subprocess timing tests (grace-0 1s keepalive vs the child's lifetime) were hardened — the child now runs ~3s so the keepalive reliably fires, removing the load-dependent flake B/C observed.
