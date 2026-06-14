# Panelist C — Honesty, Run-Contexts & the Regression Framing

**Charter:** HONESTY, RUN-CONTEXTS & THE REGRESSION FRAMING. Adversarial.
**Documents reviewed:** `docs/ACCEPTANCE_TESTS.md`, `SDLC.md` (necessary-condition + release-gate sections), `docs/TRACEABILITY.md`, `docs/REQUIREMENTS.md` (NFR-12, §9).
**Date:** 2026-06-14

## Verdict: REVISE-REQUIRED

The corrected model — pytest suite = necessary condition not a regression test; a regression test = acceptance tests AND pytest together; regressions surface only when acceptance tests run — is **applied correctly and consistently** on the framing axis (Q1). That part is genuinely good: I went looking for a single surviving "regression net = pytest" or "pytest = acceptance test" slip and the framing holds across all three docs. The §9/NFR-12 dogfooding-vs-recorded-run distinction (Q3) is also drawn correctly.

But the **run-context enumeration is incomplete and mutually inconsistent across the three docs** (Q2), and that inconsistency is itself an honesty defect: a reader cannot tell which contexts a given UC must run in to be "complete," and two of the docs silently drop use cases from their run-context lists. That is a "coverage implied that the matrix doesn't actually pin" failure — exactly the overclaim NFR-12 and the traceability gate exist to prevent. It is mechanical to fix, but it must be fixed before this is the record.

---

## Q1 — Necessary-condition vs. acceptance / regression framing: PASS (consistent)

The framing is correct and I could not find a contradicting instance.

- `SDLC.md:37-47` states it cleanly and names the trap by name ("Conflating them — calling the first 'the acceptance tests' or 'the regression net' — is the trap"), defines the necessary-condition suite as "a **necessary condition, not a regression test**," puts "where regressions actually surface" on the acceptance tests, and defines a regression test as "the acceptance tests *and* the necessary-condition suite, run together." Correct.
- `ACCEPTANCE_TESTS.md:3` mirrors it: "a real regression test is the acceptance tests **and** that suite, together." Correct.
- `TRACEABILITY.md:3` and `:8`: "regressions only surface when the acceptance tests run"; "Green floor ≠ passing acceptance test." Correct.
- `SDLC.md:84-89` release gate: gated on **both**, "Together (1) + (2) are the regression test." Correct.

**One residual term to scrub (minor, but it's the exact word the framing bans elsewhere):** `REQUIREMENTS.md:283` and `:316` still call the integration suite "the regression net" ("the **regression net** is built and pinned to current behavior before any FR-35..50 feature lands"; "every later increment runs against a standing **regression net**"). FR-51 itself at `:316` says the integration suite "**doubles as** ... the dogfood" and is "a standing regression net." Under the corrected model, the integration/pytest suite is the *necessary-condition floor*, and "regression net" is precisely the phrase `SDLC.md:39` calls out as the trap. This is in REQUIREMENTS.md, not in the three design docs under review, so it's out of the strict charter scope — but it is the canonical source of truth (`SDLC.md:20`, `:33`) and the design docs all point back to it. If REQUIREMENTS keeps calling the floor "the regression net," a future reader reconciling the design docs against the contract will hit a direct terminological contradiction.
  - **Fix:** in a REQUIREMENTS edit (separate from this design work, since REQUIREMENTS is the contract), retag FR-51's "regression net" language to "necessary-condition floor" / "necessary-condition integration suite," consistent with `SDLC.md:41`. At minimum, flag the discrepancy in the design docs so it isn't read as endorsement. **Do not let this design land while implying the FR-51 suite alone is "a standing regression net"** — that is the precise claim the corrected model retracts.

## Q2 — Run-context honesty: REVISE-REQUIRED (incomplete + inconsistent across docs)

This is the load-bearing problem. The three docs do not agree on which UCs carry which run-contexts, and two of them silently drop UCs from their run-context enumerations. A run-context list is a completeness claim ("this test isn't done until run here"); an incomplete or inconsistent one *understates* what's required, which reads as coverage the matrix hasn't actually pinned.

**Finding 2.1 — UC-12 is absent from both run-context lists in ACCEPTANCE_TESTS.md but is per-OS in TRACEABILITY.**
`ACCEPTANCE_TESTS.md:45` lists per-OS as "UC-5/6/7/8" and `:46` lists per-agent as "UC-1/2/3/9/10/11." **UC-12 appears in neither.** But `TRACEABILITY.md:28` marks UC-12 "**per-OS**" (and it's `in-agent + ticker`, so per-agent is arguably implicated too). UC-12 (activity patterns over noisy output, FR-56) touches the wrap/tail adapters and per-line scanning — exactly the file-I/O / process-spawn surface `:45` says is *why* per-OS matters (FR-40 explicitly flags Windows chunky-buffering and the absent `stdbuf`). So UC-12 should be per-OS, and ACCEPTANCE_TESTS.md silently omits it.
  - **Fix:** add UC-12 to the per-OS line in `ACCEPTANCE_TESTS.md:45` (`UC-5/6/7/8/12`), matching `TRACEABILITY.md:28`.

**Finding 2.2 — UC-4 is absent from both run-context lists in ACCEPTANCE_TESTS.md but is "per-OS, per-agent" in TRACEABILITY.**
`TRACEABILITY.md:20` marks UC-4 (resume after crash / silent loop-drop) "**per-OS, per-agent**" — correctly, since resume runs `in-agent + ticker` and crash-recovery is where file-locking / PID-reattach defects live. But `ACCEPTANCE_TESTS.md:44-46` omits UC-4 from *both* the per-OS and per-agent lists. So the most platform-sensitive case in the whole set (the one born from the two observed in-the-wild silent loop-drops, `REQUIREMENTS.md:21`, `:93`) has no run-context named in the runbook doc.
  - **Fix:** add UC-4 to both the per-OS line (`:45`) and the per-agent line (`:46`) of ACCEPTANCE_TESTS.md, matching `TRACEABILITY.md:20`.

**Finding 2.3 — SDLC.md's enumeration drops UC-2, UC-3, UC-4, UC-8, UC-12 entirely.**
`SDLC.md:43` enumerates "in-agent (rung 1) for the agent-driven cases (UC-1, UC-9, UC-10, UC-11), and the ticker/terminal floor for the no-agent cases (UC-5, UC-6, UC-7)." This omits UC-2, UC-3, UC-4 (in-agent), UC-8 (both), and UC-12 (both). UC-2/UC-3 are in-agent in `TRACEABILITY.md:18-19` and in `ACCEPTANCE_TESTS.md:46`'s per-agent list. The SDLC enumeration reads as exhaustive ("the agent-driven cases (…)") but isn't, which under-describes the acceptance layer in the methodology-of-record document.
  - **Fix:** either make the SDLC list explicitly illustrative ("agent-driven cases such as UC-1, UC-9, UC-10, UC-11") and point at TRACEABILITY for the full matrix, or complete it. Given the doc's own discipline ("Coverage is claimed only after a council review concludes every US/UC is mirrored," `SDLC.md:51`), the illustrative framing is honest and the parenthetical-as-exhaustive framing is not.

**Finding 2.4 — Cursor/Copilot DESIGNED-until-recorded: CORRECT, but one phrasing in SDLC.md risks reading as a coverage claim.**
The DESIGNED-until-recorded discipline is handled correctly in the two places it matters most:
- `ACCEPTANCE_TESTS.md:46`: "Claude Code today; Cursor and Copilot stay DESIGNED until an acceptance run on them passes and is recorded." Honest.
- `TRACEABILITY.md:43`: "Claude Code is the verified host today; Cursor and Copilot stay **DESIGNED** until an acceptance run on them passes and is recorded." Honest.

But `SDLC.md:51` writes the per-agent set flatly: "a per-agent run (Claude Code, Cursor, Copilot) for the in-agent orchestrator cases." Read in isolation, "(Claude Code, Cursor, Copilot)" lists three agents as if all three are live run-contexts. It is *rescued* by the immediately following clause — "(UC-10's 'any host' is verified only where an agent has actually driven it)" — and by `SDLC.md:87` ("on each agent claimed as an orchestrator host"). So it's not a false claim, but it is the weakest phrasing in the set: a reader skimming `:51` could come away thinking Cursor/Copilot are already required-and-runnable contexts rather than designed targets.
  - **Fix (recommended, not blocking):** add "— Claude Code verified today, Cursor/Copilot DESIGNED until a recorded run" to `SDLC.md:51`, matching the phrasing the other two docs already use. Consistency across the three docs is itself the honesty property here.

**Finding 2.5 — Linux is named inconsistently.**
`ACCEPTANCE_TESTS.md:45` says per-OS is "**Windows and macOS** (Linux too)"; `TRACEABILITY.md:42` says "**Windows and macOS** (Linux too)"; `SDLC.md:87` says "minimally Windows and macOS"; `REQUIREMENTS.md` NFR-1 (`:360`) mandates "Windows, macOS, Linux — equal support." The parenthetical "(Linux too)" treats Linux as an afterthought while NFR-1 makes it co-equal. This is a small honesty wrinkle: the floor's CI is already cross-platform (`SDLC.md:86`, `TRACEABILITY.md:34` "cross-platform CI"), so Linux is the *cheap* one — relegating it to a parenthetical understates that it's covered. Not load-bearing, but worth a consistent treatment (either "Windows, macOS, and Linux" everywhere, or a one-line note that Linux rides the floor CI and the per-OS *acceptance* runs are the Windows/macOS additions).

## Q3 — §9 / NFR-12 alignment: PASS (correct)

The acceptance-runs-are-recorded-runs / dogfooding-never-validates distinction is drawn correctly and consistently.

- `SDLC.md:53-62` (§9 ledger): "A row flips to VERIFIED only on a real, linked artifact (a named test + scenario, or a dated run-dir)"; "**No row flips on dogfooding or always-on running** — those measure survival, they do not validate a floor." Correct, matches `REQUIREMENTS.md:400` (the PENDING floor row explicitly: "dogfooding / always-on runs do not satisfy this row").
- `SDLC.md:47` and `:87`: acceptance tests "recorded, they are what lets a §9 host/floor row flip from DESIGNED to VERIFIED." This correctly classifies an acceptance-test run as a *recorded run* (legitimate evidence per `REQUIREMENTS.md:371` NFR-12, "No claim ships without a validation-matrix run behind it"), and `SDLC.md:47` keeps dogfooding separate ("(Dogfooding ... is separate from both: it *measures* ... and never *validates* a §9 row, NFR-12.)"). The distinction is exactly right.
- The mechanical guard is named (`SDLC.md:60`, `test_positioning_honesty.py`: "the floor row must stay PENDING ... no VERIFIED row may cite dogfooding/always-on"), which is the load-bearing enforcement, not just prose.

**One consistency check worth the orchestrator's attention (not a defect in the design docs):** `REQUIREMENTS.md:400` says "macOS cadence-2 cron (V-9) and rung-3 ticker are VERIFIED in the README support table, but the **Windows / full-matrix** floor claim stays PENDING." So macOS rung-3/cron is *already* VERIFIED via recorded operator runs. The acceptance suite (run from Claude Code on macOS, `ACCEPTANCE_TESTS.md:52`) will re-exercise UC-5/6/7 on macOS. The design docs don't claim that re-run flips anything Windows-side, which is correct — but the orchestrator should make sure the first macOS acceptance run isn't *reported* as progress on the PENDING floor row, because the PENDING row is specifically the **Windows / full-matrix** gap (`REQUIREMENTS.md:400`). The design honesty here is fine; the risk is in the eventual run report, not the doc. Worth a sentence in ACCEPTANCE_TESTS.md "Status / next" making explicit that the macOS-from-Claude-Code run does **not** clear the Windows floor row.

## Q4 — Overclaim check: PASS, with the run-context completeness caveat from Q2

- **"Expect genuine failures the first few times" honesty is present and well-placed.** `ACCEPTANCE_TESTS.md:52`: "First real run: from Claude Code on macOS, then Windows — expect genuine failures the first few times (that is the suite working)." This is exactly the honest framing: it pre-commits to the acceptance suite finding real regressions rather than rubber-stamping. Good.
- **The design does not claim a macOS-from-Claude-Code run says anything about Windows or about Cursor.** `ACCEPTANCE_TESTS.md:44` ("a test isn't complete until run in each"), `:46` (Cursor/Copilot DESIGNED), `TRACEABILITY.md:43`, and `SDLC.md:87` ("on each target platform ... and on each agent claimed as an orchestrator host") all keep per-context completeness explicit. No "ran it once on my Mac, therefore covered" overclaim. Good.
- **The grading-is-objective claim is honestly bounded.** `ACCEPTANCE_TESTS.md:9` ("Grading is identical and objective ... No 'did it look right' judgment") rests on the *same* independent stdlib checker the floor uses (`SDLC.md:41`, `:68`). That's a real independence claim, not a self-grading dressed up as objective. Good.
- **The residual overclaim risk is the Q2 incompleteness.** If UC-4 and UC-12 carry no named run-context in ACCEPTANCE_TESTS.md while TRACEABILITY says they're per-OS / per-OS+per-agent, then a reader following ACCEPTANCE_TESTS.md as "the runbook" (its self-description, `:3`) will under-run those two cases and the roll-up will *claim* completeness it hasn't earned. That's the overclaim NFR-12 exists to catch, arriving through a clerical gap rather than a marketing one. Q2's fixes close it.

---

## Required before SHIP

1. **(Finding 2.1)** Add UC-12 to the per-OS line of `ACCEPTANCE_TESTS.md:45`.
2. **(Finding 2.2)** Add UC-4 to both run-context lines of `ACCEPTANCE_TESTS.md:45-46`.
3. **(Finding 2.3)** Make `SDLC.md:43`'s case enumeration explicitly illustrative (or complete it) so the parenthetical isn't read as exhaustive.
4. **(Q1 residual)** Flag — and, in a separate REQUIREMENTS edit, retag — FR-51's "regression net" language (`REQUIREMENTS.md:283`, `:316`) to "necessary-condition floor," so the contract doesn't contradict the corrected model the design docs adopt.

## Recommended (non-blocking)

5. **(Finding 2.4)** Add the DESIGNED-until-recorded qualifier to `SDLC.md:51`'s "(Claude Code, Cursor, Copilot)" so all three docs phrase it identically.
6. **(Finding 2.5)** Treat Linux consistently across the three docs rather than as a parenthetical afterthought against NFR-1's co-equal mandate.
7. **(Q3 caveat)** Add a sentence to ACCEPTANCE_TESTS.md "Status / next" that the macOS-from-Claude-Code run does not clear the Windows floor row (which is the actual PENDING gap per `REQUIREMENTS.md:400`).

The framing — the thing this corrected model was built to fix — is right. The run-context matrix is where the honesty surface currently leaks, through omission rather than misstatement, and it's a clerical fix. Close items 1–4 and this is a SHIP.
