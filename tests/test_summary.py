"""FR-45 SUMMARY roll-up (instr 029, Iteration 11).

On the TRANSITION into done, the engine writes SUMMARY.md (human) + summary.json
(machine, schema_version'd) to the run-dir, sourced entirely from on-disk
records. The load-bearing guard: a post-done idempotent re-tick stays cycle-only
(FR-6) -- an already-done run with SUMMARY present is NOT rewritten.

The clock seam (WAKECYCLE_NOW) makes durations deterministic without sleeping.

MUTATION-VERIFY EVIDENCE (DEVELOPMENT_PROCESS Mutation-test), instr 029:
  Pin: test_retick_after_done_is_cycle_only.
    Mutation: in tick(), drop the `(not was_done or not SUMMARY.md exists)`
      guard so SUMMARY is (re)written on every done tick.
    Observed: a redundant re-tick rewrites summary.json with a new generated_ts
      -> the byte-compare FAILs. Restored OK.
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
    spec = importlib.util.spec_from_file_location("tick_fr45", _ROOT / "bin" / "tick.py")
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
        os.environ.pop("WAKECYCLE_NOW", None)
        self._tmp.cleanup()

    def _fresh(self, n=1):
        self._n += 1
        os.environ["WAKECYCLE_RUNS_DIR"] = str(self.tmp / ("runs%d" % self._n))
        entries = [{"task_id": "t%d" % i, "target_repo": "/tmp",
                    "dispatch_mode": "subagent", "worker_prompt": "x"}
                   for i in range(n)]
        pf = self.tmp / ("plan%d.json" % self._n)
        pf.write_text(json.dumps({"pool_size": n, "entries": entries}))
        return Path(T.init_run(pf))

    def _status(self, rd):
        return json.loads((rd / "harness_status.json").read_text())

    def _complete(self, rd, run, task_id, result_file="/tmp/out.md", summary="did it"):
        # write a terminal COMPLETED heartbeat the engine will reap
        hb = rd / run / "heartbeat.ndjson"
        hb.write_text(json.dumps({
            "ts": "2001-09-09T01:46:40Z", "task_id": task_id, "schema_version": "2",
            "status": "COMPLETED", "result_file": result_file, "summary": summary}) + "\n")

    def _drive_to_done(self, n=1):
        rd = self._fresh(n)
        os.environ["WAKECYCLE_NOW"] = "1000000000"      # dispatch tick -> claimed_at
        T.tick(rd)
        for i in range(n):
            self._complete(rd, "run-%02d" % (i + 1), "t%d" % i)
        os.environ["WAKECYCLE_NOW"] = "1000000005"      # reap tick -> reaped_ts (+5s)
        out = T.tick(rd)
        return rd, out


class SummaryWriteTests(_Base):

    def test_both_files_written_on_done_transition(self):
        rd, out = self._drive_to_done(n=2)
        self.assertTrue(out["done"])
        self.assertTrue((rd / "SUMMARY.md").is_file())
        self.assertTrue((rd / "summary.json").is_file())
        sj = json.loads((rd / "summary.json").read_text())
        self.assertEqual(sj["schema_version"], T.SUMMARY_SCHEMA_VERSION)
        self.assertTrue(sj["done"])
        self.assertEqual(sj["counts"]["completed"], 2)
        states = {j["run"]: j["state"] for j in sj["jobs"]}
        self.assertEqual(states, {"run-01": "completed", "run-02": "completed"})
        # result pointers + deterministic duration sourced from disk records
        j0 = next(j for j in sj["jobs"] if j["run"] == "run-01")
        self.assertEqual(j0["result_file"], "/tmp/out.md")
        self.assertEqual(j0["summary"], "did it")
        self.assertEqual(j0["duration_seconds"], 5.0)        # 1000000005 - 1000000000
        # the human capstone carries the same facts
        md = (rd / "SUMMARY.md").read_text()
        self.assertIn("run-01", md)
        self.assertIn("completed", md)
        self.assertIn("completed: 2", md)

    def test_retick_after_done_is_cycle_only(self):
        rd, _ = self._drive_to_done(n=1)
        before_json = (rd / "summary.json").read_bytes()
        before_md = (rd / "SUMMARY.md").read_bytes()
        cycle_before = self._status(rd)["cycle"]
        os.environ["WAKECYCLE_NOW"] = "1000000099"           # later clock
        T.tick(rd)                                           # redundant re-tick
        # SUMMARY is NOT rewritten (would change generated_ts if it were)
        self.assertEqual((rd / "summary.json").read_bytes(), before_json)
        self.assertEqual((rd / "SUMMARY.md").read_bytes(), before_md)
        # ... but the re-tick is still a real (cycle-incrementing) tick
        self.assertEqual(self._status(rd)["cycle"], cycle_before + 1)

    def test_summary_backfilled_if_absent(self):
        rd, _ = self._drive_to_done(n=1)
        (rd / "SUMMARY.md").unlink()
        (rd / "summary.json").unlink()
        os.environ["WAKECYCLE_NOW"] = "1000000200"
        T.tick(rd)                                           # done + absent -> backfill
        self.assertTrue((rd / "SUMMARY.md").is_file())
        self.assertTrue((rd / "summary.json").is_file())

    def test_not_written_before_done(self):
        rd = self._fresh(n=2)
        os.environ["WAKECYCLE_NOW"] = "1000000000"
        T.tick(rd)                                           # dispatch only, not done
        self._complete(rd, "run-01", "t0")
        os.environ["WAKECYCLE_NOW"] = "1000000005"
        out = T.tick(rd)                                     # one of two reaped, NOT done
        self.assertFalse(out["done"])
        self.assertFalse((rd / "SUMMARY.md").exists())
        self.assertFalse((rd / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
