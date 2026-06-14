"""Independent disk-assertion checker for integration scenarios (FR-51).

INVARIANT (mechanically enforced by test_checker_independence.py): this module
imports the STANDARD LIBRARY ONLY -- never the ``arunner`` package, never any
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


# FR-55: the closed halt set (kept in lockstep with tick._CONTINUATION_REASONS;
# duplicated here on purpose -- the checker imports NO repo code).
_HALT_REASONS = frozenset((
    "done", "failed", "stop", "pause", "cancel", "blocked",
    "stalled", "budget", "internal_error"))


def _cited_in_set(v):
    """True iff a yield's cited_verdict is a member of the verdict vocabulary
    (CONTINUE or HALT:<reason-in-closed-set>)."""
    if v == "CONTINUE":
        return True
    if isinstance(v, str) and v.startswith("HALT:"):
        return v[len("HALT:"):] in _HALT_REASONS
    return False


def _detect_violations(run_dir, meta):
    """FR-55 abandonment detector -- three classes, all read from disk alone:
    (i) silent abandonment, (ii) illegitimate yield, (iii) false halt claim.
    Cross-checks each host yield's cited_verdict against the engine's ACTUAL
    recorded verdict for that tick -- the yield's honesty is verified against
    ground truth, never trusted."""
    journal = []
    jp = os.path.join(run_dir, "journal.ndjson")
    if os.path.isfile(jp):
        with open(jp, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    journal.append(json.loads(ln))
                except ValueError:
                    pass
    engine_verdict_by_tick = {}
    yields = []
    for e in journal:
        if e.get("type") == "verdict":
            engine_verdict_by_tick[e.get("tick")] = e.get("verdict")
        elif e.get("type") == "yield":
            yields.append(e)

    trace = meta.get("tick_trace") or []
    host_stop = meta.get("host_stopped_after_tick")
    resumed = bool(meta.get("resumed"))
    eval_now = meta.get("eval_now")
    final_done = bool(meta.get("final_done"))
    viols = set()

    for y in yields:
        cv = y.get("cited_verdict")
        if not _cited_in_set(cv):
            # (ii) a yield citing a reason outside the closed halt set
            viols.add("illegitimate_yield")
        else:
            # (iii) an in-set claim that does NOT match the engine's actual
            # verdict for that tick (e.g. cites HALT:done while it was CONTINUE)
            actual = engine_verdict_by_tick.get(y.get("tick"))
            if actual is not None and cv != actual:
                viols.add("false_halt_claim")

    # (i) silent abandonment: the host stopped while the verdict was CONTINUE,
    # never resumed, wrote no yield, the run never finished, and wall-clock is
    # past next_tick_due by more than a cadence-interval tolerance.
    if host_stop is not None and not resumed and not yields and not final_done:
        idx = host_stop - 1
        stop_entry = trace[idx] if 0 <= idx < len(trace) else None
        if stop_entry and stop_entry.get("verdict") == "CONTINUE":
            due = stop_entry.get("next_tick_due")
            tol = int((stop_entry.get("next_tick_minutes") or 1)) * 60
            if (due is not None and eval_now is not None
                    and eval_now > due + tol):
                viols.add("silent_abandonment")
    return sorted(viols)


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

    # 5. in-flight bounds -- from the tick trace. _le: pool never exceeded
    # (staggered dispatch). _ge: in-flight REACHED at least N at some tick
    # (e.g. a POOL raise back-filling dispatch up to the new pool, FR-37).
    if "max_inflight_le" in expected or "max_inflight_ge" in expected:
        worst = 0
        for t in (meta.get("tick_trace") or []):
            c = t.get("counts", t)               # trace item carries {counts, paused}
            inflight = (c.get("claimed", 0) + c.get("running", 0)
                        + c.get("stalled", 0))
            worst = max(worst, inflight)
        if "max_inflight_le" in expected and worst > expected["max_inflight_le"]:
            fails.append("max in-flight %d exceeded pool cap %d"
                         % (worst, expected["max_inflight_le"]))
        if "max_inflight_ge" in expected and worst < expected["max_inflight_ge"]:
            fails.append("max in-flight %d never reached expected %d"
                         % (worst, expected["max_inflight_ge"]))

    # 5b. reported cadence bounds across the trace (FR-38 POLL-NOW collapse):
    # min_next_cadence proves some tick collapsed to the minimum; max_next
    # proves the run was otherwise idling at a long cadence (so the collapse is
    # meaningful, not a trivially-short cadence).
    if "min_next_cadence" in expected or "max_next_cadence" in expected:
        cadences = [t.get("next_tick_minutes") for t in (meta.get("tick_trace") or [])
                    if t.get("next_tick_minutes") is not None]
        if not cadences:
            fails.append("no next_tick_minutes recorded in the tick trace")
        else:
            if "min_next_cadence" in expected and min(cadences) != expected["min_next_cadence"]:
                fails.append("min next-cadence: expected %r, got %r (trace %r)"
                             % (expected["min_next_cadence"], min(cadences), cadences))
            if "max_next_cadence" in expected and max(cadences) != expected["max_next_cadence"]:
                fails.append("max next-cadence: expected %r, got %r (trace %r)"
                             % (expected["max_next_cadence"], max(cadences), cadences))

    # 5c. shared-state safety (FR-39 CANCEL): named results records must be
    # byte-identical before (snapshot taken just before CANCEL) vs after -- i.e.
    # CANCEL touched ONLY its own target, not a genuine FAILED/COMPLETED record.
    if expected.get("byte_identical_results"):
        snap = meta.get("results_snapshot") or {}
        for name in expected["byte_identical_results"]:
            rp = os.path.join(run_dir, "results", name)
            if not os.path.isfile(rp):
                fails.append("byte_identical_results: %s missing after run" % name)
            elif name not in snap:
                fails.append("byte_identical_results: %s not in pre-CANCEL snapshot" % name)
            else:
                with open(rp, "rb") as fh:
                    now_hex = fh.read().hex()
                if now_hex != snap[name]:
                    fails.append("CANCEL altered a foreign record %s "
                                 "(not byte-identical across the cancel)" % name)

    # 5d. FR-45 SUMMARY capstones on the done-transition.
    if expected.get("summary_present"):
        sm = os.path.join(run_dir, "SUMMARY.md")
        sj = os.path.join(run_dir, "summary.json")
        if not os.path.isfile(sm):
            fails.append("SUMMARY.md missing on a done run")
        if not os.path.isfile(sj):
            fails.append("summary.json missing on a done run")
        else:
            s = _load(sj)
            if not s.get("schema_version"):
                fails.append("summary.json missing schema_version")
            if not s.get("done"):
                fails.append("summary.json done flag not set")
            if s.get("counts") != status.get("counts"):
                fails.append("summary.json counts %r != status counts %r"
                             % (s.get("counts"), status.get("counts")))
            sj_states = {j.get("run"): j.get("state") for j in s.get("jobs", [])}
            st_states = {n: r.get("state") for n, r in runs.items()}
            if sj_states != st_states:
                fails.append("summary.json job states %r != run states %r"
                             % (sj_states, st_states))

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

    # 8. FR-55 continuation contract: the abandonment detector must fire on its
    # violation configs and stay silent on the honest ones; verdict-fidelity
    # asserts the per-tick verdict value against known state.
    cont_exp = expected.get("continuation")
    if cont_exp is not None:
        got = _detect_violations(run_dir, meta)
        want = sorted(set(cont_exp.get("violations", [])))
        if got != want:
            fails.append("continuation violations: expected %r, got %r"
                         % (want, got))
        trace_verdicts = [t.get("verdict") for t in (meta.get("tick_trace") or [])]
        for v in cont_exp.get("verdict_present", []):
            if v not in trace_verdicts:
                fails.append("verdict-fidelity: expected %r somewhere in the "
                             "trace, got %r" % (v, trace_verdicts))
        if ("final_done" in cont_exp
                and bool(meta.get("final_done")) != bool(cont_exp["final_done"])):
            fails.append("continuation final_done: expected %r, got %r"
                         % (cont_exp["final_done"], meta.get("final_done")))
    return fails
