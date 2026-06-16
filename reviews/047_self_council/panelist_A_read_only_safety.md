# Panelist A — read-only safety (FR-59)

Charter: prove the monitor cannot write, lock, or advance state under any path; the
never-writes pin actually bites; safe concurrent with a ticking engine.

1. **Strictly read-only by inspection.** `render_monitor_frame` does only `sp.read_text()`,
   `pp.read_text()` (guarded by `pp.is_file()`), a `(run_dir/"STOP").exists()` check, and
   delegates to `TICK._format_table`. `cmd_monitor` adds one `sp.is_file()` check and writes
   only to `sys.stdout`. No open-for-write, no `.tick.lock` creation/acquire, no control-file
   drop (STOP/PAUSE/RESUME/CANCEL/POOL/CADENCE/POLL-NOW), no `init_run`/tick, no `subprocess`
   on ANY path (loop / `--once` / read-failure frame / terminal-exit / KeyboardInterrupt).
2. **Delegated reads are read-only too.** `_format_table` reaches heartbeats only via
   `_hb_observe` (tail + `hb.stat()`), `_hb_age_str`, `_heartbeat_path` (reads `manifest.json`)
   — all reads.
3. **The never-writes pin bites + snapshots fully.** `_snapshot` walks `rglob("*")` over every
   file (mtime_ns + size + content hash) and asserts no `.tick.lock` / control file. Injecting
   `(run_dir/".x").write_text("x")` into `render_monitor_frame` made `test_never_writes` FAIL;
   `git checkout --` + re-run PASSED. Tree left clean.
4. **Safe concurrent with a live engine.** The monitor never takes `.tick.lock` and reads only
   atomically-written files, so it cannot race the writer. A run-dir the monitor touched with
   no ticker left no `.tick.lock` and no control file and was byte-identical across 4 passes —
   the monitor creates neither.
5. **Terminal/`--once`/Ctrl-C don't sneak a write.** done/STOP self-exit rc 0; `--once` returns
   after one frame rc 0; Ctrl-C (monkeypatched `time.sleep`) returns 0 with no traceback; a read
   failure returns `(None, False, False)` and skips. 12 tests pass.

VERDICT: SHIP
