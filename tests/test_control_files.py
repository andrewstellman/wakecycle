"""FR-35 control-file convention + FR-36 PAUSE/RESUME (instr 019).

The control machinery reads a closed, named set at the run-dir root INSIDE the
not-stopped path, in a fixed precedence, with one-shot vs sticky kinds and a
Postel posture (unknown / unparseable ignored with a warning, never wedge).
PAUSE/RESUME are the first consumers. The load-bearing invariant: a STOP tick
stays FULLY read-only even with a stray control present, because controls are
read only inside the ``if not stop:`` gate.

MUTATION-VERIFY EVIDENCE (DEVELOPMENT_PROCESS §Mutation-test), instr 019:
  Pin: test_stop_wins_over_stray_control_fully_read_only.
  Mutation: in tick(), move the ``_apply_controls(...)`` call to BEFORE the
    ``if not stop:`` gate (so a STOP tick reads/consumes controls).
  Observed: the test FAILs -- the stray PAUSE is consumed and/or the paused
    flag is set on a STOP tick, breaking STOP read-only (FR-10). Restored -> OK.
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load_tick():
    spec = importlib.util.spec_from_file_location("tick_ctl", _ROOT / "bin" / "tick.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = _load_tick()


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._n = 0

    def tearDown(self):
        os.environ.pop("WAKECYCLE_RUNS_DIR", None)
        self._tmp.cleanup()

    def _fresh(self, n=2, pool=1):
        # unique runs dir per init avoids the same-second timestamp collision
        self._n += 1
        os.environ["WAKECYCLE_RUNS_DIR"] = str(self.tmp / ("runs%d" % self._n))
        entries = [{"task_id": "t%d" % i, "target_repo": "/tmp",
                    "dispatch_mode": "subagent", "worker_prompt": "x"}
                   for i in range(n)]
        pf = self.tmp / ("plan%d.json" % self._n)
        pf.write_text(json.dumps({"pool_size": pool, "entries": entries}))
        return Path(T.init_run(pf))

    def _status(self, rd):
        return json.loads((rd / "harness_status.json").read_text())


class PauseResumeTests(_Base):

    def test_pause_blocks_dispatch_without_terminating(self):
        rd = self._fresh(2, 1)
        (rd / "PAUSE").touch()
        out = T.tick(rd)
        self.assertEqual(out["dispatch_list"], [])      # no NEW dispatch
        self.assertTrue(out["paused"])
        self.assertFalse(out["done"])                   # not terminal
        self.assertEqual(self._status(rd)["runs"]["run-01"]["state"], "queued")
        self.assertFalse((rd / "PAUSE").exists())        # sticky -> consumed
        self.assertIn("PAUSED", out["status_table"])

    def test_resume_clears_pause_and_dispatch_continues(self):
        rd = self._fresh(2, 1)
        (rd / "PAUSE").touch(); T.tick(rd)               # paused, run-01 queued
        (rd / "RESUME").touch()
        out = T.tick(rd)
        self.assertFalse(out["paused"])
        self.assertFalse((rd / "RESUME").exists())       # consumed
        self.assertTrue(len(out["dispatch_list"]) >= 1)  # dispatch resumed
        self.assertEqual(self._status(rd)["runs"]["run-01"]["state"], "claimed")

    def test_pause_persists_across_restart(self):
        rd = self._fresh(2, 1)
        (rd / "PAUSE").touch(); T.tick(rd)
        # a fresh tick (simulating a ticker restart) re-reads status from disk
        self.assertTrue(self._status(rd).get("paused"))
        out = T.tick(rd)
        self.assertTrue(out["paused"])
        self.assertEqual(out["dispatch_list"], [])

    def test_stop_wins_over_stray_control_fully_read_only(self):
        # THE load-bearing invariant + the mutation pin.
        rd = self._fresh(2, 1)
        T.tick(rd)                                       # cycle 1
        before = self._status(rd)
        (rd / "STOP").touch()
        (rd / "PAUSE").touch()                           # stray control
        out = T.tick(rd)
        after = self._status(rd)
        self.assertTrue(out["stop"])
        self.assertEqual(before["cycle"], after["cycle"])           # cycle untouched
        self.assertEqual({k: v["state"] for k, v in before["runs"].items()},
                         {k: v["state"] for k, v in after["runs"].items()})
        self.assertTrue((rd / "PAUSE").exists())          # NOT consumed (never read)
        self.assertFalse(after.get("paused"))             # PAUSE never applied
        self.assertFalse(out["paused"])

    def test_unknown_control_warns_state_untouched(self):
        rd = self._fresh(1, 1)
        (rd / "FOOBAR").touch()                           # control-style, unknown
        warnings = T._apply_controls(rd, {})
        self.assertTrue(any("FOOBAR" in w for w in warnings))
        # real artifacts (lowercase / extensions) are NOT flagged as controls
        self.assertFalse(any("harness_status" in w or "plan" in w
                             for w in warnings))


class PrecedenceAndExtensibilityTests(_Base):

    def test_precedence_order_is_explicit_and_stop_first(self):
        # STOP is handled by the gate (not in the dispatched order); the
        # dispatched order is the canonical precedence after STOP.
        self.assertEqual(T._CONTROL_ORDER,
                         ("CANCEL", "PAUSE", "RESUME", "CADENCE", "POOL", "POLL-NOW"))
        self.assertEqual(T._ALL_CONTROLS[0], "STOP")

    def test_reserved_controls_recognized_but_unhandled(self):
        # CANCEL/CADENCE/POOL/POLL-NOW are recognized (in the set) but have no
        # handler yet -- a present one must NOT crash or mis-fire.
        rd = self._fresh(1, 1)
        for name in ("CANCEL", "CADENCE", "POOL", "POLL-NOW"):
            (rd / name).touch()
        status = self._status(rd)
        warnings = T._apply_controls(rd, status)          # must not raise
        # unhandled controls are left on disk (not consumed, not acted on)
        for name in ("CANCEL", "CADENCE", "POOL", "POLL-NOW"):
            self.assertTrue((rd / name).exists(), "%s should be left for its iteration" % name)
        self.assertFalse(status.get("paused"))
        # they are recognized, so they are NOT warned about as "unknown"
        self.assertFalse(any("CANCEL" in w or "CADENCE" in w for w in warnings))

    def test_resume_wins_when_both_present(self):
        rd = self._fresh(1, 1)
        (rd / "PAUSE").touch(); (rd / "RESUME").touch()
        out = T.tick(rd)
        self.assertFalse(out["paused"])                   # RESUME (later) clears
        self.assertFalse((rd / "PAUSE").exists())
        self.assertFalse((rd / "RESUME").exists())


if __name__ == "__main__":
    unittest.main()
