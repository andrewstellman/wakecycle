"""FR-43 `jobs:` shorthand expander + the examples/ anti-drift binding
(instr 027, Iteration 9).

The shorthand is a pure convenience layer: it expands to the CANONICAL plan the
engine consumes, injecting the placeholders so the expanded plan passes
`tick.py --check` (FR-42). The load-bearing test is the anti-drift binding:
EVERY template in examples/ is expanded and `--check`ed, so if the canonical
schema later grows a required key and the expander isn't updated, an example
fails loudly here -- the guard that keeps the two config formats from drifting.

MUTATION-VERIFY EVIDENCE (DEVELOPMENT_PROCESS Mutation-test), instr 027:
  Pin: test_examples_round_trip_through_check / test_shorthand_expands_clean.
    Mutation: drop a key from jobs._PLACEHOLDER_KEYS (so the injected header
      omits a placeholder).
    Observed: every subagent example's expanded worker_prompt is missing that
      placeholder -> --check reports it -> the tests FAIL. Restored OK.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLES = sorted((_ROOT / "examples").glob("*.json"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


JOBS = _load("jobs_fr43", "bin/jobs.py")
TICK = _load("tick_fr43", "bin/tick.py")


def _check_plan_dict(plan, real_repo):
    # Templates carry illustrative repo paths; substitute a REAL dir so the
    # binding validates SCHEMA + placeholder conformance, not literal paths.
    for e in plan.get("entries", []):
        if e.get("target_repo"):
            e["target_repo"] = str(real_repo)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(json.dumps(plan))
        path = fh.name
    try:
        return TICK.check_plan(path)
    finally:
        Path(path).unlink()


class ShorthandExpansionTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.real = Path(self._tmp.name)        # an existing dir for target_repo

    def tearDown(self):
        self._tmp.cleanup()

    def test_shorthand_expands_clean(self):
        doc = {"pool_size": 2, "jobs": [
            {"id": "a", "repo": "/x/a", "agent": "subagent", "prompt": "review A"},
            {"id": "b", "repo": "/x/b", "adapter": "wrap", "command": ["pytest", "-q"]},
            {"id": "c", "repo": "/x/c", "adapter": "tail", "log_path": "/x/c/log",
             "success_regex": "OK"}]}
        plan = JOBS.expand_jobs(doc)
        self.assertEqual(len(plan["entries"]), 3)
        # subagent prompt carries the full placeholder block
        sub = plan["entries"][0]
        for ph in ("HEARTBEAT_PATH", "TASK_ID", "RUN_DIR", "TARGET_REPO", "HARNESS_BIN"):
            self.assertIn("{%s}" % ph, sub["worker_prompt"])
        # adapter jobs route to shell adapter entries
        self.assertEqual(plan["entries"][1]["adapter"], "wrap")
        self.assertEqual(plan["entries"][2]["adapter"], "tail")
        # the WHOLE expansion passes --check
        self.assertEqual(_check_plan_dict(plan, self.real), [])

    def test_canonical_doc_passes_through_unchanged(self):
        canon = {"pool_size": 1, "entries": [
            {"task_id": "t", "target_repo": "/x", "dispatch_mode": "shell",
             "adapter": "wrap", "command": ["echo", "hi"]}]}
        self.assertEqual(JOBS.expand_jobs(canon), canon)   # no jobs -> unchanged

    def test_toplevel_knobs_pass_through(self):
        doc = {"pool_size": 4, "tick_interval_minutes": 9,
               "stall_threshold_minutes": 30, "jobs": [
                   {"repo": "/x", "prompt": "p"}]}
        plan = JOBS.expand_jobs(doc)
        self.assertEqual(plan["pool_size"], 4)
        self.assertEqual(plan["tick_interval_minutes"], 9)
        self.assertEqual(plan["stall_threshold_minutes"], 30)
        # generated task_id when no id given
        self.assertEqual(plan["entries"][0]["task_id"], "job-01")


class ExamplesAntiDriftTests(unittest.TestCase):
    """The load-bearing binding: every examples/ template round-trips clean."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.real = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_examples_directory_is_populated(self):
        self.assertTrue(_EXAMPLES, "no examples/*.json found")
        names = {p.name for p in _EXAMPLES}
        # the FR-43 required common cases are present
        self.assertIn("agent_review.jobs.json", names)
        self.assertIn("shell_jobs.jobs.json", names)
        self.assertIn("mixed.jobs.json", names)
        self.assertIn("wrap_vs_tail.jobs.json", names)

    def test_examples_round_trip_through_check(self):
        for ex in _EXAMPLES:
            with self.subTest(example=ex.name):
                doc = json.loads(ex.read_text(encoding="utf-8"))
                plan = JOBS.expand_jobs(doc)
                problems = _check_plan_dict(plan, self.real)
                self.assertEqual(problems, [],
                                 "%s expand->check failed: %s" % (ex.name, problems))

    def test_binding_catches_a_bad_example(self):
        # prove the binding can FAIL: a shorthand with a job missing repo/prompt
        # plumbing expands to a plan --check rejects.
        bad = {"jobs": [{"id": "x", "agent": "subagent"}]}   # no repo, no prompt
        plan = JOBS.expand_jobs(bad)
        # don't substitute target_repo (it's empty) -> --check flags it
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps(plan)); path = fh.name
        try:
            problems = TICK.check_plan(path)
        finally:
            Path(path).unlink()
        self.assertTrue(any("target_repo" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
