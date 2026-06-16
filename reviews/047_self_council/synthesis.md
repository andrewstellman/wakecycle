# Instruction 047 self-council synthesis — FR-59 read-only disk monitor (`arunner monitor`)

*Mandatory 3-panel (concurrency + honesty hazards). Three fresh-context, role-locked,
adversarial reviewers verifying on disk: tracing every code path for a write, biting the
never-writes pin, confirming the renderer is reused not forked, exercising the
freshness split on a real heartbeat-vs-lifecycle divergence, and stress-checking
read-tolerance / Ctrl-C / terminal-exit. Date: 2026-06-15.*

| Panelist | Charter | Verdict |
|----------|---------|---------|
| `panelist_A_read_only_safety.md` | the monitor cannot write/lock/advance under any path; never-writes pin bites; safe concurrent with a live engine | **SHIP** |
| `panelist_B_renderer_reuse_freshness.md` | `_format_table` reused not forked; the freshness line cannot overclaim; lifecycle-vs-heartbeat split correct | **SHIP** |
| `panelist_C_robustness_regression.md` | read-tolerance never crashes/busy-spins; cross-platform/stdlib/no-shell-out; terminal-exit + Ctrl-C; no regression | **SHIP** |

## Outcome: unanimous SHIP (round 1)

### Panelist A — read-only safety (SHIP)
`render_monitor_frame` does only `sp.read_text()` / `pp.read_text()` (guarded) + a
`(run_dir/"STOP").exists()` check + delegates to `_format_table`; `cmd_monitor` writes only
to `sys.stdout`. No open-for-write, no `.tick.lock`, no control-file drop, no `init_run`/tick,
no `subprocess` on any path (loop, `--once`, error frame, terminal-exit, Ctrl-C). The
delegated `_format_table` reaches heartbeats only via reads (`_hb_observe`/`_hb_age_str`/
`_heartbeat_path`). The never-writes pin snapshots the FULL file set (`rglob("*")`, mtime_ns
+ size + content hash) and asserts no `.tick.lock`/control file — injecting a stray write made
it FAIL, restore → PASS. A run-dir the monitor touched with no ticker stayed byte-identical
across 4 passes with no lock created → safe concurrent with a live engine by construction.

### Panelist B — renderer reuse + freshness honesty (SHIP)
`git diff -- arunner/engine/tick.py` is empty — the monitor edited `_format_table`'s body
zero lines and only PREPENDS a header (`return header + "\n" + table`); `RendererReuse` pins
`text.endswith(_format_table(...))`. Genuine freshness split: ACTIVITY/HB-AGE are read live
inside `_format_table` off `heartbeat.ndjson`; lifecycle/counts come from the passed
`harness_status.json` and are labeled `run-state as of last tick: Xs ago` (age from
`status.last_tick_wall` else mtime). `test_freshness_split` is a REAL divergence (a heartbeat
lands while status is held fixed → ACTIVITY moves, counts frozen, age advances 10s→1m). A
stale heartbeat can't masquerade as live — HB-AGE prints its actual age. The C-6 case is
surfaced honestly.

### Panelist C — robustness + regression (SHIP)
A transient read failure returns `ok=False` → the loop keeps the last good render (or a
"waiting" line) and ALWAYS falls through to `time.sleep(interval)` — no path bypasses the
sleep, so a persistent error cannot busy-spin; `--once` with no status exits 2. ANSI clear +
`--no-clear` separator fallback; no `curses`, no `os.system`, no `subprocess` in the monitor
path. Terminal (done/stop incl. the STOP file) + `--once` + Ctrl-C all return 0 cleanly (no
traceback). `--interval` floored at 0.05 so 0/negative can't tight-spin. Full suite 323
passed (+12); positioning-honesty 7; diff confined to `cli.py` + `test_monitor.py` + the FR-59
docs — NO engine change.

## Note on a transient pin "failure" during review
Panelists B and C each observed one transient `NeverWrites::test_never_writes` failure on
their first run — caused by Panelist A's concurrent mutation-bite injecting a `.x` write into
the SHARED working tree mid-review (the parallel-reviewer race). Both confirmed the committed
HEAD is clean (`git diff HEAD` empty, no `.x` write in source) and the test passes 12/12 on
the authoritative tree; C saw no reproduction in 2000 iterations. Not a code defect — the
read-only pin correctly caught a real (injected) write, which is exactly its job.

## Net
FR-59 lands as a strictly read-only sidecar: `arunner monitor <run-dir>` reuses the shared
`_format_table` over disk-loaded state every interval, writing nothing, taking no lock,
advancing no tick — safe alongside a live engine on any rung. A monitor-owned freshness line
keeps the lifecycle-vs-heartbeat split honest (NFR-12). Cross-platform/stdlib/no-shell-out;
read-tolerant; clean terminal/`--once`/Ctrl-C exits. 12 tests, 3 mutation-pinned invariants
(never-writes, renderer-reuse-no-fork, freshness-split). No engine change; suite 311 → 323.
