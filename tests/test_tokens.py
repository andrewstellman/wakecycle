"""FR-65 — per-run and per-sub-run token reporting (input + output) (instr 002).

The engine reads EXACTLY ``data.usage = {input_tokens, output_tokens}`` off
heartbeats into TOP-LEVEL result fields, sums them per step/entry/run, and
surfaces a TOKENS column (status table + monitor) + SUMMARY. Reporting-only:
never interpreted for control flow, never changes the {done, stop} outcome.
Degrades honestly (NFR-12): no usage -> '-' (never a fabricated 0); partial ->
labeled "partial (N of M jobs reported)"; malformed -> skipped-with-warning.

MUTATION-VERIFY EVIDENCE (instr 002):
  Pin: test_additive_rollup_multistep.
    Mutation: in _add_tok, replace `acc[i] = (acc[i] or 0) + v` with
      `acc[i] = v` (overwrite instead of sum). Observed: a 2-step entry rolls up
      the LAST step's count, not the sum, and the test FAILs. Restored -> OK.
  Pin: test_no_usage_shows_dash_never_zero.
    Mutation: in _tokens_cell / _run_tokens, default a missing count to 0.
      Observed: an unreported run shows 0/0 and the test FAILs (NFR-12 honesty).
      Restored -> OK.
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
        "tick_fr65", _ROOT / "arunner" / "engine" / "tick.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = _load_tick()
_PH = "".join("{%s}" % p for p in T._PLACEHOLDERS)


def _entry(tid):
    return {"task_id": tid, "target_repo": "/tmp", "dispatch_mode": "subagent",
            "worker_prompt": "go " + _PH}


def _ms_entry(tid, n):
    return {"task_id": tid, "target_repo": "/tmp", "dispatch_mode": "subagent",
            "steps": [{"label": "s%d" % i, "worker_prompt": "s%d " % i + _PH}
                      for i in range(n)]}


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

    def _hb(self, path, status="COMPLETED", usage="ok", **f):
        line = {"status": status, "task_id": "x", "result_file": "r",
                "summary": "s"}
        if usage == "ok":
            line["data"] = {"usage": {"input_tokens": 100, "output_tokens": 20}}
        elif usage == "bad":
            line["data"] = {"usage": {"input_tokens": "lots"}}
        elif isinstance(usage, dict):
            line["data"] = {"usage": usage}
        line.update(f)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")


class SinglePromptTests(_Base):
    def test_usage_lands_in_result_and_table(self):
        rd = self._init({"pool_size": 1, "entries": [_entry("t1")]})
        T.tick(rd)
        self._hb(rd / "run-01" / "heartbeat.ndjson")
        out = T.tick(rd)
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertEqual(rec["input_tokens"], 100)
        self.assertEqual(rec["output_tokens"], 20)
        self.assertIn("100/20", out["status_table"])

    def test_no_usage_shows_dash_never_zero(self):
        rd = self._init({"pool_size": 1, "entries": [_entry("t1")]})
        T.tick(rd)
        self._hb(rd / "run-01" / "heartbeat.ndjson", usage=None)
        out = T.tick(rd)
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertNotIn("input_tokens", rec)       # never fabricated
        # TOKENS column never shows a fabricated 0/0 for an unreported run
        self.assertNotIn("0/0", out["status_table"])
        sj = json.loads((rd / "summary.json").read_text())
        self.assertIsNone(sj["tokens"]["input_tokens"])
        self.assertEqual(sj["tokens"]["label"], "no token usage reported")

    def test_malformed_usage_skipped_with_warning(self):
        rd = self._init({"pool_size": 1, "entries": [_entry("t1")]})
        T.tick(rd)
        self._hb(rd / "run-01" / "heartbeat.ndjson", usage="bad")
        T.tick(rd)
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertNotIn("input_tokens", rec)       # malformed -> skipped
        self.assertEqual(self._status(rd)["runs"]["run-01"]["state"], "completed")
        self.assertIn("malformed data.usage",
                      (rd / "harness_tick.log").read_text())

    def test_partial_run_labeled(self):
        rd = self._init({"pool_size": 2, "entries": [_entry("t1"), _entry("t2")]})
        T.tick(rd)
        self._hb(rd / "run-01" / "heartbeat.ndjson")           # reports usage
        self._hb(rd / "run-02" / "heartbeat.ndjson", usage=None)  # no usage
        T.tick(rd)
        sj = json.loads((rd / "summary.json").read_text())
        self.assertEqual(sj["tokens"]["label"], "partial (1 of 2 jobs reported)")
        self.assertEqual(sj["tokens"]["input_tokens"], 100)

    def test_tokens_never_change_done(self):
        # an IN_PROGRESS line carrying data.usage must NOT make a run done.
        rd = self._init({"pool_size": 1, "entries": [_entry("t1")]})
        T.tick(rd)
        self._hb(rd / "run-01" / "heartbeat.ndjson", status="IN_PROGRESS")
        out = T.tick(rd)
        self.assertFalse(out["done"])               # tokens don't fabricate doneness
        self.assertNotEqual(self._status(rd)["runs"]["run-01"]["state"], "completed")


class MultiStepTokenTests(_Base):
    def _complete_step(self, rd, m, usage):
        sd = rd / "run-01" / "steps" / ("step-%02d" % (m + 1))
        self._hb(sd / "heartbeat.ndjson", usage=usage)

    def test_additive_rollup_multistep(self):
        rd = self._init({"pool_size": 1, "entries": [_ms_entry("t1", 2)]})
        T.tick(rd)
        self._complete_step(rd, 0, {"input_tokens": 100, "output_tokens": 10})
        T.tick(rd)
        self._complete_step(rd, 1, {"input_tokens": 50, "output_tokens": 5})
        T.tick(rd)
        # per-step results carry their own counts
        s0 = json.loads((rd / "run-01" / "steps" / "step-01" / "result.json").read_text())
        self.assertEqual(s0["input_tokens"], 100)
        # entry total = additive sum (NOT the last step's count)
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertEqual(rec["input_tokens"], 150)
        self.assertEqual(rec["output_tokens"], 15)
        sj = json.loads((rd / "summary.json").read_text())
        self.assertEqual(sj["tokens"]["input_tokens"], 150)


if __name__ == "__main__":
    unittest.main()
