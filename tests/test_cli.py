"""FR-53 lifecycle verbs + FR-52.4 persist (instr 032).

Every verb is a thin wrapper over an existing bin/ entry point; these tests
exercise the deterministic surface (no agent loop). Subagent runs are driven to
done by writing terminal heartbeats the engine reaps (the test_summary pattern).

MUTATION-VERIFY EVIDENCE (DEVELOPMENT_PROCESS Mutation-test), instr 032:
  Pin 1: test_status_is_read_only.
    Mutation: make cmd_status advance a tick (call TICK.tick) before printing.
    Observed: harness_status.json changes (cycle++) -> the byte-compare FAILs.
      Restored OK.
  Pin 2: test_bundle_drift_warning_fires.
    Mutation: make jobs.bundle_drifted always return False.
    Observed: a hand-edited my_run.json no longer warns -> the test FAILs.
      Restored OK.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLI = _load("arunner_cli_t", "arunner/cli.py")
TICK = _load("arunner_tick_t", "bin/tick.py")
JOBS = _load("arunner_jobs_t", "bin/jobs.py")


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._n = 0
        os.environ["ARUNNER_RUNS_DIR"] = str(self.tmp / "runs")

    def tearDown(self):
        for k in ("ARUNNER_RUNS_DIR", "ARUNNER_NOW"):
            os.environ.pop(k, None)
        self._tmp.cleanup()

    def _write(self, name, doc):
        p = self.tmp / name
        p.write_text(json.dumps(doc))
        return p

    def _subagent_jobs(self, n=1):
        return {"pool_size": n, "jobs": [
            {"id": "j%d" % i, "repo": str(self.tmp), "agent": "subagent",
             "prompt": "do %d" % i} for i in range(n)]}

    def _cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def _status(self, rd):
        return json.loads((rd / "harness_status.json").read_text())

    def _complete(self, rd, run, task_id):
        (rd / run / "heartbeat.ndjson").write_text(json.dumps({
            "ts": "2001-09-09T01:46:40Z", "task_id": task_id, "schema_version": "2",
            "status": "COMPLETED", "result_file": "/x", "summary": "done"}) + "\n")

    def _prepare(self, doc):
        pf = self._write("plan.json", doc)
        rd, problems = CLI.prepare_run(pf)
        self.assertEqual(problems, [])
        return rd


class VerbTests(_Base):

    def test_run_shorthand_expands_checks_inits(self):
        rc, out, err = self._cli("run", str(self._write("j.json", self._subagent_jobs(2))),
                                 "--no-drive")
        self.assertEqual(rc, 0)
        self.assertIn("initialized", out)
        rd = Path(out.split("initialized", 1)[1].strip())
        self.assertTrue((rd / "harness_status.json").is_file())
        self.assertEqual(len(self._status(rd)["runs"]), 2)

    def test_run_canonical_passes_through(self):
        plan = {"pool_size": 1, "entries": [
            {"task_id": "t", "target_repo": str(self.tmp), "dispatch_mode": "subagent",
             "worker_prompt": "{HEARTBEAT_PATH}{TASK_ID}{RUN_DIR}{TARGET_REPO}{HARNESS_BIN}"}]}
        rc, out, _ = self._cli("run", str(self._write("p.json", plan)), "--no-drive")
        self.assertEqual(rc, 0)
        self.assertIn("initialized", out)

    def test_run_reports_check_failures(self):
        bad = {"entries": [{"task_id": "t", "target_repo": "/no/such/dir",
                            "dispatch_mode": "rocket", "worker_prompt": "x"}]}
        rc, out, _ = self._cli("run", str(self._write("bad.json", bad)), "--no-drive")
        self.assertEqual(rc, 1)
        self.assertIn("plan FAILED", out)

    def test_status_is_read_only(self):
        rd = self._prepare(self._subagent_jobs(1))
        TICK.tick(rd)                                    # dispatch -> claimed
        before = (rd / "harness_status.json").read_bytes()
        rc, out, _ = self._cli("status", str(rd))
        self.assertEqual(rc, 0)
        self.assertIn("Run-Dir", out)                    # printed the table
        self.assertEqual((rd / "harness_status.json").read_bytes(), before)  # NO mutation

    def test_stop_writes_file_and_halts(self):
        rd = self._prepare(self._subagent_jobs(1))
        TICK.tick(rd)
        cyc = self._status(rd)["cycle"]
        rc, out, _ = self._cli("stop", str(rd))
        self.assertEqual(rc, 0)
        self.assertTrue((rd / "STOP").is_file())
        outd = TICK.tick(rd)                              # STOP tick = read-only halt
        self.assertTrue(outd["stop"])
        self.assertEqual(self._status(rd)["cycle"], cyc)  # cycle unchanged (read-only)

    def test_summary_not_done_then_done(self):
        rd = self._prepare(self._subagent_jobs(1))
        os.environ["ARUNNER_NOW"] = "1000000000"
        TICK.tick(rd)
        rc, out, _ = self._cli("summary", str(rd))
        self.assertIn("not done yet", out)
        self._complete(rd, "run-01", "j0")
        os.environ["ARUNNER_NOW"] = "1000000005"
        TICK.tick(rd)                                    # reaps -> done -> SUMMARY written
        rc, out, _ = self._cli("summary", str(rd))
        self.assertEqual(rc, 0)
        self.assertIn("run summary", out.lower())
        self.assertIn("completed", out)

    def test_resume_once_is_idempotent(self):
        rd = self._prepare(self._subagent_jobs(1))
        TICK.tick(rd)
        cyc = self._status(rd)["cycle"]
        rc, out, err = self._cli("resume", str(rd), "--once")  # subprocess ticker --once
        self.assertEqual(rc, 0)
        self.assertEqual(self._status(rd)["cycle"], cyc + 1)   # one real tick, no double-run

    def test_new_prints_pointer(self):
        rc, out, _ = self._cli("new")
        self.assertEqual(rc, 0)
        self.assertIn("interactive", out.lower())


class PersistTests(_Base):

    def test_expand_out_writes_valid_plan(self):
        jf = self._write("j.json", self._subagent_jobs(2))
        out_path = self.tmp / "expanded.json"
        rc, _, _ = self._cli("expand", str(jf), "--out", str(out_path))
        self.assertEqual(rc, 0)
        plan = json.loads(out_path.read_text())
        self.assertEqual(len(plan["entries"]), 2)
        self.assertEqual(TICK.check_plan(out_path), [])    # the written plan is --check clean

    def test_my_run_bundle_round_trips(self):
        doc = self._subagent_jobs(2)
        bundle = JOBS.session_bundle(doc)
        self.assertIn("jobs", bundle); self.assertIn("plan", bundle)
        self.assertEqual(bundle["plan"], JOBS.expand_jobs(doc))   # plan == expansion
        self.assertFalse(JOBS.bundle_drifted(bundle))             # fresh bundle: no drift

    def test_bundle_drift_warning_fires(self):
        doc = self._subagent_jobs(1)
        bundle = JOBS.session_bundle(doc)
        bundle["plan"]["pool_size"] = 999                # hand-edit the saved plan
        self.assertTrue(JOBS.bundle_drifted(bundle))     # the drift signal
        # ... and `run` warns (not blocks) on it
        bp = self._write("my_run.json", bundle)
        rc, out, err = self._cli("run", str(bp), "--no-drive")
        self.assertEqual(rc, 0)                          # not blocked
        self.assertIn("drift", err.lower())              # warned on stderr


if __name__ == "__main__":
    unittest.main()
