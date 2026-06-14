# FR-56 Council — Panelist C: SCOPE, NECESSITY & HONESTY

**Charter:** adversarial — find where FR-56 is scope creep, gold-plating, or dishonestly framed.

**Verdict: CONCERN (lean SHIP-the-spec / DEFER-the-build to v0.2).**

The spec is unusually honest and the doneness/display firewall is real and correct. FR-56 is *not* dishonestly framed in the way the charter feared — it does not leak into correctness, and the §9 PENDING row tells the truth. But it **is** mild gold-plating relative to a real but narrow pain, and the right call is to keep the spec captured (as it is) and **build it in v0.2, not reopen v0.1.0's adapter surface for it.** Detailed findings below.

---

## 1. Necessity vs. gold-plating

**The pain is real but narrow.** Today both adapters (FR-40 wrap, FR-41 tail) set `label` to the *most-recent non-empty output line*. For a chatty wrapped tool that line is whatever printed last — often a progress-bar repaint, a debug line, or a warning — not the meaningful status. The operator who wraps `make`, a noisy test runner, or a verbose third-party CLI genuinely sees junk in the ACTIVITY column. That is a real UX defect, and it sits squarely on the "wrap anything that knows nothing about arunner" identity (FR-54), which is the project's headline promise. So this is not invented need.

**But the value is bounded and the workaround is cheap.** The cost of *not* having FR-56 is: the ACTIVITY label is sometimes noise. That is a cosmetic, display-only degradation. Doneness is unaffected (FR-40 exit code, FR-41 precedence). The operator who cares can already reduce noise at the source — quiet the wrapped tool's flags, or use `tail` against a log the tool writes to a cleaner file. The displayed label is also truncated-to-width and is explicitly *not* load-bearing for any decision the operator must make (the disk and the status table are the UI per §8; doneness is elsewhere). So the harm of the status quo is "the human glances at a column and sees a less-useful string."

**Who actually hits it:** an operator wrapping a *single* chatty tool whose status is buried in noise and who *also* knows a stable regex for the status line and *cares enough* to maintain it across tool version bumps. That is a thin slice of the user base. The much more common adapter user — wrapping an agent CLI or a build that already prints reasonable last-lines — gets nothing from FR-56.

**Proportionality.** Against that thin-slice cosmetic win, FR-56 adds: a new plan schema field (`adapter_status_patterns`), `--check` validation logic, adapter matching logic in *two* code paths (wrap's capture-file scan + tail's line scan), capture-group extraction, a neutral-fallback state machine, a per-line length cap, a per-job pattern-count cap, operator documentation, tests for all of it, and a council review. Even granting that tail already has regex plumbing (FR-41 success/failure regex), this is **net-new feature surface** for a cosmetic improvement. The capture-group extraction in particular (§Matching semantics) is the gold-plated part — "show the whole matched line" satisfies 90% of the need; "extract a named capture group" is a connoisseur feature added because regex makes it easy, not because the use case demanded it.

**Verdict on necessity:** Real pain, narrow audience, cosmetic stakes, disproportionate-but-not-egregious build. This is **soft gold-plating** — defensible to build eventually, not justified as urgent. Capture-group extraction should be explicitly flagged as the trim-first candidate if scope needs cutting.

## 2. v0.1.0 vs v0.2 placement — recommendation: **v0.2.**

A real recommendation, not a hedge:

**Build it in v0.2.** Reasons:

- **v0.1.0 just passed a clean release-gate.** FR-56 reopens the adapter surface (`heartbeat.py` labeling, plan schema, `--check`) immediately after the gate closed. The marginal *code* is modest because tail has regex plumbing — but the marginal *risk* is not just code: it's re-validating the adapter doneness firewall, re-running the wrap/tail scenario suite, and a fresh council, all to ship a cosmetic column improvement. Reopening a just-gated surface for a non-correctness feature is exactly the kind of velocity-driven scope creep a correctness-over-speed project should resist.

- **"No deadline" cuts the other way here.** The project explicitly values correctness over speed and has no ship pressure. The *only* argument for cramming FR-56 into v0.1.0 is "the plumbing is already warm." That is a velocity argument dressed as efficiency — the same continuation-pressure shape FR-55 exists to catch. With no deadline, there is no cost to letting v0.1.0 stay frozen at its gate and batching FR-56 with the v0.2 adapter/host-matrix work (§8 already parks "full Codex/Cursor per-host validation matrices (v0.2)" and "harness resume/iterate strategies" there — FR-56 belongs in that same batch).

- **The display-only nature makes deferral free.** Because FR-56 touches nothing in the correctness path, deferring it costs the operator *nothing structural* — only the cosmetic noise persists, and only for the thin slice in §1. There is no migration, no schema break (the field is additive), no doneness regression risk created by waiting. A feature whose deferral cost is purely cosmetic should be deferred when the alternative is reopening a sealed gate.

- **The marginal-code argument is real but secondary.** Yes, tail's regex plumbing lowers the v0.2 build cost too — it doesn't expire. So "build now because plumbing is warm" buys nothing that "build in v0.2 on the same warm plumbing" doesn't also get.

**What I am NOT recommending:** killing the spec. Capturing FR-56 now (as done) is correct — it documents the known adapter limitation honestly and gives v0.2 a councilled starting point. The PENDING-with-open-placement state is the right artifact; my recommendation just *resolves* the open placement to v0.2.

## 3. Honesty of the spec

This is where FR-56 is strongest, and I went looking hard for the leak.

**(a) Display-only vs. doneness — HONEST, and unusually well-firewalled.** The "Scope" bullet is explicit and repeated: "governs the human-facing ACTIVITY column… does NOT change lifecycle/terminal determination." UC-12 step 5 and the Postconditions both re-assert it. No wording leaks into implying correctness improvement. I checked the rationale lines (UC-12 line 208: "without touching doneness correctness"; FR-56 line 351: "never the correctness of done/failed") — they actively *guard against* the misread rather than inviting it. **No overclaim here.** If anything the spec over-protests, which is the right direction.

**(b) ReDoS "reduced and disclosed, not eliminated" — HONEST, not hand-wavy.** The framing is accurate. stdlib `re` genuinely has no match timeout, the `regex`-module timeout is genuinely barred by NFR-3, and the three named mitigations (4 KiB line cap, bounded pattern count, anchored-pattern guidance) are real and stdlib-only. Critically, it does **not** claim safety — it says the catastrophic-backtracking exposure is *capped*, not removed, and ties the honesty posture explicitly to FR-55. That is the correct calibration. **One residual nit:** the 4 KiB cap bounds input length per match, which bounds but does not *eliminate* pathological backtracking blow-up (a 4 KiB adversarial line against a nested-quantifier pattern can still burn real CPU). The spec says "capping catastrophic-backtracking exposure" — accurate as "capping," would be dishonest as "preventing." It says capping. Fine. The operator-authored-pattern threat model also means the regex is *operator's own foot-gun*, not an external-attacker injection — worth one sentence in the build to say the ReDoS surface is operator-self-inflicted (the operator writes the pattern; external output only triggers it), which further deflates the severity. Recommend that clarification but it's not a blocker.

**(c) "Harness interprets output" tension with FR-18 — HONEST, and the firewall holds.** This was the sharpest charge and I checked FR-18 directly (line 250): "`status` is the ONLY field the harness interprets… `label` is a short free-form string displayed verbatim." The question is whether FR-56 quietly re-introduces output interpretation. **It does not, and here's the precise reason:** FR-18's "harness" is the *engine/state machine*. FR-40/41 already establish that the **adapter** (not the engine) sets `label` from the wrapped command's output and decides doneness — "the adapter, not the engine, decides doneness" (FR-41 line 299). FR-56's regex matching runs *inside the adapter*, choosing *which output line becomes `label`* before `label` is emitted. The engine still sees only a pre-computed `label` string and still interprets only `status`. So FR-56 moves the adapter's *existing* "pick a line for label" logic from "most-recent line" to "most-recent matching line" — it does not push interpretation into the engine. The FR-18 invariant ("label is free/uninterpreted by the engine") is intact: the engine never reads the patterns or the output; it reads a finished `label`. **No violation.** I'd recommend the build add one sentence to FR-56 making this explicit ("the matching is adapter-internal; the engine still receives only the finished `label` and interprets only `status`, FR-18 unchanged") so a future reader doesn't re-litigate this — but the design is correct as written.

**(d) §9 PENDING / open build placement — HONEST.** The §9 row (line 412) says "PENDING — spec'd + councilled 2026-06-14; build placement (v0.1.0 vs v0.2) open." The v0.1.x-addition note (line 348) and the v1.3 closing summary (line 416) all consistently say PENDING with placement open. This is the honest state: spec captured, not built, placement undecided. It does **not** masquerade as VERIFIED or claim any evidence it lacks — contrast the disciplined VERIFIED rows above it that cite specific test files and run-dirs. The §9 row correctly carries *no* evidence link because there is no build yet. **This is exactly the honest state NFR-12 demands.** My §2 recommendation would change "placement open" to "v0.2" — that's a decision, not a correction to a dishonesty.

**Honesty verdict: PASS on all four.** FR-56 is one of the more honestly-framed requirements in the doc. The charter's suspicion of dishonest framing is not borne out.

## 4. Edge / UX coherence — the genuinely underspecified part

This is where REVISE pressure actually lives. The matching semantics ("recency wins across lines," capture-group extraction, neutral fallback) create confusing cases the spec waves past:

1. **Stale "most-recent relevant line."** The headline failure: the tool prints `Step 7/12`, then goes quiet (or spews 500 lines of noise that match no pattern). Recency-wins-across-*matching*-lines means the label stays pinned at `Step 7/12` indefinitely — it *looks* live but is stale. The operator reads "Step 7/12" and believes the tool is at step 7 *now*, when it may have been there for 20 minutes. **This is the one place FR-56's display can actively mislead** — worse than the raw-last-line behavior it replaces, because the raw last line at least keeps moving (and a frozen raw last line is a visible "nothing's changing" signal, whereas a frozen *status* line looks like deliberate status). **Recommendation: the displayed label MUST carry an age/staleness hint** when the chosen line is older than some interval (e.g. `Step 7/12 (8m ago)` or a `⏱`/`~` marker). Without it, FR-56 trades "noisy but honestly-live" for "clean but potentially-stale," which is a net honesty *regression* in exactly the cases the feature targets. This alone justifies CONCERN over SHIP-as-spec'd.

2. **Two patterns matching different recent lines.** "A line is relevant if it matches *any* pattern; the most-recent relevant line becomes the label." So if line N matches pattern 2 and line N+1 matches pattern 1, the label is line N+1's extraction. Fine — recency wins, ordered list is just an OR. But the spec calls it an "*ordered* list," implying priority, then uses it as an unordered OR. **The ordering is decorative** — it does nothing in the recency-wins model. Either make ordering meaningful (priority tiebreak when two patterns match the *same* line) or drop the "ordered" framing as misleading. Minor, but it's a small honesty wrinkle in a spec that's otherwise crisp.

3. **Status line scrolls off the wrap capture file.** Wrap "scans its capture file" — if the file is rotated/truncated, or the adapter only scans *new* lines per tick, a status line emitted between ticks competes with later noise lines in the same tick's batch (recency within the batch resolves it — OK), but a status line from *before* the adapter started reading is lost. UC-12 assumes the adapter sees every line; the spec should state the scan window (since-last-tick incremental, matching FR-41's existing incremental scan) so "most-recent relevant line" is well-defined as "most-recent within the scanned window," not "most-recent ever."

4. **Capture-group extraction failure modes.** A pattern names a capture group that the matched line doesn't populate (optional group, alternation where one branch lacks the group) → empty label? Falls back to whole line? Falls back to neutral? Unspecified. This is the gold-plated feature (§1) generating its own edge cases. **Recommend deferring capture-group extraction entirely** (show the matched line) and adding it only if a real user asks — that removes both the §1 proportionality complaint and this edge case at once.

5. **UC-12 realism.** UC-12 is realistic for the *motivated* operator (`^\[PHASE\]`, `Step \d+/\d+` are plausible real patterns). It is *unrealistic* as a default-path UX — it requires the operator to know the tool's log format, author correct regex, and maintain it. UC-12 honestly scopes itself to "the operator knows a pattern" (Preconditions, line 210), so it's not overclaiming reach — but it confirms the §1 thin-slice point: this is a power-user feature, which reinforces the v0.2 placement.

---

## Summary of required changes (if/when built)

**REVISE-REQUIRED before any build (blocking):**
- **Staleness hint on the displayed label** (Finding 4.1) — without it FR-56 can present stale status as live, a net honesty regression in its own target case.

**Recommended in the build (non-blocking but strong):**
- Specify the scan window explicitly (4.3): "most-recent matching line *within the per-tick incremental scan window*," not "ever."
- Drop or define the "ordered" semantics (4.2) — it's currently decorative.
- **Defer capture-group extraction** (4.4 + §1) — show the matched line; add extraction only on real demand. Removes the gold-plated edge cases.
- One sentence each making the FR-18 firewall explicit (3c) and the ReDoS surface operator-self-inflicted (3b).

**Build placement: v0.2** (§2) — capture the spec now (done), do not reopen v0.1.0's just-gated adapter surface for a display-only feature. Batch with the v0.2 adapter/host-matrix work already parked in §8.

**Bottom line:** Honest spec, real-but-narrow need, soft gold-plating on the capture-group corner, one genuine display-honesty hole (staleness). Not dishonest, not urgent, not v0.1.0. CONCERN, resolve placement to v0.2, fix the staleness hint before it ships.
