"""FR-53 end-to-end journey (instr 032) — the backbone test of the real user
flow, driven by the DETERMINISTIC settle runner (no agent loop, no wall-clock
races): a shorthand session is `run` (expand -> --check -> --init), advanced to
done by the ticker, `status` shows the table mid-run, `summary` prints the
capstone, and a persisted my_run.json re-`run`s reproducibly. The verdict is the
INDEPENDENT stdlib-only checker.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLI = _load("arunner_cli_j", "arunner/cli.py")
JOBS = _load("arunner_jobs_j", "bin/jobs.py")
RUNNER = _load("arunner_runner_j", "tests/integration/runner.py")
CHECKER = _load("arunner_checker_j", "tests/integration/checker.py")


def _drive_to_done(run_dir, env, max_ticks=6):
    """Tick (ticker --once) + settle on disk truth until done — the same
    deterministic loop the integration suite uses, no minute sleeps."""
    entries = json.loads((run_dir / "plan.json").read_text()).get("entries", [])
    for _ in range(max_ticks):
        subprocess.run([sys.executable, str(RUNNER._TICKER), "--once", str(run_dir)],
                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=120)
        st = RUNNER._read_status(run_dir)
        if st and st.get("done"):
            return True
        RUNNER._settle(run_dir, entries)
    return bool((RUNNER._read_status(run_dir) or {}).get("done"))


class JourneyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._n = 0

    def tearDown(self):
        os.environ.pop("ARUNNER_RUNS_DIR", None)
        self._tmp.cleanup()

    def _runs_env(self):
        # a UNIQUE runs-dir per run, so the first run and the re-run never
        # collide on init_run's second-granular timestamp stamp (a known hazard).
        self._n += 1
        runs = str(self.tmp / ("runs%d" % self._n))
        os.environ["ARUNNER_RUNS_DIR"] = runs
        return dict(os.environ, ARUNNER_RUNS_DIR=runs)

    def _wrap_job_doc(self):
        # a finite shell job via the FR-40 wrap adapter (completes on exit 0)
        return {"pool_size": 1, "jobs": [
            {"id": "build", "repo": str(self.tmp), "adapter": "wrap",
             "command": ["python3", "-c", "print('built ok')"]}]}

    def _status_table(self, rd):
        out = io.StringIO()
        with redirect_stdout(out):
            CLI.main(["status", str(rd)])
        return out.getvalue()

    def _summary(self, rd):
        out = io.StringIO()
        with redirect_stdout(out):
            CLI.main(["summary", str(rd)])
        return out.getvalue()

    def test_full_journey_run_status_summary_then_rerun(self):
        jf = self.tmp / "session.json"
        jf.write_text(json.dumps(self._wrap_job_doc()))

        # --- run: expand -> --check -> --init (prepare; we drive deterministically)
        env1 = self._runs_env()
        rd, problems = CLI.prepare_run(jf)
        self.assertEqual(problems, [])
        self.assertTrue((rd / "harness_status.json").is_file())

        # --- status: read-only table on the fresh run
        self.assertIn("Run-Dir", self._status_table(rd))

        # --- drive to done via the settle runner, then status + summary
        self.assertTrue(_drive_to_done(rd, env1), "run did not reach done")
        self.assertIn("DONE", self._status_table(rd))            # status reflects done
        summary = self._summary(rd)
        self.assertIn("run summary", summary.lower())
        self.assertIn("completed", summary)

        # --- independent checker: done + completed + SUMMARY capstone present
        self.assertEqual(CHECKER.check(rd, {
            "done": True, "run_states": {"run-01": "completed"},
            "counts": {"completed": 1}, "summary_present": True}), [])

        # --- persist a my_run.json and re-run it reproducibly
        save_path = self.tmp / "my_run.json"
        CLI.main(["expand", str(jf), "--save", str(save_path)])
        bundle = json.loads(save_path.read_text())
        self.assertIn("jobs", bundle); self.assertIn("plan", bundle)
        self.assertFalse(JOBS.bundle_drifted(bundle))

        env2 = self._runs_env()                                 # distinct runs-dir
        rd2, problems2 = CLI.prepare_run(save_path)             # run my_run.json
        self.assertEqual(problems2, [])
        self.assertNotEqual(rd, rd2)                            # a fresh run-dir
        self.assertTrue(_drive_to_done(rd2, env2), "rerun did not reach done")
        self.assertEqual(CHECKER.check(rd2, {
            "done": True, "run_states": {"run-01": "completed"},
            "counts": {"completed": 1}, "summary_present": True}), [])


if __name__ == "__main__":
    unittest.main()
