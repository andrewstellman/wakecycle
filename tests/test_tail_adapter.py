"""FR-41 tail-existing-log adapter + `adapter` selector + falsy-zero fold-in
(instr 026, Iteration 8).

`tail` watches a log a job already writes, surfaces its most recent line as the
IN_PROGRESS label, and decides doneness by PRECEDENCE: an optional success/
failure regex or sentinel-file overlay, then the authoritative process exit
(default COMPLETED on a clean exit). The engine never guesses a terminal from
text -- the adapter emits it. The one-field `adapter: "wrap"|"tail"` selector
synthesizes the worker_cmd so the operator wires nothing.

MUTATION-VERIFY EVIDENCE (DEVELOPMENT_PROCESS Mutation-test), instr 026:
  Pin 1: test_watcher_failure_marker_wins.
    Mutation: in _TailWatcher.poll, check success before failure on a line.
    Observed: a line matching BOTH markers maps to COMPLETED instead of FAILED
      -> the test FAILs. Restored OK.
  Pin 2: test_selector_routes_wrap_and_tail.
    Mutation: in _adapter_worker_cmd, return the wrap cmd for adapter 'tail'.
    Observed: a tail entry synthesizes a `wrap` invocation -> the test FAILs.
      Restored OK.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HB = _load("hb_fr41", "bin/heartbeat.py")
TICK = _load("tick_fr41", "bin/tick.py")


def _statuses(hb_path):
    return [json.loads(l)["status"]
            for l in Path(hb_path).read_text().splitlines() if l.strip()]


class _FakeProc:
    def __init__(self, rc=None):
        self._rc = rc
    def poll(self):
        return self._rc


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class LogTailTests(_Base):
    def test_incremental_new_lines_with_partial_buffering(self):
        log = self.tmp / "j.log"
        log.write_text("alpha\nbeta\n")
        t = HB._LogTail(log)
        self.assertEqual(t.new_lines(), ["alpha", "beta"])
        self.assertEqual(t.new_lines(), [])              # nothing new
        with open(log, "a") as fh:
            fh.write("gamma")                            # partial (no newline)
        self.assertEqual(t.new_lines(), [])              # buffered, not yet complete
        with open(log, "a") as fh:
            fh.write(" done\ndelta\n")
        self.assertEqual(t.new_lines(), ["gamma done", "delta"])

    def test_missing_log_is_empty(self):
        self.assertEqual(HB._LogTail(self.tmp / "nope.log").new_lines(), [])


class TailWatcherPrecedenceTests(_Base):
    def _watcher(self, **kw):
        log = self.tmp / "j.log"
        log.write_text("")
        return HB._TailWatcher(log_file=log, **kw), log

    def test_success_marker_completes(self):
        w, log = self._watcher(success_re=re.compile("BUILD OK"))
        self.assertIsNone(w.poll())
        log.write_text("step\nBUILD OK\n")
        self.assertEqual(w.poll(), "COMPLETED")

    def test_failure_marker_fails(self):
        w, log = self._watcher(failure_re=re.compile("FATAL"))
        log.write_text("FATAL: boom\n")
        self.assertEqual(w.poll(), "FAILED")

    def test_watcher_failure_marker_wins(self):
        # a line matching BOTH markers -> FAILED (failure precedence). PINNED.
        w, log = self._watcher(success_re=re.compile("done"),
                               failure_re=re.compile("error"))
        log.write_text("done but error happened\n")
        self.assertEqual(w.poll(), "FAILED")

    def test_sentinel_file_completes(self):
        sent = self.tmp / "DONE"
        w, _ = self._watcher(sentinel=sent)
        self.assertIsNone(w.poll())
        sent.write_text("")
        self.assertEqual(w.poll(), "COMPLETED")

    def test_process_exit_authoritative_default_completed(self):
        # no marker at all -> process exit decides (clean exit -> COMPLETED).
        w, _ = self._watcher(proc=_FakeProc(rc=0))
        self.assertEqual(w.poll(), "COMPLETED")

    def test_process_exit_nonzero_failed(self):
        w, _ = self._watcher(proc=_FakeProc(rc=2))
        self.assertEqual(w.poll(), "FAILED")

    def test_running_process_no_marker_is_none(self):
        w, _ = self._watcher(proc=_FakeProc(rc=None))   # still running, no marker
        self.assertIsNone(w.poll())

    def test_marker_beats_exit_code(self):
        # overlay is checked BEFORE process exit: a success marker reaps even if
        # the owned process would exit nonzero.
        w, log = self._watcher(success_re=re.compile("OK"), proc=_FakeProc(rc=1))
        log.write_text("all OK here\n")
        self.assertEqual(w.poll(), "COMPLETED")

    def test_dead_watched_pid_completes_by_default(self):
        # watching an external PID that's gone, no marker -> COMPLETED (clean).
        w, _ = self._watcher(pid=2**31 - 1)             # surely-dead PID
        self.assertEqual(w.poll(), "COMPLETED")


class PidBackstopTests(_Base):
    def test_record_pid_in_lock_updates_field(self):
        lock = self.tmp / "job.lock"
        lock.write_text(json.dumps({"task_id": "t", "pid": None, "claimed_ts": "x"}))
        HB._record_pid_in_lock(lock, 4242)
        obj = json.loads(lock.read_text())
        self.assertEqual(obj["pid"], 4242)
        self.assertEqual(obj["task_id"], "t")            # other fields preserved
        self.assertEqual(obj["claimed_ts"], "x")

    def test_record_pid_creates_lock_if_absent(self):
        lock = self.tmp / "new.lock"
        HB._record_pid_in_lock(lock, 99)
        self.assertEqual(json.loads(lock.read_text())["pid"], 99)


class FalsyZeroFixTests(_Base):
    def test_explicit_zero_grace_is_honored_not_defaulted(self):
        class A:
            launch_grace_minutes = 0
            stall_threshold_minutes = 0
        g, s = HB._grace_stall_secs(A())
        self.assertEqual((g, s), (0, 0))                 # explicit 0 honored, NOT 10/45
        # and the interval floors to 1s (Postel: reject non-positive -> floor)
        self.assertEqual(HB.keepalive_interval_secs(g, s), 1.0)

    def test_none_falls_back_to_defaults(self):
        class A:
            launch_grace_minutes = None
            stall_threshold_minutes = None
        g, s = HB._grace_stall_secs(A())
        self.assertEqual((g, s), (10 * 60, 45 * 60))


class TailEndToEndTests(_Base):
    """The owned-job path via main() (fast child writes its own log)."""

    def _tail(self, py, **flags):
        hb = self.tmp / "hb.ndjson"
        log = self.tmp / "job.log"
        argv = ["tail", "--task-id", "t", "--heartbeat-path", str(hb),
                "--log-file", str(log)]
        for k, v in flags.items():
            argv += ["--" + k.replace("_", "-"), v]
        argv += ["--", sys.executable, "-c", py.replace("LOG", str(log))]
        rc = HB.main(argv)
        return rc, hb

    def test_success_regex_completes(self):
        rc, hb = self._tail("open('LOG','w').write('working\\nBUILD OK\\n')",
                            success_regex="BUILD OK")
        self.assertEqual(_statuses(hb)[-1], "COMPLETED")
        self.assertEqual(rc, 0)

    def test_failure_regex_fails_even_on_clean_exit(self):
        rc, hb = self._tail("open('LOG','w').write('BUILD FAILED\\n')",
                            failure_regex="BUILD FAILED")
        self.assertEqual(_statuses(hb)[-1], "FAILED")
        self.assertEqual(rc, 1)

    def test_clean_exit_no_marker_defaults_completed(self):
        rc, hb = self._tail("open('LOG','w').write('did work\\n')")
        self.assertEqual(_statuses(hb)[-1], "COMPLETED")


class SelectorTests(_Base):
    def test_selector_routes_wrap_and_tail(self):
        wrap = TICK._adapter_worker_cmd({"adapter": "wrap", "command": ["c", "-x"]})
        self.assertEqual(wrap[2], "wrap")
        self.assertEqual(wrap[-3:], ["--", "c", "-x"])
        tail = TICK._adapter_worker_cmd(
            {"adapter": "tail", "log_path": "/l.log", "success_regex": "OK"})
        self.assertEqual(tail[2], "tail")
        self.assertIn("--log-file", tail); self.assertIn("/l.log", tail)
        self.assertIn("--lock-file", tail); self.assertIn("{LOCK_FILE}", tail)
        self.assertIn("--success-regex", tail)
        self.assertIsNone(TICK._adapter_worker_cmd({"dispatch_mode": "shell"}))

    def test_check_validates_adapter(self):
        d = str(self.tmp)
        good = {"entries": [
            {"task_id": "w", "target_repo": d, "dispatch_mode": "shell",
             "adapter": "wrap", "command": ["echo", "hi"]},
            {"task_id": "t", "target_repo": d, "dispatch_mode": "shell",
             "adapter": "tail", "log_path": "/tmp/x.log"}]}
        gp = self.tmp / "good.json"; gp.write_text(json.dumps(good))
        self.assertEqual(TICK.check_plan(gp), [])
        bad = {"entries": [
            {"task_id": "w", "target_repo": d, "adapter": "rocket"},     # bad enum
            {"task_id": "x", "target_repo": d, "adapter": "wrap"},       # missing command
            {"task_id": "y", "target_repo": d, "adapter": "tail"}]}      # missing log_path
        bp = self.tmp / "bad.json"; bp.write_text(json.dumps(bad))
        probs = TICK.check_plan(bp)
        self.assertTrue(any("adapter" in p and "rocket" in p for p in probs))
        self.assertTrue(any("command" in p for p in probs))
        self.assertTrue(any("log_path" in p for p in probs))


if __name__ == "__main__":
    unittest.main()
