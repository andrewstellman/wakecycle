# Panelist A — UC Fidelity Review of `ACCEPTANCE_TESTS.md`

**Charter:** Fidelity to the use cases. Does each acceptance test (a) route to the *right rung*, (b) actually *mirror* its use case rather than quietly relabeling the engine-only floor scenario, (c) cover the *whole* use case, and (d) is anything still mislabeled in `SDLC.md`/`TRACEABILITY.md` per the corrected model.

**Verdict: REVISE-REQUIRED.**

The design has internalized the corrected model at the framing level — the opening paragraph and the "core idea" section are correct, and the routing of the no-agent floor cases (UC-5/6/7) is right. But several individual runbook steps re-drift in exactly the way the charter warns about: they describe the *engine slice* (ticker-driven, read-the-disk) rather than the *lived* use case (the agent driving the loop / the operator's conversational experience). Two use cases are also mis-rung against their own REQUIREMENTS text, and three have material coverage gaps. The runbook is one revision pass away from being faithful; it is not shippable as written.

---

## Findings

### F1 — UC-2 monitor: REVISE. The runbook collapses the use case to a read-only diff and loses the operator-experience half. (coverage gap + partial re-drift)

REQUIREMENTS UC-2 (lines 65-75) is *"the operator reads per-tick status tables (or the disk) to understand run state"* — the table **is the UI**, and the postcondition is explicitly **None (read-only)**. The runbook step (line 28):

> **UC-2 (monitor):** during UC-1, confirm each tick's status table matches `harness_status.json` (read-only).

This grades the wrong thing. "Status table byte-matches `harness_status.json`" is a *necessary-condition* check — it's the engine emitting a faithful serialization, which the floor already covers (`test_cli` status read-only, per TRACEABILITY line 18). The *use case* is that **the operator can understand run state from what the agent prints**, including the alternative path (UC-2 line 74): *"Operator asks the agent to 'run another tick now' — safe by idempotency; only the cycle counter changes if nothing advanced."* That on-demand-tick branch is the actual lived monitoring interaction and it is **not** in the runbook at all.

The deeper problem: UC-2 is *"monitoring is an observation, not a run"* (the charter flagged this explicitly). A checker that reads a run-dir cannot grade "the operator understood the table," so the design correctly cannot make UC-2 a checker-graded run — but it then silently downgrades UC-2 to a serialization-equality assertion to *get* something checkable. That is the re-drift pattern: when the lived use case isn't disk-gradable, fall back to the engine slice and relabel it.

**Fix:** Split UC-2 into the two things it actually is. (1) The disk-truth invariant (table == `harness_status.json`) stays where it belongs — the necessary-condition floor — and is **removed from the acceptance runbook**, or explicitly named as "floor, surfaced here for the agent's convenience," not as the acceptance test. (2) The acceptance test proper exercises the *operator-facing* branch: during the UC-1 run, the agent honors an on-demand "tick now" request mid-run and the checker confirms the idempotent cycle-only diff (UC-2 line 74) — that is gradable *and* it is the lived interaction. Cite UC-2 lines 73-74 in the runbook step.

### F2 — UC-9 in-context: REVISE. The rehydrate half is asserted, not exercised; "simulate a drop" is underspecified to the point of being ungradable. (re-drift risk)

UC-9 (lines 156-168) is the richest agent-driven case: do in-context tasks → tick the background harness → **rehydrate from disk after a crash/compaction (FR-48)** → continue. The runbook (line 35):

> **UC-9 (in-context):** bootstrap on an instruction folder; do in-context tasks + tick the background harness; simulate a drop and rehydrate; grade outputs + background run-dir.

The grading target — "grade outputs + background run-dir" — checks the *engine* half (background run-dir consistent) and the *output* half (instruction outputs written), both of which are disk artifacts a checker can read. But the **load-bearing, hardest-to-test claim of UC-9 is the rehydrate itself**: that a *fresh agent context*, after a real drop/compaction, reconstructs in-context state from disk (FR-48) and resumes the *in-context queue* — not just the background harness (which UC-4 already covers via the deterministic floor; UC-9 alt-path (a), line 167, explicitly notes the background workers are rescuable by the floor while *the in-context queue waits for an operator re-bootstrap*). "Simulate a drop" with no definition of what gets torn down lets the test pass by re-bootstrapping the *same* warm context, which proves nothing about rehydration.

**Fix:** Specify the drop concretely — the agent's working context is discarded and a *fresh* bootstrap is pointed at the existing instruction folder + run-dir with no in-memory carryover (the FR-48 rehydrate path), and the pass criterion must include **the in-context queue resuming** (unfinished instruction tasks completed after rehydrate, no instruction task double-done), not only background-run-dir consistency. Also exercise alt-path (b) (line 167): a single in-context task longer than ~4× the stall threshold passing the "busy, not asleep" hint so the engine doesn't false-STALL — that is a real UC-9 behavior with a checkable journal signature, currently uncovered.

### F3 — UC-10 conversational build: CONCERN bordering REVISE. A disk checker structurally cannot grade the headline use case; the design names the steps but never says how "the agent assembled the *right* plan from NL" is graded. (the charter's "can a disk checker grade it?" — answer as written: no)

UC-10 (lines 170-183) is *"the headline UX"*: describe → preview → run → persist, **in natural language**, with the agent *inferring* dispatch mode, reading files as prompts, asking a clarifying question on ambiguity (alt-path b), and refining mid-build (alt-path c). The runbook (line 36):

> **UC-10 (conversational build):** describe → preview → run → persist a session in natural language; confirm the previewed plan ran and the saved bundle re-runs faithfully.

Two of the four grading hooks are disk-checkable and fine: "the previewed plan ran" (compare launched plan to preview) and "saved bundle re-runs faithfully" (re-run determinism). But the **essence of UC-10 is the NL→plan inference**, and *nothing in the runbook grades that the agent built the plan the operator actually asked for.* "run three jobs from ABC/DEF/GHI, pool 2, subagents" must produce pool=2, subagent dispatch, three entries with those three files as prompts (UC-10 step 1, FR-52.1). A checker *can* grade that — by diffing the assembled canonical plan against a per-scenario expected plan — but the design doesn't say so, so as written UC-10's pass condition is satisfiable by *any* plan that previews-then-runs-then-re-runs, regardless of whether it matches the request. That is the re-drift hole: the checkable surface (preview==run, rerun fidelity) is being substituted for the lived surface (NL produced the *correct* plan).

**Fix:** Add an explicit expected-canonical-plan comparison to UC-10's pass criteria — the checker diffs the agent-assembled expanded plan against a frozen `expected_plan.json` for a fixed NL prompt (pool size, dispatch mode per entry, entry count, prompt-file binding). Then cover at least one alt-path with a checkable signature: alt-path (a) shell-inference + FR-40 wrap (the assembled plan must show `dispatch_mode: shell` + wrap adapter), which is plan-diffable. Alt-path (b) "asks a clarifying question rather than guessing" is **not** disk-gradable and should be honestly marked as a *judgment* check the agent self-reports in the roll-up, not as a checker pass — call that out rather than letting it ride silently under "describe → preview."

### F4 — UC-8 demo: REVISE. Routed "both" but the runbook conflates the two rungs into one line and never names the per-rung pass, weakening the locked-down half. (rung honesty)

UC-8 (lines 143-154) requires the demo to run **at any rung** — explicitly *"in-agent and via ticker"* — because *"the demo works identically at rung 3 (that's the point)"* (line 153). The runbook (line 34) says "both in-agent and via ticker; grade." TRACEABILITY (line 24) correctly rungs it "both" and marks it PARTIAL. This is *closer* to right than most, but the runbook treats "both" as a single grade. The whole rationale of UC-8's dual-rung claim is that the *same disk record* results at rung 1 and rung 3 (line 154 "every architectural claim demonstrated"); if the runbook grades them as one pass it can't catch a rung-3-only divergence — which is exactly the locked-down-host class of defect the per-OS contract exists to find.

**Fix:** Make UC-8 two recorded passes (rung-1 in-agent, rung-3 ticker) with the **same `expected`** asserted against both run-dirs, and state that tier-invariance (UC-1 postcondition "same disk record as UC-1", echoed at UC-5 line 115) is the actual UC-8 claim being tested.

### F5 — UC-11 autonomy integrity: CONCERN. Correctly in-agent and correctly checker-graded, but the runbook tests only the happy path and omits the violation cases that are the *reason FR-55 exists*. (coverage gap)

UC-11 (lines 185-203) is well-modeled in principle: the runbook (line 37) drives a long stub run, asserts the continuation contract holds, and runs the 3-class detector for `CONTINUE`-state yields. Rung (in-agent) is right — only a live agent can *be* under the turn-completion pressure UC-11 reproduces. But UC-11's whole point is the **violation-detection** alt-paths: (b) silent abandonment, (c) illegitimate yield ("good checkpoint"), (e) false halt claim (`HALT:done` on a non-terminal run). The runbook's pass = "no `CONTINUE`-state yields" only checks that the *honest* agent didn't trip the detector. It never confirms the **detector fires when it should** — i.e., that a deliberately-misbehaving run *is* flagged (UC-11 line 199: *"This is the failure the FR-55 test deliberately reproduces"*).

A detector that never false-positives but also never true-positives passes this runbook. That's the re-drift in its autonomy-integrity form: grading the agent's good behavior instead of grading that the *contract is enforceable*.

**Fix:** Add to UC-11's runbook the deliberate-violation fixtures — a journal with a `CONTINUE`-state yield (b/c) and one with a mismatched cited verdict (e) — and assert the checker **flags** each. This is checker-gradable and is the half of UC-11 that actually protects the promise. (Note: the floor's `continuation_*` scenarios may already do some of this at engine level; if so, the acceptance step should still confirm it *on an agent-driven run*, since UC-11 is in-agent by nature.)

### F6 — UC-4 resume: minor REVISE. Rung is honestly "in-agent + ticker" but the runbook offers them as interchangeable; UC-4 distinguishes them. (fidelity nuance)

UC-4 (lines 90-101) has two resume actors — *"any fresh orchestrator agent OR the ticker"* — and the runbook (line 30) reflects both ("re-bootstrap against the run-dir … or run `ticker.py --once`"). Good. But UC-4 alt-path (line 100) — the *wall-clock-jump guard* after sleep/hibernate (FR-8/E2) — is a distinct, defect-prone behavior (heartbeat ages inflated → suppress STALL for one tick) and is absent from the runbook. The slept-machine case is precisely the "in the wild" failure (two silent drops, line 93) UC-4 was written for.

**Fix:** Add the sleep/hibernate alt-path as an explicit UC-4 acceptance step (inject a wall-clock jump, confirm no false-STALL on the resume tick) — checkable via the journal/status. Keep both actors but require *each* to be recorded at least once (the agent-driven resume is the in-agent rung; the ticker resume is the floor rung — they exercise different code paths and the matrix should show both ran).

### F7 — Mislabeling check on `SDLC.md` / `TRACEABILITY.md`: mostly CLEAN, one inconsistency to fix.

`SDLC.md` is correct and is the strongest statement of the corrected model — lines 37-47 draw the necessary-condition-vs-acceptance line cleanly, line 41 nails *"ticker.py --once, **never the agent loop**"* for the floor, and line 43 routes UC-1/9/10/11 in-agent and UC-5/6/7 to the floor. No mislabel there.

**One inconsistency:** `SDLC.md` line 43 enumerates the in-agent acceptance cases as **"UC-1, UC-9, UC-10, UC-11"** — it omits UC-2 and UC-3. `TRACEABILITY.md` (lines 18-19) and `ACCEPTANCE_TESTS.md` (line 46) both list **UC-1/2/3/9/10/11** as in-agent. UC-2 (monitor) and UC-3 (halt) are genuinely in-agent (the operator reads the agent's table; the operator drops STOP and the *agent's* next tick halts read-only — UC-3 line 86 "The agent/ticker prints the table, states STOP detected"). So TRACEABILITY/ACCEPTANCE are right and **SDLC.md's enumeration is incomplete** — it should read UC-1/2/3/9/10/11. This is a small drift but it's the exact kind of list that gets copied into a gate checklist; fix it so the three docs agree.

Two further consistency notes (not blockers):
- TRACEABILITY UC-12 is rung "in-agent + ticker" (line 28) and ACCEPTANCE UC-12 (line 38) implies in-agent; REQUIREMENTS UC-12 (lines 205-220) is adapter behavior that genuinely runs at any rung, so "in-agent + ticker" is defensible — but the *activity-pattern relevance filtering* is an engine/adapter behavior the floor (`test_activity_patterns`, `sim_*_log_noise`) already grades. The acceptance step (line 38) "confirms ACTIVITY shows the relevant line" risks being the floor scenario relabeled (same re-drift as F1). Recommend the UC-12 acceptance step grade the *operator-visible table over a live run* (does the agent's printed ACTIVITY column show `Step 7/12` not noise, with the age hint once stale — UC-12 line 214), not just re-assert the adapter unit behavior.
- The `SDLC.md`/`TRACEABILITY` "regression test = acceptance + floor together" framing (SDLC 45, TRACE 8) is correct and consistent; no issue.

---

## Routing scorecard (UC → rung, against REQUIREMENTS text)

| UC | Design rung | Correct per REQUIREMENTS? | Note |
|----|-------------|--------------------------|------|
| UC-1 | in-agent | ✅ (UC-1 is "natively," cadence 1 + dispatch 1) | faithful |
| UC-2 | in-agent | ✅ rung; ❌ *content* (F1 — graded as serialization diff) | re-drift |
| UC-3 | in-agent | ✅ | faithful |
| UC-4 | in-agent + ticker | ✅ both actors; alt-path gap (F6) | minor |
| UC-5 | ticker (3) | ✅ — explicitly the no-agent floor (UC-5 line 103) | faithful |
| UC-6 | ticker (2) | ✅ — real scheduler, no agent | faithful |
| UC-7 | ticker (4) | ✅ — manual, no agent/no ticker loop | faithful |
| UC-8 | both | ✅ rung; runbook conflates the two passes (F4) | minor |
| UC-9 | in-agent | ✅ rung; ❌ rehydrate not exercised (F2) | re-drift |
| UC-10 | in-agent | ✅ rung; ❌ NL→plan not graded (F3) | re-drift |
| UC-11 | in-agent | ✅ rung; violation cases missing (F5) | gap |
| UC-12 | in-agent + ticker | ✅ rung; risk of floor-relabel (F7 note) | watch |

**No UC is mis-rung.** The no-agent floor cases (UC-5/6/7) are correctly *kept off* the in-agent rung — the primary drift the charter warned about did not recur at the routing level. The remaining failures are all the *second* form of drift: an in-agent test whose grading target quietly reduces to the engine slice (UC-2, UC-9, UC-10, and the UC-12 note).

## Concrete fix list (blocking for SHIP)

1. **UC-2:** move table==`harness_status.json` to the floor; make the acceptance test the on-demand-"tick now" idempotency branch (UC-2 lines 73-74).
2. **UC-9:** define "simulate a drop" as a *fresh-context* rehydrate; require the **in-context queue** to resume (not just the background run-dir); add the long-task "busy not asleep" alt-path.
3. **UC-10:** add an expected-canonical-plan diff for a fixed NL prompt; cover the shell-inference/wrap alt-path; mark the clarifying-question path as agent-self-reported, not checker-graded.
4. **UC-8:** two recorded passes (rung 1 + rung 3) asserted against the *same* `expected`; name tier-invariance as the claim.
5. **UC-11:** add deliberate-violation fixtures (silent abandonment, illegitimate yield, false halt claim) and assert the detector **fires**.
6. **UC-4:** add the sleep/hibernate wall-clock-jump alt-path; require both the agent resume and the ticker resume to be recorded.
7. **SDLC.md line 43:** correct the in-agent enumeration to **UC-1/2/3/9/10/11** (currently omits UC-2/UC-3).
8. **UC-12 (recommended):** grade the live operator-visible ACTIVITY column, not the adapter unit behavior the floor already covers.

Once F1–F5 (the re-drift trio plus the two coverage gaps) and F7's SDLC enumeration are addressed, this is SHIP-able — the framing is already correct and the floor cases are already faithful.
