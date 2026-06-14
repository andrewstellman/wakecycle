# Acceptance-test design — council synthesis

*2026-06-14. 3-panel review of `docs/ACCEPTANCE_TESTS.md` (+ the corrected `SDLC.md`/`TRACEABILITY.md`) against `REQUIREMENTS.md` §4. All three: REVISE-REQUIRED. The corrected **framing** (pytest = necessary-condition floor, acceptance tests mirror the use cases agent-run across rungs, regression = both together) is confirmed right and consistent — no re-drift. The problems are in the design's mechanics and completeness.*

## Two blockers (Panelist B) — the "just expose a CLI" claim is false

1. **The checker depends on `_check_meta.json`, which only the test *runner* writes.** STOP, pool bounds, cadence, CANCEL, and the **entire** FR-55 continuation contract grade from `meta` (`tick_trace`, `pre_stop_status`, `results_snapshot`, `host_stopped_after_tick`, …) produced exclusively at `runner.py:288-294`. An agent-driven run has the `journal.ndjson` verdicts + `harness_status.json.continuation` + `results/` but **not** the runner's meta — and the irreducible *pre*-STOP / *pre*-CANCEL snapshots can't be reconstructed post-hoc. So `checker.py <run-dir> <expected.json>` is **not sufficient** for the control/continuation scenarios. Fix: extend the checker to rebuild what it can from disk, and have the agent capture the pre-action snapshot for control scenarios — a real piece of work, not a one-line CLI.

2. **Reuse breaks at rung 1: the scenarios are `dispatch_mode:"shell"`, but rung-1 dispatch is subagents** — and `SKILL.md` *requires* a rung-1 agent handed a shell plan to refuse and hand off to the ticker. So "bootstrap rung-1 on the [existing] plan" can't run for the in-agent cases. The in-agent acceptance tests need **subagent-dispatch stub plans** (new), not the shell scenarios. (Related: a subagent is a real model call, so in-agent runs are **cheap, not free** — Finding 4.)

## Fidelity re-drift (Panelist A) — several tests grade the engine slice, not the lived case

- **UC-2:** graded as table==status serialization (floor's job). The lived monitoring is a transcript property; make the acceptance test the idempotent *tick-now* branch.
- **UC-8:** one pass can't catch a rung-3-only divergence — require **two** recorded passes (rung 1 + rung 3) vs the same `expected`.
- **UC-9:** "simulate a drop" can pass with a warm context — require a **fresh-context** rehydrate and assert the **in-context queue** resumes (FR-48), not just the background run-dir.
- **UC-10:** nothing grades that the agent built the plan the operator *asked for* — add an expected-canonical-plan diff for a fixed NL prompt.
- **UC-11:** only the happy path — add deliberate-violation fixtures and assert the detector **fires** (the reason FR-55 exists).
- **UC-4:** add the sleep/hibernate wall-clock-jump leg (the actual in-the-wild failure); require both the agent resume and the ticker resume.

## Honesty / run-contexts (Panelist C) — framing right, matrix leaks by omission

- `ACCEPTANCE_TESTS.md` run-context lists **omit UC-4 and UC-12** (TRACEABILITY marks them per-OS / per-OS+agent). Add them.
- `SDLC.md:43` in-agent enumeration drops UC-2/3 (Panelist A: also UC-4/8/12 if read as exhaustive) — make it complete or illustrative.
- **`REQUIREMENTS.md` still calls the FR-51 integration suite "the regression net"** (the exact phrase the model retracts) — retag to "necessary-condition floor."
- Non-blocking: Cursor/Copilot DESIGNED-qualifier on `SDLC.md:51`; Linux co-equal per NFR-1; add a note that a macOS-from-Claude-Code run does **not** clear the Windows §9 floor row.

## Disposition

The framing holds; the design needs real revision. The two blockers raise genuine design questions that are the operator's calls (the cost/shape of in-agent runs; how to grade control-timing scenarios in a live run). Incorporate the unambiguous doc fixes; surface the blockers to the operator before building the checker + the subagent plans.
