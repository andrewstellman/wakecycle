# Wakecycle Toolkit

> This file is for your AI assistant to read — not for you to read yourself.
> Open it in Claude Code, Cursor, Copilot, or any AI coding tool and say:
> "Read TOOLKIT.md and help me set up the harness for my jobs."
> The assistant will guide you through everything.

## How to respond when the user opens this file with you

*This section is instructions for **you, the AI assistant** reading this
file — not for the user.*

When a user opens this file and says "now you're an expert in the harness,"
"read TOOLKIT.md and help me," or just attaches it: **keep your first
response brief — two or three sentences plus one question.** One sentence on
what the harness does (it runs a batch of jobs — AI agents or any process
that appends JSON lines to a file — in a pool, from your existing agent
session or one terminal command, leaving a full record on disk). Then ask
the one question that decides everything: *"Where will this run — inside a
Claude Code session, or a plain terminal on a locked-down machine?"* — that
selects the rung. Don't dump the whole manual. Pull from the sections below
as the conversation needs them, and **verify every command against the
installed files before you give it** — never invent a flag.

## What this is

A batch orchestrator for AI coding agents. You write a **plan** (JSON: a list
of jobs + a pool size + a tick cadence). The harness runs the jobs a poolful
at a time, watches each one's heartbeat file, prints a status table every
tick, and stops when they're all done. All the logic is in one stdlib Python
script; your agent session (or a plain terminal) just relays and sleeps. No
server, no API keys beyond the session you already have, no admin rights.

The contract for a "job" is tiny: **a job is anything that appends JSON lines
to a file.** So a worker can be an AI agent, a shell script, a `make` target,
a CI job, or anything that can write a line.

## Install (user-level, no admin)

```bash
pip install --user wakecycle      # Python 3.10+
# or
npm install wakecycle
```

> **At 0.0.1 the package is a name reservation** — installing gives the
> `wakecycle` placeholder command. Run the harness from this repo today with
> `python3 bin/tick.py` / `bin/ticker.py` / `bin/heartbeat.py`; the
> `wakecycle-ticker` / `wakecycle-heartbeat` console commands wire up at v0.1.0
> (this manual uses those names).

Nothing needs root. The only requirements are user-level Python 3.10+ and, if
your workers are AI agents, a host CLI (Claude Code, Codex, Copilot, …) on
PATH and authenticated.

## Building a plan from a description (the procedure)

*This is the primary job of this file: the operator describes a batch in plain
language — "run **this** across **these** repos with **these** agents/commands"
— and you, the assistant, turn it into a conformant plan that passes
`--check` on the first try. Follow these steps; pull field details from the
sections below.*

**Step 1 — gather the batch.** From the request, extract: the list of
**repos/targets** (absolute paths); per target, **what runs** (an AI-agent
prompt, or a shell command, or a log a job already writes); and the **pool
size / cadence** if they care (else defaults).

**Step 2 — pick the dispatch for each job** (decision table):

| The job is… | Use | Form |
|---|---|---|
| an AI agent working **inside this session** (rung 1) | `dispatch_mode: subagent` | a `prompt` |
| a command **wakecycle launches** (test/build/script, any CLI) | `adapter: "wrap"` | a `command` |
| a process **something else launches** that writes its own log | `adapter: "tail"` | a `log_path` (+ optional `success_regex`/`failure_regex`/`sentinel_file`) |
| a host-CLI agent you invoke by argv yourself (advanced) | `dispatch_mode: shell` | a `worker_cmd` |

Rule of thumb: **subagent** when this session does the work; **wrap** when
wakecycle owns the launch; **tail** when something else does. Prefer `wrap`/
`tail` over a hand-written `worker_cmd` — the adapter wires the heartbeat
plumbing for you.

**Step 3 — choose the form.** Default to the **`jobs:` shorthand** (one line
per job; the expander injects placeholders and adapter plumbing). Drop to the
**full plan** only when you need a field the shorthand doesn't expose. Either
way the engine consumes the same canonical `plan.json`.

**Step 4 — write it, never hand-writing paths.** The engine substitutes
`{HEARTBEAT_PATH}`/`{TASK_ID}`/`{RUN_DIR}`/`{TARGET_REPO}`/`{HARNESS_BIN}` at
dispatch. **Never** ask the model to fill a real path into a prompt (FR-21a — a
hand-copied path once silently killed a job). In the shorthand you don't write
placeholders at all; in a full subagent `worker_prompt` use the placeholder
tokens verbatim.

**Step 5 — expand + pre-flight, always.** If you wrote shorthand, expand it;
then `--check` before launching:

```bash
python3 bin/jobs.py expand my_jobs.json > plan.json   # shorthand -> canonical
python3 bin/tick.py --check plan.json                  # validate -- fix every problem it lists
```

`--check` reports **all** problems at once (missing fields, a bad
`dispatch_mode`, a missing placeholder, a non-existent `target_repo`, an
unknown `adapter`) and exits nonzero. A clean `--check` is the green light to
`--init` and run. See the worked example at the end of this file.

## Writing a plan

A plan is one JSON file. Top-level knobs (all optional except `entries`):

| Field | Default | Meaning |
|---|---|---|
| `tick_interval_minutes` | 10 | Minutes between ticks while work is in flight. |
| `pool_size` | 3 | Max concurrent in-flight jobs (claimed/running/stalled). |
| `stall_threshold_minutes` | 45 | A job whose heartbeat is older than this is marked `stalled`. |
| `launch_grace_minutes` | 10 | A claimed job that emits no heartbeat within this is marked `LAUNCH-FAIL`. |
| `idle_tick_multiplier` | 1 | Lengthens the cadence when nothing is actively running. |
| `entries` | — | The list of jobs (one run per entry). Required. |

Each **entry**:

| Field | Required | Meaning |
|---|---|---|
| `task_id` | yes | Stable id (a UUID is recommended); threaded into the heartbeat and result. |
| `target_repo` | yes | Absolute path the worker operates on. Never cwd-derived. |
| `dispatch_mode` | yes | `"subagent"` (in-session agent, rung 1) or `"shell"` (detached host-CLI process, rungs 2–4). |
| `worker_prompt` | yes | The worker prompt. Contains placeholders the engine substitutes mechanically (below). |
| `worker_cmd` | shell only | The argv template the ticker runs detached. Tokens may carry the placeholders + `{PROMPT_FILE}`. |
| `auth_check` | shell, optional | A cheap argv run once before the first shell dispatch to confirm the CLI is present + authed (else `LAUNCH-FAIL`). |
| `heartbeat_path` | optional | An absolute path to a status file the job **already** writes — point the harness at it instead of the run-dir default. |

**Placeholders (substituted by the engine before dispatch — you never type a
real path):** `{HEARTBEAT_PATH}`, `{TASK_ID}`, `{RUN_DIR}`, `{TARGET_REPO}`,
`{HARNESS_BIN}` (the harness's own bin directory), and `{PROMPT_FILE}` (shell
mode — the per-job prompt file). **Never** write a prompt that asks the model
to "replace `<X>` with the path to…": that transcription hazard once
silently killed a job (a hallucinated username in a hand-copied path). Always
use a placeholder.

### Annotated example (both dispatch modes + an external heartbeat file)

```json
{
  "schema_version": "1",
  "tick_interval_minutes": 5,
  "pool_size": 2,
  "stall_threshold_minutes": 45,
  "launch_grace_minutes": 10,
  "entries": [
    {
      "task_id": "11111111-1111-1111-1111-111111111111",
      "target_repo": "/abs/path/repo-a",
      "dispatch_mode": "subagent",
      "worker_prompt": "TASK_ID={TASK_ID} RUN_DIR={RUN_DIR} TARGET_REPO={TARGET_REPO}\nHEARTBEAT_PATH={HEARTBEAT_PATH}\n\nDo the work on {TARGET_REPO}. Emit heartbeats to {HEARTBEAT_PATH} (see the worker contract). Return one line."
    },
    {
      "task_id": "22222222-2222-2222-2222-222222222222",
      "target_repo": "/abs/path/repo-b",
      "dispatch_mode": "shell",
      "worker_prompt": "Audit {TARGET_REPO}. Heartbeat to {HEARTBEAT_PATH}; task {TASK_ID}.",
      "worker_cmd": ["claude", "--print", "--model", "sonnet",
                     "--append-system-prompt", "$(cat {PROMPT_FILE})"],
      "auth_check": ["claude", "--version"]
    },
    {
      "task_id": "33333333-3333-3333-3333-333333333333",
      "target_repo": "/abs/path/repo-c",
      "dispatch_mode": "shell",
      "worker_prompt": "Run the existing job on {TARGET_REPO}.",
      "worker_cmd": ["python3", "{HARNESS_BIN}/your_worker.py", "--repo", "{TARGET_REPO}"],
      "heartbeat_path": "/abs/path/repo-c/.status/heartbeat.ndjson"
    }
  ]
}
```

A `subagent` entry is run by an in-session agent (rung 1 only). A `shell`
entry is run by the ticker as a detached process (rungs 2–4). The third entry
points the harness at a status file the job already maintains, with no change
to the job.

## The `jobs:` shorthand (the quick form)

Most plans don't need the full entry shape. The **`jobs:` shorthand** is a
higher-level form the expander (`bin/jobs.py`) turns into the canonical
`plan.json` — injecting the placeholders and adapter plumbing so the result
passes `--check`. The engine only ever sees the expanded plan; the shorthand is
pure convenience (the low-level schema stays canonical).

Each job in `jobs:` is one of:

```jsonc
{ "repo": "/abs/repo-a", "agent": "subagent", "prompt": "Review for security bugs." }
{ "repo": "/abs/repo-b", "adapter": "wrap", "command": ["pytest", "-q"] }
{ "repo": "/abs/repo-c", "adapter": "tail", "log_path": "/abs/repo-c/build.log",
  "success_regex": "BUILD OK", "failure_regex": "BUILD FAILED" }
```

Top-level knobs (`pool_size`, `tick_interval_minutes`, …) sit beside `jobs:` and
pass through. Add an `"id"` to a job to set its `task_id` (else `job-NN`).
Expand with `python3 bin/jobs.py expand my_jobs.json > plan.json`. Ready-to-edit
templates live in **`examples/`** (`agent_review`, `shell_jobs`, `mixed`,
`wrap_vs_tail`, plus a `canonical_plan`); each one expands to a `--check`-clean
plan.

## Adapters: turn any command or log into a job (`wrap` / `tail`)

An adapter makes a non-AI job a conformant worker with **no change to the
command**:

- **`wrap`** — wakecycle launches your `command` as a child, captures its
  stdout+stderr, emits keepalives, and reports **COMPLETED/FAILED straight from
  the exit code** (never by parsing output). Use when wakecycle owns the launch.
- **`tail`** — wakecycle watches a `log_path` a job already writes, surfaces its
  latest line as the activity, and decides doneness by **precedence**: an
  optional `success_regex`/`failure_regex`/`sentinel_file` overlay (for jobs
  that signal only in their log), then the authoritative process exit (default
  COMPLETED on a clean exit). Use when something else owns the launch.

In the shorthand you just set `adapter` + its command/log; the engine
synthesizes the `heartbeat.py wrap|tail …` invocation. (Under the hood that's
the `adapter` field on a `shell` entry — you never wire it by hand.)

## Pre-flight: always `--check` before launching

`python3 bin/tick.py --check <plan>` validates a plan **before** you spend
anything — schema, required placeholders, `target_repo` existence, the adapter
config — and reports every problem at once. Make it a habit: **expand →
`--check` → `--init` → run.** A reactive `LAUNCH-FAIL` after spend is exactly
what the pre-flight prevents.

## Choosing a rung (which entry point?)

Ask, in order:

1. **Inside an agent session with a timer (Claude Code with `ScheduleWakeup`)?**
   Paste the bootstrap prompt (`references/BOOTSTRAP_PROMPT.md`) — the session
   drives the run. **Use `subagent` entries.** Keep the session open. *Pair
   with a safety tick (troubleshooting).*
2. **No session, but you can add a cron/Task-Scheduler/launchd entry?**
   Install the printed `wakecycle-ticker --once <run-dir>` schedule at the
   plan cadence. **Use `shell` entries.** No window stays open.
3. **No scheduler rights (locked-down machine)?** Run
   `wakecycle-ticker <plan.json>` in a terminal — the foreground loop. **Use
   `shell` entries.** The window stays open for the run.
4. **Can't keep a window open?** Run `wakecycle-ticker --once <run-dir>` by
   hand whenever convenient; each call is one safe tick. Every failure path
   prints this exact command.

The ticker runs **`shell` entries only** — a `subagent` entry it encounters
is reported and skipped with the rung-1 instruction.

## In-context mode (dispatch-to-self)

A third dispatch option (FR-46), beyond subagent and shell: the orchestrator can
do tasks **itself**, in its own context, between ticks. Enable it with an
`instruction_folder` setting at bootstrap — the agent then watches that folder
and processes the **lowest-numbered `NNN-` instruction with no matching output**
(a streaming queue, distinct from the plan's fixed batch), writes the output,
and idles when the queue is empty; a `STOP` file halts it, including mid-queue.
It's a superset that keeps every harness feature; while the agent does
in-context work, background monitoring pauses and resumes between tasks (the
turn is single-threaded), and the next tick absorbs whatever the background
workers did.

**Honesty (C-7) — important.** In-context mode **does not fix Class-C** loop
drops, has **no auto-recovery** of the in-context queue (a silently-dropped turn
needs operator re-bootstrap), and is **rung-1 only**. It is a convenience/
unification superset, **not the unattended-reliability path**. For reliable
unattended runs, use the deterministic ticker/cron rungs — the floor rescues
only the background/harness portion of an in-context session, never the
in-context tasks themselves.

## Running and reading the status table

Rung 1: paste the bootstrap, watch the table each tick. Rung 3:

```bash
wakecycle-ticker path/to/plan.json     # loop: --init, then tick -> spawn -> sleep -> repeat
wakecycle-ticker path/to/run-dir       # resume an existing run (loop)
wakecycle-ticker --once path/to/run-dir  # exactly one tick (cron / manual floor)
```

The table:

```
Run-Dir: <stamp> (cycle N)
RUN  REPO                  MODE    STATE        ACTIVITY        LAST-HB      HB-AGE
01   /repos/a              shell   running      2:generation    IN_PROGRESS  0m18s
...
Queue: q  Claimed: c  Running: r  Stalled: s  Completed: x  Failed: y
Next tick in M min
```

- **RUN** — the run number (`run-NN`). **REPO** — the target. **MODE** —
  `subgnt` or `shell`.
- **STATE** — `queued`, `claimed`, `running`, `stalled`, `completed`,
  `failed`, or `LAUNCH-FAIL`.
- **ACTIVITY** — the worker's latest `label` (free text it chose; never
  interpreted; sanitized + truncated for display).
- **LAST-HB** — the last heartbeat `status`. **HB-AGE** — how long since the
  last heartbeat.
- A `LAUNCH-FAIL` row adds a footnote: *"no heartbeat received within launch
  grace — check worker-side launch: auth, helper availability, paths."* —
  because that state covers more than auth (a bad path, a missing helper, a
  broken `worker_cmd`).
- **`cycle`** only ever increments; if nothing else changed, the tick was an
  idle tick (that's expected and cheap).

## Stopping, resuming, manual ticks

- **Stop:** create a file named `STOP` in the run directory. The next tick
  sees it, changes nothing, exits cleanly. In-flight detached workers finish
  on their own (no kill this release). Delete `STOP` and tick to continue.
- **Resume after a crash / closed window / slept machine:** point a fresh
  session at the existing run directory (skip `--init`), or run
  `wakecycle-ticker --once <run-dir>`. Disk state resumes; nothing
  double-runs.
- **Manual ticks:** `wakecycle-ticker --once <run-dir>` as often as you like —
  over-ticking is harmless (cycle-only).

## The worker contract — for custom (non-AI) jobs

Any job qualifies if it appends JSON lines to its heartbeat file (one writer
per file). Lines are schema v2:

```json
{"ts":"2026-06-12T05:00:00Z","task_id":"<id>","schema_version":"2","label":"build","status":"IN_PROGRESS","message":"optional detail","data":{"anything":"opaque"}}
```

- `status` (the only field the harness reads): `STARTING` / `IN_PROGRESS` /
  `COMPLETED` / `FAILED` / `ABANDONED`. The **last** line should be a terminal
  one (`COMPLETED`/`FAILED`/`ABANDONED`) carrying `result_file` + `summary`.
- `label` is free text shown in ACTIVITY. `message` is longer human detail.
  `data` is an opaque object the harness never reads.

**With the helper (convenience):**

```bash
wakecycle-heartbeat emit      --task-id <id> --heartbeat-path <p> --label build --status IN_PROGRESS [--message ...] [--data '{"k":"v"}']
wakecycle-heartbeat keepalive --task-id <id> --heartbeat-path <p>      # re-pings the last label
wakecycle-heartbeat terminal  --task-id <id> --heartbeat-path <p> --status COMPLETED --result-file <f> --summary "done"
```

The helper JSON-encodes every value (so `%`, `"`, `\` in a field can't
corrupt the line), appends with `O_APPEND` (concurrent appends can't tear),
and if a write fails it exits loudly nonzero — a silent worker must never look
healthy. Task id and heartbeat path may come from `--flags` or the
`HARNESS_TASK_ID` / `HARNESS_HEARTBEAT_PATH` env vars the engine sets.

**Without the helper (any language):** just append the JSON line yourself.
`printf '{"ts":"...","task_id":"...","schema_version":"2","status":"IN_PROGRESS","label":"step"}\n' >> "$HEARTBEAT_PATH"`. The harness is liberal in what
it accepts (Postel) — it even still reads old v1 `phase`/`step` lines, and
skips a malformed line with a warning rather than dying.

## Troubleshooting

- **The loop went silent (rung 1).** This is the Class-C in-session failure:
  the wakeup fired but the resumed turn mis-serialized its first action and
  ended without rescheduling. **Fix:** deploy a **safety tick** — a
  cron/scheduler or second terminal running `wakecycle-ticker --once <run-dir>`
  at ~3× the plan cadence against the same run-dir. It's a no-op while the
  loop is healthy and rescues it within one interval if the turn dies. To
  resume right now, just run that command once. (Tracking:
  anthropics/claude-code#67945.)
- **A job shows `LAUNCH-FAIL`.** It was claimed but never heartbeated within
  the launch grace. Causes, in order of likelihood: the worker CLI isn't
  authenticated (add an `auth_check`), the helper/worker path is wrong, the
  worker prompt asked the model to fill a path it got wrong (use a
  placeholder), or `worker_cmd` is malformed. The result record carries the
  same hint.
- **A job shows `stalled`.** Its heartbeat is older than
  `stall_threshold_minutes`. A live worker should emit a keepalive every few
  minutes; if it can't, raise the threshold. A late heartbeat moves it back to
  `running`. (If the machine just woke from sleep, the next tick's
  wall-clock-jump guard suppresses a false stall for one tick.)
- **Nothing happens / "synced folder" warning.** Run directories must be on
  local disk; OneDrive/Dropbox-style synced paths are unsupported and warned.
  Move the run-dir parent to local disk.
- **Paths with spaces.** Shell-mode prompts are written to a `{PROMPT_FILE}`
  and referenced by path (no shell re-quoting), so spaces are safe; keep
  `target_repo` absolute and quote it in your own `worker_cmd` if you
  interpolate it elsewhere.

## FAQ seeds

- *Does the worker have to be an AI?* No — anything that appends JSON lines
  qualifies (shell script, `make`, CI, a human with `echo >>`).
- *Will it spend API tokens to orchestrate?* No — orchestration is a stdlib
  script and ~7 fixed agent steps per tick; the example plan's stub workers
  spend nothing. Your spend is whatever your real workers cost.
- *Can I point it at a status file my tool already writes?* Yes — set
  `heartbeat_path` on the entry.
- *Is it safe to tick twice?* Yes — ticks are idempotent; redundant ticking is
  safe by construction (that's what makes the safety tick free).
- *Windows / locked-down machine?* That's the design floor — rung 3, one
  terminal, no admin.

## Worked example: request → shorthand → plan → check

**The operator says:** *"Review repo-a and repo-b for bugs with an in-session
agent, and run the test suite in repo-c."*

**You write the `jobs:` shorthand** (`examples/toolkit_walkthrough.jobs.json`) —
two subagent reviews + one wrapped shell command, no placeholders typed by hand:

```json
{
  "pool_size": 2,
  "tick_interval_minutes": 5,
  "jobs": [
    {"id": "review-a", "repo": "/abs/repo-a", "agent": "subagent",
     "prompt": "Review this repository for bugs and risky patterns; summarize the findings."},
    {"id": "review-b", "repo": "/abs/repo-b", "agent": "subagent",
     "prompt": "Review this repository for missing or weak test coverage."},
    {"id": "tests-c", "repo": "/abs/repo-c", "adapter": "wrap",
     "command": ["pytest", "-q"]}
  ]
}
```

**Expand it.** `python3 bin/jobs.py expand examples/toolkit_walkthrough.jobs.json`
produces the canonical plan: each subagent job becomes an entry whose
`worker_prompt` carries the injected placeholder header
(`HEARTBEAT_PATH={HEARTBEAT_PATH}` … `HARNESS_BIN={HARNESS_BIN}`) followed by
the prompt; the `wrap` job becomes a `shell` entry with `adapter: "wrap"` and
the command — the engine synthesizes its `heartbeat.py wrap …` invocation.

**Pre-flight.** `python3 bin/tick.py --check plan.json`, once the repo paths
point at real directories, prints:

```
plan OK: plan.json -- no problems found
```

Then `--init` and run at your chosen rung. If anything is wrong it prints
`plan FAILED: <plan> -- N problem(s):` followed by one line per problem (it
reports them all at once) and exits nonzero — fix every one.

> The repo paths above are placeholders to edit. `--check` requires each
> `target_repo` to be a real directory; everything else (schema, the injected
> placeholders, the adapter config) validates as written. The `examples/`
> templates are bound to `--check` in the test suite, so this walkthrough can't
> silently drift from what the engine accepts.
