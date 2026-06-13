"""FR-44 TOOLKIT-as-plan-builder (instr 028, Iteration 10).

TOOLKIT.md is the documented procedure for turning a natural-language batch
description into a conformant plan. This binds the doc's worked example to
executable validation (it must expand + --check clean) and pins that the
procedure still documents its load-bearing steps -- so the docs can't silently
drift from what the engine accepts. Docs-first iteration; the heavy round-trip
coverage lives in test_jobs_shorthand.py (the examples/ anti-drift binding).
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


JOBS = _load("jobs_fr44", "bin/jobs.py")
TICK = _load("tick_fr44", "bin/tick.py")
_TOOLKIT = (_ROOT / "TOOLKIT.md").read_text(encoding="utf-8")


class ToolkitPlanBuilderTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.real = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _expand_check(self, doc):
        plan = JOBS.expand_jobs(doc)
        for e in plan.get("entries", []):
            if e.get("target_repo"):
                e["target_repo"] = str(self.real)   # illustrative path -> real dir
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps(plan)); path = fh.name
        try:
            return TICK.check_plan(path)
        finally:
            Path(path).unlink()

    def test_toolkit_worked_example_exists_and_validates(self):
        # the doc points at this file; it must expand + --check clean.
        self.assertIn("toolkit_walkthrough.jobs.json", _TOOLKIT)
        ex = _ROOT / "examples" / "toolkit_walkthrough.jobs.json"
        self.assertTrue(ex.is_file(), "TOOLKIT worked-example file missing")
        doc = json.loads(ex.read_text(encoding="utf-8"))
        # it is the documented mix: subagent reviews + a wrap adapter job
        agents = [j for j in doc["jobs"] if j.get("agent") == "subagent"]
        adapters = [j for j in doc["jobs"] if j.get("adapter")]
        self.assertTrue(agents and adapters, "walkthrough should mix subagent + adapter")
        self.assertEqual(self._expand_check(doc), [])

    def test_toolkit_documents_the_procedure(self):
        # the load-bearing steps an agent must follow are present.
        for needle in ("--check", "jobs.py expand", "adapter", "wrap", "tail",
                       "subagent", "{HEARTBEAT_PATH}", "Building a plan"):
            self.assertIn(needle, _TOOLKIT, "TOOLKIT missing: %r" % needle)

    def test_toolkit_steers_to_placeholders_not_hand_written_paths(self):
        # FR-21a: never instruct hand-writing a real path into a prompt.
        self.assertIn("FR-21a", _TOOLKIT)
        self.assertIn("Never", _TOOLKIT)

    def test_toolkit_check_output_matches_the_code(self):
        # Pin the quoted --check output to what the engine actually emits, so
        # the doc can't drift (the Iter-10 review caught exactly this).
        ok = TICK._format_check_report("plan.json", [])
        self.assertEqual(ok, "plan OK: plan.json -- no problems found")
        self.assertIn(ok, _TOOLKIT)                       # doc quotes the EXACT line
        fail = TICK._format_check_report("plan.json", ["entries[0].task_id: bad"])
        self.assertTrue(fail.startswith("plan FAILED:") and "problem(s):" in fail)
        self.assertIn("plan FAILED:", _TOOLKIT)
        self.assertIn("problem(s):", _TOOLKIT)

    def test_a_hand_built_request_validates(self):
        # a second worked request (shell wrap + tail) also expand+checks clean.
        doc = {"pool_size": 2, "jobs": [
            {"repo": "/abs/a", "adapter": "wrap", "command": ["make", "test"]},
            {"repo": "/abs/b", "adapter": "tail", "log_path": "/abs/b/run.log",
             "success_regex": "PASSED"}]}
        self.assertEqual(self._expand_check(doc), [])


if __name__ == "__main__":
    unittest.main()
