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
