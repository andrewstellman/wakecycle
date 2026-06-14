# Panelist B — Grading Soundness & Buildability of the Acceptance-Test Mechanism

**Charter:** Soundness and buildability of the grading mechanism. Adversarial.
**Document under review:** `docs/ACCEPTANCE_TESTS.md`
**Verdict: REVISE-REQUIRED.**

The central claim of the design — "the only difference between a necessary-condition test and its acceptance test is *who drives the run*; grading is identical and objective" (`ACCEPTANCE_TESTS.md:9`) — is **false as written** for the majority of the use cases. The checker does not grade the run-dir alone; it grades the run-dir **plus** a `_check_meta.json` file that **only the test `runner.py` produces** and that the engine, the ticker, and a rung-1 agent never produce. Strip the runner away (which is exactly what "the agent drives the run" does) and the meta-dependent assertions — STOP, PAUSE-resume bounds, pool bounds, cadence collapse, CANCEL shared-state, and the entire FR-55 continuation contract — have nothing to read. The design's headline buildability claim ("the checker already exists, just expose it as a CLI") is true for the function signature but materially incomplete for the inputs that function requires.

The four findings below are ordered by severity.

---

## Finding 1 (BLOCKER) — The checker is NOT a pure run-dir reader; it depends on `_check_meta.json`, which only the test runner writes. The `checker.py <run-dir> <expected.json>` CLI is insufficient for at least 6 of the 12 use cases.

**Evidence.**
- `checker.py:109-110` loads `_check_meta.json` from the run-dir and falls back to `{}` if absent — it does **not** error on absence, so an agent-driven run grades *green-by-omission* on every meta-backed assertion.
- The following checker branches read **only** from `meta`, never from disk artifacts the engine writes:
  - `checker.py:118-120` — `stopped` flag (UC-3 halt).
  - `checker.py:144-156` — `max_inflight_le` / `max_inflight_ge` read `meta["tick_trace"]` (UC-1 pool, FR-37 backfill).
  - `checker.py:162-173` — cadence bounds read `meta["tick_trace"]["next_tick_minutes"]` (FR-38 POLL-NOW / UC implied).
  - `checker.py:178-191` — `byte_identical_results` reads `meta["results_snapshot"]` (FR-39 CANCEL).
  - `checker.py:216-230` — `stop_readonly` reads `meta["pre_stop_status"]` (UC-3).
  - `checker.py:249-264` — the entire `continuation` contract reads `meta["tick_trace"]`, `host_stopped_after_tick`, `resumed`, `eval_now`, `final_done` (UC-11).
- Confirmed by search: `_check_meta`, `tick_trace`, `pre_stop_status`, `results_snapshot`, `host_stopped_after` appear **nowhere** in `arunner/` — they are produced exclusively at `runner.py:288-294`.
- The engine *does* write `journal.ndjson` (`tick.py:863-877`, per-tick `verdict` records with `next_tick_due`) and persists `continuation` into `harness_status.json` (`tick.py:962-963, 988`). So a *subset* of the continuation signal survives on disk — but the checker's `_detect_violations` (`checker.py:42-99`) cross-references `meta["tick_trace"]`, `meta["host_stopped_after_tick"]`, and `meta["eval_now"]`, none of which are on disk after an agent run. The journal alone is not enough for the detector as written.

**Why this is a blocker, not a nit.** Because `meta` defaults to `{}` and the checker reads keys with `.get(...)`, a `stop_readonly`/`stopped`/`continuation` assertion against a meta-less agent run does not *fail loudly* — `meta.get("stopped")` returns `None`/falsy and several branches simply don't fire or compare against `None`. Several scenarios would either spuriously fail (e.g. `stop_readonly` at `checker.py:218-220` appends "no pre-STOP snapshot recorded") or spuriously *pass* depending on the `expected` shape. Both outcomes mean the acceptance test is **not grading the property it claims to grade**. A test that passes for the wrong reason is worse than no test.

**Concrete fix (pick one, state it in the doc):**

(a) **Agent-records-meta path.** The runbook must require the agent (or a thin host-side helper the agent runs) to record an equivalent `_check_meta.json` as it drives: capture `harness_status.json["continuation"]["verdict"]`, `counts`, `paused`, `next_tick_minutes`, `next_tick_due` after **every** tick into `tick_trace`; snapshot `harness_status.json` into `pre_stop_status` immediately before writing `STOP`; snapshot `results/` bytes before writing `CANCEL`; set `stopped`/`host_stopped_after_tick`/`resumed`/`eval_now`/`final_done` per the control actions it performs. This is non-trivial agent work and must be written into the runbook step-by-step, not hand-waved. Right now `ACCEPTANCE_TESTS.md:25-27` says only "performing any control actions" — it does not say "and record the per-tick trace and pre-action snapshots the checker needs," which is the load-bearing half.

(b) **Reconstruct-from-disk path (preferred, more honest).** Extend the *checker* to reconstruct what it can from disk when `meta` is absent: build the verdict trace from `journal.ndjson` (`tick.py` already writes per-tick `verdict` + `next_tick_due` there), derive `final_done` from `harness_status.json["done"]`, derive `stopped` from the presence of a `STOP` file + a non-advancing cycle. This shrinks the agent's recording burden to only the things genuinely unobservable post-hoc (the *pre*-STOP and *pre*-CANCEL snapshots, which are inherently point-in-time and cannot be reconstructed after the fact). The doc should be explicit that pre-action snapshots are the irreducible residue the agent MUST capture even under (b).

(c) **Scope the CLI honestly.** If neither (a) nor (b) is built for v1, the doc must state which `expected` keys the bare `checker.py <run-dir> <expected.json>` CLI can and cannot grade, and restrict the meta-dependent use cases to "ticker-driven acceptance only" (where the existing `runner.py` already produces meta) rather than "agent-driven." As written, `ACCEPTANCE_TESTS.md:13-21` ("The one new piece to build … this just exposes it as a command. … small") is a buildability *under*statement that will produce a CLI that silently mis-grades.

**Minimum doc change:** replace the "this just exposes it as a command" framing with an explicit input contract: *what files must exist in the run-dir for each `expected` key to be gradeable, and who writes each one in the agent-driven path.*

---

## Finding 2 (BLOCKER) — The reuse does not hold for an agent-driven (rung-1) run, because rung-1 dispatch is **subagents**, and every integration scenario is **`dispatch_mode:"shell"`**. The SKILL explicitly tells a rung-1 agent it CANNOT run these plans in-session.

**Evidence.**
- Every scenario I inspected dispatches via `worker_cmd` shell stubs: `pool_staggered/scenario.json:14`, `stop_readonly/scenario.json:13`, `resume_continues/scenario.json:13`, `continuation_abandon/scenario.json:13` — all `"dispatch_mode": "shell"`. The runner substitutes `{STUB}`/`{SCENARIO_DIR}` and the ticker spawns them detached (`runner.py:135-137, 190-192`).
- `SKILL.md:50-52`: "Dispatch … rung 1 = in-session subagents (`Task`/`Agent`); rung 2 = detached host-CLI processes (`dispatch_mode: "shell"`)."
- `SKILL.md:60-63`: **"If the plan's entries are `dispatch_mode: "shell"`, you cannot run them in-session — tell the operator to drive the run with the ticker (the printed command below) and stop."**

So a faithful rung-1 agent, handed any current scenario plan, is **required by its own SKILL** to refuse to drive it and hand off to the ticker. That means UC-1/2/3/9/11 "bootstrap rung-1 on the … plan" (`ACCEPTANCE_TESTS.md:27-37`) cannot execute as described against the existing scenarios. The acceptance test would either (i) test the agent's *correct refusal* (a real behavior, but not what the runbook says it grades) or (ii) require the agent to violate its SKILL and shell out manually — at which point it is not exercising the rung-1 *subagent* dispatch path the use case is supposed to mirror, and the whole "mirror the use case at the right rung" premise (`ACCEPTANCE_TESTS.md:50`) collapses.

**What won't transfer cleanly, itemized (charter Q1):**
- **(a) Dispatch the same stub plan.** No. Rung-1 = subagent dispatch; the stubs are shell `worker_cmd`. The agent cannot dispatch a shell stub *as a rung-1 subagent*. To reuse the *exact* canned plan, the agent must drop to rung-2 dispatch (shell), which is a different rung than UC-1/2/3/9/11 claim.
- **(b) Perform control actions by hand at the right tick.** Partially. The agent *can* `touch STOP`/`PAUSE`/`RESUME` and write `POOL`/`CADENCE`/`CANCEL` bodies between ticks (`runner.py:211-247` shows these are just file writes) — but "at the right moment" is the problem: the runner injects after a deterministic settle (`runner.py:266-272, _settle`) that waits on disk truth for every non-held worker's terminal heartbeat. A rung-1 agent is forbidden from reading heartbeats (`SKILL.md:108-110`: "Do not read, tail, or echo any heartbeat.ndjson"). Without the settle it does not know *when* "the next tick" actually reflects the dispatched workers, so its control-file timing races process startup — the exact environment-dependence `_settle` exists to eliminate. STOP-read-only and CANCEL-shared-state in particular depend on the snapshot being taken at a *precise* state; an agent that can't observe settle can't take the snapshot at the right instant.
- **(c) Produce a run-dir the existing checker can grade against the same `expected`.** No, for all meta-backed `expected` keys — see Finding 1.

**Concrete fix.** The doc must choose and state one of:
1. **Add subagent-dispatch stub plans** parallel to the shell scenarios for the rung-1 in-agent cases (the stub subagent shells out to the same `stub_worker.py`, but the *plan entry* is `dispatch_mode:"subagent"` so the agent actually exercises rung-1 dispatch). This is new canned-plan work the doc currently disclaims ("already exist as the integration scenarios," `ACCEPTANCE_TESTS.md:21`).
2. **Reclassify UC-1/2/3/9/11 acceptance as rung-2 (shell) when reusing the existing scenarios**, and add a *separate* rung-1 acceptance that uses subagent plans. Don't claim a rung-1 test while handing the agent a shell plan its own SKILL refuses.
3. At minimum, add a control action to the runbook for "if the plan is shell-mode, the agent's correct rung-1 behavior is to refuse and print the ticker command — grade *that*" and make it a distinct, named acceptance (it's a real UC-5/FR-25 degrade path), not a silent substitution inside UC-1.

---

## Finding 3 (REVISE) — Several use cases are not objectively disk-gradeable even in principle; the doc gives them a "grade" verb anyway. Make the honest pass criterion explicit.

The checker grades disk artifacts. Use cases whose property lives in the agent's *behavior or comprehension*, not the run-dir, cannot be graded by `checker.check` no matter how the meta is produced. Charter Q3:

- **UC-2 (monitor), `ACCEPTANCE_TESTS.md:28`:** "confirm each tick's status table matches `harness_status.json` (read-only)." The *match* is a property of what the agent *displayed*, which is not on disk. The checker can confirm `harness_status.json` is internally consistent and unchanged across a read-only tick, but it cannot confirm the agent *rendered it faithfully* — that requires comparing the agent's transcript output to the file. Honest pass criterion: this is a **transcript-assertion**, not a disk-assertion; either out of scope for the checker or graded by diffing the agent's printed table against `tick.py`'s `status_table` stdout. Say so.
- **UC-10 (conversational build), `ACCEPTANCE_TESTS.md:36`:** "describe → preview → run → persist … in natural language; confirm the previewed plan ran and the saved bundle re-runs faithfully." Two halves: (i) "previewed plan == ran plan" and "saved bundle re-runs faithfully" **are** disk-gradeable (diff the previewed `plan.json` against the run's `plan.json`; re-run the bundle through the checker and compare end-states). (ii) "NL understanding produced the *right* plan from the prose" is **not** objectively gradeable — it's a judgment about whether the agent interpreted intent correctly. Honest pass criterion: grade the *fidelity* legs on disk; mark the *comprehension* leg as a human/transcript judgment and don't pretend the checker covers it.
- **UC-8 (demo), `ACCEPTANCE_TESTS.md:34`:** disk-gradeable (reaches `done`) — fine.
- **UC-9 (in-context), `ACCEPTANCE_TESTS.md:35`:** "do in-context tasks + simulate a drop and rehydrate; grade outputs + background run-dir." The background run-dir is disk-gradeable; "rehydrated correctly after the drop" is a behavioral property (did the agent re-read the source of truth per the AGENTS.md ERR protocol?) that disk cannot witness directly. Honest criterion: grade the *output artifacts* the in-context tasks were supposed to produce; the rehydration-discipline leg is transcript/behavioral.
- **UC-11 (autonomy integrity), `ACCEPTANCE_TESTS.md:37`:** "run the checker's 3-class detector over the journal — pass = no CONTINUE-state yields." This one *is* designed to be disk-gradeable via `journal.ndjson` — **but** see Finding 1: the detector (`checker.py:42-99`) needs `meta["tick_trace"]`/`host_stopped_after_tick`/`eval_now`, which an agent run lacks. So UC-11 is gradeable *in principle from the journal* but *not by the current detector* without either reconstructing those from disk or having the agent record them.

**Concrete fix.** Add a column/marker to the runbook (and to `docs/TRACEABILITY.md`) classifying each UC's pass criterion as **disk-objective** (checker grades it), **disk-objective-but-needs-meta** (Finding 1 applies), or **transcript/behavioral** (a human or a transcript diff judges it, the checker cannot). The current uniform "grade" verb (`ACCEPTANCE_TESTS.md:25, 27-38`) implies disk-objectivity for all twelve, which is not true.

---

## Finding 4 (REVISE) — "Zero API spend" is *almost* honest but conflates two different spends; state the distinction explicitly.

**Evidence & analysis (charter Q4).**
- `ACCEPTANCE_TESTS.md:11`: "Stub workers mean **zero API spend**, so the whole set runs back to back." `:7` reiterates the agent drives rung-1.
- This is true for **worker dispatch**: the workers are `stub_worker.py` shell stubs (`README.md:42-49`) — no model call, no API cost. Correct.
- It is **not** true for the **orchestrator** in the agent-driven path. The whole point of an agent-driven acceptance test (vs. the ticker-driven necessary-condition test) is that a *live agent* drives the loop — and that agent spends its own tokens on every tick: parsing the tick engine's JSON, dispatching, calling `ScheduleWakeup`, relaying the status table (`SKILL.md:97-105`). For UC-1/2/3/9/10/11 the orchestrator is a billed model. "Zero API spend" is only literally true for the **ticker-driven** cases (UC-5/6/7, and the ticker half of UC-8), where there is no model in the loop at all.

So the claim as phrased ("Stub workers mean zero API spend, so the whole set runs back to back") is doing a sleight-of-hand: it attributes the zero-spend property to the *stub workers*, then generalizes it to *the whole set*, but the set includes agent-driven runs whose orchestrator is not free.

**Concrete fix.** Reword `ACCEPTANCE_TESTS.md:11` to:
> "Stub workers mean **zero worker API spend** — no run dispatches a billed model as a worker. The agent-driven cases still spend the **orchestrator's** tokens (one model driving the tick loop); the ticker-driven cases (UC-5/6/7) spend nothing at all. The set is cheap, not free."

This matters for the "runs back to back" claim too: an agent-driven sweep across 6 use cases × N platforms × N agents is a real (if small) token budget, not a free CI job. The doc should not lead an operator to expect $0.

---

## Summary of required revisions before SHIP

| # | Severity | Fix |
|---|---|---|
| 1 | BLOCKER | Define the checker's real input contract; the bare `checker.py <run-dir> <expected.json>` CLI cannot grade STOP/PAUSE/pool/cadence/CANCEL/continuation without `_check_meta.json`, which agent runs don't produce. Either have the agent record meta (esp. the irreducible pre-STOP / pre-CANCEL snapshots) or extend the checker to reconstruct trace/`final_done`/`stopped` from `journal.ndjson` + disk. |
| 2 | BLOCKER | Resolve the rung-1-subagent vs. shell-stub mismatch. Current scenarios are all `dispatch_mode:"shell"`; SKILL forbids a rung-1 agent from running them in-session. Add subagent-dispatch stub plans, or reclassify the in-agent cases' rung, or grade the refusal-and-degrade path explicitly. |
| 3 | REVISE | Classify each UC's pass criterion: disk-objective vs. needs-meta vs. transcript/behavioral. UC-2 (table fidelity), UC-10 (NL comprehension leg), UC-9 (rehydration leg) are not disk-gradeable; stop implying the checker covers them. |
| 4 | REVISE | Fix "zero API spend": it's zero *worker* spend; agent-driven cases still burn orchestrator tokens. Only the ticker-driven cases are truly free. |

The design's instinct — reuse canned plans + the independent stdlib checker, vary only the driver — is sound and worth keeping. But the two blockers mean the mechanism as described **does not actually grade the properties it claims** for the meta-dependent and rung-1 cases. Fix the input contract and the dispatch-rung mismatch, classify the non-disk-gradeable legs honestly, and it ships.
