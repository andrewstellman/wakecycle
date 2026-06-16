# Panelist C — robustness + regression (FR-59)

Charter: read-tolerance never crashes/busy-spins; cross-platform/stdlib/no-shell-out held;
terminal-exit + Ctrl-C correct; no regression in the existing suite.

1. **Read-tolerance, no crash / no spin.** `render_monitor_frame` wraps both reads in one try,
   catches `(FileNotFoundError, json.JSONDecodeError, OSError)` → `return None, False, False`.
   In `cmd_monitor` the error path keeps `last_good` (or a "waiting" line) and ALWAYS falls
   through to `time.sleep(interval)` — no `continue`/early-loop bypasses the sleep, so a
   persistent read error cannot busy-spin. `--once` with no `harness_status.json` exits 2
   (matches `cmd_status`). `ReadTolerance` 3 tests pass.
2. **Cross-platform / stdlib / no shell-out.** ANSI clear `\033[H\033[2J` + `--no-clear`
   separator-append fallback. The three `subprocess` hits in cli.py are all in
   `cmd_run`/`cmd_resume` ticker launch — none in the monitor path; no `os.system`, no
   `curses`. `ClearModes` passes.
3. **Terminal-exit + Ctrl-C.** `terminal` is true on `done`, `stop`, or a `STOP` file; the LOOP
   (not just `--once`) renders once then `return 0`; `--once` returns after one frame;
   `KeyboardInterrupt` is caught, writes a newline, returns 0, no traceback. `TerminalAndOnce`
   4 tests pass.
4. **No regression + scope.** Full suite 323 passed (was 311; +12 monitor tests);
   `test_positioning_honesty` 7 passed. Diff confined to `arunner/cli.py` + `tests/test_monitor.py`
   + REQUIREMENTS/TRACEABILITY — `git diff --stat d81aef7 HEAD -- arunner/engine/` empty (no
   engine change). `--interval` floored at `max(0.05, float(interval))` so 0/negative cannot
   tight-spin.

Flake note (non-blocking): the first post-checkout run showed `test_never_writes` failing
(Panelist A's concurrent `.x` mutation in the shared tree); it did not reproduce in 2000
iterations or 14 subsequent full-suite runs. Not a code defect; the read-only pin correctly
caught a real injected write.

VERDICT: SHIP
