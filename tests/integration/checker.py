"""Independent disk-assertion checker for integration scenarios (FR-51).

INVARIANT (mechanically enforced by test_checker_independence.py): this module
imports the STANDARD LIBRARY ONLY -- never the ``wakecycle`` package, never any
``bin/`` module. The harness must never grade its own homework: the verdict is
a plain-Python read of the disk artifacts the run left behind
(``harness_status.json``, ``results/result-NNNNN.json``, per-run
``heartbeat.ndjson``, ``claimed/*.lock``) plus the runner's ``_check_meta.json``
(tick trace + pre-STOP snapshot), checked against the scenario's ``expected``.

``check(run_dir, expected)`` returns a list of human-readable failure strings
(empty == PASS), so a caller can assert ``not failures``.
"""
from __future__ import annotations

import json
import os


def _load(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return json.load(fh)


def check(run_dir, expected):
    fails = []
    run_dir = str(run_dir)
    status_path = os.path.join(run_dir, "harness_status.json")
    if not os.path.isfile(status_path):
        return ["no harness_status.json in %s" % run_dir]
    status = _load(status_path)
    meta_path = os.path.join(run_dir, "_check_meta.json")
    meta = _load(meta_path) if os.path.isfile(meta_path) else {}

    # 1. done flag
    if "done" in expected and bool(status.get("done")) != bool(expected["done"]):
        fails.append("done: expected %r, got %r"
                     % (expected["done"], status.get("done")))

    # 2. stopped (the run halted on a STOP file) -- from the runner's meta
    if "stopped" in expected and bool(meta.get("stopped")) != bool(expected["stopped"]):
        fails.append("stopped: expected %r, got %r"
                     % (expected["stopped"], meta.get("stopped")))

    # 2b. paused (FR-36) -- persisted in harness_status.json
    if "paused" in expected and bool(status.get("paused")) != bool(expected["paused"]):
        fails.append("paused: expected %r, got %r"
                     % (expected["paused"], status.get("paused")))

    # 3. counts (subset match)
    counts = status.get("counts", {})
    for k, v in (expected.get("counts") or {}).items():
        if counts.get(k, 0) != v:
            fails.append("counts[%s]: expected %r, got %r"
                         % (k, v, counts.get(k, 0)))

    # 4. per-run final states
    runs = status.get("runs", {})
    for run, st in (expected.get("run_states") or {}).items():
        got = (runs.get(run) or {}).get("state")
        if got != st:
            fails.append("runs[%s].state: expected %r, got %r" % (run, st, got))

    # 5. pool never exceeded (staggered dispatch) -- from the tick trace
    if "max_inflight_le" in expected:
        cap = expected["max_inflight_le"]
        worst = 0
        for t in (meta.get("tick_trace") or []):
            c = t.get("counts", t)               # trace item carries {counts, paused}
            inflight = (c.get("claimed", 0) + c.get("running", 0)
                        + c.get("stalled", 0))
            worst = max(worst, inflight)
        if worst > cap:
            fails.append("max in-flight %d exceeded pool cap %d" % (worst, cap))

    # 6. STOP read-only: the stop tick changed NOTHING (not even cycle)
    if expected.get("stop_readonly"):
        pre = meta.get("pre_stop_status")
        if not pre:
            fails.append("stop_readonly asserted but no pre-STOP snapshot recorded")
        else:
            if pre.get("cycle") != status.get("cycle"):
                fails.append("stop tick changed cycle: %r -> %r"
                             % (pre.get("cycle"), status.get("cycle")))
            pre_runs = pre.get("runs", {})
            for run, r in runs.items():
                if (pre_runs.get(run) or {}).get("state") != r.get("state"):
                    fails.append("stop tick changed runs[%s].state: %r -> %r"
                                 % (run, (pre_runs.get(run) or {}).get("state"),
                                    r.get("state")))

    # 7. every terminal run has a results record (audit-record discipline)
    if expected.get("results_for_terminal", True):
        terminal = ("completed", "failed", "auth_or_launch_failed", "abandoned")
        results_dir = os.path.join(run_dir, "results")
        for run, r in runs.items():
            if r.get("state") in terminal:
                jid = (r.get("job_id")
                       or "job-%05d" % int(run.split("-")[1]))
                rp = os.path.join(results_dir,
                                  jid.replace("job-", "result-") + ".json")
                if not os.path.isfile(rp):
                    fails.append("terminal run %s (%s) has no results record"
                                 % (run, r.get("state")))
    return fails
