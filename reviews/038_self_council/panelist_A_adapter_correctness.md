# Panel 038 — Reviewer A: Adapter Correctness (wrap + tail)

FR-56 — activity-pattern extraction for the wrap/tail adapters. Spec authority:
`docs/REQUIREMENTS.md` FR-56. Work is UNCOMMITTED (working tree). Adversarial,
independent inspection + driven runs + mutation bite.

## 1. Matcher selects the MOST-RECENT matching line — VERIFIED

`_ActivityMatcher.feed` (heartbeat.py ~350-366) iterates new lines; on every
`rx.search(ln)` hit it OVERWRITES `self.matched`/`self.matched_at`, so the last
matching line in scan order wins — not the raw last line, not noise. `label`
returns `self.matched`. Test `Matcher::test_most_recent_match_wins` (PIN) feeds
`["noise","Step 1","noise","Step 2 ok","noise"]` and asserts `"Step 2 ok"` — the
most-recent MATCH, dropping the trailing noise. `test_no_match_placeholder` and
`test_byte_ceiling_stops_and_retains_last` confirm the selection discipline.
`python3 -m pytest tests/test_activity_patterns.py -q` → 21 passed (green).

## 2. Wrap's NEW incremental reader correct; tail rides the existing pass — VERIFIED

- WRAP: `_cmd_wrap` (heartbeat.py ~500-506) builds `reader = _LogTail(capture_path)`
  when `activity.patterns` and passes it as `activity_reader`. `_Keepalive.maybe_emit`
  feeds `self.activity_reader.new_lines()` incrementally on each due tick. `_LogTail`
  (536-560) is genuine new seek/tell state (`self.pos`, buffered partial line) —
  NOT free-riding `_last_output_line` (which reads the whole file). Spec demand met.
- Label selection: `maybe_emit` uses `self.activity.label(...)` when
  `self.activity is not None and self.activity.patterns`, else
  `_last_output_line(...) or "(running, no output yet)"`. Correct fallback.
- TAIL: `_TailWatcher.poll(now)` calls `self.tail.new_lines()` ONCE into `lines`,
  feeds the SAME list to `self.activity.feed(lines, now)`, then iterates `lines`
  for doneness — no second reader. Keepalive gets `activity_reader=None`. Confirmed.

## 3. Staleness age hint + ASCII placeholder — VERIFIED

`label` emits the bare match when fresh; when `(now - matched_at) > max(1.0, interval)`
it appends `" (%s ago)" % _age_hint(...)` so a pinned-but-stale status never looks
live. Placeholder before any match is `(running...)`. `test_staleness_age_hint`
(PIN) pins fresh-no-hint + stale-with-`ago)`. **NFR-7 ASCII (CRITICAL):** all
runtime-emitted strings — `(running...)`, `(running, no output yet)`, `%ds/%dm/%dh`,
`(%s ago)` — are pure ASCII. `grep -nP '[^\x00-\x7F]'` over ADDED lines hits ONLY
em-dashes in source COMMENTS/docstrings (tick.py 147/154/156/179/232; heartbeat.py
110 is pre-existing) — none reach the status table. `test_placeholder_and_hint_are_ascii`
pins `.isascii()`. No non-ASCII in any runtime label path.

## 4. Display-only firewall — doneness UNTOUCHED — VERIFIED (mutation-pinned)

- Structure: in `_TailWatcher.poll` the activity feed is a separate guarded branch
  (`if self.activity is not None and now is not None`) that cannot alter the
  doneness scan over `lines`; wrap doneness stays exit-code-only (keepalive only
  reads the capture for its label). Legacy `poll()` callers (no `now`) keep exact
  prior behavior — `now=None` default + the guard. `test_tail_adapter.py` (11
  `poll()` calls) unaffected.
- `DonenessFirewall::test_never_matching_pattern_does_not_affect_doneness` (PIN):
  never-matching pattern → `poll(now=100)` still returns `COMPLETED` via success_re
  while `label` stays `(running...)`.
- `EndToEnd` drives the REAL wrap + tail subprocess (grace 0) against the simulator
  emitting `step N.R` amid `noise: chatter`; asserts a relevant `step` label AND no
  `noise: chatter` label. `pytest ...::EndToEnd -q` → 2 passed.
- MUTATION BITE: snapshot via shutil.copy2 → broke `_ActivityMatcher.feed` to record
  the LAST line regardless of match → purged `__pycache__` → both EndToEnd tests
  FAILED with label = `'...noise: chatter ... (irrelevant, ignore me)'` (the
  noise-exclusion assertion fired exactly as designed) → restored via shutil.copy2 +
  re-purge → mutation gone, `git diff --stat` matches intended, EndToEnd green.

## 5. Synthesis emits --activity-regex for BOTH adapters — VERIFIED

`_adapter_worker_cmd` (tick.py ~1093-1106) builds `activity` from
`entry["adapter_activity_patterns"]` as repeated `["--activity-regex", str(pat)]`
and splices it: wrap → `helper + activity + ["--"] + command` (BEFORE the `--`);
tail → `helper + activity + ["--log-file", ...]`. `Synthesis::test_wrap_...` pins
2 flags with `index("--activity-regex") < index("--")`; `test_tail_...` pins presence.

## 6. Full suite — VERIFIED

`python3 -m pytest -q | tail -3` → **255 passed in ~23s**.

## Notes (non-blocking)

- ReDoS: `_regex_complexity_problem` is a reduced-and-disclosed heuristic (nested
  unbounded quantifier + length cap), honestly documented; stdlib `re` has no match
  timeout. In-scope per NFR-11/FR-56. The per-line cap (4 KiB) + per-scan ceiling
  (256 KiB) bound INPUT only, also disclosed.
- `--check` retrofit compiles success/failure regexes too (was type-check only);
  pinned by `test_retrofit_*`. Good.

VERDICT: SHIP
