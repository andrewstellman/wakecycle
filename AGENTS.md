# Arunner — Agent Guide

*For an AI coding agent working **on** this repository. Read this, then the
file you need from the table, then `docs/REQUIREMENTS.md` for the spec behind
any change.*

## Rehydrate on context loss (ERR — read this first if you were summarized)

If your context was summarized, truncated, compacted, or continued from a prior
session — i.e. you are reasoning from a summary rather than the original material
(you will usually see a system note that the conversation was continued) — then
**before taking any action**:

1. **Recognize** that your working context is now a lossy re-rendering. Summaries
   drift toward restating *decisions* and *your own prior output*, and away from
   the user's literal intent.
2. **Rehydrate** from the **source of truth** — `docs/REQUIREMENTS.md` (the user
   stories, use cases, and FR/NFR that define what arunner must do), plus any
   file this guide's table marks canonical. Read those as ground truth; treat the
   summary, your earlier notes, and any derived docs (plans, this guide's prose,
   prior instructions) as suspect.
3. **Re-ground** the task you are about to act on against the *literal text* of
   the requirement — quote the FR/UC and check your action matches the words, not
   your memory of them.
4. If the source of truth and a derived artifact (or your own prior plan)
   conflict, **stop and surface the conflict** rather than proceeding on the
   drifted version.

Do this on every context-loss event, not only when something feels wrong — drift
is silent. (Externalize–Recognize–Rehydrate: the spec lives on disk; recognize
the context-loss event; rehydrate from durable truth instead of the lossy
summary. This is the same FR-48 / UC-9 discipline arunner asks of its own
in-context agent, turned on the process that builds it.)

> Portable form: to reuse this in another project's `AGENTS.md`, copy this
> section and change only the step-2 pointer to that project's source-of-truth
> spec.

## What this repo is

A batch orchestrator for AI coding agents with an inverted architecture: all
the determinism lives in one small stdlib Python state-machine script (the
**tick engine**); an agent session — or, degraded, a plain terminal ticker —
only relays and sleeps. A run is a directory; disk is the database;
crash-recovery is one tick. The worker contract is *a job is anything that
appends JSON lines to a file.* No runtime dependencies beyond user-level
Python 3.10+.

## Key files

| File (current path) | Role | When to read |
|---|---|---|
| `arunner/engine/tick.py` | **The tick engine.** The whole state machine: `--init` scaffolds a run-dir; `<run-dir>` runs one idempotent tick and prints `{dispatch_list, status_table, next_tick_minutes, done, stop}`. | Before ANY change to run state, dispatch, stall/launch logic, the status table, or placeholders. This is the heart. |
| `arunner/engine/ticker.py` | **The ticker** — foreground/`--once` driver for cadence rungs 3–4 (the no-admin floor). Spawns shell workers detached, records PIDs, prints the table, sleeps/loops or exits. | When touching cadence below rung 1, detached spawning, or the printed-command floor (FR-25). |
| `arunner/engine/heartbeat.py` | **The heartbeat helper** — the optional convenience SDK workers use to append v2 (`label`/`data`) heartbeat lines (`emit`/`keepalive`/`terminal`). Payload-agnostic, stdlib, the part that defines the worker contract. | When touching the heartbeat line format, the helper CLI, or E6 (loud write-failure). |
| `arunner/engine/demo_worker.py` | **The demo stub worker** — cross-platform, zero-API; walks the heartbeat lifecycle so the example plan runs identically everywhere (UC-8). | When touching the demo, or as the reference for "what a conformant worker does." |
| `plugins/arunner/skills/arunner/SKILL.md` | The orchestrator agent's per-tick instructions (cadence rung 1) + the capability-ladder probe/announce/degrade prose + the worker contract. | When changing what the agent does per tick, or the ladder. |
| `references/BOOTSTRAP_PROMPT.md` | The paste-once prompt that turns a fresh agent session into the orchestrator. Deliberately restates the per-tick sequence (carried a low-reasoning model to a clean pass). | When changing the operator's rung-1 entry experience. |
| `references/STATE_MACHINE.md` | The canonical state-machine reference: states, transitions, idempotency, STOP/orphan semantics, shell dispatch, PID locks, E1/E2, schema v2, Postel, FR-21a/21b. | The companion to the engine — read alongside it. |
| `schemas/*.json` | `plan`, `heartbeat`, `job_manifest`, `result` schemas. The heartbeat schema is the load-bearing cross-surface contract (worker emits / harness reads). | When changing any on-disk shape. Keep schema + code + tests in lockstep. |
| `references/examples/` | The example plan (Python stub workers) — the ~minutes, zero-API demo. | When touching the demo or onboarding. |
| `tests/` | The suite: `test_tick.py` (engine), `test_heartbeat.py` (helper), `test_ticker.py` (ticker), `test_schemas.py` (schema byte-identity), `test_windows_readiness.py` (cross-platform + ASCII sweeps). | Before and after every change. |

> The engine ships **inside the package** at `arunner/engine/<name>.py`, so the
> installed wheel's single console entry point — `arunner` (→ `arunner.cli:main`,
> the FR-53 lifecycle CLI) — can reach it. While developing, run the engine
> modules directly as `python3 arunner/engine/<name>.py`, or drive the CLI with
> `python -m arunner <verb>`.

## Load-bearing conventions (do not violate)

1. **Stdlib only.** The tick engine, ticker, and heartbeat helper import
   nothing outside the Python standard library (NFR-3). A new third-party
   import is a design change, not a convenience. The only runtime
   requirements are user-level Python 3.10+ and (for agent workers) a host
   CLI.
2. **The spec is `docs/REQUIREMENTS.md`.** Every behavior traces to an
   `FR-NN`/`NFR-NN`/`UC-N`. Cite the number in commits and tests. If a change
   isn't covered by a requirement, the requirement is missing — add it,
   don't freelance.
3. **Idempotency is sacred.** Same disk state ⇒ same tick outcome; a double
   tick changes only the `cycle` counter (a true empty diff is impossible by
   design — the cycle is the idle-tick witness). The double-tick tests must
   stay green. Every rung of the capability ladder leans on this (NFR-6,
   FR-6).
4. **No model-transcribed paths, ever (FR-21a).** Every harness-known path a
   worker needs — `{HEARTBEAT_PATH}`, `{TASK_ID}`, `{RUN_DIR}`,
   `{TARGET_REPO}`, `{HARNESS_BIN}` — is substituted **mechanically by the
   engine before dispatch**. A worker prompt must never ask a model to copy
   or substitute a literal path. *Why:* in run `20260612T005833Z` a worker
   hand-copied a helper path and transcribed `/Users/anthropic/...` (username
   hallucinated), silently killing every heartbeat while the job "completed"
   invisibly. If you add a path a worker needs, add a placeholder — never a
   "replace `<X>` with…" instruction.
5. **ASCII output + utf-8/errors=replace reads.** All console/table output is
   pure ASCII (no em-dash, box-drawing, or arrows) — Windows cp1252 consoles
   crash on the rest (NFR-7). All reads of external/worker content use
   `encoding="utf-8", errors="replace"`. Both are enforced by AST sweep tests
   with mutation-verified pins; keep them green and keep new strings ASCII.
6. **Mutation-verify your regression pins.** A test that pins a behavior must
   demonstrably *fail* when the behavior is reverted. When you add a pin,
   bite it: revert the line, watch the test fail, restore, and record the
   bite in the test's docstring (the in-tree mutation-verify convention).
7. **Schema, code, and tests move in lockstep.** The two heartbeat schema
   copies are byte-identical by contract (pinned). A one-sided edit is a
   silent-drift bug.

## Running the suite

```bash
python3 -m pytest tests/ -q
```

Run it before and after every change. Time-dependent transitions (stall,
launch grace) are driven by a `*_NOW` epoch env override the engine reads —
no test sleeps. Purge `__pycache__` before a post-restore re-verify so a
stale `.pyc` can't mask a mutation restore.
