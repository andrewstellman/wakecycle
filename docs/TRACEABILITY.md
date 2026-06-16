# Arunner — Requirements → Acceptance Test Traceability

*Every user story and use case is mirrored by an **acceptance test**: the agent — from Claude Code, acting as the operator's seat — driving arunner through that use case **at its proper rung** and checking the outcome. The deterministic pytest/unittest suite is the **necessary-condition floor** underneath, not the acceptance test (see `SDLC.md`): a green floor is necessary but never sufficient, and regressions only surface when the acceptance tests run. This matrix is the plan and the record. The floor exists and is green; the agent-run acceptance layer is the current build.*

## How to read this

- **Acceptance test** = the agent runs arunner through the use case the way it's actually used, and checks the observable result (run-dir / journal / status table). This is where regressions surface.
- **Necessary-condition floor** = the deterministic pytest/unittest tests that exercise the engine slice underneath (ticker-driven, stub worker, independent stdlib checker). Green floor ≠ passing acceptance test.
- **Rung** = where the use case runs: **in-agent** (rung 1, the agent drives the ticks) or **ticker/terminal** (rungs 2–4, no agent).
- **Run-contexts** = where an acceptance test must pass to be complete: **per-OS** (Windows, macOS, Linux) for platform-sensitive cases, and **per-agent** (Claude Code, Cursor, Copilot) for the in-agent orchestrator cases.
- **Status** = most acceptance tests are **TO BUILD**: the floor exists; the agent-run layer + the runbook + the `AGENTS.md` "run the acceptance tests" bootstrap are the current work.

## Use case → acceptance test

| UC | Use case | Acceptance test — the agent… | Rung | Necessary-condition floor (exists) | Run-contexts | Status |
|----|----------|------------------------------|------|-----------------------------------|--------------|--------|
| UC-1 | Run a multi-job plan natively | bootstraps a fresh session, drives a stub-worker plan to `done`, checks the run-dir record | in-agent | `autonomous_loop`, `pool_staggered` | per-OS, per-agent | TO BUILD |
| UC-1 (alt: grow a running batch) | `arunner add`s jobs to a live run; the next tick absorbs them (append-only `run-NN`) and drives the grown batch to `done` (FR-57) | in-agent + ticker | `test_live_enqueue` (stage-and-absorb, append-only) | per-agent | **VERIFIED** (deterministic leg) |
| UC-2 | Monitor a run in progress | runs a plan, reads the status table each tick, confirms it reflects disk state | in-agent | `test_cli` (status read-only), journey | per-agent | TO BUILD |
| UC-2 (alt: ACTIVITY moves) | runs an adapter job whose ACTIVITY column refreshes on the `--keepalive-seconds` cadence and tracks the latest relevant line as the job progresses (FR-58a) | in-agent / ticker | `test_activity_cadence` (default-grace path, label-moves) | per-OS | **VERIFIED** (FR-58a engine leg; FR-58b visible-table is per-host DESIGNED) |
| UC-2 (alt: read-only monitor sidecar) | opens `arunner monitor <run-dir>` in a second terminal; confirms it renders the live table from disk and refreshes ACTIVITY/HB-AGE while the orchestrator is blocked — and writes nothing (no `.tick.lock`, no control file, no state mutation) (FR-59) | sidecar (rung-independent) | `test_monitor` (never-writes pin; live-heartbeat vs per-tick-state freshness; shared-renderer-no-fork) | per-OS | TO BUILD |
| UC-3 | Halt a run early (STOP) | runs a plan, drops `STOP`, confirms a clean, read-only halt | in-agent | `stop_readonly`, `test_control_files` | per-agent | TO BUILD |
| UC-4 | Resume after a crash / loop-drop | runs a plan, simulates a drop, re-bootstraps against the run-dir, confirms resume with no double-dispatch | in-agent + ticker | `continuation_crash_then_resume`, `resume_continues` | per-OS, per-agent | TO BUILD |
| UC-5 | Locked-down host floor | launches the **ticker** in a terminal (no admin), drives a shell-dispatch plan to `done` | ticker (3) | `wrap_adapter_completes`, `tail_adapter_completes` | per-OS (esp. **Windows**) | TO BUILD |
| UC-6 | Scheduled run via cron | a real scheduler fires `--once`; one tick per fire, run reaches `done` | ticker (2) | ticker `--once` scenarios | per-OS (real scheduler — recorded) | TO BUILD |
| UC-7 | Manual-tick floor | runs `--once` by hand repeatedly until `done` | ticker (4) | every ticker scenario | per-OS | TO BUILD |
| UC-8 | Install + run the demo | from a fresh install, drives the bundled demo to `done` (in-agent and via ticker) | both | `test_packaging` (13a smoke) | per-OS | PARTIAL (13a install-smoke exists; in-agent demo TO BUILD) |
| UC-9 | In-context worker | bootstraps on an instruction folder, does in-context work + ticks the background harness, rehydrates on a drop | in-agent | `test_incontext` | per-agent | TO BUILD |
| UC-10 | Conversational build | describe → preview → run → persist a session in natural language | in-agent | `test_preview`, `test_cli_journey` | per-agent (Claude Code verified; others DESIGNED) | TO BUILD |
| UC-11 | Unattended run resists stop-pressure | drives a long stub run; the continuation contract holds; the journal is audited for `CONTINUE`-state yields | in-agent | `continuation_*` (7) + 3-class detector | per-agent | TO BUILD (live audit) |
| UC-12 | Activity patterns from a noisy tool | runs a wrap/tail job with `adapter_activity_patterns` over noisy output; confirms ACTIVITY shows the relevant line | in-agent + ticker | `sim_wrap_log_noise`, `sim_tail_log_noise`, `test_activity_patterns` | per-OS | TO BUILD |

User stories cluster onto the same use cases: US-1→UC-1, US-2→UC-2, US-3→UC-3, US-4→UC-4, US-5→UC-5, US-6→UC-5/adapters, US-7→UC-1 on a small model (recorded), US-8→UC-8, US-9→every run's disk record, US-10→the §9/`test_positioning_honesty` honesty surface, US-11→UC-11, US-12→UC-12, US-13→UC-1, US-14→UC-2, US-15→UC-2 (read-only monitor sidecar).

## What "TO BUILD" means here

The necessary-condition floor (the pytest/unittest tests named above) exists and is green (257 tests, cross-platform CI). The **acceptance layer** is the current work, and consists of three things:

1. **Canned scenarios** — a stub-worker plan per use case (cross-platform, no API spend), so the agent can run the whole set back to back.
2. **A runbook** the agent follows for each: the rung, the exact steps it performs, and the expected observable outcome (run-dir / journal / status) that constitutes a pass.
3. **The `AGENTS.md` bootstrap** — the "run the acceptance tests" section that turns "Read AGENTS.md, then run the acceptance tests" into a definite procedure.

## Required run-contexts (why "back to back, per platform and per agent")

- **Per-OS:** the platform-sensitive cases (UC-5/6/7/8, and anything touching file-locking or process spawn) must pass on **Windows and macOS** (Linux too). This is where the Windows-specific defects the floor can't see — file locking against the claim-lock model, `python` vs `py` — surface.
- **Per-agent:** the in-agent orchestrator cases (UC-1/2/3/9/10/11) must pass on **each agent claimed as an orchestrator host**. Claude Code is the verified host today; Cursor and Copilot stay **DESIGNED** until an acceptance run on them passes and is recorded.

## The traceability gate

Coverage is claimed only after a **council review concludes every US/UC is mirrored by an acceptance test**, with each test's required run-contexts named and the necessary-condition floor green. This table is the input and the record; the review is the gate.
