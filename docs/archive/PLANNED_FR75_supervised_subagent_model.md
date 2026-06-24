# Planned: FR-75 (retry) + FR-76 (idempotent resume) + FR-77 (supervised-bounded-subagent model)

*Roadmap doc, drafted 2026-06-24 from the gen-007 design brainstorm. The durable robustness layer for in-session subagent dispatch. **Builds on FR-72** (subagent liveness, on branch `fr72-subagent-liveness`) and **instruction 006** (FR-74 reclaim-and-continue + FR-73 `OUT-AGE`, written, not yet run). Lands on `1.1` after 006. Everything here goes through the established change process (REQUIREMENTS.md → instruction → runner → self-Council → operator lands), **NOT a direct Cowork edit** (SDLC.md). FR numbers are provisional — next-free after FR-72/73/74 is **FR-75**; assign US/UC at instruction time per the renumbering discipline.*

---

## The forcing constraint (read first — it reframes everything)

The gen-007 run was rescued by a `run_playbook` shell wrapper, whose robustness came from the OS owning each worker process (`claude -p`): unambiguous exit codes, failure isolation, the ability to kill/bound a hang, retry-by-re-run. **But `claude -p` (headless CLI automation) is going away** — which is *why* arunner exists: the durable, policy-safe path is for each worker to live **inside an interactive agent session**. And not just Claude Code — any agentic session whose subagent tool meets minimum criteria (Codex, Copilot, …). So arunner becomes a **platform-agnostic orchestration layer over interactive agent sessions**, and we must recover the four process-supervisor properties *without* a killable OS process:

| property | shell mode got it from | in-session, we get it from |
|---|---|---|
| unambiguous pass/fail | process exit code | **terminal-from-Task-outcome** (FR-77) |
| failure isolation | separate process | orchestrator try/except + continue (FR-74, here generalized) |
| bound / kill a hang | SIGKILL | **bounded worker turns** (FR-77) — a hang self-terminates |
| retry | re-run the wrapper | **per-job retry policy** (FR-75) |

The achilles heel we already found: **leaning on worker heartbeats.** The model below leans on the *Task outcome* and the *worker's output* instead.

---

## FR-77 — the supervised-bounded-subagent model (the spine)

Three primitives; the four properties fall out of them.

### Primitive 1 — Terminal-from-Task-outcome (the supervision contract)
The orchestrator already *holds* each subagent's result (the Task is synchronous). Wrap every dispatch: **Task returns cleanly ⇒ write `COMPLETED`; Task throws/errors ⇒ write `FAILED`.** Doneness comes from the outcome, **never** from whether the worker self-heartbeated. This is the in-session "exit code," and it extends FR-72's layer B from "emit `STARTING` at dispatch" to "**emit the terminal from the return**." It directly removes the false-stall class — gen-007's `defu`/`zitadel`/`source-controller` would be judged by their Task's return, not their silence. (Engine authority note: `auth_or_launch_failed` stays reachable only where the engine has authority; a subagent terminal now comes from the orchestrator that owns the Task, written to disk as the worker's terminal heartbeat.)

### Primitive 2 — Bounded worker turns
Every subagent runs **at most N turns / T minutes, then returns** (new plan fields, e.g. `worker_turn_budget` / `worker_time_budget`). Consequences:
- The orchestrator is **never permanently wedged** — a stuck worker self-terminates at the bound and returns (→ FAILED → retry). This is the "kill a hang" substitute.
- **The bound *is* the orchestrator's tick period.** Because in-session Task dispatch is synchronous, the orchestrator can only act *between* Tasks; bounding gives it control every bound-interval to reap, run liveness-checks, re-dispatch. (Want a status check every 5 min? Set the bound to 5 min.)
- It converts a "permanent wedge" into a **series of short turns** — the decision points the wedge handling needs.

This is the **linchpin host capability** (see Portability): the host's subagent tool must be able to cap a subagent and surface its failure as a catchable outcome.

### Primitive 3 — Resumable workers + `OUT-AGE` wedge classification
Workers checkpoint to disk (QPB already does, via phase artifacts), so a re-dispatched worker **resumes** rather than restarts. The wedge classifier is **output activity (`OUT-AGE`, FR-73), not heartbeat**: a worker producing no new disk bytes across K bounded turns is wedged; one still writing is alive-but-slow. **Resumability makes a misjudgment cheap** — a *wrong* `wedged` call costs a resume, not lost work, which is what makes the next part (an AI making the call) safe to ship.

### The `liveness-check` plan property (how the wedge call is made)
The judge is an AI orchestrator, so the wedge rule need not be hard-coded. Each job declares a **`liveness-check`**, either **prose** or a **command**:
```jsonc
// prose: the orchestrator interprets it (given the current time + relevant mtimes)
"liveness-check": "This worker writes a heartbeat like {\"heartbeat-time\": <epoch>}. If the heartbeat is >5 min old, check whether files under quality/ are still being written; if no heartbeat AND no file writes for >20 min, consider it wedged."
// command: the orchestrator runs it and interprets exit code + stdout
"liveness-check": "bin/check_heartbeat.sh"
```
Guardrails (load-bearing):
- **Classify, don't transition.** The `liveness-check` yields a **classification — `alive` | `wedged` | `uncertain`** — and the *deterministic engine* still owns the state transition (abandon after K consecutive `wedged`; hold on `uncertain`). This preserves arunner's core principle ("all the determinism in the engine; the agent is a relay, not the decider"): the AI supplies a *signal*, the engine's rules consume it.
- **Validated at startup, fail-fast.** Before any dispatch, the orchestrator checks every `liveness-check` is actionable enough to yield the 3-way classification (prose: names the signal(s), location, thresholds, decision boundary; command: exists/executes + dry-run-interprets). Under-specified ⇒ **refuse to start** (a non-actionable check is worse than none), with an explicit override that prints the exact failure mode being accepted ("this worker can't be wedge-detected → a hang pins its slot to the hard cap; at pool N that risks a wedge"). Two-stage: *actionable-at-init* (gate) then *accurate-on-first-evaluation* (warn loudly if the check can't find its signal on the first real run).
- **Runs at the boundary or out-of-band.** The orchestrator can't evaluate it mid-Task (it's suspended), so it runs at each bounded-turn boundary — or, cleaner, the **FR-59 read-only monitor** evaluates it out-of-band, writes the classification to disk, and the engine reads it on the next tick (keeps the "go read the output" cost off the critical path).
- **Conservative + audited.** Hold/retry on `uncertain`; abandon only on clear no-progress evidence; log a one-line "why I judged it wedged" (the evidence-ledger discipline).

### The wedge handling (the user's "expand/collapse," realized at boundaries)
At each bounded-turn boundary, for workers that didn't finish:
- **made progress** (`OUT-AGE` fresh) ⇒ alive-but-slow: **re-dispatch (resume)** — hold its slot, temporarily flexing the batch (the "expand around it"), with a **ceiling** so the resource budget (the reason for a small pool) isn't blown.
- **no progress across K turns** ⇒ confirmed wedged: **abandon** (free the slot). Safe *because* it's output-confirmed, not a heartbeat guess.
When a held worker finishes, the batch flexes back down ("collapse"). (If a host ever offers *non-blocking* subagent dispatch, the fully-live grow/shrink-mid-flight version drops in; bounded-turns is the realization until then.)

### Tick / return-control modes
Same engine, two triggers for "advance":
- **Autonomous** — `ScheduleWakeup` loop + the FR-26a external safety tick (the in-session timer is empirically fragile — the 4+ silent drops). For unattended runs.
- **Return-control** — run one bounded batch → return control with the status table → operator (or a safety tick) says continue. **More robust** (no dependency on the fragile timer); good for supervised runs.
Either way, the **FR-59 monitor** gives out-of-band status anytime — "ask for status" never requires the orchestrator to be free.

---

## FR-75 — per-job retry policy

**Gap:** arunner has **no retry** — a `FAILED`/abandoned job stays failed (`grep` of REQUIREMENTS + tick.py finds none). gen-007 had a **~20% transient abort rate** (`child runner exited 1`) that needed a manual wrapper re-run.

**Fix:** `max_attempts` (+ optional backoff) per job. A `FAILED` or stalled-then-reclaimed job is **requeued up to `max_attempts`** before going terminal-`FAILED`; with resumable workers a retry **resumes** (doesn't restart). Generalizes instruction 006's reclaim-then-retry-once into a policy. Optional transient-vs-fatal classification (a pre-flight/auth error shouldn't burn retries; a transient one should) — default: retry all up to the cap. This is the in-engine version of "recovery by re-run," made automatic.

**Route:** instruction → runner → 3-panel Council (state-machine: requeue can't double-dispatch (compose with FR-6 claim-lock); `max_attempts` honored; terminal-FAILED still reachable; resume-not-restart on retry). Add FR-75 + US/UC + §9.

---

## FR-76 — target-state done-check + idempotent resume

**Gap:** arunner's idempotency is **tick-level** (crash-safe within a run-dir, FR-6), not **target-state-level**. A fresh plan re-run re-dispatches everything; `resume` (cmd_resume) is **run-dir-bound** (it just re-ticks an existing run-dir). The wrapper's killer property — `bash run_rest.sh` again *is* the resume — is target-state-based and survives run-dir loss.

**Fix:** a plan-declared per-job **`done_check`** (artifact-exists predicate | check command) evaluated **before dispatch**; a target already satisfying it is marked `completed`/skipped without re-running. So **re-running the same plan = resume, derived from target state**, independent of run-dir survival — a lost/rotated run-dir, a new machine, or a fresh session all resume identically. Composes with FR-6 (don't double-dispatch an in-flight job) and with the FR-41 sentinel concept (extended from in-run doneness to a pre-dispatch gate). This is the engine half of the **stop/restart acceptance** in `PLANNED_FR73_run_observability.md`; the gen-007 stop-at-33/58-then-resume is its regression pin.

**Route:** instruction → runner → 3-panel Council (the done-check is consulted on *every* (re-)entry; partially-written targets are redone not skipped; never re-does a genuinely-complete target). Add FR-76 + US/UC + §9.

---

## Portability / capability ladder

The model rests on the host session's subagent tool meeting **minimum criteria**: (a) **bound a subagent** (turn/time cap so a hang self-terminates) and (b) **surface a subagent failure as a catchable outcome** (so terminal-from-outcome works). Probe these per platform (Claude Code, Codex, Copilot) at startup, in the existing **capability-ladder** framing. A host that **can't bound a subagent** is flagged **`supervision-degraded`**: a true no-progress wedge can still block its orchestrator, so fall back to the FR-72 generous hard cap + operator CANCEL, and warn the operator at startup. Dogfooding arunner as the *only* path across hosts is how these criteria get pinned.

---

## Suggested sequencing (after instruction 006 lands on `1.1`)

1. **006** (FR-74 + FR-73) — already spec'd; lands first on `1.1`. The `OUT-AGE` it adds is the wedge signal everything below consumes.
2. **FR-76** (target-state done-check / idempotent resume) — high value, fairly self-contained, delivers seamless stop/restart. Do early.
3. **FR-75** (retry) — small, high value; auto-heals the transient aborts.
4. **FR-77** (supervised-bounded-subagent model) — the big one: supervision contract + bounded turns + `liveness-check` + tick/return-control. Gated on the host capability probe. Land incrementally (the supervision contract + bounded turns first; the `liveness-check`/wedge-handling on top).

## Invariants the reviewers must hold the line on
- **Determinism in the engine.** The `liveness-check` *classifies*; the engine *transitions*. No freeform AI flipping run state.
- **Doneness from declared status/outcome, never from parsed output** — except the FR-76 `done_check`, which is an explicit, operator-declared predicate (not the engine guessing).
- **NFR-3 stdlib engine** — any AI/monitor evaluation lives in the orchestrator/monitor layer, not the engine.
- **Regression pins:** the gen-007 false-stall (zitadel/source-controller: heartbeat-silent while writing) and the gen-007 stop/restart (33/58 → resume the 25, zero repeats/loss).
- Single-trunk, operator lands. This doc is a roadmap, not an implementation.
