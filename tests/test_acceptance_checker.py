"""instr 041 — the acceptance-layer checker grades a LIVE run from its DURABLE
artifacts (harness_status.json incl. continuation, journal.ndjson, results/),
with no runner `_check_meta.json`. Covers: durable pass, a deliberately-wrong
expected fails (the meta-test), the UC-3 before-snapshot stop_readonly path, the
meta-only-key honesty guard, and the standalone CLI.

MUTATION PIN (instr 041): `test_durable_grading_detects_wrong_run_state` is the
load-bearing assertion — durable grading must catch a run that didn't reach the
expected state (else the acceptance verdict is worthless). Breaking the
run_states check makes it fail.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CHECKER_SRC = _ROOT / "tests" / "integration" / "checker.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


C = _load("checker_acc", _CHECKER_SRC)
_TERMINAL = ("completed", "failed", "auth_or_launch_failed", "abandoned")


def _build_run_dir(states=("completed", "completed", "completed"), done=True,
                   with_journal=True):
    """A durable run-dir a real run would leave (NO _check_meta.json)."""
    rd = Path(tempfile.mkdtemp())
    runs = {("run-%02d" % (i + 1)): {"state": s, "job_id": "job-%05d" % (i + 1)}
            for i, s in enumerate(states)}
    counts = {"queued": 0, "claimed": 0, "running": 0, "stalled": 0,
              "completed": 0, "failed": 0, "auth_or_launch_failed": 0,
              "abandoned": 0}
    for s in states:
        counts[s] = counts.get(s, 0) + 1
    status = {"cycle": 4, "done": done, "counts": counts, "runs": runs,
              "continuation": {"verdict": "HALT", "reason": "done"}}
    (rd / "harness_status.json").write_text(json.dumps(status), encoding="utf-8")
    res = rd / "results"
    res.mkdir()
    for i, s in enumerate(states):
        if s in _TERMINAL:
            (res / ("result-%05d.json" % (i + 1))).write_text(
                json.dumps({"terminal_status": s}), encoding="utf-8")
    if with_journal:
        lines = [json.dumps({"tick": k, "type": "verdict",
                             "verdict": "CONTINUE" if k < len(states) + 1 else "HALT:done"})
                 for k in range(1, len(states) + 2)]
        (rd / "journal.ndjson").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rd


class DurableGrading(unittest.TestCase):

    def test_durable_pass_with_no_meta(self):
        rd = _build_run_dir()
        self.assertFalse((rd / "_check_meta.json").exists())   # no runner meta
        expected = {"done": True, "counts": {"completed": 3, "failed": 0},
                    "run_states": {"run-01": "completed", "run-02": "completed",
                                   "run-03": "completed"},
                    "results_for_terminal": True}
        self.assertEqual(C.check(rd, expected), [])

    def test_durable_grading_detects_wrong_run_state(self):       # PIN
        rd = _build_run_dir(states=("completed", "failed", "completed"))
        expected = {"run_states": {"run-02": "completed"}}       # it actually failed
        fails = C.check(rd, expected)
        self.assertTrue(any("run-02" in f for f in fails), fails)

    def test_durable_grading_detects_wrong_done_and_counts(self):
        rd = _build_run_dir(states=("completed", "running"), done=False)
        fails = C.check(rd, {"done": True, "counts": {"completed": 2}})
        self.assertTrue(any("done" in f for f in fails))
        self.assertTrue(any("counts[completed]" in f for f in fails))

    def test_verdict_present_from_durable_journal(self):
        rd = _build_run_dir()
        self.assertEqual(C.check(rd, {"continuation": {
            "verdict_present": ["HALT:done"], "final_done": True}}), [])
        self.assertTrue(C.check(rd, {"continuation": {
            "verdict_present": ["HALT:blocked"]}}))   # never emitted -> fails

    def test_meta_only_key_is_flagged_not_silently_passed(self):
        rd = _build_run_dir()
        fails = C.check(rd, {"max_inflight_le": 2})
        self.assertTrue(any("requires the runner" in f for f in fails), fails)


class StopReadonlySnapshot(unittest.TestCase):
    """UC-3: a live run can't reconstruct the pre-STOP state, so the agent
    snapshots harness_status.json to _before_snapshot.json before dropping STOP;
    the checker compares against it."""

    def test_snapshot_match_passes(self):
        rd = _build_run_dir(states=("running", "queued"), done=False)
        # snapshot BEFORE the (read-only) STOP tick, then a STOP tick that
        # changed nothing -> the current status equals the snapshot.
        (rd / "_before_snapshot.json").write_text(
            (rd / "harness_status.json").read_text(), encoding="utf-8")
        (rd / "STOP").write_text("", encoding="utf-8")
        self.assertEqual(C.check(rd, {"stop_readonly": True, "stopped": True}), [])

    def test_snapshot_mismatch_fails(self):
        rd = _build_run_dir(states=("running", "queued"), done=False)
        snap = json.loads((rd / "harness_status.json").read_text())
        snap["cycle"] = 99                          # a STOP tick that DID change state
        (rd / "_before_snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
        fails = C.check(rd, {"stop_readonly": True})
        self.assertTrue(any("cycle" in f for f in fails), fails)

    def test_missing_snapshot_is_flagged(self):
        rd = _build_run_dir(done=False)
        fails = C.check(rd, {"stop_readonly": True})
        self.assertTrue(any("snapshot" in f for f in fails), fails)


class CheckerCLI(unittest.TestCase):

    def _run_cli(self, rd, expected):
        ep = Path(rd) / "expected.json"
        ep.write_text(json.dumps(expected), encoding="utf-8")
        return subprocess.run([sys.executable, str(_CHECKER_SRC), str(rd), str(ep)],
                              capture_output=True, text=True)

    def test_cli_pass_exit_0(self):
        rd = _build_run_dir()
        r = self._run_cli(rd, {"done": True, "counts": {"completed": 3}})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CHECK PASSED", r.stdout)

    def test_cli_fail_exit_1_prints_failures(self):
        rd = _build_run_dir(states=("completed", "failed"))
        r = self._run_cli(rd, {"done": True, "run_states": {"run-02": "completed"}})
        self.assertEqual(r.returncode, 1)
        self.assertIn("CHECK FAILED", r.stdout)
        self.assertIn("run-02", r.stdout)


if __name__ == "__main__":
    unittest.main()
