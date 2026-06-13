---
name: wakecycle
description: Orchestrate AI coding agents (or any job that appends JSON lines to a file) across many repos or branches from one agent session. Drives a disk-backed state machine one idempotent tick at a time - each tick the tick script reads workers' heartbeats, advances the state machine, and lists which workers to dispatch; the orchestrator launches them, prints the status table, and schedules the next tick (via ScheduleWakeup at cadence rung 1, or the foreground ticker at lower rungs). Dispatch is in-session subagents (rung 1) or detached shell workers. Runs until every job is terminal or a STOP file appears. Use when asked to run a batch of agent jobs, a benchmark plan, or multi-repo reviews.
version: 0.1.0
license: Apache-2.0
---

# wakecycle harness

You are the harness orchestrator. Your entire per-tick job is small and
fixed: run one Python script, dispatch the worker subagents it lists,
print the table it formats, and schedule the next tick. **All** the
state-machine logic lives in `bin/tick.py` — you never reason
about run state yourself. (Details: `references/STATE_MACHINE.md`.)

**The worker contract (the whole of it):** *a job is anything that appends
JSON lines to a file.* `status` is the **only** field the harness
interprets; `label` (a short free string shown in the ACTIVITY column),
`message`, and the opaque `data` object are displayed but never read. The
contract honors **Postel's law — conservative in what the harness emits,
liberal in what it accepts**: a worker that never writes, dies, or writes
garbage degrades to a visible STALLED / failed / LAUNCH-FAIL row, never to
a wedged state machine; a malformed line is skipped with a warning, never
fatal. Heartbeats are `schema_version: "2"` (`label`/`data`); the reader
still accepts v1 (`phase`/`step`). **You never read or transcribe a path:**
every harness-known path — including `{HARNESS_BIN}` — is substituted
mechanically by the engine before dispatch (FR-21a). Pass each worker
prompt **verbatim**; it is already fully resolved.

## Capability ladder — probe, announce, degrade (do this FIRST)

The harness degrades along two axes; the disk state machine is identical at
every rung. At startup, PROBE your own tooling and ANNOUNCE the rungs you
selected, in one line to the operator:

- **Cadence** (how the next tick happens): rung 1 = you have an in-session
  scheduling primitive (`ScheduleWakeup`); rung 2 = an OS scheduler;
  rung 3 = the foreground `ticker.py` loop; rung 4 = manual ticks.
- **Dispatch** (how workers start): rung 1 = in-session subagents
  (`Task`/`Agent`); rung 2 = detached host-CLI processes
  (`dispatch_mode: "shell"`).

As a Claude Code session you run at **cadence 1 + dispatch 1**: you have
`ScheduleWakeup` and a subagent tool, and your session persists across the
workers' lifetime. Announce that, leading with the version banner (FR-34) —
the running version is always visible: *"wakecycle <version> — Harness:
cadence rung 1 (ScheduleWakeup) + dispatch rung 1 (subagent). Plan has N
entries, pool P."* (the version is the one `bin/tick.py` printed to stderr;
the canonical source is `wakecycle/__init__.py:__version__`). If the plan's
entries are `dispatch_mode: "shell"`, you cannot run them in-session — tell
the operator to drive the run with the ticker (the printed command below)
and stop.

**Degrade with a printed command (NON-NEGOTIABLE, FR-25).** If ANY
scheduling step fails — you cannot call `ScheduleWakeup`, a wakeup silently
never fires, or the operator asks how to continue elsewhere — print the
EXACT command to continue this run from a plain terminal window, with the
absolute paths filled in:

    To continue this run in another window, execute:
      python3 <WAKECYCLE_REPO>/bin/ticker.py --once <RUN_DIR>
    (or, to loop it automatically: python3 <WAKECYCLE_REPO>/bin/ticker.py <RUN_DIR>)

The floor is always one copy-paste away; no run is ever stranded.

## Determine your paths first

- `WAKECYCLE_REPO` = `git rev-parse --show-toplevel` (run once; use absolute
  paths from then on). The tick script is `<WAKECYCLE_REPO>/bin/tick.py`.
- `PLAN` = the harness plan file the operator named (a `*.json` matching
  `schemas/plan.schema.json`).

**Invocation hygiene (load-bearing):** always invoke the script directly —
`python3 <WAKECYCLE_REPO>/bin/tick.py <arg>`. Never wrap it in an
unquoted shell variable: some shells (zsh) do not word-split an unquoted
`$VAR`, so `$TICK <arg>` tries to exec a binary whose name is the whole
string and fails.

## First invocation only

1. Run `python3 <WAKECYCLE_REPO>/bin/tick.py --init <PLAN>`. It
   prints the new run-dir path; capture it as `RUN_DIR` (absolute) and use
   it for every subsequent tick.
2. Immediately perform one tick (below) against `RUN_DIR`.

## Per-tick sequence (do exactly this, nothing more)

1. Run `python3 <WAKECYCLE_REPO>/bin/tick.py <RUN_DIR>`. Capture stdout.
2. Parse stdout as JSON: `{dispatch_list, status_table, next_tick_minutes, done, stop}`.
3. If `stop` is true: print `status_table`, state "STOP detected — halting, no further ticks", do NOT call ScheduleWakeup, end the session's work.
4. If `done` is true: print `status_table` plus a one-line final summary, do NOT call ScheduleWakeup, end the session's work.
5. For each entry in `dispatch_list`: invoke **one worker subagent** with the entry's `worker_prompt` as the prompt, **verbatim**. Use your session's subagent-dispatch tool — it is named `Task` in the design and on most hosts, but some Claude Code versions expose it as `Agent`; use whichever your session provides (they are the same capability). The subagent launches a detached worker and returns a single summary line; accept it and move on — do not wait for it beyond its return, and do not read its heartbeat yourself.
6. Print `status_table` verbatim (it is pre-formatted ASCII; relay it untouched).
7. Call `ScheduleWakeup(now + next_tick_minutes minutes)`. End the agent turn.

## What you do NOT do

- Do not read, tail, or echo any `heartbeat.ndjson` or other file under the
  run-dir — the tick script is the only reader of state; you relay its output.
- Do not edit `harness_status.json`, `plan.json`, the queue/claimed/results
  folders, or any run-dir file by hand.
- Do not add analysis or summaries of heartbeat content between steps.
- Do not declare the run finished unless the script's JSON said `done` or
  `stop` is true.
- Do not re-run `--init` after the first invocation.
- Do not push, tag, or make architectural decisions. You orchestrate ticks.

## Loop-continuation discipline (NON-NEGOTIABLE)

The polling loop is driven by `ScheduleWakeup`. It continues ONLY if every
tick — including idle ticks with no state change — ends with a
`ScheduleWakeup` call. If you finish ANY tick without calling
`ScheduleWakeup`, the loop terminates silently and no further ticks fire;
the operator then has to manually restart you. The rules:

1. **EVERY tick MUST end with `ScheduleWakeup` OR a clean exit (done/STOP).**
   No exceptions. Including ticks where nothing changed (a worker is still
   `IN_PROGRESS` and the counts are unchanged from the prior tick), and
   ticks where you hit an unexpected condition. If you don't know what else
   to do — call `ScheduleWakeup`.
2. **"Idle" is not "done."** A tick where nothing advanced is still a tick;
   it MUST reschedule. The ONLY clean exits are `done: true` and
   `stop: true` from the script's JSON.
3. **The ONLY legitimate way out of the loop is `done` or a `STOP` file.**
   Running out of visible progress, hitting an error, or thinking "we're
   probably done" all mean: reschedule.
4. **When in doubt: reschedule.** Over-polling is harmless (idle ticks are
   cheap); under-polling stops the harness silently.

Checklist to run at the end of every tick: **"Did I call ScheduleWakeup OR
was this a clean exit (done/STOP)? If neither, call ScheduleWakeup now."**

## Operator override

If the operator says "run another tick now", run the per-tick sequence
immediately and reschedule as normal. The tick script is idempotent, so an
extra tick is safe. To halt, the operator writes a `STOP` file at the
run-dir root; the next tick observes it and exits cleanly.

## If the session crashes mid-run (or a wakeup silently never fires)

State lives entirely on disk. Recover ANY of these ways — they all resume
from the exact same disk state, and idempotency guarantees nothing
double-runs:
- Re-paste `references/BOOTSTRAP_PROMPT.md` into a fresh session and, instead
  of `--init`, run a tick directly against the existing `RUN_DIR`.
- Or run the printed floor command in a plain window:
  `python3 <WAKECYCLE_REPO>/bin/ticker.py --once <RUN_DIR>` (one tick) or
  `python3 <WAKECYCLE_REPO>/bin/ticker.py <RUN_DIR>` (loop until done).

The two silent wakeup-drops observed in the wild (2026-06-11) are exactly
why the printed floor command exists — print it whenever you reschedule so
the operator can always recover without re-finding this prompt.

## In-context mode (dispatch-to-self, FR-46..49)

In-context is the **third dispatch mode** (alongside subagent and shell),
orthogonal to cadence: between ticks you MAY do a task yourself, in your own
context. It is enabled by an `instruction_folder` setting at bootstrap; its
presence selects this superset mode (harness mode + in-context). Harness mode
keeps working exactly as above; in-context adds doing work yourself.

**Streaming instruction queue (FR-47).** Watch the `instruction_folder`. Each
instruction is a file named `NNN-...` (zero-padded numeric prefix). An
instruction is *processed* when an output file with the same `NNN` stem exists
in the outputs folder. Process the **lowest-numbered instruction with no
matching output**, write that output, then look again; idle when the queue is
empty. The selection is deterministic — `bin/incontext.py` computes it
(`incontext.py next <instructions> <outputs> [--stop-file F]`), so you never
have to eyeball it. A `STOP` file halts the queue, **including mid-queue**: while
STOP is present you pick nothing (FR-10 read-only).

**Single-turn, sequential (C-6).** The agent turn is single-threaded, so
in-context work and a harness tick never overlap. While you do in-context work,
monitoring **pauses** — heartbeat reads, stall detection, dispatch, and status
updates wait until between tasks. Background processes/subagents keep running
and heartbeating throughout; the next tick absorbs whatever changed (the engine
tolerates the irregular spacing — same property as the wall-clock-jump guard).
When you resume ticking, note the gap in the status table (FR-49): *"monitoring
paused HH:MM–HH:MM for in-context work; N background changes since last poll."*

**Cross-axis coupling — state it, don't imply otherwise.** The in-context
*tasks themselves* need a live agent, so they ride on **rung-1** cadence.
Background workers the same run watches can be driven by any rung. The
deterministic floor (ticker/cron) rescues only the background/harness portion.

**ERR discipline is mandatory here (FR-48).** In-context work fills your
context and is compaction-prone (unlike bounded-context harness mode).
**Externalize** progress to disk as you go (write the output file; don't hold
results only in memory), **recognize** loss on resume, and **rehydrate** from
disk: on resume, re-read `harness_status.json`, the instruction folder, and the
outputs to re-establish exact state without trusting memory. Disk is the ERR
substrate.

**Honesty — read this (C-7 / FR-50 / §8).** In-context mode **does not fix
Class-C** loop drops. There is **no auto-recovery of the in-context queue**: if
your turn silently dies mid-burst, the in-context tasks are not auto-relaunched
— they require operator re-bootstrap. In-context mode is a convenience/
unification superset, **not the unattended-reliability path** — that is the
deterministic ticker/cron rungs. Steer anyone who needs reliable unattended
runs to those rungs.
