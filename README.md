# Arunner

**A batch orchestrator for _any_ agentic coding system — Claude Code, GitHub
Copilot, Codex, Cursor, Antigravity, … — not one vendor. It runs inside the
agent session you already have: no server, no daemon, no framework, no API
keys beyond your session, no admin rights.**

Point it at a list of jobs (audit these ten repos, run this benchmark across
these branches) and it runs them in a pool, watches their progress, and
leaves a complete record on disk — driven by your existing agent session
waking itself on a timer, or, on a locked-down machine, by one plain Python
script in a terminal window.

**"Any agentic system" is structural, not a slogan.** Arunner is vendor-neutral
*by construction*: the orchestration engine is stdlib-only Python (it runs
wherever Python 3 does), the worker contract is vendor-neutral (*a job is
anything that appends JSON lines to a file*), and work is dispatched either as
in-session subagents or as detached host-CLI processes (FR-14/15) — no vendor
SDK anywhere. **The honesty split that keeps that claim true:** the
deterministic engine and the terminal/cron floor are genuinely host-agnostic
and run identically everywhere; the one place hosts differ is the *in-session
agent rung* (each host has its own scheduling quirks — Class-C is a Claude Code
one). So "runs on any agentic system" describes the engine + floor — never an
unvalidated per-host claim about the agent rung. Which hosts are proven, and in
which role (as a **worker** or as a builder-driving **orchestrator**), is in the
support table below.

## The thesis (30 seconds)

Most orchestration frameworks put the intelligence in external
infrastructure and treat the model as a worker. **The harness inverts that.**

- **All the determinism lives in one small stdlib Python script** — the tick
  engine: a disk-truth state machine advanced one idempotent tick at a time.
- **The agent only relays and sleeps.** Each tick it runs the script, reads
  the heartbeats of the workers it started, starts whatever the script tells
  it to, prints a status table, and schedules the next tick. It never decides
  anything.
- **Disk is the database.** Every run is a directory: the plan, the live
  status, one heartbeat file per job, one result record per job.
- **Crash recovery is "run one tick."** Lost the session? Closed the window?
  Machine slept? Run one command against the run directory and it picks up
  exactly where it left off. Idempotency guarantees nothing double-runs.

Because the state machine is a few hundred lines of stdlib Python and the
agent's job is ~7 fixed steps, the orchestration runs on a small, cheap model
— you spend your capable-model budget on the *workers*. (Verified on Haiku
4.5; see the support table.)

## The worker contract (the whole of it)

> **A job is anything that appends JSON lines to a file.**

A line when it starts, a line every so often, a terminal line at the end —
single-line JSON, one writer per file. The `status` field is the only thing
the harness interprets; everything else (`label`, `message`, an opaque `data`
object) is decoration it displays but never reads. The worker doesn't have to
be an AI: a shell script, a `make` target, a CI job, or a human with
`echo >>` all qualify. A convenience helper ships for emitting the lines, but
it's optional.

The contract follows **Postel's law — conservative in what the harness
emits, liberal in what it accepts.** A worker that never writes, dies
mid-run, or writes garbage degrades to a visible `STALLED` / `failed` /
`LAUNCH-FAIL` row in the status table — never to a wedged state machine. A
malformed line is skipped with a warning, never fatal. (FR-18, FR-19)

---

## Quickstart — watch the whole architecture happen, zero API spend

Install at user level (no admin):

```bash
pipx install arunner            # Python 3.10+ (recommended)
# or
pip install --user arunner
# or, secondary channel — a thin launcher that execs the Python CLI:
npm install arunner
```

> **v0.1.0 installs three real console commands:** `arunner` (the lifecycle CLI —
> `run`/`status`/`stop`/`resume`/`summary`/`preview`/`expand`), `arunner-ticker`
> (the foreground/`--once` driver), and `arunner-heartbeat` (the worker helper).
> pip/pipx is the primary channel; the npm package only installs a thin Node
> launcher that execs the Python CLI. From a source checkout the engine modules
> are at `python3 arunner/engine/tick.py` / `ticker.py` / `heartbeat.py`, or
> drive the CLI with `python -m arunner <verb>`.

The package ships an **example plan with cross-platform Python stub
workers** — they do no real work and spend nothing; they just walk the
heartbeat lifecycle so you watch the architecture happen (pool-limited
dispatch, a genuine idle tick, staggered dispatch when the first stub
finishes, heartbeat-driven reaps, clean self-termination). (UC-8, FR-31)

You can run the demo two ways. **Pick the row that matches your setup** (see
the decision tree below for the full logic):

### Path A — inside a Claude Code session (cadence rung 1)

Open a fresh Claude Code session at the install and paste the bootstrap
prompt (`references/BOOTSTRAP_PROMPT.md`), pointing it at the example plan.
The session becomes the orchestrator and drives the run to completion on its
own `ScheduleWakeup` timer — one paste, no further interaction until it
reports `done` (or you drop a `STOP` file). This path uses **in-session
subagents** as workers.

### Path B — a plain terminal window (cadence rung 3, the no-admin floor)

No agent session, no scheduler, no admin rights — just Python:

```bash
arunner-ticker path/to/demo-plan.json      # loop: tick -> spawn -> sleep -> repeat
```

The ticker replaces the agent: each tick it runs the engine, spawns the
listed workers detached, prints the table, sleeps the cadence, repeats —
until every job is terminal. This path uses **detached shell workers**
(`dispatch_mode: "shell"`). The ticker runs shell entries only (a subagent
entry is reported and skipped with the rung-1 instruction), so point it at a
shell-dispatch plan — adapt the example by switching `dispatch_mode` to
`"shell"` and adding a `worker_cmd`, or use the shell demo plan shipped with
v0.1.0. (UC-5, FR-24)

> The demo runs in **~20 minutes** with the shipped example plan (UC-8); its
> pace is set by the plan's `tick_interval_minutes` and the stub's `--steps`
> / `--sleep`, so tune those down for a faster run. Both paths produce the
> **same** run directory — the artifacts are tier-invariant.

---

## Which entry point do I use? (the capability ladder)

The harness degrades along two independent axes; the disk state machine is
**identical at every rung**. At startup an orchestrating agent probes its own
tooling and announces the rungs it selected (FR-22). As an operator, walk
this tree:

**1. Do you have an agent session with a scheduling primitive (e.g. Claude
Code with `ScheduleWakeup`)?**
→ **Yes:** paste the bootstrap. **Cadence rung 1 + dispatch rung 1**
(in-session subagents). The headline workflow — zero infrastructure, one
paste. Your session must stay open for the run's duration. *(Pair it with a
safety tick — see below.)*

**2. No agent session, but you can install a scheduler entry (cron / Task
Scheduler / launchd / host Automations)?**
→ Install the printed one-line schedule running `--once` at the plan cadence.
**Cadence rung 2 + dispatch rung 2** (detached shell workers). No window
needs to stay open; survives logout. (UC-6)

**3. No scheduler rights (locked-down corporate machine)?**
→ Run the foreground ticker in a terminal window. **Cadence rung 3 + dispatch
rung 2** — the no-admin floor that must work everywhere. The window stays
open for the run's duration. (UC-5)

**4. Can't even keep a window open?**
→ Advance the run by hand, one printed command at a time
(`arunner-ticker --once <run-dir>`). **Cadence rung 4.** The harness never
strands a run: every failure path prints the exact next command. (UC-7,
FR-25)

Rungs 2–4 require `dispatch_mode: "shell"` entries (an externally-ticked
context can't launch in-session subagents — C-2).

**In-context mode (a third dispatch option, FR-46).** Beyond dispatching
subagents/shell workers, the orchestrator can also do tasks *itself*, in its
own context, between ticks — enabled by an `instruction_folder` setting, which
turns the agent into a streaming-queue worker (it processes the
lowest-numbered instruction with no matching output). It is a convenience
superset that keeps every harness feature. **Honesty (C-7):** in-context mode
**does not fix Class-C** loop drops and has no auto-recovery of its own queue —
it is **rung-1 only** and is **not the unattended-reliability path**. If you
need reliable *unattended* runs, use the deterministic ticker/cron rungs (2–4),
which are immune to the agent-loop drop; the in-context/agent rung is the
interactive/convenient mode, backed by the safety tick.

---

## Host support — what's verified vs designed (honest)

Per NFR-12, every claim is labeled **VERIFIED** (evidence behind it) or
**DESIGNED** (built and unit-tested, but no end-to-end host run yet). Don't
trust a DESIGNED cell as if it were proven.

A host can play **two distinct roles**, verified separately: (i) as a detached
**worker** — a `worker_cmd` agent CLI the engine launches and reaps (Copilot and
Codex are VERIFIED here, macOS, rung 3, V-14); and (ii) as the builder-driving
**orchestrator** that reads the skill and assembles/drives a run conversationally
(the FR-52 interactive builder). The two are independent: V-14 verified Copilot
and Codex as *workers*, **not** as builder-driving orchestrators. The builder is
*designed* for any capable host but VERIFIED only on Claude Code (it embeds no
model — the host does the natural-language understanding — so "any host" is a
design property, not yet a proven per-host claim).

| Host / rung / role | Dispatch | Status | Evidence |
|---|---|---|---|
| Claude Code, cadence 1 (in-session timer) | subagent | **VERIFIED** | 3 Sonnet validation passes + Haiku 4.5 (one clean autonomous-loop pass + one observed failure path — the low-reasoning-model bet), 2026-06-11; multi-entry pool run with staggered dispatch, agent-honored STOP, detached workers outliving the dispatch turn (pgrep-verified) |
| Foreground ticker, cadence 3 (no-admin floor), macOS | shell | **VERIFIED** | Live end-to-end demo in-repo, 2026-06-12: pool gating, real detached PIDs, idle tick, staggered dispatch on reap, clean DONE — independently reproduced |
| Idempotency / idle-tick survival / STOP / resume | both | **VERIFIED** | Unit suite (mutation-verified) + spike passes; double-tick is cycle-only by construction |
| Encoding safety (cp1252 / utf-8) | both | **VERIFIED** | AST sweep tests with mutation-verified pins |
| OS scheduler, cadence 2 — **cron (macOS)** | shell | **VERIFIED** | cron drove a shell plan to `done` fully unattended (no foreground process), 2026-06-12, after the double-fork detachment fix that lets workers survive the cron job's process-tree teardown; E1 overlapping-fire lockfile skip witnessed; crontab snapshot/restored (VALIDATION V-9). |
| OS scheduler, cadence 2 — Windows Task Scheduler / other hosts | shell | **DESIGNED** | `--once` is the cross-host schedule target and unit-tested; no Windows/other scheduled-run yet |
| Windows / Linux, foreground ticker | shell | **DESIGNED** | Platform branches are stdlib + unit-tested (detach flags, PID liveness, lockfile); verified live on macOS only |
| **Copilot + Codex** CLIs — host as **worker** (macOS, rung 3) | shell | **VERIFIED** | both ran as detached `worker_cmd` agent workers under the plain ticker (no agent-orchestrator), 2026-06-12: heartbeated STARTING/running, terminal COMPLETED carrying a real `result_file` with a real summary (Copilot→README, Codex→REQUIREMENTS); auth pre-flight ran (FR-16) (VALIDATION V-14). |
| Interactive builder — host as **orchestrator** (FR-52) | subagent/shell | **DESIGNED** (VERIFIED: Claude Code only) | The builder is host-agent-driven and embeds no model; designed for any capable host, but only Claude Code has driven it end-to-end. V-14 verified Copilot/Codex as detached *workers*, not as builder-driving orchestrators. |
| Cursor CLI + per-host orchestrator matrices | shell | **DESIGNED** | `worker_cmd` is CLI-agnostic; Cursor + the cross-host matrix are v0.2 |

The in-session timer (rung 1) is reliable *as a timer* but the resumed turn
has a host-side fragility — see the safety tick.

## Deploy rung 1 with a safety tick (recommended)

Because ticks are idempotent and a per-run-dir lockfile serializes concurrent
ticks, **redundant ticking is safe by construction.** So pair a rung-1
(in-session timer) run with a low-frequency **external safety tick** —
cron/scheduler or a second terminal running `arunner-ticker --once <run-dir>`
at roughly 3× the plan cadence against the same run directory. While the
in-session timer is alive, safety ticks are cycle-only no-ops; if the timer's
turn dies, the safety tick rescues the run within one safety interval, with no
detection logic and no operator nudge. (FR-26a)

**Why this matters (the honest paragraph).** In-session autonomous loops have
a host-side failure mode we root-caused on 2026-06-12 (Claude Code 2.1.174,
4 observed drops). The wakeup timer itself is **reliable** — it fired 4/4. The
failure is in the resumed turn: it intermittently serializes its first tool
call into the *text* channel as literal `<invoke …>` markup instead of a real
tool call; when the turn ends cleanly on that, the host injects no retry and
the loop dies silently until a human nudges it. (When the host *does* flag the
malformed call, it injects a retry and the loop self-heals — 4/4 of those
survived.) Context compaction was **refuted** as the cause. The safety tick
sidesteps the whole class because an external `--once` tick is independent of
the in-session turn. Upstream issue: **anthropics/claude-code#67945**
(filed 2026-06-12).

---

## A run is a directory

`--init` scaffolds a timestamped run directory; everything about the run
lives there (FR-4):

```
<run-dir>/
  plan.json              snapshot of the plan
  harness_status.json    the live state machine (cycle counter, per-run state, counts)
  queue/                 jobs not yet dispatched (+ per-job prompt files in shell mode)
  claimed/               in-flight jobs (+ <job>.lock with PID for shell workers)
  results/               one terminal result record per finished job
  run-NN/                one per plan entry:
    manifest.json          task id, target, dispatch mode, (optional) heartbeat_path
    heartbeat.ndjson       the worker's append-only progress log
  harness_tick.log       per-tick diagnostics
  .tick.lock             concurrent-tick serialization (E1)
```

A completed run directory is a **self-sufficient audit record** — final
status, every heartbeat line, every result, the plan snapshot. No chat
scrollback required (NFR-9, FR-28).

### The status table (the UI)

Every tick prints a pure-ASCII table — per-run state, the worker's current
activity label, last heartbeat status and age, plus aggregate counts and the
next-tick time:

```
Run-Dir: 20260612T0500Z (cycle 4)
--------------------------------------------------------------------------------------
RUN  REPO                  MODE    STATE        ACTIVITY        LAST-HB      HB-AGE
01   /repos/service-a      shell   completed    -               COMPLETED    1m12s
02   /repos/service-b      shell   running      2:generation    IN_PROGRESS  0m18s
03   /repos/service-c      shell   LAUNCH-FAIL  -               -            -
--------------------------------------------------------------------------------------
Queue: 0  Claimed: 0  Running: 1  Stalled: 0  Completed: 1  Failed: 1
LAUNCH-FAIL: no heartbeat received within launch grace - check worker-side launch: auth, helper availability, paths.
Next tick in 5 min
```

States: `queued → claimed → running → completed | failed`. `stalled` (a
heartbeat older than the threshold) is non-terminal and recoverable. A job
that's claimed but never heartbeats past the launch grace becomes
`LAUNCH-FAIL` (displayed; `auth_or_launch_failed` on disk) and carries a
diagnostic hint in both the result record and the table (FR-21b).

## Stop and resume

- **Stop:** drop a file named `STOP` in the run directory. The next tick sees
  it, changes nothing, and exits cleanly — a race-free shutdown that never
  interrupts the agent mid-action. In-flight detached workers run to their own
  terminal states (documented orphan behavior; no kill in this release). (UC-3)
- **Resume:** point any fresh session at the existing run directory (skip
  `--init`), or run `arunner-ticker --once <run-dir>` — or just delete the
  `STOP`. Disk state resumes the loop; at most one cycle increment beyond the
  interruption, zero duplicated work. (UC-4)

---

## Lineage

The harness was built as the **Quality Playbook's** test harness — replacing
a ~10,000-line Python subprocess harness, deleted 2026-06-11 — but its core
is payload-agnostic: the validated end-to-end runs orchestrated stub workers
with zero Quality-Playbook involvement. It's extracted here because *a job is
anything that appends JSON lines to a file* is a general contract, not a
quality-tooling one. The Quality Playbook keeps a vendored copy with a
lineage note. (See the [Quality Playbook](https://github.com/andrewstellman/quality-playbook).)

*Naming: developed under the working name **wakecycle** (the abandoned 0.0.1
PyPI/npm reservations are under that name); renamed to **arunner** — agent
runner — at v0.1.0.*

## License

Apache-2.0. No network calls of its own, no telemetry, no shell-out except
the `worker_cmd` templates you declare (NFR-11).
