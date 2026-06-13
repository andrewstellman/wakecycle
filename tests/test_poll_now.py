"""FR-38 POLL-NOW (instr 022, Iteration 4).

POLL-NOW is a one-shot, value-less control (lowest precedence in
_CONTROL_ORDER). When a tick observes it and the run is NOT paused, the tick
collapses next_tick_minutes to the immediate minimum (overriding whatever
_next_cadence would return -- an idle multiplier OR an Iter-3 CADENCE override)
and consumes the file. It does NOT pierce PAUSE: while paused it is inert and
LEFT on disk to fire after RESUME (paused dominates, per the FR-35 precedence).

MUTATION-VERIFY EVIDENCE (DEVELOPMENT_PROCESS Mutation-test), instr 022:
  Pin: test_poll_now_inert_under_pause.
    Mutation: drop the `if status.get("paused"): return` guard in
      _ctl_poll_now (so POLL-NOW fires even while paused).
    Observed: next_tick_minutes collapses to 1 on a PAUSED tick and POLL-NOW is
      consumed -> the test FAILs (expects the un-collapsed paused cadence and
      the file still present). Restored -> OK.
  Pin: test_poll_now_collapses_cadence_when_active.
    Mutation: in tick(), drop the `_POLL_NOW_CADENCE_MINUTES if poll_now else`
      branch (always use _next_cadence).
    Observed: next_tick_minutes stays at the plan interval (9) instead of
      collapsing to 1 -> FAIL. Restored -> OK.
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
    spec = importlib.util.spec_from_file_location("tick_fr38", _ROOT / "bin" / "tick.py")
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

    def _fresh(self, n=1, pool=1, interval=None, idle_mult=None):
        self._n += 1
        os.environ["WAKECYCLE_RUNS_DIR"] = str(self.tmp / ("runs%d" % self._n))
        entries = [{"task_id": "t%d" % i, "target_repo": "/tmp",
                    "dispatch_mode": "subagent", "worker_prompt": "x"}
                   for i in range(n)]
        plan = {"pool_size": pool, "entries": entries}
        if interval is not None:
            plan["tick_interval_minutes"] = interval
        if idle_mult is not None:
            plan["idle_tick_multiplier"] = idle_mult
        pf = self.tmp / ("plan%d.json" % self._n)
        pf.write_text(json.dumps(plan))
        return Path(T.init_run(pf))

    def _status(self, rd):
        return json.loads((rd / "harness_status.json").read_text())


class PollNowTests(_Base):

    def test_poll_now_collapses_cadence_when_active(self):
        # In-flight after dispatch -> normal cadence would be the interval (9).
        rd = self._fresh(n=1, pool=1, interval=9)
        base = T.tick(rd)                                 # no POLL-NOW yet
        self.assertEqual(base["next_tick_minutes"], 9)    # normal cadence
        (rd / "POLL-NOW").touch()
        out = T.tick(rd)
        self.assertEqual(out["next_tick_minutes"], 1)     # collapsed to the minimum
        self.assertFalse((rd / "POLL-NOW").exists())      # one-shot: consumed
        self.assertNotIn("_poll_now", self._status(rd))   # transient: never persisted
        # consumed-once: a later tick (no POLL-NOW) is back to normal cadence
        out2 = T.tick(rd)
        self.assertEqual(out2["next_tick_minutes"], 9)

    def test_poll_now_overrides_idle_multiplier(self):
        # pool 0 -> nothing dispatches -> idle: normal cadence = interval*idle.
        rd = self._fresh(n=1, pool=0, interval=3, idle_mult=4)
        base = T.tick(rd)
        self.assertEqual(base["next_tick_minutes"], 12)   # 3 * 4 idle multiplier
        (rd / "POLL-NOW").touch()
        out = T.tick(rd)
        self.assertEqual(out["next_tick_minutes"], 1)     # collapse beats the idle multiplier
        self.assertFalse((rd / "POLL-NOW").exists())

    def test_poll_now_overrides_cadence_override(self):
        # Composes with Iter 3: POLL-NOW beats even a CADENCE override.
        rd = self._fresh(n=1, pool=1, interval=2)
        (rd / "CADENCE").write_text("8"); T.tick(rd)      # override -> 8
        self.assertEqual(self._status(rd)["tick_interval_override"], 8)
        (rd / "POLL-NOW").touch()
        out = T.tick(rd)
        self.assertEqual(out["next_tick_minutes"], 1)     # POLL-NOW wins for this envelope
        # the override SURVIVES (POLL-NOW is one-shot, not a cadence reset)
        out2 = T.tick(rd)
        self.assertEqual(out2["next_tick_minutes"], 8)

    def test_poll_now_inert_under_pause(self):
        # idle_mult 1 so the un-collapsed paused cadence is exactly the interval.
        rd = self._fresh(n=1, pool=1, interval=7, idle_mult=1)
        (rd / "PAUSE").touch()
        (rd / "POLL-NOW").touch()
        out = T.tick(rd)
        self.assertTrue(out["paused"])
        self.assertEqual(out["next_tick_minutes"], 7)     # NOT collapsed -- paused dominates
        self.assertNotEqual(out["next_tick_minutes"], 1)
        self.assertTrue((rd / "POLL-NOW").exists())       # inert: LEFT to wait for RESUME
        self.assertNotIn("_poll_now", self._status(rd))
        # ... and it fires once RESUMED: drop RESUME, next tick collapses + consumes
        (rd / "RESUME").touch()
        out2 = T.tick(rd)
        self.assertFalse(out2["paused"])
        self.assertEqual(out2["next_tick_minutes"], 1)    # now it fires
        self.assertFalse((rd / "POLL-NOW").exists())      # and is consumed


if __name__ == "__main__":
    unittest.main()
