# Run-robustness design — FR-72 → FR-77 (in-session subagent dispatch)

*Consolidated design doc. **Supersedes** `docs/archive/PLANNED_FR73_run_observability.md` and `docs/archive/PLANNED_FR75_supervised_subagent_model.md`. Drafted from the gen-007 widenet incident + the 2026-06-24 design brainstorm; consolidated 2026-06-24. This is the durable robustness layer that lets arunner drive long multi-job runs over **interactive agent sessions** without a hung worker wedging the batch.*

*Process discipline (`SDLC.md`): everything here lands via `REQUIREMENTS.md` → numbered instruction → Claude Code worker → mandatory 3-panel self-Council → operator lands. This is a **roadmap/design doc, not an implementation**, and **not a direct Cowork edit to engine source**. FR/US/UC numbers are provisional — assigned next-free at instruction time per the renumbering discipline (cf. the instruction-005 renumbering note); FR-72…FR-77 are the working labels here. Engine line-cites are as of pre-FR-72 `main`; the worker reconfirms exact lines on `main` (post-FR-72-merge) before touching them.*

---

## 1. Why this exists — the gen-007 forcing incident

A 58-case QPB-on-secbench2 security benchmark was split into arunner batches dispatched as in-session subagents (`mode: agent`, pool 2, Opus). One 15-job batch **HALTed** — `journal.ndjson` tick 18: `"verdict": "HALT:stalled"` — with **43 jobs never started** and only 8 of 15 touched repos cleanly finished. Two workers (`wn-jsts-05` defu, `wn-go-01` goshs) had gone stale; the engine stopped the whole batch. The run was rescued by abandoning arunner for a `run_playbook` shell wrapper.

Three things the run surfaced (only the first is a correctness bug):

1. **A stalled worker wedges and HALTs the batch (the run-killer).** Root cause in `tick.py` on `main`: a run with no heartbeat past `stall_threshold_minutes` (default 45) is marked **`stalled`**, which is **non-terminal and non-killable in the MVP** (`:32`, `_TERMINAL_STATES` `:109`) yet still counts as **`inflight`** (`_INFLIGHT_STATES` `:110`, recounted `:1464-1465`), so it permanently pins its pool slot. `_halt_reason` (`:1460-1470`) returns `"stalled"` when `not progressing and not free_slot and any(stalled)` — at pool 2, the moment both slots hold stalled runs the queue starves and the run HALTs. **One hung worker per slot halts an entire unattended batch.** → **FR-74**.
2. **Quiet-heartbeat workers look dead but aren't.** Two Opus workers (`zitadel`, `source-controller`) emitted a heartbeat early then went silent 15–35 min while doing the full six-phase audit — `source-controller` beat once at `1:exploration` then nothing for ~34 min **while writing 26 files in the last 5 minutes**. `HB-AGE` climbed toward `HUNG?` though the worker was plainly alive; the only reliable check was a manual `find <repo>/quality -mmin -2`. → **FR-73** (the `OUT-AGE` signal) + the **output-fresh reclaim guard** inside FR-74.
3. **Wave-gating idles freed slots.** Synchronous subagent dispatch blocks the orchestrator until the whole dispatched batch returns, so a fast job's slot sits idle until its slowest wave-mate finishes, and `harness_status.json` freezes at the wave's cycle. → the **bounded-turns** model in FR-77 (plus an accepted property documented as a dispatch tradeoff).

### The deeper forcing constraint (reframes FR-77)

The wrapper's robustness came from the OS owning each worker process (`claude -p`): unambiguous exit codes, failure isolation, kill/bound a hang, retry-by-re-run. **But headless `claude -p` automation is going away** — which is *why* arunner exists: the durable, policy-safe path is each worker living **inside an interactive agent session** (Claude Code, and ideally Codex/Copilot too). So arunner must recover the four process-supervisor properties **without a killable OS process**:

| property | shell mode got it from | in-session, we get it from |
|---|---|---|
| unambiguous pass/fail | process exit code | **terminal-from-Task-outcome** (FR-77) |
| failure isolation | separate process | orchestrator try/except + continue (FR-74, generalized) |
| bound / kill a hang | SIGKILL | **bounded worker turns** (FR-77) — a hang self-terminates |
| retry | re-run the wrapper | **per-job retry policy** (FR-75) |

The achilles heel already found: **leaning on worker heartbeats.** The model below leans on the **Task outcome** and the **worker's output (`OUT-AGE`)** instead.

---

## 2. Status tiers (read before sequencing)

| Item | What it is | Status |
|---|---|---|
| **FR-72** | subagent launch-liveness (advisory `NO-HEARTBEAT` past grace, 720-min hard-cap reclaim, `STARTING` emit; covers every subagent path) | ✅ **implemented, NOT merged** — branch `fr72-subagent-liveness`, commit `366a5bd`, 463 tests ×3, self-Council unanimous SHIP. **Awaiting operator merge to `main`.** |
| **FR-74** | continue-past-stall: reclaim a stalled slot so the batch continues — *only when output is also stale* | 📝 **spec'd in instruction 006**, not run. The run-killer fix; top priority. |
| **FR-73** | `OUT-AGE` output-activity column (display-only); the freshness signal FR-74's guard consumes | 📝 **spec'd in instruction 006** (ships with FR-74), not run. |
| **FR-76** | target-state done-check + idempotent resume (re-run = resume, run-dir-independent) | 🧠 planned (this doc). High value, fairly self-contained. |
| **FR-75** | per-job retry policy (`max_attempts`, resume-not-restart) | 🧠 planned (this doc). Small, high value. |
| **FR-77** | supervised-bounded-subagent model (the spine): terminal-from-Task-outcome + bounded turns + `liveness-check` | 🧠 planned (this doc). The big one; gated on a host-capability probe. |

Plus secondary items (§6): bounded-heartbeat worker convention, shell-mode ergonomics, display-clarity polish.

---

## 3. FR-72 — subagent launch-liveness (landed, unmerged)

Re-derived fresh on the post-format-collapse engine (instruction 005). A subagent with no heartbeat past `launch_grace` becomes a **non-terminal `NO-HEARTBEAT` advisory** (slot held); a generous hard cap (`subagent_hard_cap_minutes`, default 720) reclaims a genuinely hung slot; the engine emits `STARTING` at dispatch so `claimed→running` advances. Covers **every** subagent path — single-prompt, `mode:pipeline` agent steps, reasoning-gate judges; shell dispatch unchanged. It fixes the **launch** half (never-heartbeated) and removes the *false-fail* risk. It deliberately does **not** add a positive "this quiet worker is doing real work" signal (that's FR-73), and its 720-min cap is far too slow to free a mid-run-stalled slot before the pool wedges (that's FR-74).

---

## 4. Instruction 006 — FR-74 + FR-73 (continue-past-stall + OUT-AGE)

These two **ship together in one instruction** because a time-only reclaim would abandon the very quiet-but-working workers it's meant to protect (the gen-007 `source-controller` case). Lands on `main` after FR-72 (single-trunk).

### FR-73 — `OUT-AGE` output-activity freshness (build first; FR-74's guard depends on it)

- **Data layer (shared, pure-stdlib):** age of the most-recent write under a run's output area (default `target_repo` tree, or a per-entry/plan `output_globs`), **newest-mtime only, bounded + cached** (file-count/age cap; never a full recursive walk per render). Lives in the stdlib data layer the FR-59 monitor / FR-71 TUI already share (NFR-3).
- **Display:** an `OUT-AGE` column in `_format_table` (between `HB-AGE` and the FR-65 `TOKENS` column). One renderer → it appears in the engine table, `arunner monitor`, and `arunner tui` (FR-71: no forked renderer).
- **Invariant — display-only:** the rendered `OUT-AGE` is **never** read by `_advance`/`_dispatch`/`_terminal_status`. Doneness stays "the declared terminal status." (FR-74's guard consumes the *data-layer mtime signal* directly — a data read, not the rendered column; keep the distinction sharp so the display-only invariant stays literally true.) Pin with a mutation test.

### FR-74 — reclaim a stalled slot and continue

- **`stall_reclaim_minutes`** (plan field; default ≫ `stall_threshold_minutes` but ≪ FR-72's 720-min cap — e.g. 2–3× the stall threshold; calibrate from Step-0). A run `stalled` past it **AND whose output is also stale** (OUT-AGE past a freshness window) → terminal **`abandoned`**, drops out of `_INFLIGHT_STATES` → `free_slot` opens → the queue dispatches. **A stalled run whose output is FRESH is NOT reclaimed** (the quiet-but-working guard). This is the continue-past-stall guarantee: a genuinely-hung worker costs its own job, not the batch.
- **`abandoned`, not `failed`** (honest: we gave up waiting; we did not observe a failure). Optional **`stall_retries`** (default 1): a reclaimed job MAY requeue once before abandon (Council picks the default).
- **Reserve `HALT:stalled`** for the genuinely-unrecoverable wedge — the `_halt_reason` stalled branch fires only when reclamation is disabled or a stalled-but-output-fresh run can't be reclaimed.
- **Subagent reclaim is an ACCOUNTING free, not a kill** (`:32`): it frees the engine's slot but cannot stop the in-session subagent, which may later write a terminal line — handle idempotently (**a reclaimed-as-`abandoned` run that later emits `completed`/`failed` must not resurrect, double-count, or double-dispatch**). Concurrency may briefly exceed `pool_size` in subagent mode (acceptable; FR-74 is cleanest in shell mode where the process is killable).
- **Compose with FR-72, don't duplicate:** FR-72 = launch liveness (keyed on `launch_grace`); FR-74 = mid-run reclaim (keyed on `stall_threshold`/`stall_reclaim`). FR-74 is the fast path that prevents the wedge FR-72's generous cap allows.

### Step-0 calibration (do FIRST — it decides the design)

From the gen-007 `journal.ndjson`, establish whether `defu`/`goshs` had **stale output** at HALT (genuinely hung → reclaim is right) or **fresh output** (alive but heartbeat-quiet → the load-bearing fix is the OUT-AGE-aware *guard*, not the reclaimer). **Calibration source (local to the arunner repo):** `harness_runs/20260622T193505Z/` (the 15-job plan + defu/goshs manifests + queue) and `harness_runs/20260622T193939Z/journal.ndjson` (the 18-tick `HALT:stalled`); target-repo output mtimes are in the `secbench2_widenet` clones (`QPB/repos/secbench2_widenet/`). Record the finding in the output file.

### Regression pins (load-bearing)

- `test_pool2_two_stalled_with_queue_drains_not_halt` — the gen-007 pin: pool-2, both slots stalled past reclaim with output stale, 40+ queued → the queue **drains**, `_halt_reason` never returns `"stalled"`. Mutation: remove the reclaim ⇒ `HALT:stalled` ⇒ bite.
- `test_stalled_but_output_fresh_is_NOT_reclaimed` — the false-alarm pin: heartbeat stale past reclaim, OUT-AGE fresh → held, NOT abandoned. Mutation: drop the guard ⇒ a live worker is abandoned ⇒ bite.

---

## 5. The durable layer — FR-76, FR-75, FR-77

### FR-76 — target-state done-check + idempotent resume

**Gap:** arunner's idempotency is **tick-level** (crash-safe within a run-dir, FR-6), not **target-state-level**; a fresh plan re-run re-dispatches everything, and `resume` is run-dir-bound. **Fix:** a plan-declared per-job **`done_check`** (artifact-exists predicate | check command) evaluated **before dispatch**; a target already satisfying it is `completed`/skipped without re-running. So **re-running the same plan = resume, derived from target state** — survives a lost/rotated run-dir, a new machine, a fresh session. Composes with FR-6 (no double-dispatch of an in-flight job) and extends the FR-41 sentinel from in-run doneness to a pre-dispatch gate. The engine half of the stop/restart acceptance (§7). **Regression pin:** the gen-007 stop-at-33/58 → resume the 25 (zero repeats, zero loss; a target written partway is redone, not skipped).

### FR-75 — per-job retry policy

**Gap:** arunner has **no retry**; gen-007 had a **~20% transient abort rate** (`child runner exited 1`) that needed manual wrapper re-runs. **Fix:** `max_attempts` (+ optional backoff) per job; a `FAILED`/stalled-reclaimed job is **requeued up to `max_attempts`** before terminal-`FAILED`, and with resumable workers a retry **resumes** (doesn't restart). Optional transient-vs-fatal classification (don't burn retries on an auth/pre-flight error). Composes with FR-6 claim-lock (a requeue can't double-dispatch). Generalizes instruction 006's reclaim-then-retry-once into a policy.

### FR-77 — the supervised-bounded-subagent model (the spine)

Three primitives; the four supervisor properties fall out of them.

**Primitive 1 — Terminal-from-Task-outcome (the supervision contract).** The orchestrator already *holds* each subagent's result (Task is synchronous). Wrap every dispatch: **Task returns cleanly ⇒ write `COMPLETED`; Task throws ⇒ write `FAILED`.** Doneness from the outcome, **never** from whether the worker self-heartbeated — the in-session "exit code." Removes the false-stall class outright (gen-007's defu/zitadel/source-controller would be judged by their Task's return, not their silence).

**Primitive 2 — Bounded worker turns.** Every subagent runs **at most N turns / T minutes, then returns** (`worker_turn_budget` / `worker_time_budget`). A stuck worker self-terminates at the bound and returns (→ FAILED → retry) — the "kill a hang" substitute. **The bound *is* the orchestrator's tick period** (synchronous Task means the orchestrator can only act *between* Tasks; the bound gives it control every interval to reap, run liveness-checks, re-dispatch). Converts a permanent wedge into a series of short, decidable turns. **This is the linchpin host capability** — the host's subagent tool must cap a subagent and surface its failure as a catchable outcome.

**Primitive 3 — Resumable workers + `OUT-AGE` wedge classification.** Workers checkpoint to disk (QPB already does, via phase artifacts) so a re-dispatched worker **resumes**. The wedge classifier is **output activity (`OUT-AGE`, FR-73), not heartbeat**: no new disk bytes across K bounded turns ⇒ wedged; still writing ⇒ alive-but-slow. **Resumability makes a misjudgment cheap** — a wrong `wedged` call costs a resume, not lost work — which is what makes an AI making the call safe to ship.

**The `liveness-check` plan property.** Each job declares a `liveness-check`, **prose** or **command**, e.g.:

```jsonc
// prose: the orchestrator interprets it (given the current time + relevant mtimes)
"liveness-check": "Worker writes {\"heartbeat-time\": <epoch>}. If >5 min old, check whether files under quality/ are still being written; if no heartbeat AND no writes for >20 min, consider it wedged."
// command: the orchestrator runs it and interprets exit code + stdout
"liveness-check": "bin/check_heartbeat.sh"
```

Guardrails (load-bearing):

- **Classify, don't transition.** The check yields a **classification — `alive` | `wedged` | `uncertain`**; the **deterministic engine** owns the transition (abandon after K consecutive `wedged`; hold on `uncertain`). The AI supplies a *signal*, the engine's rules consume it (arunner's core principle).
- **Validated at startup, fail-fast.** Before any dispatch, every `liveness-check` must be actionable enough to yield the 3-way classification (prose: names the signal(s), location, thresholds, decision boundary; command: exists/executes + dry-run-interprets). Under-specified ⇒ **refuse to start**, with an explicit override that prints the failure mode being accepted ("this worker can't be wedge-detected → a hang pins its slot to the hard cap; at pool N that risks a wedge"). Two-stage: actionable-at-init (gate) then accurate-on-first-evaluation (warn loudly).
- **Runs at the boundary or out-of-band.** Can't evaluate mid-Task (suspended); runs at each bounded-turn boundary, or — cleaner — the **FR-59 read-only monitor** evaluates it out-of-band, writes the classification to disk, the engine reads it next tick.
- **Conservative + audited.** Hold/retry on `uncertain`; abandon only on clear no-progress; log a one-line "why I judged it wedged" (the evidence-ledger discipline).

**Wedge handling (expand/collapse at boundaries).** At each bounded-turn boundary, for unfinished workers: **made progress** (OUT-AGE fresh) ⇒ re-dispatch (resume), hold its slot, temporarily flexing the batch up (with a **ceiling** so the resource budget — the reason for a small pool — isn't blown); **no progress across K turns** ⇒ abandon (free the slot), safe because it's output-confirmed. A held worker finishing flexes the batch back down. (If a host ever offers non-blocking dispatch, the fully-live grow/shrink-mid-flight version drops in.)

**Tick / return-control modes.** Same engine, two triggers to "advance": **Autonomous** (`ScheduleWakeup` loop + the FR-26a external safety tick — the in-session timer is empirically fragile) for unattended runs; **Return-control** (one bounded batch → return control with the status table → operator or safety-tick says continue) — more robust, good for supervised runs. Either way the FR-59 monitor gives out-of-band status anytime.

**Portability / capability ladder.** Rests on the host subagent tool meeting **minimum criteria**: (a) bound a subagent and (b) surface a subagent failure as a catchable outcome. Probe per platform (Claude Code, Codex, Copilot) at startup in the existing capability-ladder framing. A host that **can't bound a subagent** is flagged **`supervision-degraded`**: fall back to FR-72's hard cap + operator CANCEL, and warn at startup. **Land FR-77 incrementally — the host-capability probe FIRST (it gates the rest), then the supervision contract + bounded turns, then the `liveness-check` + wedge-handling.**

---

## 6. Secondary items

- **A — bounded-heartbeat worker convention.** FR-72 fixes the endpoints (`STARTING` + terminal); it doesn't bound the gap. Extend the worker-prompt convention so a long subagent beats **at every phase boundary and ≥ every N minutes** even heads-down (the FR-40 `heartbeat.py wrap` shell adapter already does this for shell workers). Primary implementation is **QPB-side** (its phase flow beats per transition + a timer), routed through QPB's own change process; arunner's contribution is the TOOLKIT convention. Makes `HB-AGE` trustworthy so `OUT-AGE` is the backstop, not the primary.
- **B — shell-vs-subagent concurrency tradeoff.** Document it where an operator picks a plan's dispatch (subagent = simplest, one session / inherited auth, but wave-gated; shell = true rolling refill, headless processes). Ship an example `dispatch_mode: "shell"` plan + a `worker_cmd` wrapper so big batches can pick rolling concurrency easily. Optional Council-gated preview warning when `mode:agent` + `pool_size > 1`.
- **C — display-clarity polish.** A point-of-use legend for the markers (`*` = live, `HUNG?`, the FR-72 `NO-HEARTBEAT` advisory); an "engine blocked / last tick N ago" line that says *why* counts froze mid-subagent; confirm `TOKENS` capture in subagent mode (a small FR-65 follow-up if not). Bundle as one observability-polish instruction, or fold into FR-73's renderer touch.

---

## 7. Cross-cutting acceptance — seamless stop/restart

The `run_playbook` wrapper that replaced the halted run set the bar the new arunner must **match**: stopped mid-run (33/58, on a usage-limit boundary), resumed next day with one command, zero repeats / zero loss. Make it a first-class acceptance property:

- **Stop anytime, at a safe boundary, cleaning up workers.** A STOP (FR-10) halts without corruption and frees/terminates in-flight workers so none keep burning quota. Subagent: stop dispatching + let the current **bounded** turn end (stop latency ≤ the bound — another reason bounding is load-bearing). Shell: CANCEL (FR-39) + dead-PID reap.
- **Resume = re-run the same launch, derived from target state, not the run-dir** (FR-76's done-check): skip completed targets, resume in-flight ones, redo the rest — so a lost/rotated run-dir, a new machine, or a fresh session all resume identically. Pair with idempotent dispatch (FR-6) so re-launching a still-live run never double-dispatches.
- **Survive session/orchestrator death** — disk-truth already gives "continue via one tick"; the done-check extends it to "re-derive from the targets."
- **Regression pin:** reproduce 2026-06-23 — stop a 33/58-complete batch, relaunch, assert it runs the 25 remaining only (zero repeats / loss; a partway target is redone, not skipped).

---

## 8. Sequencing (full set; hard checkpoint after 006)

1. **FR-72** (already implemented) — operator merges → `main`, pushes + verifies, prunes the stale branches. Everything below lands on `main` (single-trunk: a short-lived branch per instruction → operator lands on `main`).
2. **Instruction 006 = FR-74 + FR-73** — the run-killer + `OUT-AGE`. **← HARD CHECKPOINT:** prove a pool-saturating stall **drains** instead of HALTing before going further.
3. **FR-76** (done-check / idempotent resume) — high value, self-contained; delivers seamless stop/restart.
4. **FR-75** (retry) — small, auto-heals the transient aborts.
5. **FR-77** (supervised-bounded model) — the big one; **host-capability probe first**, then land incrementally.
6. **Orientation-doc sync** — `README.md` / `DEVELOPMENT_CONTEXT.md` / `TOOLKIT.md`, as each concept lands (docs never claim more than what's landed).

Each step = one numbered instruction → worker → 3-panel self-Council → orchestrator independent verification → operator lands. Instructions are filed **just-in-time** (one at a time, verified between), not all up front.

---

## 9. Invariants the reviewers must hold the line on

- **Determinism in the engine.** The `liveness-check` *classifies*; the engine *transitions*. No freeform AI flipping run state.
- **Doneness from declared status/outcome, never parsed output** — except FR-76's `done_check`, an explicit operator-declared predicate. `OUT-AGE` is display-only.
- **NFR-3 stdlib engine.** Any AI/monitor evaluation lives in the orchestrator/monitor layer, never the engine; no new runtime dependency.
- **No forked renderer** (FR-71): one `_format_table`, three consumers.
- **Idempotency is sacred** (FR-6): no double-dispatch on requeue or reclaim-comeback.
- **Regression pins:** the gen-007 false-stall (zitadel/source-controller: heartbeat-silent while writing) and the gen-007 stop/restart (33/58 → resume the 25, zero repeats / loss).
- **Single-trunk, operator lands.** Short-lived branch/worktree, self-Council to SHIP, operator merges/pushes. This doc is a roadmap, not an implementation.

---

*Supersedes `docs/archive/PLANNED_FR73_run_observability.md` + `docs/archive/PLANNED_FR75_supervised_subagent_model.md`. When an FR here lands, its FR/US/UC/§9 rows go into `docs/REQUIREMENTS.md` at instruction time — `REQUIREMENTS.md` is the source of truth; this is the design that feeds it.*
