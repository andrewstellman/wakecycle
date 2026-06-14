#!/usr/bin/env python3
"""Controllable cross-platform work simulator for the integration scenarios
(instr 018 FR-51; generalized in instr 037).

Two faces, one stdlib-only, cross-platform program (NFR-1/3 — it must run
identically on Windows/macOS/Linux: no POSIX-only calls):

1. **Legacy stub (the default).** Heartbeat-then-HOLD: emits STARTING +
   IN_PROGRESS x N to the ``--heartbeat`` JSONL file, then -- if a
   ``--hold-file`` is given -- polls until that file appears before its terminal
   line. That hold is what iterations 2-5 (PAUSE mid-run, stall, CANCEL) act
   against. This path is byte-identical to the original stub and is taken
   whenever none of the new simulator flags are used.

2. **Simulator (any new flag set).** Parametrized work + output: ``--emit
   jsonl|log|mixed`` (heartbeat JSONL, plain log lines for the wrap/tail
   adapters, or both), ``--rate`` (lines per step), ``--noise`` (non-matching
   filler lines — the substrate for FR-56 activity-pattern relevance later),
   ``--status-seq`` (explicit lifecycle walk), ``--final``/``--exit-code``
   (terminal outcome / process exit for wrap doneness), ``--stall`` (go silent),
   ``--hold-file``, ``--duration``. Honours ``ARUNNER_NOW`` (the clock seam) for
   every timestamp, so output is deterministic without real-time sleeps.

Stdlib only -- no dependency on arunner/engine/heartbeat.py (which also
exercises the no-helper worker contract).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

_TERMINAL = ("COMPLETED", "FAILED", "ABANDONED")


def _ts() -> str:
    override = os.environ.get("ARUNNER_NOW")
    if override:
        try:
            return datetime.fromtimestamp(
                float(override), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OverflowError, OSError):
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit(hb, obj):
    with open(hb, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, separators=(",", ":")) + "\n")


def _write_result(run_dir, tid, final):
    rf = os.path.join(run_dir, "result.txt")
    try:
        with open(rf, "w", encoding="utf-8") as fh:
            fh.write("stub %s -> %s\n" % (tid, final))
        return rf
    except OSError:
        return "(none)"


def _legacy(a) -> int:
    """The original stub, preserved byte-for-byte (taken when no simulator flag
    is set) so every pre-existing scenario stays identical."""
    hb, tid = a.heartbeat, a.task_id
    _emit(hb, {"ts": _ts(), "task_id": tid, "schema_version": "2",
               "label": a.label + ":start", "status": "STARTING"})
    for i in range(1, max(1, a.steps) + 1):
        time.sleep(max(0.0, a.sleep))
        _emit(hb, {"ts": _ts(), "task_id": tid, "schema_version": "2",
                   "label": "%s:work-%d" % (a.label, i), "status": "IN_PROGRESS"})
    if a.hold_file:
        deadline = time.monotonic() + a.hold_timeout
        while not os.path.exists(a.hold_file):
            if time.monotonic() > deadline:
                break
            time.sleep(0.1)
    rf = os.path.join(a.run_dir, "result.txt")
    try:
        with open(rf, "w", encoding="utf-8") as fh:
            fh.write("stub %s -> %s\n" % (tid, a.final))
    except OSError:
        rf = "(none)"
    _emit(hb, {"ts": _ts(), "task_id": tid, "schema_version": "2",
               "status": a.final, "result_file": rf,
               "summary": "stub %s finished %s" % (tid, a.final)})
    return 0


def _simulate(a, log_out) -> int:
    """The generalized simulator (any new flag set). Builds a status walk and
    emits it as heartbeat JSONL and/or plain log lines, with rate + noise."""
    final = {"done": "COMPLETED", "failed": "FAILED"}.get(
        a.final.lower(), a.final.upper())
    emit_hb = a.emit in ("jsonl", "mixed") and a.heartbeat
    emit_log = a.emit in ("log", "mixed")

    def hb(status, label, terminal=False):
        if not emit_hb:
            return
        obj = {"ts": _ts(), "task_id": a.task_id, "schema_version": "2",
               "status": status}
        if terminal:
            obj["result_file"] = _write_result(a.run_dir, a.task_id, status)
            obj["summary"] = "sim %s finished %s" % (a.task_id, status)
        else:
            obj["label"] = label
        _emit(a.heartbeat, obj)

    def log(text):
        if emit_log:
            log_out.write(text + "\n")
            log_out.flush()

    steps = a.steps
    if a.duration is not None and a.sleep > 0:
        steps = max(1, int(round(a.duration / a.sleep)))

    if a.status_seq:
        statuses = [s.strip().upper() for s in a.status_seq.split(",") if s.strip()]
    else:
        statuses = ["STARTING"] + ["IN_PROGRESS"] * max(1, steps)
        if not a.stall:
            statuses.append(final)

    step = 0
    for st in statuses:
        if st != "STARTING":
            time.sleep(max(0.0, a.sleep))
        terminal = st in _TERMINAL
        if terminal and a.hold_file:           # hold just before the terminal
            deadline = time.monotonic() + a.hold_timeout
            while not os.path.exists(a.hold_file) and time.monotonic() <= deadline:
                time.sleep(0.1)
        for r in range(max(1, a.rate)):
            if terminal:
                hb(st, None, terminal=True)
                log("%s [%s] %s finished" % (_ts(), st, a.label))
            else:
                label = "%s:%s-%d.%d" % (a.label,
                                         st.lower().replace("_", "-"), step, r)
                hb(st, label)
                log("%s [%s] %s step %d.%d working" % (_ts(), st, a.label, step, r))
        for k in range(max(0, a.noise)):
            log("%s noise: chatter %d.%d (irrelevant, ignore me)" % (_ts(), step, k))
        step += 1
    return a.exit_code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stub_worker")
    ap.add_argument("--heartbeat", default=None)
    ap.add_argument("--task-id", default="sim")
    ap.add_argument("--run-dir", default=".")
    ap.add_argument("--steps", type=int, default=2)
    ap.add_argument("--duration", type=float, default=None,
                    help="run ~this many seconds (overrides --steps via --sleep)")
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--final", default="COMPLETED",
                    help="terminal outcome: COMPLETED/FAILED/ABANDONED "
                         "(or the aliases done/failed)")
    ap.add_argument("--label", default="stub")
    ap.add_argument("--hold-file", default=None,
                    help="if set, wait until this file exists before the "
                         "terminal line (the controllable hold)")
    ap.add_argument("--hold-timeout", type=float, default=120.0)
    # instr 037 — the cross-platform simulator surface (all OPTIONAL; using any
    # of them takes the generalized path instead of the legacy stub).
    ap.add_argument("--emit", default="jsonl", choices=["jsonl", "log", "mixed"],
                    help="heartbeat JSONL (default), plain log lines, or both")
    ap.add_argument("--log-file", default=None,
                    help="log lines go here (default stdout) in log/mixed mode")
    ap.add_argument("--rate", type=int, default=1,
                    help="output lines emitted per step (verbose vs sparse)")
    ap.add_argument("--noise", type=int, default=0,
                    help="non-matching filler log lines interleaved per step")
    ap.add_argument("--status-seq", default=None,
                    help="comma-separated status walk overriding "
                         "STARTING,IN_PROGRESS*N,<final>")
    ap.add_argument("--exit-code", type=int, default=0,
                    help="process exit code (wrap doneness)")
    ap.add_argument("--stall", action="store_true",
                    help="skip the terminal line (the worker goes silent)")
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    legacy = (a.emit == "jsonl" and a.log_file is None and a.rate == 1
              and a.noise == 0 and a.status_seq is None and not a.stall
              and a.duration is None and a.exit_code == 0
              and a.final in ("COMPLETED", "FAILED", "ABANDONED"))
    if legacy and a.heartbeat:
        return _legacy(a)

    if a.log_file:
        with open(a.log_file, "a", encoding="utf-8") as fh:
            return _simulate(a, fh)
    return _simulate(a, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
