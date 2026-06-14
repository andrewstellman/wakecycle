# Arunner — Acceptance Tests (the runbook)

*The acceptance tests mirror the use cases by having the **agent drive arunner the way it is used**, then grading the result objectively. They are run from Claude Code (and, for the no-agent cases, the ticker), back to back, on each target platform and agent. The deterministic pytest/unittest suite is the **necessary-condition floor** underneath (see `SDLC.md`); a real regression test is the acceptance tests **and** that floor, together. This doc is the runbook an agent follows after "Read AGENTS.md, then run the acceptance tests." Status: **design (council-revised 2026-06-14) — building.***

## Two run paths

- **In-agent (rung 1)** — the agent bootstraps and drives the tick loop itself, dispatching **subagent** workers. Used for the agent-driven cases (UC-1, UC-2, UC-3, UC-4 agent leg, UC-9, UC-10, UC-11). Requires **subagent-dispatch stub plans** — *not* the shell scenarios, because a rung-1 agent handed a shell plan correctly refuses and hands off to the ticker (`SKILL.md`). The stub subagent is a trivial "emit STARTING + COMPLETED heartbeat lines and exit" prompt.
- **Ticker / terminal (rungs 2–4)** — a real `ticker.py` invocation drives a **shell-dispatch** plan (the existing stub-worker scenarios). Used for the no-agent floor cases (UC-5, UC-6, UC-7) and the ticker leg of UC-4/UC-8.

**Cost, honestly:** worker dispatch is stubs, so **zero worker API spend**. The in-agent cases still spend the agent's own tick tokens plus a trivial subagent call per job — **cheap, not free**. The ticker cases are genuinely free.

## Grading (objective, from what a real run leaves on disk)

A live run does not produce the test runner's private `_check_meta.json`, so grading reads the **durable artifacts a real run actually writes**: `harness_status.json` (incl. `continuation`), `journal.ndjson` (per-tick verdicts + yields), `results/`, and the heartbeat files. The checker is extended to grade from those and exposed as a CLI:

```
python tests/integration/checker.py <run-dir> <expected.json>   # exit 0 = pass; prints failures otherwise
```

For **control-timing** tests (STOP/CANCEL read-only), the pre-action state can't be reconstructed after the fact, so the runbook has the agent **snapshot the run-dir before the control action and compare after** (trivially: copy `harness_status.json`, drop the control file, tick, diff). That makes the read-only invariant gradeable in a live run without the runner's meta.

## The runbook (per use case)

For each: prepare the plan, drive it at the listed rung performing any control actions, grade. The full UC↔rung↔floor↔run-context matrix is `docs/TRACEABILITY.md`. Agent-facing steps:

- **UC-1 (multi-job native):** rung-1 subagent plan; tick to `done`; grade (all `completed`, `done`).
- **UC-2 (monitor):** rung-1; issue an on-demand "tick now" mid-run and confirm it is **idempotent** (only the cycle counter moves, no double-dispatch) — the lived monitoring affordance, not table-vs-status serialization (that's the floor's `test_cli`).
- **UC-3 (halt):** rung-1; **snapshot**, drop `STOP`, tick, **compare** — assert the STOP tick changed nothing (read-only) and `stopped`.
- **UC-4 (resume):** rung-1; abandon mid-run; **(a)** re-bootstrap a *fresh* session against the run-dir and **(b)** separately resume via `ticker.py --once`; both must continue with no double-dispatch. Include the **sleep/hibernate** leg (inflated heartbeat ages → wall-clock-jump guard, not a false STALL).
- **UC-5 (locked-down floor):** launch `ticker.py` in a terminal, no admin, shell-dispatch plan → `done`. **Per-OS: Windows + macOS.**
- **UC-6 (scheduled):** install the printed schedule entry firing `--once`; one tick per fire → `done`. **Recorded, real scheduler.**
- **UC-7 (manual tick):** run `--once` by hand repeatedly → `done`.
- **UC-8 (demo):** from a fresh install, drive the bundled demo to `done` **twice — rung 1 and rung 3 — against the same `expected`** (catches a rung-specific divergence).
- **UC-9 (in-context):** bootstrap on an instruction folder; do in-context tasks + tick the background harness; **kill the session and rehydrate in a *fresh* context** — assert the **in-context queue resumes** (FR-48), not just the background run-dir; include the "busy, not asleep" long-task leg.
- **UC-10 (conversational build):** for a **fixed** NL prompt, describe → preview → run → persist; grade the assembled plan against an **expected canonical plan** (pool/dispatch/entries), and confirm the saved bundle re-runs faithfully. The clarifying-question path is agent-self-reported, not checker-graded.
- **UC-11 (autonomy integrity):** drive a long stub run; confirm the contract holds (no `CONTINUE`-state yields); **and** run deliberate-violation fixtures (silent abandonment / illegitimate yield / false-halt-claim) and assert the detector **fires** on each — the reason FR-55 exists.
- **UC-12 (activity patterns):** run a wrap/tail job with `adapter_activity_patterns` over noisy output; confirm the ACTIVITY label shows the relevant line, not the noise.

## Disk-gradeable vs. agent-reported

Most legs are disk-objective (the checker grades the run-dir). A few legs live in agent behavior, not the run-dir, and are **agent-self-reported with evidence** rather than checker-graded: UC-2's table reading, UC-10's NL comprehension, UC-9's "did the fresh context actually rehydrate from disk." The runbook marks which is which so the pass criterion is honest.

## Running them

`AGENTS.md` gets a "run the acceptance tests" section: read this runbook, run each test back to back, grade each with the checker CLI, and report a pass/fail roll-up by use case (with the checker's failure lines and the run-context: OS + agent).

**Run-contexts (a test isn't complete until run in each):**
- **Per-OS (Windows, macOS, Linux — co-equal, NFR-1):** the platform-sensitive cases — UC-4, UC-5, UC-6, UC-7, UC-8, UC-12 — where file-locking / process-spawn defects the floor can't see surface.
- **Per-agent:** the in-agent cases (UC-1/2/3/9/10/11) on **each agent claimed as an orchestrator host** — Claude Code today; Cursor and Copilot stay **DESIGNED** until an acceptance run on them passes and is recorded.
- A pass from Claude Code on macOS does **not** clear the §9 Windows floor row; that row flips only on a recorded Windows run (NFR-12).

## Status / next (the build)

1. **Checker extension + CLI** — grade from the durable run artifacts (journal / continuation / results / heartbeats); standalone `checker.py <run-dir> <expected.json>`.
2. **Subagent-dispatch stub plans** for the in-agent cases + the trivial stub-subagent prompt.
3. **Demonstrate one in-agent acceptance test end-to-end** (agent drives rung-1 on a stub plan, grades via the CLI) to prove the flow.
4. **`AGENTS.md` bootstrap** — the "run the acceptance tests" section.
5. Council the build; then first real runs from Claude Code on macOS, then Windows — **expect genuine failures the first few times** (that is the suite working).
