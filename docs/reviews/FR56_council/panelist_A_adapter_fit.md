# Panelist A — ADAPTER-FIT & COHERENCE — FR-56

**Charter:** Does FR-56 correctly extend the real adapter code and cohere with the existing contract?

**Verdict: REVISE-REQUIRED**

FR-56's *contract* posture is sound (display-only, doneness untouched, FR-18 respected). But its **wrap integration story is factually wrong about the code it claims to extend**, and that wrongness is load-bearing: the spec asserts a "rides the same new-lines pass" symmetry between wrap and tail that does not exist in `heartbeat.py`. Wrap has no incremental new-lines reader; it re-reads the whole capture file and returns one line. Implementing FR-56's stated matching semantics on the wrap side requires NEW state the spec doesn't acknowledge, and the spec's own "scans its capture file" parenthetical quietly contradicts its headline claim. That is a spec-vs-code mismatch a builder will trip over. Below, numbered, with citations and concrete edits.

---

## Finding 1 (REVISE-REQUIRED) — The wrap "rides the same new-lines pass" claim is false; wrap has no new-lines pass and no most-recent-match capability today.

**Spec text under review** (REQUIREMENTS.md:352):
> "Patterns are an ordered list applied to each new output/log line (the tail adapter already scans new lines incrementally for its doneness regexes — status patterns ride the same pass; wrap scans its capture file). A line is *relevant* if it matches any pattern; the most-recent relevant line becomes the label (recency wins across lines)."

**What the code actually does:**

- Tail side — the claim holds. `_LogTail.new_lines()` (heartbeat.py:442-455) is a real incremental seek/tell reader with a partial-line buffer (`self.pos`, `self._buf`), and `_TailWatcher.poll()` (heartbeat.py:473-489) already iterates `self.tail.new_lines()` matching `failure_re`/`success_re` per line (heartbeat.py:475-479). A status-pattern scan genuinely *can* ride that loop. Good.

- **Wrap side — the claim is false.** Wrap's label comes from `_Keepalive.maybe_emit` (heartbeat.py:338-350), which calls `_last_output_line(self.capture_path)` (heartbeat.py:344). `_last_output_line` (heartbeat.py:294-305) does `Path(capture_path).read_text(...)` — it reads the **ENTIRE capture file** every keepalive, splits it, and returns **only the single most-recent non-empty line** (the first hit walking `reversed(text.splitlines())`, heartbeat.py:301-304). There is:
  - no incremental reader on the wrap path (no `_LogTail`, no `pos`/`_buf` — `_Keepalive` holds none, heartbeat.py:326-333);
  - no per-line scan loop — it returns on the FIRST non-empty line from the end and never inspects earlier lines;
  - no notion of "most-recent line *matching a pattern*."

**Why this is more than a trivial change (the question I was asked to press):** "most-recent line matching a status pattern" over the wrap capture file is NOT a filter you can drop into the existing one-line return. `_last_output_line` short-circuits at the last non-empty line; to find the most-recent *matching* line you must walk backward through *all* lines until a pattern hits (worst case the whole file, every keepalive — O(file) per ping, and the capture file grows unboundedly for a chatty tool, which is exactly FR-56's motivating case). So the wrap implementation is one of:

  (a) **Keep the whole-file re-read** and change the inner loop from "first non-empty" to "first that matches any pattern" — simplest, but it re-scans the entire (growing) capture file on every keepalive. For the chatty-tool case FR-56 exists to serve, that is a real per-tick cost the spec never flags.

  (b) **Add incremental state to the wrap path** (a `_LogTail` over the capture file, or a remembered "last matching line" carried on `_Keepalive`) so it scans only new bytes — which is genuinely NEW buffering/state on `_Keepalive`, the thing the spec's "rides the same pass" framing implies already exists but does not.

Either way the spec's symmetry framing is wrong. The parenthetical "wrap scans its capture file" (REQUIREMENTS.md:352) is the spec **contradicting its own headline** in the same sentence: the headline says both adapters ride the incremental new-lines pass; the parenthetical admits wrap does something different (whole-file scan). A builder reading this cannot tell which is normative.

**Recommended edit (REQUIREMENTS.md:352).** Split the matching-semantics bullet so it states the two adapters' mechanisms HONESTLY and picks a wrap strategy:

> "**Matching semantics.** Patterns are an ordered list applied per output/log line. **Tail** rides its existing incremental pass: `_TailWatcher.poll` already iterates `_LogTail.new_lines()` for the doneness regexes (`success_re`/`failure_re`); status patterns are matched in that same per-line loop, and the most-recent matching line is retained as the label across polls. **Wrap** has no incremental reader today — `_Keepalive.maybe_emit` reads the most-recent line via `_last_output_line`, which reads the whole capture file and returns only the last non-empty line. FR-56 therefore adds, on the wrap path, EITHER (a) a most-recent-*matching*-line scan inside `_last_output_line` (whole-file re-read each keepalive — acceptable given the per-line length cap and bounded pattern count, but O(file) per ping for a growing capture file), OR (b) an incremental `_LogTail`-style reader over the capture file carrying a remembered last-match so each keepalive scans only new bytes. The choice is an explicit implementation decision, not a free 'ride the same pass'. A line is *relevant* if it matches any pattern; the most-recent relevant line becomes the label (recency wins)."

Pick (a) or (b) in the spec; do not leave the builder to infer it from a self-contradicting parenthetical.

---

## Finding 2 (SHIP, with a wording nit) — The FR-18 boundary is respected; the adapter selecting a label by regex is NOT a "harness interprets label" violation.

FR-18 (REQUIREMENTS.md:250): "**`status` is the ONLY field the harness interprets**" and "`label` is a short free-form string displayed verbatim … never reads."

The correct reading: FR-18's "harness" is the **engine/reader** — the tick state machine that consumes heartbeat lines off disk and drives transitions. FR-56's regex selection happens **worker-side, inside the adapter** (`heartbeat.py` running as the dispatched shell worker, dispatch synthesized at tick.py:955-973). The adapter is a heartbeat *producer*, not the harness reader. It is choosing *which already-displayed-as-label line* to put in the `label` field — exactly the freedom FR-18 already grants any worker ("a vendored integration may set `label` from its own identity," REQUIREMENTS.md:250). The engine still reads only `status` (the state machine in `_reap`/transitions never touches `label`; confirmed — doneness for these adapters comes from exit code (heartbeat.py:411-417) / FR-41 precedence (heartbeat.py:473-489), and the engine reads the terminal `status`, FR-18). So no field the engine interprets is being driven by a regex. **Boundary respected.**

FR-56's own wording is careful here ("display/activity only — NOT doneness," REQUIREMENTS.md:351), which is correct. One nit: the term **"status pattern" / `--status-regex`** invites exactly the confusion FR-18 guards against — it reads as "a pattern that sets `status`," the one interpreted field. See Finding 3.

**Recommended edit:** Add one clause to FR-56's lead bullet making the producer/reader split explicit:

> "The adapter (a heartbeat *producer*, worker-side) selects which output line to place in the free-string `label`; the engine reader still interprets only `status` (FR-18) — regex selection of a display label is the same latitude FR-18 already grants any worker, not the harness interpreting `label`."

---

## Finding 3 (CONCERN) — Naming collision: `--success-regex`/`--failure-regex` (doneness) vs `--status-regex` (display) on the SAME `tail` subcommand will confuse operators; precedence is correct but undocumented at the surface.

On the tail adapter both kinds of regex now coexist on one CLI surface:

- `--success-regex` / `--failure-regex` → **doneness** (heartbeat.py:630-633; matched in `_TailWatcher.poll`, heartbeat.py:475-479; synthesized at tick.py:962-965).
- proposed `--status-regex` (repeated) → **display label only** (FR-56, REQUIREMENTS.md:353).

Three coherence problems:

1. **The word "status" is the most overloaded term in this codebase.** `status` is *the one interpreted field* (FR-18, REQUIREMENTS.md:250) and the literal heartbeat key (`build_progress`, heartbeat.py:140-142). Naming the **display-only** knob `--status-regex` / `adapter_status_patterns` collides head-on with the field that means doneness. An operator could very reasonably expect `--status-regex` to influence `status` (terminal determination), which is precisely the opposite of FR-56's scope. The doneness regexes, by contrast, are named for their *outcome* (`success`/`failure`), not for "status" — so the new name is the odd one out and the misleading one.

   **Recommendation:** rename to a label/display term — `--activity-regex` + `adapter_activity_patterns` (ACTIVITY is already the column name, REQUIREMENTS.md:250, and FR-56 itself says "the ACTIVITY column," REQUIREMENTS.md:207). This makes the surface self-documenting: `--success-regex`/`--failure-regex` decide done; `--activity-regex` decides what shows in ACTIVITY. No "status" overload, and it telegraphs display-only. (If the council prefers to keep "status" for operator-facing intuition — "show me real status" — then the CLI help and TOOLKIT MUST state in the same breath "display only; does not affect done/failed," and FR-56 should mandate that help text.)

2. **Precedence/interaction is correct but unspecified at the surface.** The two regex kinds are independent (one decides doneness, one decides display), so there's no precedence *conflict* — but an operator will ask "if a line matches both my failure-regex and my status-pattern, what happens?" The code answer: `failure_re` short-circuits `poll()` to FAILED (heartbeat.py:476-477) and the run terminates; the same line could also be the most-recent activity match. That's fine and consistent, but FR-56 should state it: a line may be BOTH a doneness marker and an activity match; they are evaluated independently and do not interfere.

3. **The deferred wrap-doneness companion (REQUIREMENTS.md:351) sharpens this.** FR-56 notes wrap *might later* accept the tail's success/failure overlay. If that lands, wrap too would carry both a doneness-regex pair and an activity-regex — so picking distinct, non-"status" names NOW prevents a worse collision later. Decide the naming with that future in view.

---

## Finding 4 (SHIP, two concrete gaps to close) — Config synthesis fits `_adapter_worker_cmd` cleanly; `--check` validation slots into `_check_adapter_entry` cleanly; but the spec under-specifies the WRAP path and the `re.compile`-vs-runtime story.

**Synthesis fit (good).** `adapter_status_patterns` → repeated `--status-regex` mirrors the EXACT pattern already used for the tail doneness regexes: `_adapter_worker_cmd` appends `--success-regex`/`--failure-regex` from `entry["success_regex"]`/`entry["failure_regex"]` (tick.py:962-965). A repeated-flag list is the natural extension — iterate `entry.get("adapter_status_patterns") or []` and append `["--status-regex", p]` per pattern. Consistent with FR-56's claim (REQUIREMENTS.md:353). **But note the asymmetry the spec ignores:** in the code today, those doneness regexes are appended **only in the `tail` branch** (tick.py:959-972); the **`wrap` branch is a single line** with no optional flags at all (tick.py:957-958). So "synthesized … consistent with `_adapter_worker_cmd`" is true for tail but requires a NEW append site in the wrap branch (tick.py:957-958) plus a NEW `--status-regex` arg on the `wrap` subparser (heartbeat.py:612-624 has no such arg today). The spec should say status-pattern synthesis must be added to BOTH branches, since wrap currently synthesizes zero optional flags.

**`--check` fit (good, with a placement note).** `_check_adapter_entry` (tick.py:1278-1305) is exactly the right home and already validates the tail regex fields as strings (tick.py:1300-1302). FR-56's compile-validation slots in here. Two concrete requirements the spec should pin:

- **Type + compile, both branches.** Add validation that `adapter_status_patterns`, IF present, is a list of strings (mirroring the `command` array check at tick.py:1297-1299) AND that each compiles via `re.compile` — for wrap AND tail (today `_check_adapter_entry`'s regex checks only run in the tail branch, tick.py:1294-1302; status patterns must be allowed on wrap too, so the new check belongs ABOVE the `wrap`/`tail` split or be duplicated). FR-56 says "each must compile as a Python `re`" (REQUIREMENTS.md:353) — note `_check_adapter_entry` does NOT currently `re.compile` the existing `success_regex`/`failure_regex` (it only type-checks them, tick.py:1300-1302). So FR-56 introduces the FIRST compile-validation in `--check`. That's fine and is an improvement, but the council should decide whether to ALSO retrofit compile-validation onto the existing doneness regexes for consistency (otherwise `--check` compiles status patterns but not success/failure patterns — an odd half-measure). I'd recommend retrofitting both in the same change.

- **Bounded pattern count enforced at `--check`.** FR-56's safety bullet (REQUIREMENTS.md:354) mandates "a bounded number of patterns per job." That bound must be enforced in `_check_adapter_entry` (fail loudly pre-flight), not merely documented — otherwise "bounded" is aspirational. Add `len(patterns) > N → error`.

**Per-line length cap (REQUIREMENTS.md:354) — name the seam.** The 4 KiB truncation must live where matching happens: the tail loop (`_TailWatcher.poll` over `_LogTail.new_lines()`, heartbeat.py:475) and the wrap scan (inside/around `_last_output_line`, heartbeat.py:301-304). Truncate each line to the cap **before** `pattern.search(line)`. FR-56 should name these two seams so the cap can't be applied in only one adapter (a chatty WRAP tool is the motivating case, and wrap is the path with no incremental reader — easiest to forget).

---

## Summary of required revisions before SHIP

1. **(Blocking)** Fix the false "rides the same new-lines pass" symmetry (REQUIREMENTS.md:352). Wrap has no incremental new-lines reader (`_last_output_line` whole-file-reads and returns one line, heartbeat.py:294-305). State the wrap strategy explicitly (whole-file most-recent-match re-scan, OR a new incremental reader) and stop implying the capability already exists.
2. **(Blocking)** Spec must say status-pattern synthesis + `--check` compile/type/count validation apply to BOTH `wrap` and `tail` — today the optional-flag synthesis (tick.py:957-958) and the regex checks (tick.py:1294-1302) exist ONLY on the tail path; wrap is a bare one-liner with no subparser arg (heartbeat.py:612-624).
3. **(Strong recommendation)** Rename `--status-regex`/`adapter_status_patterns` away from "status" (the one interpreted field, FR-18) → `--activity-regex`/`adapter_activity_patterns`, OR mandate "display only — does not affect doneness" in the CLI help and TOOLKIT.
4. **(Should-fix)** Add a clause making the producer(adapter)/reader(engine) split explicit so the FR-18 boundary is unambiguous; document that a line may be both a doneness marker and an activity match (independent evaluation).
5. **(Should-fix)** Name the truncation/cap seam in BOTH adapters (`_TailWatcher.poll`/`new_lines` and the wrap `_last_output_line` scan); enforce the pattern-count bound in `_check_adapter_entry`, don't merely document it.

The contract reasoning (display vs doneness, FR-18 respected) is correct and coherent. The defect is that FR-56 describes an adapter code shape that only half-exists: tail fits beautifully, wrap does not, and the spec papers over the difference with a symmetry claim the code refutes.
