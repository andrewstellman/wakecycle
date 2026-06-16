# Panelist B — renderer reuse + freshness honesty (FR-59)

Charter: `_format_table` is reused not forked; the freshness line cannot overclaim; the
lifecycle-vs-heartbeat split is correct and tested on a real divergence; no edits to
`_format_table`'s body.

1. **Reuse, not forked.** `git diff d81aef7 HEAD -- arunner/engine/tick.py` is empty — the
   monitor edited `_format_table`'s body zero lines. `render_monitor_frame` calls
   `TICK._format_table(run_dir, status, plan, terminal=terminal)` and only PREPENDS a header
   (`return header + "\n" + table`). `RendererReuse::test_reuses_renderer_no_fork` pins
   `text.endswith(expected)` against a direct `_format_table` call — PASS.
2. **The freshness line cannot overclaim (NFR-12).** ACTIVITY/LAST-HB/HB-AGE are read live
   inside `_format_table` at render time off `heartbeat.ndjson`; lifecycle/counts come from the
   passed `harness_status.json` dict and are labeled `run-state as of last tick: Xs ago`. The
   header tags `ACTIVITY/HB-AGE: live` vs lifecycle `as of last tick`. Age sources
   `status.last_tick_wall` if present, else file mtime (both branches verified).
3. **C-6 case surfaces correctly.** `test_freshness_split` is a real divergence: a heartbeat
   lands ("step 2 of 5") while `harness_status.json` is held fixed → `t1` "step 1" / `t2`
   "step 2 of 5" (ACTIVITY moves), `Queue:`/`Completed: 0` identical in both (counts frozen),
   age advances `10s`→`1m` with wall-clock. A stale heartbeat cannot masquerade as live —
   HB-AGE prints the actual mtime age.
4. **Math sanity.** Clock-seam repro: age = now − last_tick_wall; negative clamps to 0;
   `_age_str` thresholds correct (89→`89s`, 90→`1m`, 5399→`89m`, 5400→`1h`).

Note: a transient `test_never_writes` failure on my first run was Panelist A's concurrent
mutation-bite injecting a `.x` write into the shared tree; HEAD is clean and the suite passes
12/12 on the committed tree.

VERDICT: SHIP
