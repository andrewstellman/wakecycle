# Panelist B — In-Agent Fidelity (instr 041, deliverable C)

Charter: prove the rung-1 (subagent-dispatch) acceptance path is a *real* in-agent
mechanism with a trivial stub and an objective disk grade. Adversarial review.

## 1. Real subagent plan, not a relabeled shell plan — PASS
`tests/acceptance/plans/uc1_multijob.json`: pool_size 2, 3 entries. Every entry:
`dispatch_mode: "subagent"`, has a `worker_prompt`, and carries NO forbidden
shell key (`worker_cmd` / `adapter` / `command` all absent — verified
programmatically). `preview`:
```
arunner preview: uc1_multijob.json - 3 job(s), pool 2
  job 1 [uc1-a]: SUBAGENT  in-session agent prompt
  job 2 [uc1-b]: SUBAGENT  in-session agent prompt
  job 3 [uc1-c]: SUBAGENT  in-session agent prompt
--check: OK - no problems found. Safe to run.
```
(`--check` is a positional sub-arg of `preview`, not a separate flag; the
combined `preview --check` form errors, but `preview ... --check` is OK.)

## 2. Trivial, cheap-not-free stub — PASS
`worker_prompt` is "emit STARTING then terminal COMPLETED via heartbeat helper,
return ONE line, do nothing else." No real work, no extra API spend. All five
placeholders present in every entry: `{HEARTBEAT_PATH} {TASK_ID} {RUN_DIR}
{TARGET_REPO} {HARNESS_BIN}`. At dispatch the engine substitutes them — the
dispatched prompt contains ZERO surviving `{...}` placeholders and absolute
engine-resolved paths (`{HARNESS_BIN}` -> `.../arunner/engine`, FR-21a; the
model is never asked to transcribe a path).

## 3. Independent reproduction of the rung-1 mechanism — PASS
Temp `ARUNNER_RUNS_DIR`; `--init`; then I (not the ticker) drove the loop,
reading each tick's `dispatch_list`, extracting the two `heartbeat.py` lines from
each `worker_prompt`, and running them myself as the stub subagent:
```
TICK 1: dispatched=2 done=False
TICK 2: dispatched=1 done=False
TICK 3: dispatched=0 done=True   <- staggered pool-2 (2 -> 1 -> 0)
checker: CHECK PASSED  (exit 0)
_check_meta.json: No such file  <- graded as a LIVE run, durable artifacts only
```
Staggered counts confirm pool=2 (2 in flight, then the 3rd). Checker
`tests/integration/checker.py` exits 0 against `uc1_expected.json`, and the
runner's private `_check_meta.json` is absent — the verdict came from
harness_status.json / journal / results / heartbeats, not the test-runner meta.
Temp dirs cleaned.

## 4. Honesty — PASS
No `outputs/041-*.md` claim doc exists (only the reviews/ dir), so nothing
asserts the ticker drove this; the reproduced path is genuinely the agent-self
driven rung-1 loop graded by an independent stdlib-only checker. checker.py's
header documents the additive-grading invariant (imports stdlib only, never
arunner/bin — "the harness must never grade its own homework").

VERDICT: SHIP
