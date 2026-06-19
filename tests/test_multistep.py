"""FR-62 — multi-step entries (ordered sub-runs) (instr 002).

A `steps:[...]` entry runs its steps SEQUENTIALLY within ONE pool slot. Each step
is a full FR-18 sub-run at run-NN/steps/step-MM/ (own heartbeat/manifest/result).
The engine dispatches only the CURRENT step per tick and advances on the
predecessor's clean terminal. Resume reads step_index and reaps-not-re-runs
completed steps (NFR-6). step_index/step_count surface on disk + in the table.

MUTATION-VERIFY EVIDENCE (instr 002):
  Pin: test_resume_does_not_rerun_completed_steps.
    Mutation: in _advance_multistep, drop the `if rp.exists(): return` guard in
      _reap_step so a completed step is re-reaped, OR re-scaffold+re-dispatch a
      completed step. Observed: step-01's heartbeat/result change across ticks
      and the test FAILs. Restored -> OK.
  Pin: test_pool1_second_entry_waits.
    Mutation: make _holds_slot return False for a started multi-step run between
      steps. Observed: the second entry dispatches while the first is mid-
      sequence (slot double-booked) and the test FAILs. Restored -> OK.
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
    spec = importlib.util.spec_from_file_location(
        "tick_fr62", _ROOT / "arunner" / "engine" / "tick.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = _load_tick()
_PH = "".join("{%s}" % p for p in T._PLACEHOLDERS)


def _step(label, **extra):
    e = {"label": label, "worker_prompt": label + " " + _PH}
    e.update(extra)
    return e


def _ms_entry(tid, nsteps, repo="/tmp/x"):
    return {"task_id": tid, "target_repo": repo, "dispatch_mode": "subagent",
            "steps": [_step("step%d" % (i + 1)) for i in range(nsteps)]}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.environ["ARUNNER_RUNS_DIR"] = str(self.tmp / "harness_runs")
        os.environ.pop("ARUNNER_NOW", None)

    def tearDown(self):
        os.environ.pop("ARUNNER_RUNS_DIR", None)
        self._tmp.cleanup()

    def _init(self, plan):
        pf = self.tmp / "plan.json"
        pf.write_text(json.dumps(plan))
        return Path(T.init_run(pf))

    def _status(self, rd):
        return json.loads((rd / "harness_status.json").read_text())

    def _complete_step(self, rd, run, m, status="COMPLETED"):
        sd = rd / run / "steps" / ("step-%02d" % (m + 1))
        line = json.dumps({"status": status, "task_id": "x",
                           "result_file": "r.md", "summary": "%s done" % status,
                           "label": "lbl"})
        with (sd / "heartbeat.ndjson").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class StructureTests(_Base):
    def test_init_records_step_index_count_and_scaffolds_first_step(self):
        rd = self._init({"entries": [_ms_entry("t1", 3)]})
        s = self._status(rd)
        r = s["runs"]["run-01"]
        self.assertEqual(r["step_index"], 0)
        self.assertEqual(r["step_count"], 3)
        sd = rd / "run-01" / "steps" / "step-01"
        self.assertTrue((sd / "heartbeat.ndjson").is_file())
        mf = json.loads((sd / "manifest.json").read_text())
        self.assertEqual(mf["step_index"], 0)
        self.assertEqual(mf["step_count"], 3)

    def test_table_shows_step_n_of_m(self):
        rd = self._init({"entries": [_ms_entry("t1", 3)]})
        out = T.tick(rd)
        self.assertIn("s1/3", out["status_table"])


class OrderingTests(_Base):
    def test_three_step_ordering_advances_one_per_terminal(self):
        rd = self._init({"pool_size": 2, "entries": [_ms_entry("t1", 3)]})
        out = T.tick(rd)
        d = [x for x in out["dispatch_list"]]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["run"], "run-01")
        self.assertEqual(d[0]["step"], "step-01")
        for m in range(3):
            self.assertEqual(self._status(rd)["runs"]["run-01"]["step_index"], m)
            self._complete_step(rd, "run-01", m)
            out = T.tick(rd)
            if m < 2:
                steps = [x["step"] for x in out["dispatch_list"]]
                self.assertEqual(steps, ["step-%02d" % (m + 2)])
        self.assertEqual(self._status(rd)["runs"]["run-01"]["state"], "completed")
        self.assertTrue(self._status(rd)["done"])

    def test_pool1_second_entry_waits(self):
        rd = self._init({"pool_size": 1,
                         "entries": [_ms_entry("t1", 3), _ms_entry("t2", 2)]})
        out = T.tick(rd)
        self.assertEqual([x["run"] for x in out["dispatch_list"]], ["run-01"])
        # drive run-01 through all 3 steps; run-02 must NOT dispatch until done
        for m in range(3):
            self._complete_step(rd, "run-01", m)
            out = T.tick(rd)
            if m < 2:
                self.assertTrue(all(x["run"] == "run-01" for x in out["dispatch_list"]),
                                "run-02 dispatched while run-01 mid-sequence")
        # run-01 done -> the freed slot now lets run-02 start
        self.assertEqual(self._status(rd)["runs"]["run-01"]["state"], "completed")
        self.assertEqual([x["run"] for x in out["dispatch_list"]], ["run-02"])
        self.assertEqual(out["dispatch_list"][0]["step"], "step-01")


class ResumeTests(_Base):
    def test_resume_does_not_rerun_completed_steps(self):
        rd = self._init({"pool_size": 1, "entries": [_ms_entry("t1", 3)]})
        T.tick(rd)
        self._complete_step(rd, "run-01", 0)
        T.tick(rd)                                  # reap step-01, dispatch step-02
        step1_hb = (rd / "run-01" / "steps" / "step-01" / "heartbeat.ndjson").read_text()
        step1_res = (rd / "run-01" / "steps" / "step-01" / "result.json").read_text()
        # a plain re-tick (resume) must NOT re-run or re-reap the completed step
        T.tick(rd)
        T.tick(rd)
        self.assertEqual(step1_hb, (rd / "run-01" / "steps" / "step-01"
                                    / "heartbeat.ndjson").read_text())
        self.assertEqual(step1_res, (rd / "run-01" / "steps" / "step-01"
                                     / "result.json").read_text())
        self.assertEqual(self._status(rd)["runs"]["run-01"]["step_index"], 1)
        # now complete step-02 -> step-03 dispatches (not step-01 again)
        self._complete_step(rd, "run-01", 1)
        out = T.tick(rd)
        self.assertEqual([x["step"] for x in out["dispatch_list"]], ["step-03"])

    def test_crash_between_reap_and_dispatch_resumes(self):
        rd = self._init({"pool_size": 1, "entries": [_ms_entry("t1", 3)]})
        T.tick(rd)
        self._complete_step(rd, "run-01", 0)
        # Simulate a crash AFTER reap+advance but BEFORE step-02 dispatched:
        # craft that on-disk state by hand (step-01 reaped, index advanced,
        # state queued, no step-02 claim).
        s = self._status(rd)
        T._reap_step(rd, "run-01", 0, "COMPLETED",
                     rd / "run-01" / "steps" / "step-01" / "heartbeat.ndjson")
        s["runs"]["run-01"].update({"step_index": 1, "state": "queued",
                                    "started": True, "claimed_at": None})
        (rd / "harness_status.json").write_text(json.dumps(s))
        out = T.tick(rd)                            # resume tick
        self.assertEqual([x["step"] for x in out["dispatch_list"]], ["step-02"])
        self.assertEqual(self._status(rd)["runs"]["run-01"]["state"], "claimed")


class FailureTests(_Base):
    def test_failed_step_terminals_entry_with_per_step_failure(self):
        rd = self._init({"pool_size": 1, "entries": [_ms_entry("t1", 3)]})
        T.tick(rd)
        self._complete_step(rd, "run-01", 0)
        T.tick(rd)                                  # at step-02
        self._complete_step(rd, "run-01", 1, status="FAILED")
        T.tick(rd)
        r = self._status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "failed")
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertEqual(rec["terminal_status"], "FAILED")
        self.assertEqual(rec["steps"][1]["terminal_status"], "FAILED")
        # step-03 was never dispatched
        self.assertFalse((rd / "run-01" / "steps" / "step-03" / "result.json").exists())


class SummaryTests(_Base):
    def test_summary_lists_per_step_statuses(self):
        rd = self._init({"pool_size": 1, "entries": [_ms_entry("t1", 2)]})
        T.tick(rd)
        self._complete_step(rd, "run-01", 0)
        T.tick(rd)
        self._complete_step(rd, "run-01", 1)
        T.tick(rd)                                  # entry completes -> SUMMARY
        self.assertTrue(self._status(rd)["done"])
        sj = json.loads((rd / "summary.json").read_text())
        steps = sj["jobs"][0]["steps"]
        self.assertEqual([s["terminal_status"] for s in steps],
                         ["COMPLETED", "COMPLETED"])
        self.assertIn("steps:", (rd / "SUMMARY.md").read_text())


class CheckTests(_Base):
    def test_check_rejects_prompt_and_steps_both(self):
        e = {"task_id": "t1", "target_repo": str(self.tmp),
             "dispatch_mode": "subagent", "worker_prompt": _PH,
             "steps": [_step("a")]}
        pf = self.tmp / "p.json"
        pf.write_text(json.dumps({"entries": [e]}))
        probs = T.check_plan(pf)
        self.assertTrue(any("exactly one" in p for p in probs), probs)

    def test_check_step_needs_a_prompt_source(self):
        e = {"task_id": "t1", "target_repo": str(self.tmp), "dispatch_mode": "subagent",
             "steps": [{"label": "bare"}]}
        pf = self.tmp / "p.json"
        pf.write_text(json.dumps({"entries": [e]}))
        probs = T.check_plan(pf)
        self.assertTrue(any("exactly one prompt source" in p for p in probs), probs)


if __name__ == "__main__":
    unittest.main()
