"""FR-59 -- read-only disk monitor (`arunner monitor`).

A strictly read-only sidecar that re-renders the SHARED `_format_table` from a
run-dir's externalized state every interval, so an operator can watch a run from
a second terminal even while the orchestrator is blocked on a long synchronous
subagent (C-6). It advances nothing and writes nothing.

MUTATION PINS (instr 047):
  * test_never_writes -- the load-bearing safety property: the monitor creates
    no file, modifies none, takes no `.tick.lock`, drops no control file. A
    mutation that introduces ANY write must make this bite.
  * test_reuses_renderer_no_fork -- the monitor's table IS `_format_table`'s
    output (no divergent copy).
  * test_freshness_split -- ACTIVITY/HB-AGE are live (heartbeat files) while
    lifecycle/counts are per-tick; the "as of last tick" age advances.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import tempfile

import arunner.cli as CLI
from arunner.cli import render_monitor_frame, TICK


def _heartbeat(run_dir, run, status="IN_PROGRESS", label="step 1"):
    hb = Path(run_dir) / run / "heartbeat.ndjson"
    hb.parent.mkdir(parents=True, exist_ok=True)
    with hb.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-06-15T00:00:00Z", "task_id": run,
                             "status": status, "label": label}) + "\n")


def _static_run_dir(done=False, last_tick_wall=1000.0):
    """A minimal, STATIC run-dir (no live workers) so the never-writes pin is not
    confounded by detached workers still writing."""
    rd = Path(tempfile.mkdtemp())
    runs = {"run-01": {"task_id": "a", "job_id": "job-00001", "target_repo": ".",
                       "state": "completed" if done else "running",
                       "last_hb_status": "COMPLETED" if done else "IN_PROGRESS",
                       "claimed_at": 1.0},
            "run-02": {"task_id": "b", "job_id": "job-00002", "target_repo": ".",
                       "state": "queued", "last_hb_status": None, "claimed_at": None}}
    counts = {"queued": 0 if done else 1, "claimed": 0, "running": 0 if done else 1,
              "stalled": 0, "completed": 2 if done else 0, "failed": 0,
              "auth_or_launch_failed": 0, "abandoned": 0}
    status = {"cycle": 3, "pool_size": 2, "counts": counts, "done": done,
              "runs": runs, "last_tick_wall": last_tick_wall}
    (rd / "harness_status.json").write_text(json.dumps(status), encoding="utf-8")
    plan = {"pool_size": 2, "entries": [
        {"task_id": "a", "target_repo": ".", "dispatch_mode": "shell",
         "adapter": "wrap", "command": ["x"]},
        {"task_id": "b", "target_repo": ".", "dispatch_mode": "shell",
         "adapter": "wrap", "command": ["y"]}]}
    (rd / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    _heartbeat(rd, "run-01", "IN_PROGRESS", "step 1")
    return rd


def _snapshot(rd):
    """(path -> (mtime_ns, size, sha-ish)) for every file under the run-dir."""
    out = {}
    for p in sorted(Path(rd).rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(rd))] = (st.st_mtime_ns, st.st_size,
                                           hash(p.read_bytes()))
    return out


def _mon_args(rd, **kw):
    ns = argparse.Namespace(run_dir=str(rd), interval=2.0, once=False,
                            no_clear=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class NeverWrites(unittest.TestCase):

    def tearDown(self):
        os.environ.pop("ARUNNER_NOW", None)

    def test_never_writes(self):                            # PIN
        rd = _static_run_dir()
        before = _snapshot(rd)
        # render several frames + a full --once loop pass
        for _ in range(3):
            render_monitor_frame(rd, interval=2.0, now=2000.0)
        out = io.StringIO()
        with redirect_stdout(out):
            CLI.cmd_monitor(_mon_args(rd, once=True))
        after = _snapshot(rd)
        self.assertEqual(before, after, "monitor mutated the run-dir")
        self.assertFalse((rd / ".tick.lock").exists(), "monitor created .tick.lock")
        for ctrl in ("STOP", "PAUSE", "RESUME", "CANCEL", "POOL", "CADENCE",
                     "POLL-NOW"):
            self.assertFalse((rd / ctrl).exists(),
                             "monitor dropped a %s control file" % ctrl)


class RendererReuse(unittest.TestCase):

    def test_reuses_renderer_no_fork(self):                 # PIN
        rd = _static_run_dir()
        status = json.loads((rd / "harness_status.json").read_text())
        plan = json.loads((rd / "plan.json").read_text())
        expected = TICK._format_table(rd, status, plan, terminal=False)
        text, terminal, ok = render_monitor_frame(rd, interval=2.0, now=2000.0)
        self.assertTrue(ok)
        # the frame is the monitor-owned freshness header + the SHARED table
        self.assertTrue(text.endswith(expected),
                        "monitor table diverged from _format_table")
        self.assertIn("monitor: refresh", text.split("\n", 1)[0])


class FreshnessSplit(unittest.TestCase):

    def tearDown(self):
        os.environ.pop("ARUNNER_NOW", None)

    def test_activity_live_lifecycle_per_tick(self):        # PIN
        rd = _static_run_dir(last_tick_wall=1000.0)
        t1, _, _ = render_monitor_frame(rd, interval=2.0, now=1010.0)
        # a heartbeat lands (a live worker), but NO tick rewrites harness_status
        _heartbeat(rd, "run-01", "IN_PROGRESS", "step 2 of 5")
        t2, _, _ = render_monitor_frame(rd, interval=2.0, now=1100.0)
        # ACTIVITY moved (heartbeats are live)...
        self.assertIn("step 1", t1)
        self.assertIn("step 2 of 5", t2)
        self.assertNotIn("step 2 of 5", t1)
        # ...lifecycle/counts did NOT change (same harness_status.json)
        for line in ("Queue:", "Completed: 0"):
            self.assertIn(line, t1)
            self.assertIn(line, t2)
        # the "as of last tick" age ADVANCES with wall-clock though status is fixed
        self.assertIn("as of last tick: 10s ago", t1)
        self.assertIn("as of last tick: 1m ago", t2)


class TerminalAndOnce(unittest.TestCase):

    def tearDown(self):
        os.environ.pop("ARUNNER_NOW", None)

    def _run(self, args):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = CLI.cmd_monitor(args)
        return rc, out.getvalue()

    def test_done_status_renders_once_and_exits(self):
        rd = _static_run_dir(done=True)
        rc, txt = self._run(_mon_args(rd))         # NOT --once: loop must self-exit
        self.assertEqual(rc, 0)
        self.assertIn("Run-Dir", txt)

    def test_stop_file_exits(self):
        rd = _static_run_dir(done=False)
        (rd / "STOP").write_text("", encoding="utf-8")
        rc, _ = self._run(_mon_args(rd))
        self.assertEqual(rc, 0)

    def test_once_returns_after_one_frame(self):
        rd = _static_run_dir(done=False)           # not terminal, but --once exits
        rc, txt = self._run(_mon_args(rd, once=True))
        self.assertEqual(rc, 0)
        self.assertEqual(txt.count("Run-Dir"), 1)

    def test_keyboardinterrupt_exits_clean(self):
        rd = _static_run_dir(done=False)
        orig = time.sleep
        time.sleep = lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt())
        try:
            rc, _ = self._run(_mon_args(rd))       # non-terminal -> would loop+sleep
        finally:
            time.sleep = orig
        self.assertEqual(rc, 0)                     # no traceback, clean exit


class ReadTolerance(unittest.TestCase):

    def test_garbage_status_frame_is_skipped(self):
        rd = _static_run_dir()
        (rd / "harness_status.json").write_text("{ not json", encoding="utf-8")
        text, terminal, ok = render_monitor_frame(rd, interval=2.0, now=1.0)
        self.assertFalse(ok)                        # skipped, no exception
        self.assertIsNone(text)

    def test_missing_status_frame_is_skipped(self):
        rd = _static_run_dir()
        (rd / "harness_status.json").unlink()
        _t, _term, ok = render_monitor_frame(rd, interval=2.0, now=1.0)
        self.assertFalse(ok)

    def test_once_with_no_status_exits_2(self):
        rd = Path(tempfile.mkdtemp())               # empty: no harness_status.json
        out = io.StringIO()
        with redirect_stdout(out):
            rc = CLI.cmd_monitor(_mon_args(rd, once=True))
        self.assertEqual(rc, 2)


class ClearModes(unittest.TestCase):

    def _run(self, args):
        out = io.StringIO()
        with redirect_stdout(out):
            CLI.cmd_monitor(args)
        return out.getvalue()

    def test_default_emits_ansi_clear(self):
        rd = _static_run_dir(done=True)
        txt = self._run(_mon_args(rd, once=True))
        self.assertIn("\033[2J", txt)

    def test_no_clear_appends_with_separator(self):
        rd = _static_run_dir(done=True)
        txt = self._run(_mon_args(rd, once=True, no_clear=True))
        self.assertNotIn("\033[2J", txt)
        self.assertIn("-" * 60, txt)


if __name__ == "__main__":
    unittest.main()
