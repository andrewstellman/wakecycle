# FR-56 Council Review — Panelist B: SECURITY (ReDoS) & SCHEMA

**Verdict: REVISE-REQUIRED**

FR-56 is sound in shape — display-only, doneness-isolated, stdlib-only, compile-at-`--check`. The injection surface is clean. But the ReDoS section as written (§350 line 354) is **underspecified and one of its claims is misleading**: 4 KiB per-line truncation does *not* bound catastrophic backtracking — it only reduces the constant factor, and the spec presents it as if it "caps exposure." There is no per-tick total-bytes bound and no compile-time complexity screen, both of which are cheap and stdlib-only. The schema addition is good but the proposed `--check` validation list has gaps (empty-pattern, cap, capture-group-arity). I'd ship after the mitigations below land in the spec; the current spec text would let an operator wedge a tick with a single pathological pattern and call it "reduced and disclosed."

---

## Finding 1 — ReDoS: truncation is mischaracterized; the mitigation set is incomplete (REVISE-REQUIRED)

**1a. 4 KiB truncation does not bound catastrophic backtracking — it bounds the input length only.**
Catastrophic backtracking in `re` is super-linear in input length `n` (e.g. `(a+)+$` against `n` `a`s followed by a non-match is exponential in `n`; `(a|a)*` and nested quantifiers are similar). Truncating `n` to 4096 does **reduce** the worst case — but `2^4096` and `4096^2` are both "the tick never returns" for practical purposes. For an exponential pattern, even a few dozen characters wedges. So the spec's framing — "(a) each line is truncated… capping catastrophic-backtracking exposure" (§354) — is **not honest**: truncation caps the *input*, not the *blowup*. The blowup is a function of the *pattern*, which the operator controls and the truncation does not touch.

The honest statement is: *truncation bounds linear and quadratic scans to a fixed ceiling, and makes exponential patterns wedge a little later — it does not prevent a pathological pattern from wedging a tick.* The spec must say that, or the "reduced and disclosed, not eliminated" posture is itself overselling.

**1b. There is no bound on total bytes scanned per tick — only per line.**
Per-line truncation × `N` matching passes × `P` patterns is the real cost. The tail adapter scans *every new line* each poll (`_LogTail.new_lines()` returns all complete lines since last call — heartbeat.py:442), and a chatty tool can append megabytes between polls. 4 KiB/line is meaningless if a single `new_lines()` returns 50,000 lines and each runs `P` patterns. **Required: a per-tick (per-`new_lines()`-batch) total-bytes-scanned ceiling** (e.g. 256 KiB), after which the adapter stops status-matching for that batch and keeps the last matched label. This is a few lines of stdlib accounting and it is the bound that actually protects the tick loop.

**1c. There is no compile-time complexity screen.**
The cheapest real defense against the exponential class is a *pattern* check, not an *input* check. Two stdlib-only screens, both at `--check` (compile-time, free):
  - **Pattern-length cap** (e.g. reject patterns > 512 chars) — crude but catches generated/pasted monsters.
  - **Nested-quantifier heuristic** — reject patterns where a quantified group itself contains an unbounded quantifier (the `(x+)+`, `(x*)*`, `(x+)*` shapes). A conservative regex-on-the-regex or a small scan over `re.sre_parse`/`re._parser.parse(pat)` AST flagging `MAX_REPEAT` directly nested inside `MAX_REPEAT` catches the classic catastrophic forms. This is heuristic (it will have false positives and miss exotic cases) and must be *documented as heuristic*, but it converts "operator can wedge a tick" into "operator has to work to wedge a tick." This is the single highest-value addition and it is missing from §354.

**1d. "Bounded number of patterns per job" has no number.**
§354(b) says "a bounded number of patterns per job" but names no cap. Pick one and put it in the spec and the `--check` (I'd say 16 — generous for real status-line filtering, low enough to bound the per-line cost). An unnamed cap is not a cap.

**Required mitigation set (all stdlib, all must appear in the spec before ship):**
1. Per-line truncation to a fixed ceiling (4 KiB is fine as a value) — **re-described honestly** as bounding linear/quadratic scans, *not* "capping catastrophic backtracking."
2. **Per-batch total-bytes-scanned ceiling** (NEW — the bound that actually protects the tick): stop status-matching a `new_lines()` batch once a byte budget is exhausted; retain the last matched label.
3. **Explicit pattern cap** with a named number, enforced at `--check`.
4. **Compile-time complexity screen** at `--check`: pattern-length cap + nested-unbounded-quantifier heuristic (documented as heuristic, conservative-reject).
5. Operator guidance to prefer anchored, possessive-free, non-nested patterns (keep — but it is the weakest layer, not a substitute for 2–4).
6. The disclosure sentence must state plainly that a determined operator can still author a pattern that slows or wedges a single tick, and that this is an *operator-trusts-their-own-config* surface (consistent with NFR-11: the operator already controls `worker_cmd` and `command`, so a self-inflicted regex DoS is in the same trust bucket — say so, don't imply the mitigations remove it).

**On "reduced and disclosed, not eliminated":** the *posture* is honest and correct (you cannot eliminate ReDoS without a match-timeout, and the `regex` module is barred by NFR-3). The *current spec text* undersells the residual risk by attributing backtracking protection to truncation. Fix the attribution and add bounds 2–4; then the honesty claim holds.

---

## Finding 2 — Injection / synthesis: CLEAN (SHIP, with one assertion to lock in)

The pattern strings are synthesized into the `worker_cmd` **list** in `_adapter_worker_cmd` (tick.py:945–973) exactly as `success_regex`/`failure_regex` already are: `out += ["--success-regex", str(entry["success_regex"])]` — each pattern is its own argv element, never concatenated into a shell string. Dispatch resolves placeholders token-by-token (`[_resolve_template(tok, sh_values) for tok in cmd_template]`, tick.py:1030) and the ticker `Popen`s the argv **with no shell** (no `shell=True` anywhere; the engine's whole adapter path is `subprocess.Popen(cmd, ...)` with a list). So:
  - **Spaces / quotes / `;` / `$()` / backticks in a pattern** → inert. They land in a single argv element and reach `re.compile` verbatim; no shell ever sees them. Safe.
  - **Leading `--` in a pattern** → the one real edge. A pattern like `--foo` arrives as the *value* of `--status-regex`, so argparse consumes it as that option's argument (argparse binds the next token to the option regardless of its leading dashes) — *provided* the synthesis always emits `--status-regex` immediately before the value, which it does (mirroring the `["--success-regex", str(...)]` pattern). **Required to confirm in the heartbeat.py `tail`/`wrap` argparse:** `--status-regex` must be `action="append"` (repeatable) and must NOT be declared with `nargs` that could swallow following tokens; a bare `add_argument("--status-regex", action="append")` is correct and a pattern beginning with `-`/`--` will be taken as its value. Add a test fixture with a pattern literally `^--marker`.
  - **`_resolve_template`** does `{KEY}` replacement only (tick.py:935–939). A pattern containing a literal `{TASK_ID}` or `{HARNESS_BIN}` substring **would be rewritten** by the resolver. This is a (minor) correctness/footgun, not an injection: the substituted value is an engine-controlled path, not attacker text, so it can't break the argv or reach a shell — worst case the operator's pattern is silently mangled. **Recommend** documenting that status patterns must not contain `{PLACEHOLDER}`-shaped substrings, or (better) resolving placeholders only in tokens the engine emits, not in operator-supplied values. Low severity; note it, don't block on it.

No shell-interpolation path exists. Injection: **safe.** Lock in the two argparse assertions (append-action, no greedy nargs) and the `^--` test.

---

## Finding 3 — Schema & `--check`: sound addition, validation list has gaps (REVISE-REQUIRED)

`adapter_status_patterns: ["...", ...]` is the right shape and the right home — per-job in the plan entry, parallel to the existing `success_regex`/`failure_regex` adapter fields, validated in `_check_adapter_entry` (tick.py:1278). Compile-at-`--check` is the **correct failure point**: it makes a bad pattern a pre-flight error (UC-12 alt-path (a): "the run never starts on a bad regex") instead of a runtime crash inside a detached worker that the engine can only observe as a launch failure. Runtime compile would surface as an opaque `auth_or_launch_failed` with no operator-facing "your regex is broken" — strictly worse. Compile-at-check is right.

The current `_check_adapter_entry` checks `success_regex`/`failure_regex` only for **string type** (tick.py:1300–1302) — it does **not** currently `re.compile` them. So FR-56 should not just add `adapter_status_patterns`; it should set the standard the existing regex fields don't yet meet. Required `--check` validations for `adapter_status_patterns`:

1. **Non-list** → reject (`must be an array of strings`).
2. **Non-string element** → reject (per-element type check).
3. **Empty-string element** → reject. An empty pattern compiles fine and `re.compile("").search(x)` matches *every* line at position 0, so an empty pattern makes *every* line "relevant" — silently defeating the entire point of FR-56 and pinning the label to the literal last line (noise). This is a quiet footgun; reject it explicitly with a clear message.
4. **More than the cap** (Finding 1d) → reject with the count and the cap.
5. **`re.compile` failure** → reject, naming the offending pattern *and* the `re.error` message (operators need the position/reason, not just "pattern 3 is bad").
6. **Complexity screen** (Finding 1c) → reject length-over-cap and nested-unbounded-quantifier patterns, flagged as heuristic.

Also: **whichever field FR-56 ships with should retroactively compile-check `success_regex`/`failure_regex` too** (those are doneness-bearing — a bad doneness regex is worse than a bad status regex, yet today they're only type-checked). If that's out of FR-56 scope, file it as a follow-up, but note in the spec that the existing fields are weaker than the new one so the asymmetry is intentional/tracked, not an oversight.

Catch a `re.error` (and `RecursionError`/`OverflowError` from pathological compiles) around the compile so `--check` reports the bad pattern rather than tracebacking.

---

## Finding 4 — Named-capture-group label: needs an explicit fallback ladder (CONCERN → REVISE)

The spec (§352) allows "a pattern may name a capture group whose text becomes the displayed label." The edge cases the spec does not pin down:

1. **Pattern matches but the named group did not participate** (e.g. `(?P<s>A)|B` matches on `B` → `m.group("s")` is `None`). Display `None` is wrong, and stringifying it to the literal `"None"` is worse (misleading status). **Required fallback:** when the configured group is `None`/didn't participate, fall back to the **whole matched line** (truncated), not to `None`/`"None"`/empty.
2. **The named group does not exist in the pattern** (operator declared a label-group name FR-56-side but the pattern has no such group). This should be an `IndexError`/no-such-group condition — decide at *`--check`*, not runtime: if FR-56 lets the operator name the group out-of-band, `--check` must verify the named group exists in the compiled pattern (`compiled.groupindex`). If instead the convention is "use the first/only named group in the pattern" (simpler, and I'd recommend it — no separate group-name config to validate), then `--check` just confirms ≤1 named group or documents which one wins.
3. **Empty-string capture** (group participated but matched empty) → treat as "no useful label," fall back to the whole line.
4. **Multiple named groups** → spec is silent. Pick a rule (first by position, or first by `groupindex`) and document it; don't leave it to `re` internals.

**Required safe fallback ladder, in order:** named group's text (if the group exists, participated, and is non-empty) → the whole matched line (truncated to the per-line ceiling, ASCII-sanitized as the table already does at tick.py:1106) → the neutral `"(running…)"` placeholder if nothing has matched yet (the spec already specifies this last rung — keep it). The label must **never** be `None`, the literal string `"None"`, or an empty string. Add a test for the non-participating-group case specifically — it's the one that silently produces `"None"`.

---

## Summary of required changes before SHIP

| # | Severity | Required change |
|---|----------|-----------------|
| 1a | REVISE | Re-describe truncation honestly: it bounds input length, NOT catastrophic backtracking. |
| 1b | REVISE | Add a per-batch total-bytes-scanned ceiling (the bound that actually protects the tick). |
| 1c | REVISE | Add a compile-time complexity screen at `--check` (length cap + nested-unbounded-quantifier heuristic, documented as heuristic). |
| 1d | REVISE | Name the pattern cap (suggest 16); enforce at `--check`. |
| 1-disclosure | REVISE | State plainly that a determined operator can still wedge a tick; frame as an operator-trusts-own-config surface per NFR-11. |
| 2 | SHIP | Injection is clean. Lock in: `--status-regex` is `action="append"`, no greedy `nargs`; add a `^--marker` test. Document the `{PLACEHOLDER}`-in-pattern footgun. |
| 3 | REVISE | `--check` must reject: non-list, non-string element, **empty pattern**, over-cap, `re.compile` failure (naming pattern + `re.error`). Compile-at-check is the right failure point. Note the existing `success/failure_regex` fields are only type-checked today (asymmetry — track it). |
| 4 | REVISE | Specify the capture-group fallback ladder: named group → whole matched line → neutral placeholder; **never** `None`/`"None"`/empty. Validate group existence at `--check` (or adopt "first named group" convention). Test the non-participating-group case. |

The architecture is right (display-only, doneness-isolated, stdlib, compile-at-check, list-synthesized argv). The ReDoS section needs an honest rewrite plus two real bounds (per-batch byte cap + complexity screen), and the `--check` list needs the empty-pattern and capture-arity gaps closed. Land those in the spec and it ships.
