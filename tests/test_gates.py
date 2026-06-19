"""FR-63/64 — continuation gates + outcome vocabulary (instr 002).

Between steps a step MAY declare a `gate`. A `kind:"shell"` gate (the default,
the only kind allowed in measurement runs) maps the argv EXIT CODE (exit-code
only -- no stdout/regex, Council FIX-5) to a closed-set FR-64 outcome:
continue / halt(->FR-55 failed) / skip-to-next / behavior-flag:<name> /
internal_error. The verdict is persisted to step-MM/gate.json and READ on resume
(never recomputed, NFR-6). A `kind:"reasoning"` gate is FENCED: opt-in only,
rejected in measurement runs, judged in a SEPARATE context (a distinct judge
sub-run writing a structured data.verdict); a same-context judge is a --check
error; a malformed/absent verdict halts fail-closed.

MUTATION-VERIFY EVIDENCE (instr 002):
  Pin: test_gate_read_on_resume_after_crash_does_not_rerun_argv.
    Mutation: in _evaluate_gate, drop the `if gp.exists(): return ...` read-on-
      resume guard so a persisted verdict is recomputed. Observed: after a crash
      between gate-persist and step-advance, the resume tick RE-RUNS the argv
      (the counter file appears) and the test FAILs. Restored -> OK. (The plain
      re-tick in test_shell_gate_recorded_and_read_on_resume does NOT bite this
      guard -- step_index has already advanced past the gated step -- so this
      crash-window test is the one that pins the read-on-resume guard.)
  Pin: test_shell_out_of_set_outcome_is_internal_error.
    Mutation: in _evaluate_gate, skip the `if not _valid_outcome: internal_error`
      coercion. Observed: a bogus outcome is applied instead of fail-closed halt
      and the test FAILs. Restored -> OK.
  Pin: test_shell_gate_runs_in_target_repo_cwd (instr 003).
    Mutation: in _eval_shell_gate, remove the `cwd=(values.get("TARGET_REPO")
      or None)` argument from subprocess.run. Observed: the gate runs from the
      engine's incidental cwd, the relative-path sentinel is absent -> exit 1 ->
      unmapped nonzero -> 'halt' (not 'continue'), and the test FAILs. Restored
      -> OK. (Surfaced 2026-06-19 in a QPB v1.5.10 regression run: a gate
      `python3 -m bin.validate_phase_artifacts ...` ModuleNotFound'd from the
      arunner root and false-failed three phases that actually passed.)
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load_tick():
    spec = importlib.util.spec_from_file_location(
        "tick_fr63", _ROOT / "arunner" / "engine" / "tick.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = _load_tick()
_PH = "".join("{%s}" % p for p in T._PLACEHOLDERS)
_PY = sys.executable or "python3"


def _step(label, gate=None, **extra):
    # bare agent prompt (the engine auto-injects the placeholder preamble)
    s = {"label": label, "mode": "agent", "prompt": label}
    if gate is not None:
        s["gate"] = gate
    s.update(extra)
    return s


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

    def _complete(self, rd, run, m, status="COMPLETED"):
        sd = rd / run / "steps" / ("step-%02d" % (m + 1))
        with (sd / "heartbeat.ndjson").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"status": status, "task_id": "x",
                                 "result_file": "r", "summary": "s"}) + "\n")

    def _judge(self, rd, run, m, verdict=None, status="COMPLETED"):
        jd = rd / run / "steps" / ("step-%02d" % (m + 1)) / "gate"
        line = {"status": status, "task_id": "judge", "result_file": "r", "summary": "j"}
        if verdict is not None:
            line["data"] = {"verdict": verdict}
        with (jd / "heartbeat.ndjson").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")

    def _gate_json(self, rd, run, m):
        return json.loads((rd / run / "steps" / ("step-%02d" % (m + 1))
                           / "gate.json").read_text())


def _exit_argv(code):
    return [_PY, "-c", "import sys; sys.exit(%d)" % code]


class ShellGateTests(_Base):
    def test_exit0_continue_advances(self):
        e = {"id": "t", "repo": "/tmp", "mode": "pipeline",
             "steps": [_step("a", gate={"kind": "shell", "argv": _exit_argv(0)}),
                       _step("b")]}
        rd = self._init({"pool_size": 1, "jobs": [e]})
        T.tick(rd)
        self._complete(rd, "run-01", 0)
        out = T.tick(rd)
        self.assertEqual(self._gate_json(rd, "run-01", 0)["outcome"], "continue")
        self.assertEqual([x["step"] for x in out["dispatch_list"]], ["step-02"])

    def test_nonzero_halts_entry(self):
        e = {"id": "t", "repo": "/tmp", "mode": "pipeline",
             "steps": [_step("a", gate={"kind": "shell", "argv": _exit_argv(1)}),
                       _step("b")]}
        rd = self._init({"pool_size": 1, "jobs": [e]})
        T.tick(rd)
        self._complete(rd, "run-01", 0)
        T.tick(rd)
        self.assertEqual(self._gate_json(rd, "run-01", 0)["outcome"], "halt")
        self.assertEqual(self._status(rd)["runs"]["run-01"]["state"], "failed")
        # halt maps to an FR-55 terminal (failed), recorded in the entry result
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertEqual(rec["terminal_status"], "FAILED")

    def test_skip_to_next_synthesizes_skipped(self):
        e = {"id": "t", "repo": "/tmp", "mode": "pipeline",
             "steps": [_step("a", gate={"kind": "shell", "argv": _exit_argv(3),
                                        "outcomes": {"3": "skip-to-next"}}),
                       _step("b"), _step("c")]}
        rd = self._init({"pool_size": 1, "jobs": [e]})
        T.tick(rd)
        self._complete(rd, "run-01", 0)
        out = T.tick(rd)
        # step-02 skipped (synthesized SKIPPED), step-03 dispatched
        sk = json.loads((rd / "run-01" / "steps" / "step-02" / "result.json").read_text())
        self.assertEqual(sk["terminal_status"], "SKIPPED")
        self.assertEqual([x["step"] for x in out["dispatch_list"]], ["step-03"])

    def test_behavior_flag_exposed_as_next_step_var(self):
        e = {"id": "t", "repo": "/tmp", "mode": "pipeline",
             "steps": [_step("a", gate={"kind": "shell", "argv": _exit_argv(7),
                                        "outcomes": {"7": "behavior-flag:phase3_skipped"}}),
                       {"label": "b", "mode": "agent",
                        "prompt": "flag={phase3_skipped}"}]}
        rd = self._init({"pool_size": 1, "jobs": [e]})
        T.tick(rd)
        self._complete(rd, "run-01", 0)
        out = T.tick(rd)
        self.assertEqual(self._gate_json(rd, "run-01", 0)["outcome"],
                         "behavior-flag:phase3_skipped")
        prompt = [x for x in out["dispatch_list"]][0]["worker_prompt"]
        self.assertIn("flag=1", prompt)            # the flag exposed as a {var}
        self.assertNotIn("{phase3_skipped}", prompt)

    def test_shell_out_of_set_outcome_is_internal_error(self):
        # bypasses --check (init does not --check): a mapped bogus outcome must
        # be coerced to internal_error (fail-closed halt) at runtime.
        e = {"id": "t", "repo": "/tmp", "mode": "pipeline",
             "steps": [_step("a", gate={"kind": "shell", "argv": _exit_argv(0),
                                        "outcomes": {"0": "frobnicate"}}),
                       _step("b")]}
        rd = self._init({"pool_size": 1, "jobs": [e]})
        T.tick(rd)
        self._complete(rd, "run-01", 0)
        T.tick(rd)
        self.assertEqual(self._gate_json(rd, "run-01", 0)["outcome"], "internal_error")
        self.assertEqual(self._status(rd)["runs"]["run-01"]["state"], "failed")

    def test_shell_gate_recorded_and_read_on_resume(self):
        counter = self.tmp / "gatecount.txt"
        argv = [_PY, "-c", "open(%r,'a').write('1')" % str(counter)]
        e = {"id": "t", "repo": "/tmp", "mode": "pipeline",
             "steps": [_step("a", gate={"kind": "shell", "argv": argv}), _step("b")]}
        rd = self._init({"pool_size": 1, "jobs": [e]})
        T.tick(rd)
        self._complete(rd, "run-01", 0)
        T.tick(rd)                                  # gate runs once -> advances
        self.assertEqual(counter.read_text(), "1")
        T.tick(rd); T.tick(rd)                      # resume: gate.json read, no re-run
        self.assertEqual(counter.read_text(), "1")

    def test_gate_read_on_resume_after_crash_does_not_rerun_argv(self):
        # Crash window: gate.json persisted, but the process died BEFORE the step
        # advanced. On resume the engine reaps the still-current COMPLETED step
        # and MUST read the persisted verdict, never re-run the argv (NFR-6).
        counter = self.tmp / "gc.txt"
        argv = [_PY, "-c", "open(%r,'a').write('1')" % str(counter)]
        e = {"id": "t", "repo": "/tmp", "mode": "pipeline",
             "steps": [_step("a", gate={"kind": "shell", "argv": argv}), _step("b")]}
        rd = self._init({"pool_size": 1, "jobs": [e]})
        T.tick(rd)
        self._complete(rd, "run-01", 0)
        # simulate the crash: verdict already on disk, step NOT yet advanced
        T._persist_gate(rd, "run-01", 0, {"step": "step-01", "kind": "shell",
                                          "outcome": "continue", "ts": "x"})
        out = T.tick(rd)                            # resume: reap step-01 -> read verdict
        self.assertFalse(counter.exists())          # argv NEVER ran (read-on-resume)
        self.assertEqual([x["step"] for x in out["dispatch_list"]], ["step-02"])


class ReasoningGateCheckTests(_Base):
    def _check(self, plan):
        pf = self.tmp / "p.json"
        pf.write_text(json.dumps(plan))
        return T.check_plan(pf)

    def _reasoning_entry(self, **gate_extra):
        gate = {"kind": "reasoning", "judge_prompt": "judge it " + _PH}
        gate.update(gate_extra)
        return {"id": "t", "repo": str(self.tmp), "mode": "pipeline",
                "steps": [_step("a", gate=gate), _step("b")]}

    def test_rejected_without_opt_in(self):
        probs = self._check({"jobs": [self._reasoning_entry()]})
        self.assertTrue(any("allow_reasoning_gates" in p for p in probs), probs)

    def test_rejected_in_measurement_run(self):
        probs = self._check({"allow_reasoning_gates": True, "measurement": True,
                             "jobs": [self._reasoning_entry()]})
        self.assertTrue(any("measurement" in p for p in probs), probs)

    def test_same_context_judge_is_error(self):
        probs = self._check({"allow_reasoning_gates": True,
                             "jobs": [self._reasoning_entry(same_context=True)]})
        self.assertTrue(any("same context" in p for p in probs), probs)

    def test_missing_judge_is_error(self):
        e = {"id": "t", "repo": str(self.tmp), "mode": "pipeline",
             "steps": [_step("a", gate={"kind": "reasoning"}), _step("b")]}
        probs = self._check({"allow_reasoning_gates": True, "jobs": [e]})
        self.assertTrue(any("distinct judge" in p for p in probs), probs)

    def test_opt_in_clean(self):
        self.assertEqual(self._check({"allow_reasoning_gates": True,
                                      "jobs": [self._reasoning_entry()]}), [])


class ReasoningGateRuntimeTests(_Base):
    def _reasoning_plan(self):
        e = {"id": "t", "repo": "/tmp", "mode": "pipeline",
             "steps": [_step("a", gate={"kind": "reasoning",
                                        "judge_prompt": "judge " + _PH}),
                       _step("b")]}
        return {"pool_size": 1, "allow_reasoning_gates": True, "jobs": [e]}

    def test_judge_dispatched_in_separate_context_and_verdict_applied(self):
        rd = self._init(self._reasoning_plan())
        T.tick(rd)
        self._complete(rd, "run-01", 0)
        out = T.tick(rd)                            # enters judging + dispatches judge
        steps = [x.get("step") for x in out["dispatch_list"]]
        self.assertIn("step-01-gate", steps)
        self.assertEqual(self._status(rd)["runs"]["run-01"]["gate_pending"], 0)
        self._judge(rd, "run-01", 0, verdict={"outcome": "continue"})
        out = T.tick(rd)                            # reads verdict -> advances
        gj = self._gate_json(rd, "run-01", 0)
        self.assertEqual(gj["outcome"], "continue")
        self.assertEqual(gj["kind"], "reasoning")
        self.assertIsNotNone(gj["judge"])           # judge identity recorded
        self.assertEqual([x["step"] for x in out["dispatch_list"]], ["step-02"])

    def test_malformed_verdict_halts_fail_closed(self):
        rd = self._init(self._reasoning_plan())
        T.tick(rd)
        self._complete(rd, "run-01", 0)
        T.tick(rd)                                  # judging
        self._judge(rd, "run-01", 0, verdict=None)  # terminal w/ NO data.verdict
        T.tick(rd)
        self.assertEqual(self._gate_json(rd, "run-01", 0)["outcome"], "internal_error")
        self.assertEqual(self._status(rd)["runs"]["run-01"]["state"], "failed")


class ShellGateCwdTests(_Base):
    """instr 003: a shell gate runs with cwd = the step/entry target_repo, so its
    success never depends on the orchestrator's incidental cwd. Uses a SENTINEL
    file (not os.getcwd()==EXPECT -- /tmp->/private/tmp symlinks on macOS make a
    path-equality check flaky)."""

    def test_shell_gate_runs_in_target_repo_cwd(self):
        (self.tmp / "sentinel.txt").write_text("x")     # lives in target_repo
        argv = [_PY, "-c",
                "import os,sys; sys.exit(0 if os.path.exists('sentinel.txt') else 1)"]
        e = {"task_id": "t", "target_repo": str(self.tmp), "dispatch_mode": "subagent",
             "steps": [_step("a", gate={"kind": "shell", "argv": argv}), _step("b")]}
        rd = self._init({"pool_size": 1, "entries": [e]})
        T.tick(rd)
        self._complete(rd, "run-01", 0)
        out = T.tick(rd)
        # cwd=target_repo -> the relative-path sentinel is found -> exit 0 -> continue
        self.assertEqual(self._gate_json(rd, "run-01", 0)["outcome"], "continue")
        self.assertEqual([x["step"] for x in out["dispatch_list"]], ["step-02"])


if __name__ == "__main__":
    unittest.main()
