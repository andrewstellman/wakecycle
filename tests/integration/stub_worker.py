#!/usr/bin/env python3
"""Controllable stub worker for the integration scenarios (instr 018, FR-51).

Heartbeat-then-HOLD: emits STARTING + IN_PROGRESS x N, then -- if a
``--hold-file`` is given -- polls until that file appears before emitting its
terminal line. That hold is what iterations 2-5 (PAUSE mid-run, stall, CANCEL)
act against: the scenario can keep a worker open, do something, then release
it by creating the hold-file. Without ``--hold-file`` it terminates as soon as
its steps are done (the complete-on-launch behaviour the simple scenarios use).

It appends raw single-line JSON heartbeats with the standard library only --
no dependency on bin/heartbeat.py -- which also exercises the no-helper worker
contract. Honours WAKECYCLE_NOW for the timestamp (the clock seam).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone


def _ts() -> str:
    override = os.environ.get("WAKECYCLE_NOW")
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stub_worker")
    ap.add_argument("--heartbeat", required=True)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--steps", type=int, default=2)
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--final", default="COMPLETED",
                    choices=["COMPLETED", "FAILED", "ABANDONED"])
    ap.add_argument("--label", default="stub")
    ap.add_argument("--hold-file", default=None,
                    help="if set, wait until this file exists before the "
                         "terminal line (the controllable hold)")
    ap.add_argument("--hold-timeout", type=float, default=120.0)
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

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


if __name__ == "__main__":
    sys.exit(main())
