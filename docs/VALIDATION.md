# Wakecycle Validation Matrix

*Status: v1.0, 2026-06-12. The test plan that flips README host-support cells from DESIGNED to VERIFIED. Every PASS recorded here must cite a run-dir or transcript; every README cell change must cite a row here (NFR-12). Executors: OPERATOR (human, owns Windows + anything requiring his machines/accounts), WORKER (the QPB runner session, native macOS), ORCH (the orchestrating chat's Linux sandbox).*

## Already VERIFIED (evidence on record)

| ID | What | Evidence |
|---|---|---|
| V-1 | Claude Code rung 1 + subagent dispatch, multi-entry pool, staggered dispatch, STOP, idle ticks | Sonnet ×3 + Haiku ×2 runs, 2026-06-11/12 (spike-evidence.md, run-dirs, QPB tracker) |
| V-2 | Ticker rung 3 + shell dispatch, macOS, stub workers | In-repo live demo, instruction 009/014 evidence |
| V-3 | Low-reasoning-model orchestration (Haiku) incl. launch-failure path | Run-dirs `20260611T231408Z`, `20260612T005833Z` |
| V-4 | Idempotency / STOP read-only / resume / encoding safety | Mutation-verified unit suite (65 tests) |

## To run

### Platform column

| ID | Test | Executor | Setup | PASS criteria | README cell |
|---|---|---|---|---|---|
| V-5 | **Linux, manual floor (rung 4) + shell dispatch** — `--once` ticks advancing a short shell-plan to done | ORCH (Linux sandbox) | repo clone + fast shell plan (short stub sleeps) | each `--once` = one safe tick; detached workers survive between invocations; 3/3 completed; lockfile honored | "Windows/Linux ticker" → Linux half |
| V-6 | **Linux, ticker loop (rung 3)** — `nohup ticker &` surviving between observer checks | ORCH | same plan | loop unattended to done; table ASCII-clean | same |
| V-7 | **Windows, ticker loop + manual floor** — user-level Python, no admin: the floor that MUST work (NFR-2) | OPERATOR (needs: any Windows 10/11 machine or VM, `py` user install, repo clone) | clone repo; `py bin\ticker.py examples\<shell-plan>.json`; then a `--once` pass | detach flags work (workers outlive ticker exit? no — workers are children in loop mode: verify documented semantics), lockfile via `msvcrt`, PID liveness, paths-with-spaces run-dir, table renders in cmd.exe AND PowerShell (cp1252 — the load-bearing Windows check) | "Windows/Linux ticker" → Windows half |
| V-8 | **Windows path edge** — run-dir under a path with spaces (e.g. `C:\Users\Andrew Stellman\...`) and the OneDrive-warning pre-flight | OPERATOR (same session as V-7) | point a run-dir there | quoting holds end-to-end; E4 warning fires on a OneDrive-ish path | edge register E3/E4 |

### Cadence column

| ID | Test | Executor | Setup | PASS criteria | README cell |
|---|---|---|---|---|---|
| V-9 | **Cron rung 2** — user crontab entry firing `--once` at 1-2 min cadence, macOS | WORKER (native; temporary crontab entry, removed after) | shell plan; `crontab -l` snapshot before/after for clean restore | every fire = exactly one tick; overlapping fires skip via lockfile (set cadence ≤ tick duration once to force overlap); run reaches done with NO foreground process; crontab restored | "OS scheduler, cadence 2" |
| V-10 | **Safety-tick pattern (FR-26a)** — rung-1 Claude Code run paired with a cron/terminal `--once` at 3× cadence | OPERATOR (the rung-1 session) + WORKER (the safety tick side) | one shared run-dir | safety ticks are cycle-only no-ops while the session lives; kill/quit the session mid-run → safety tick completes the run unattended | the safety-tick section's claim |

### Real-process column (the worker-contract claim: "a job is anything that appends JSON lines to a file")

| ID | Test | Executor | Setup | PASS criteria | README cell |
|---|---|---|---|---|---|
| V-11 | **Real compute jobs, no AI** — 3 shell jobs that do actual work (e.g. clone a small public repo + run its test suite; tar/compress a tree; run wakecycle's own 65-test suite) each wrapped in a ~10-line script that heartbeats via `bin/heartbeat.py` before/during/after | WORKER (macOS) | shell plan, pool 2, realistic stall threshold | mixed durations → staggered dispatch; real exit codes mapped to COMPLETED/FAILED terminals; one job INTENTIONALLY failing (nonzero exit → FAILED heartbeat) displays correctly | the worker-contract paragraph; "no-helper path" if one job uses raw `echo >>` instead of the helper |
| V-12 | **Stall detection live** — a real worker killed mid-run (`kill <PID from claim lock>`), short `stall_threshold_minutes` (2) | WORKER | V-11's plan, one entry | STALLED appears after threshold; PID-liveness shows dead-process fast-fail (A-5); run continues; table honest | stall claims |
| V-13 | **STOP with real workers in flight** | WORKER | V-11 rerun | stop tick read-only; orphan semantics as documented (real processes run to completion); resume after deleting STOP | UC-3/UC-4 with real processes |
| V-14 | **Real AI workers via `worker_cmd`** — at least one entry whose worker is a real agent CLI invocation doing a small real task (e.g. `claude` non-interactive or `codex exec` summarizing a repo to a file, heartbeat-wrapped) | OPERATOR decision first (real API/subscription spend, ~minutes of agent time) then WORKER | shell plan, 1-2 entries | agent worker launches detached, heartbeats, terminal sentinel carries a real `result_file` | "Codex/Copilot/Cursor CLIs as workers" partially; full per-host matrix stays v0.2 |

### Results log (append-only; cite run-dirs)

| ID | Date | Executor | Verdict | Evidence |
|---|---|---|---|---|
| | | | | |

*README table updates happen ONLY by citing a row above with evidence. Failures are findings, not embarrassments — log them with the same rigor (the Haiku launch-failure run improved the product more than the clean passes did).*
