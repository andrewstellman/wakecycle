# Panelist B — cadence correctness (FR-58a)

Charter: tested on the DEFAULT grace path (not grace-0); first-scan-at-start; the adapter-synthesis
fix makes grace/stall/keepalive live; `--check` rejects keepalive>grace; ACTIVITY actually moves.

1. **Interval DECOUPLED from stall/3.** `_resolve_keepalive_secs` returns the explicit
   `--keepalive-seconds` (is-not-None) else `_DEFAULT_KEEPALIVE_SECONDS=45.0`, floored to 1s — no
   `min(launch_grace, stall/3)`. `_cmd_wrap` and `_cmd_tail` both call it; `keepalive_interval_secs`
   survives only as the cadence test's old-formula assertion (`==600.0`), never called by an adapter.
   11 tests pass.
2. **First-scan-at-start + re-scan==emit.** `emit_first` calls `_emit(now)`; `_emit` re-scans
   activity AND appends the IN_PROGRESS in one event (NOT split). Called in both adapters. PIN bite:
   neutering `emit_first` to `return False` → `test_first_scan_at_start` FAILED ("0 != 1"); restored
   → pass.
3. **Adapter synthesis LIVE.** `_adapter_worker_cmd(entry, plan)` emits all three flags via `_knob`
   (entry>plan>default); wired into `_dispatch`. Independent call returned
   `--launch-grace-minutes 20 --stall-threshold-minutes 60 --keepalive-seconds 30`. Before this the
   flags were never synthesized (adapter fell back to 10/45 — inert plan knobs).
4. **`--check` rejects keepalive>grace** plan-level + per-entry (with the entry-grace override);
   explicit-override-wins + 1s floor proven. CheckGate tests pass.
5. **ACTIVITY moves on the DEFAULT grace path.** `RealDefaultGracePath.test_wrap_default_grace_label_moves`
   drives the real subprocess with default grace (NOT grace-0) + a fast `--keepalive-seconds`;
   `test_label_moves_across_keepalives` is the clock-seam mover. The previously-grace-0 fixtures
   (EndToEnd, UC-12) now pass an explicit `--keepalive-seconds`, so the regression is no longer
   masked. Full suite 310 passed.

Non-blocking: one stale grace-0 docstring (test_activity_patterns EndToEnd) — comment-only.

VERDICT: SHIP
