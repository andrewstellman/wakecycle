# Planned: FR-74 (continue-past-stall) + FR-73 (output-activity liveness) + run-observability roadmap

*Roadmap doc, drafted 2026-06-22, **addended 2026-06-23** after the gen-007 widenet baseline run actually HALTed on stalled workers. Source: 58 QPB-on-secbench2 jobs, `mode: agent` → subagent dispatch, pool 2, Opus. Execute AFTER that run finishes — don't perturb a running orchestrator. Everything here goes through the established arunner change process (REQUIREMENTS.md → instruction → Claude Code worker → self-Council → operator lands), **NOT a direct Cowork edit** (SDLC.md). Numbers are provisional: **FR-72 is in-flight** on instruction `005-fr72-subagent-liveness-rederive.md` and not yet in REQUIREMENTS.md, so the firm next-free FRs are **FR-74** (the run-killer — top priority) and **FR-73**; secondary items take next-free numbers at instruction time. **FR-74 is the highest-priority item here — it is the bug that actually halted a 58-job run with 43 jobs unstarted.***

---

## Context — what the gen-007 run surfaced

A 58-repo QPB baseline run dispatched as in-session subagents (pool 2). It ran **correctly** start to finish, but three things made it hard to operate, and two of them repeatedly read as "is it stuck?" when nothing was wrong:

1. **Quiet-heartbeat workers looked dead.** Two Opus QPB workers (`wn-go-09-zitadel`, `wn-go-12-source-controller`) emitted a heartbeat early, then went silent for 15–35 min while doing the full six-phase audit — `source-controller` heartbeat once at `1:exploration` then nothing for ~34 min while writing **26 files in the last 5 minutes** and reaching its completion artifacts. `HB-AGE` climbed toward the `HUNG?` threshold even though the worker was plainly alive. The only reliable liveness check was a manual `find <repo>/quality -mmin -2`.
2. **Wave-gating idled freed slots.** With synchronous subagent dispatch the orchestrator blocks until the *whole* dispatched batch returns, so a fast job's slot sits idle until its slower wave-mate finishes (e.g. `shell-quote` done in 12 min waited ~25+ min on `zitadel`). The persisted `harness_status.json` also froze at the wave's cycle because the engine can't tick while the orchestrator is blocked.
3. **Display markers needed a decoder ring.** `running*` / `completed*` (the `_LIVE_MARKER`, tick.py:136), `HUNG?`, and an empty `TOKENS` column were all correct but unexplained at the point of use.

None of these is a correctness bug. (1) is the gap **FR-72 does not close** (see below); (2) is an accepted property of subagent dispatch that FR-59/FR-71 already mitigate for *display*; (3) is UX. This doc proposes the smallest set of changes that make a long subagent run **legible** without weakening any invariant.

## How this relates to FR-72 (read first — do not duplicate it)

Instruction `005-fr72-subagent-liveness-rederive.md` (FR-72, in-flight) fixes the **lifecycle** half of the liveness problem: a subagent with no heartbeat past `launch_grace` becomes a **non-terminal `NO-HEARTBEAT` advisory** (slot held), a generous hard cap (720 min) reclaims a genuinely hung slot, and the engine emits `STARTING` on the subagent's behalf so `claimed→running` advances. That is exactly right and it removes the *false-fail* risk.

What FR-72 deliberately does **not** add is a **positive signal that a quiet worker is doing real work.** The lineage doc (`PLANNED_FR61_FR62.md`) is explicit that the engine has "no awareness of a worker's actual output — only its heartbeat," and it ranks *result-artifact reconciliation* as the least-preferred option **for lifecycle decisions**, to protect the invariant "doneness from the declared status field, never from parsing output." FR-73 respects that line by being **display-only**: it never drives a state transition. It is the operator-facing complement to FR-72's engine-facing fix.

---

## FR-74 — A stalled worker must not wedge/HALT the run: reclaim its slot and continue (TOP PRIORITY)

### The bug (observed 2026-06-23, gen-007 widenet baseline run)
A 58-job pool-2 subagent run **HALTed** — `journal.ndjson` tick 18: `"verdict": "HALT:stalled"` — with **43 jobs never started** and only 8 of 14 touched repos cleanly finished. Two workers had gone stale (`defu` heartbeated through Phase 2 then went silent; `goshs` similarly), and the engine stopped the entire batch.

### Root cause (verified in `tick.py`)
- A run with no heartbeat past `stall_threshold_minutes` (`tick.py:1544`, default 45 min) is marked **`stalled`**, which is **NON-terminal and NON-killable in the MVP** (`tick.py:32`; FR-55 "the non-killable wedge; operator out: CANCEL").
- A `stalled` run still counts toward **`inflight`** (`tick.py:1465`), so it **permanently pins its pool slot**.
- `_halt_reason` (`tick.py:1460-1470`) returns `"stalled"` when **`not progressing and not free_slot and any(stalled)`** — i.e. every non-terminal run is stalled and `pool − inflight == 0`, so no queued job can dispatch. At pool 2, the moment both slots hold stalled runs the queue starves and the run HALTs.

So a single hung worker per slot is enough to wedge and **halt an entire unattended batch.** This is correct behavior for the interactive single-worker MVP it was designed for; it is wrong for a multi-job pool.

### Why it's not "working as designed" (lineage)
arunner already commits to *not letting one worker false-kill a run* — **FR-40** gives shell workers launch liveness, **FR-72** (in-flight) gives subagent workers launch liveness + a hard-cap reclaim. Both target **launch** (never-heartbeated). Neither covers a **mid-run stall** (heartbeated, then went stale past `stall_threshold`) — and FR-72's 720-min hard cap is far too slow to free a slot before the pool wedges. FR-74 extends the same already-blessed "one worker can't kill the batch" invariant to the mid-run-stall case.

### Fix — layers (to be Council'd)
1. **Reclaim a stalled slot (make `stalled` terminal-after-grace).** Add `stall_reclaim_minutes` (plan field; default ≫ `stall_threshold_minutes` but ≪ FR-72's 720-min cap — e.g. 2-3× the stall threshold). A run `stalled` past the reclaim threshold transitions to a **terminal** state (`abandoned`, or `failed` with `reason: stalled`), dropping out of `inflight` so `free_slot` opens and the queue dispatches. **This is the continue-past-stall guarantee: a hung worker costs its own job, not the batch.** Lifts the `tick.py:32` non-killable-MVP limitation for the stall path specifically.
2. **Optional retry-once.** `stall_retries` (default 1): a reclaimed stalled job MAY be requeued once before being abandoned, so a transient hang gets a second chance; a second stall is terminal. (Council: requeue vs. abandon as the default.)
3. **Reserve `HALT:stalled` for the genuinely-unrecoverable wedge.** With (1), the `_halt_reason` stalled branch (`tick.py:1468-1470`) only fires when reclamation is disabled or a stalled run can't be reclaimed — HALT becomes the rare last resort, not the default response to any pool-saturating stall.
4. **Compose with FR-72, don't duplicate it.** FR-72 = *launch* liveness (advisory + 720-min hard cap, keyed on `launch_grace`). FR-74 = *mid-run* reclaim (keyed on `stall_threshold`/`stall_reclaim`). Shared goal (free a slot so the engine isn't wedged), different thresholds; FR-74 is the fast path that prevents the wedge FR-72's generous cap allows.

### Display
A reclaimed stalled run shows a terminal `STALLED→ABANDONED` (or `FAILED:stalled`) row; the run continues; the status footer notes `N stalled-reclaimed`. Cross-reads naturally with FR-73's `OUT-AGE` (a run with stale heartbeat **and** stale output is a real stall safe to reclaim; stale heartbeat + fresh output is the false alarm that must NOT be reclaimed — see the gen-007 zitadel/source-controller cases).

### Route
Instruction → worker → **3-panel self-Council** (A: state-machine — `stalled→terminal` transition correct, slot freed, no false-completion of a still-live worker; B: regression — `HALT:stalled` still reachable for the *true* unrecoverable wedge, shell parity, `CANCEL` still works, no double-dispatch on requeue, FR-72 launch path unchanged; C: tests). **Pin the gen-007 incident as the load-bearing regression test:** a pool-2 run with 2 stalled workers and a 40+ job queue must **drain the queue** (continue-past-stall), never return `HALT:stalled`. Add FR-74 + US/UC + a §9 row; note the FR-40/FR-72 lineage.

---

## FR-73 — Output-activity freshness (`OUT-AGE`) in the read-only status table

### The gap
The status table's only liveness signal is `HB-AGE` (age of the newest heartbeat). When a worker is alive but heartbeat-quiet (the common case for a long synchronous QPB subagent inside a heavy phase), `HB-AGE` grows and trends toward `HUNG?` while the worker is visibly writing files. The operator has no in-table way to tell "quiet because hung" from "quiet because heads-down working," and falls back to a manual filesystem scan.

### The proposal
Add an **`OUT-AGE`** column to the shared renderer — the age of the **most recent write under the worker's output area** (default: the run's `target_repo` working tree, or a per-entry/plan-configurable `output_globs`, scanning newest-mtime only, bounded + cheap). Render it next to `HB-AGE` in `_format_table` (tick.py:2551; header at :2559–2560, between `HB-AGE` and the FR-65 `TOKENS` column). Because the FR-59 monitor and FR-71 TUI reuse `_format_table` (no forked renderer), the column appears in the engine table, `arunner monitor`, and `arunner tui` from one change.

### Why it's safe (invariants preserved)
- **Display-only, never lifecycle.** `OUT-AGE` is rendered, never read by `_advance`/`_dispatch`/`_terminal_status_of`. Doneness stays "the declared terminal status," per FR-72 and the FR-61/62 lineage. (Pin this with a mutation test: deleting the lifecycle code must still bite; deleting `OUT-AGE` must change only the rendered table.)
- **Read-only + decoupled.** Same property as FR-59/FR-71: it only *reads* mtimes; it writes nothing, takes no `.tick.lock`, advances no tick. The mtime scan lives in the pure-stdlib data layer the monitor/TUI already share.
- **Cheap + bounded.** Newest-mtime via a shallow scan with a file-count/age cap and a per-render cache; never a full recursive walk of a huge repo every refresh. (Council item: exact scan bound + whether to cap depth or use a sentinel dir like `quality/`.)

### Operator value
`HB-AGE 34m / OUT-AGE 40s` instantly reads as "alive, just quiet on beats" — the single most repeated false-alarm in the gen-007 run, closed in the table itself. Pairs with FR-72: FR-72 keeps the slot held and non-terminal; FR-73 shows *why* that's correct.

### Route
Instruction → worker → **3-panel self-Council** (A: display-only/no-lifecycle-coupling + invariant; B: regression — renderer parity across engine/monitor/TUI, scan cost bounded, no new dependency in the stdlib engine; C: test sufficiency — mutation pins on "not a lifecycle input" + "newest-mtime correct"). Add **FR-73 + a US/UC + a §9 row** to REQUIREMENTS.md (next-free US/UC; do not reuse numbers — cf. the instruction-005 renumbering note). Sequence **after** FR-72 lands (it touches the same `_format_table`/dispatch region; rebase on it).

---

## Secondary item A — bounded heartbeats so `HB-AGE` is trustworthy (worker-prompt convention)

### The gap
FR-72 Layer C adds the convention that a subagent prompt should emit `STARTING` first and a terminal last. That fixes the *endpoints*; it does not bound the **gap in between**. The gen-007 QPB workers honored neither a phase-boundary nor a timed beat, so `HB-AGE` was uninformative mid-run.

### The proposal
Extend the worker-prompt convention (TOOLKIT note) so a long subagent worker emits a heartbeat **at every phase boundary and at least every N minutes (timed keepalive)** even when heads-down — the FR-40 `heartbeat.py wrap` shell adapter already does the equivalent ("`STARTING` then periodic `IN_PROGRESS` beats") for shell workers; this gives subagent workers the same cadence by convention. Primary implementation is **QPB-side** (the QPB skill's phase flow should beat at each phase transition + a timer), routed through QPB's own change process; arunner's contribution is the TOOLKIT convention + optionally an engine-side keepalive note for `mode: agent` plans. This makes `HB-AGE` meaningful and reduces reliance on FR-73's `OUT-AGE` to the genuinely-pathological case.

### Route
TOOLKIT convention note (arunner instruction, small). The QPB worker-prompt change is a **separate QPB-side** item (diagnosis → QPB Claude Code → Council), referenced here, not landed from arunner.

---

## Secondary item B — make the subagent vs shell concurrency tradeoff a deliberate, easy choice

### The reality (not a bug)
arunner's engine **already does rolling refill** — "pool slot frees on any terminal" (tick.py:29 diagram), and `_dispatch` claims `pool_size − inflight` each tick (tick.py:2353, the `inflight >= pool_size` gate at :2379), with FR-37 honoring a lowered pool "as slots drain." Shell-mode realizes it: the ticker `Popen`s workers as background processes (ticker.py:109) and reaps/refills each freed slot independently. **Subagent-mode cannot**, because in-session `Task` is synchronous and blocks the orchestrator from ticking mid-batch — so it advances in waves gated by the slowest job in each batch. This is the same C-6 property FR-59/FR-71 were built around.

### The proposal (ergonomics + docs, likely no new engine FR)
1. **Document the tradeoff** where an operator chooses a plan's dispatch: subagent = simplest (one session, inherited model/auth) but wave-gated; shell = true rolling refill but each worker is a headless process. A short decision note in TOOLKIT/REQUIREMENTS §dispatch.
2. **Lower the barrier to shell-mode for QPB-style workers**: ship an example `dispatch_mode: "shell"` plan + a `worker_cmd` wrapper that runs a QPB audit headlessly (`claude -p` with the right flags — `--max-turns`, `--dangerously-skip-permissions`, `--model`, the `{TARGET_REPO}`/`{HEARTBEAT_PATH}` tokens), so an operator who wants rolling concurrency on a big batch can pick it without hand-rolling the invocation. (`examples/` already exists.)
3. **Optional, Council-gated:** consider whether `mode: agent` plans should warn at preview when `pool_size > 1` that subagent dispatch is wave-gated (set expectations up front).

### Route
Docs + an `examples/` plan + wrapper (arunner instruction). No engine behavior change unless the Council wants the preview warning (small FR then).

---

## Secondary item C — small display-clarity wins (fold into FR-59/FR-71 follow-ups)

- **Legend / glossary** for the table markers at the point of use: `*` = live (heartbeat ahead of the last reaped tick, tick.py:136); `HUNG?` = claimed past grace with no fresh heartbeat (and, post-FR-73, cross-read with `OUT-AGE`); the FR-72 `NO-HEARTBEAT` advisory.
- **"Engine blocked / last tick N ago"** line: the FR-59 monitor already shows an as-of-last-tick freshness line; make it say *why* counts are frozen when the orchestrator is mid-subagent (cross-reads with `OUT-AGE` to show the run is nonetheless advancing).
- **`TOKENS` populated in subagent mode**: the FR-65 `TOKENS` column (tick.py:2595 `_run_tokens`) showed `-` for these subagent runs. Confirm whether per-worker token capture is wired for subagent dispatch; if not, a small FR-65 follow-up gives cost visibility (useful for usage-budgeted runs).

### Route
These attach to the FR-59/FR-71 renderer and the FR-65 token path; bundle as a single "observability polish" instruction after FR-73, or fold into FR-73's renderer touch if cheap.

---

## Suggested sequencing

1. **FR-72** (already in flight) — lands first; everything else rebases on it.
2. **FR-74** (continue-past-stall) — **top priority.** It's the only item here that *halts a run*; until it lands, any long pool run is one slot-saturating stall away from a dead batch. Smallest behavior change with the biggest reliability payoff.
3. **FR-73** (`OUT-AGE`) — closes the repeated false-"stuck" alarm, and is the signal FR-74 should consult to avoid reclaiming a quiet-but-working worker. Do soon after FR-74.
4. **Secondary A** (bounded heartbeats) — mostly QPB-side; makes `HB-AGE` trustworthy so `OUT-AGE` is the backstop, not the primary.
5. **Secondary B/C** (shell-mode ergonomics + display polish) — as appetite allows.

## Cross-cutting acceptance: seamless stop/restart (carried from the 2026-06-23 run)

The run_playbook wrapper that replaced the halted arunner run set a robustness bar the new arunner must **match**: it was stopped mid-run (33/58 done, on a usage-limit boundary) and resumed the next day with one command — zero repeats, zero loss. Make this a first-class acceptance property, not an emergent accident:

- **Stop anytime, at a safe boundary, cleaning up workers.** A STOP (FR-10) halts without corruption and frees/terminates in-flight workers so none keep burning quota. Subagent mode: stop dispatching + let the current **bounded** turn end (stop latency ≤ the bound — another reason bounding is load-bearing). Shell mode: CANCEL (FR-39) + dead-PID reap. The wrapper's equivalent was `Ctrl-C` + `run_playbook --kill`; arunner needs the same "stop and clean up" in one move.
- **Resume = re-run the same launch, derived from target state — not the run-dir.** Resuming must NOT require the original run-dir or a special verb. Re-pointing the plan must re-derive "what's left" from each target's **done-check (planned FR-76)**: skip completed targets, resume in-flight ones (resumable workers), redo the rest. This is the wrapper's killer property (`bash run_rest.sh` again *is* the resume). arunner's current `resume` is run-dir-bound; elevate the done-check so **target state** drives resume — so a lost/rotated run-dir, a new machine, or a fresh session all resume identically. Pair with idempotent dispatch (FR-6 claim-lock) so re-launching a still-live run never double-dispatches.
- **Survive session/orchestrator death.** Disk-truth already gives "continue this run via one tick"; done-check + resumability extend it to "re-derive from the targets," which is strictly more robust.
- **Regression pin:** reproduce 2026-06-23 exactly — stop a 33/58-complete batch, relaunch, assert it runs the 25 remaining only (zero repeats, zero loss; a target written partway is redone, not skipped).

## Provenance / invariants this doc must not break (for the reviewers)
- Doneness is the declared terminal status, never parsed output (FR-61/62 lineage, FR-72). `OUT-AGE` is **display-only**.
- The monitor/TUI never fork the renderer (FR-71): one `_format_table`, three consumers.
- The stdlib engine takes no new dependency (NFR-3); any scan lives in the pure-stdlib data layer.
- Single-trunk: short-lived branch off `main`, self-Council to SHIP, **operator lands** (SDLC.md). This doc is a roadmap, not an implementation.
