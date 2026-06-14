"""FR-51 — the deterministic integration suite: every scenario green under the
ticker, verdict by the INDEPENDENT checker.

Each scenario folder is run by the ticker-driven runner (no agent loop), then
the stdlib-only checker reads the disk artifacts and asserts the expected
end-state. A meta-test proves the checker actually distinguishes pass from
fail (a deliberately-wrong expected must produce failures), so a green run
means something.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_INT = _ROOT / "tests" / "integration"
_SCENARIOS = sorted((_INT / "scenarios").iterdir())


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


RUNNER = _load("wc_int_runner", _INT / "runner.py")
CHECKER = _load("wc_int_checker", _INT / "checker.py")


class IntegrationScenarioTests(unittest.TestCase):

    def _run(self, scenario_dir):
        with tempfile.TemporaryDirectory() as d:
            run_dir = RUNNER.run_scenario(scenario_dir, d)
            expected = json.loads(
                (Path(scenario_dir) / "scenario.json").read_text())["expected"]
            failures = CHECKER.check(run_dir, expected)
            return failures, run_dir

    def test_each_scenario_passes(self):
        self.assertTrue(_SCENARIOS, "no scenarios discovered")
        for sc in _SCENARIOS:
            with self.subTest(scenario=sc.name):
                failures, _ = self._run(sc)
                self.assertEqual(failures, [],
                                 "scenario %s failed: %s" % (sc.name, failures))

    def test_harness_rejects_malformed_plan_via_check(self):
        # FR-42 dogfood: a scenario whose plan is malformed (bad dispatch_mode)
        # must fail loudly at --check on load, not produce a confusing run.
        with tempfile.TemporaryDirectory() as d:
            sc = Path(d) / "bad_scenario"
            sc.mkdir()
            (sc / "scenario.json").write_text(json.dumps({
                "plan": {"pool_size": 1, "entries": [
                    {"task_id": "t", "target_repo": "{SCENARIO_DIR}",
                     "dispatch_mode": "bogus",          # invalid enum
                     "worker_prompt": "x"}]},
                "expected": {}}))
            with self.assertRaises(RuntimeError) as cm:
                RUNNER.run_scenario(sc, d)
            self.assertIn("--check", str(cm.exception))
            self.assertIn("dispatch_mode", str(cm.exception))

    def test_continuation_detector_discriminates(self):
        # FR-55: the abandonment detector must genuinely FIRE on a real
        # violation and STAY SILENT on an honest run -- not rubber-stamp the
        # scenario's own `expected`. Run `abandon`, then re-grade against a
        # wrong expected (no violation) -> must fail; run `honor`, then grade
        # it claiming a violation -> must fail.
        ab = _INT / "scenarios" / "continuation_abandon"
        with tempfile.TemporaryDirectory() as d:
            run_dir = RUNNER.run_scenario(ab, d)
            real = json.loads((ab / "scenario.json").read_text())["expected"]
            self.assertEqual(CHECKER.check(run_dir, real), [])     # truly green
            wrong = copy.deepcopy(real)
            wrong["continuation"]["violations"] = []               # claim no abandon
            self.assertTrue(CHECKER.check(run_dir, wrong),
                            "detector missed a real silent abandonment")
        ho = _INT / "scenarios" / "continuation_honor"
        with tempfile.TemporaryDirectory() as d:
            run_dir = RUNNER.run_scenario(ho, d)
            wrong = copy.deepcopy(
                json.loads((ho / "scenario.json").read_text())["expected"])
            wrong["continuation"]["violations"] = ["silent_abandonment"]
            self.assertTrue(CHECKER.check(run_dir, wrong),
                            "detector flagged an honest honored run")

    def test_checker_distinguishes_pass_from_fail(self):
        # Run a real scenario, then check it against a DELIBERATELY-WRONG
        # expected -- the checker must report failures (it can fail, not just
        # rubber-stamp).
        sc = _INT / "scenarios" / "autonomous_loop"
        with tempfile.TemporaryDirectory() as d:
            run_dir = RUNNER.run_scenario(sc, d)
            good = json.loads((sc / "scenario.json").read_text())["expected"]
            self.assertEqual(CHECKER.check(run_dir, good), [])  # truly green
            wrong = copy.deepcopy(good)
            wrong["counts"] = {"completed": 99, "failed": 7}
            wrong["done"] = False
            bad = CHECKER.check(run_dir, wrong)
            self.assertTrue(bad, "checker rubber-stamped a wrong expected")


if __name__ == "__main__":
    unittest.main()
