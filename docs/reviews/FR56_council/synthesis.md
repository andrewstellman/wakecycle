# FR-56 (activity-pattern extraction) — 3-panel council synthesis

*2026-06-14. Panelists: A (adapter-fit), B (security/schema), C (scope/honesty). All findings incorporated into FR-56 / UC-12 / §9.*

## Verdict: SHIP the spec (revised); **build target v0.2**

No panelist found dishonesty in the framing (C verified all four honesty axes pass). The findings were correctness/precision fixes on a sound spec, plus a build-placement recommendation.

## Findings incorporated

| # | Panelist | Finding | Fix applied |
|---|---|---|---|
| 1 | A | The "rides the same new-lines pass" claim is **false for wrap** — `_last_output_line` reads the whole capture file and returns only the last line; tail has the incremental `_LogTail` pass, wrap does not. | Spec now states wrap needs a **new incremental reader** in `_Keepalive`, not a free ride; the self-contradiction removed. |
| 2 | A | Synthesis + `--check` validation are **tail-only today**; wrap has no optional flags. | Config-surface bullet now says "both wrap and tail," and that FR-56 adds flags to the wrap branch. |
| 3 | A | `status` collides with the one interpreted field (FR-18). | **Renamed** `adapter_status_patterns`→`adapter_activity_patterns`, `--status-regex`→`--activity-regex`, throughout (FR-56, UC-12, §9). |
| 4 | A | FR-18 boundary needs an explicit producer/reader clause. | Added: the adapter is a worker-side producer; the engine reader still interprets only `status`. |
| 5 | B | ReDoS: per-line truncation caps *input length*, **not** backtracking (pattern-driven) — original wording was dishonest. | Rewrote the safety bullet honestly; truncation reframed as input-length only. |
| 6 | B | Missing the bound that actually protects the tick loop. | Added a **per-tick total-bytes-scanned ceiling (≈256 KiB)**, after which matching stops and the last label is retained. |
| 7 | B | No compile-time complexity screen; pattern cap unnamed. | Added a **complexity screen at `--check`** (AST scan for nested `MAX_REPEAT`, conservative heuristic) + a named **≤16-pattern cap**; both enforced at `--check`. |
| 8 | B | `--check` must reject the **empty pattern** (`re.compile("")` matches everything, silently defeating the filter); compile all adapter regexes (existing ones are only type-checked). | Added to the `--check` validation bullet, incl. the retrofit of the existing success/failure regexes. |
| 9 | B | Injection is clean (list-synthesized, no shell), but `{TASK_ID}`-style template substitution could rewrite a token-shaped substring in a pattern. | Documented as a footgun (not injection) in the implementation note. |
| 10 | B/C | Capture-group extraction adds edge cases (non-participating group → `None`) and is gold-plating. | **Deferred** capture groups from v1; v1 shows the whole matched line, truncated. |
| 11 | C | **Staleness honesty hole (blocking):** a matched line pins indefinitely when the tool goes quiet — looks live, is stale, *worse* than the raw last line. | Added a **staleness age hint** to the label (`Step 7/12 (8m ago)`) once the source line is older than the current keepalive. |
| 12 | C | "Ordered list" is decorative under recency-wins; scan window unspecified. | Reworded to "a set"; matching is within the **per-tick scan window**. |
| 13 | C | **Build placement: v0.2.** v0.1.0 just gated clean; reopening for a cosmetic, display-only win; "warm plumbing" is a velocity argument (the FR-55 shape). Deferral is structurally free (additive, no migration). | Adopted **build target v0.2** in the section intro, the §9 row, and the version notes; flagged for operator confirmation. |

## Note for the operator

The build-placement recommendation (v0.2) is yours to confirm. It does not block any v0.1.0 work — FR-56 is purely additive and deferring it costs nothing. I've recorded v0.2 as the recommended target and am proceeding with the rest of the arc; say the word if you'd rather pull it into v0.1.0.
