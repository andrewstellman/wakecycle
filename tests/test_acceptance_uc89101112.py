"""instr 044 -- acceptance tests for the remaining use cases (UC-8..UC-12).

These wire each UC's DISK-GRADEABLE legs into the deterministic suite, drawn
honestly against ACCEPTANCE_TESTS.md (the agent-reported legs -- UC-8's live
two-rung drive, UC-9's "did the fresh context truly rehydrate", UC-10's NL
comprehension -- are demonstrated by the worker and recorded in outputs/044,
not asserted here; what lives here is what the checker / the deterministic
builders can grade from disk).

  * UC-8  two-rung: both rung plans grade the SAME expected; a rung-specific
          divergence is caught against that shared expected.
  * UC-9  in-context: the FR-47 queue RESUMES across a fresh context (not just
          the background run-dir); STOP halts mid-queue; the FR-49 "busy, not
          asleep" note renders; the background run grades done.
  * UC-10 conversational build: the assembled plan matches the FROZEN canonical
          (pool/dispatch/entries) and the saved bundle re-runs faithfully.
  * UC-11 autonomy integrity: a long run holds the contract (no CONTINUE-state
          yield); the detector FIRES on each of the three violation fixtures.
  * UC-12 activity patterns: the real wrap adapter shows the RELEVANT line over
          noisy output, never the noise.

MUTATION PINS (instr 044):
  * Uc10::test_assembled_plan_matches_frozen_canonical -- the plan-fidelity
    grade: the builder must assemble EXACTLY the committed canonical.
  * Uc11::test_detector_fires_on_the_three_violations -- the reason FR-55
    exists: the detector must genuinely fire on abandonment / illegitimate
    yield / false-halt-claim, not rubber-stamp the happy path.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PLANS = _ROOT / "tests" / "acceptance" / "plans"
_INT = _ROOT / "tests" / "integration"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("checker_uc8_12", _INT / "checker.py")
TICK = _load("tick_uc8_12", _ROOT / "arunner" / "engine" / "tick.py")
JOBS = _load("jobs_uc8_12", _ROOT / "arunner" / "engine" / "jobs.py")
IC = _load("incontext_uc8_12", _ROOT / "arunner" / "engine" / "incontext.py")
RUNNER = _load("runner_uc8_12", _INT / "runner.py")

_TERMINAL = ("completed", "failed", "auth_or_launch_failed", "abandoned")


def _plan(name):
    return json.loads((_PLANS / name).read_text(encoding="utf-8"))


def _build_run_dir(states, done=True, journal_verdicts=None, starts_per_run=None):
    """A durable run-dir a real live run would leave (NO _check_meta.json):
    harness_status.json + results/ + per-run heartbeat.ndjson + journal.ndjson.
    Mirrors what the in-agent / ticker drives actually write so the checker
    grades it exactly as it would a real acceptance run."""
    rd = Path(tempfile.mkdtemp())
    runs = {("run-%02d" % (i + 1)): {"state": s, "job_id": "job-%05d" % (i + 1)}
            for i, s in enumerate(states)}
    counts = {k: 0 for k in ("queued", "claimed", "running", "stalled",
                             "completed", "failed", "auth_or_launch_failed",
                             "abandoned")}
    for s in states:
        counts[s] = counts.get(s, 0) + 1
    status = {"cycle": len(states) + 1, "done": done, "counts": counts,
              "runs": runs}
    (rd / "harness_status.json").write_text(json.dumps(status), encoding="utf-8")
    res = rd / "results"
    res.mkdir()
    for i, s in enumerate(states):
        if s in _TERMINAL:
            (res / ("result-%05d.json" % (i + 1))).write_text(
                json.dumps({"terminal_status": s}), encoding="utf-8")
    # per-run heartbeats (1 STARTING each unless overridden)
    spr = starts_per_run if starts_per_run is not None else [1] * len(states)
    for i, n in enumerate(spr):
        hbdir = rd / ("run-%02d" % (i + 1))
        hbdir.mkdir(exist_ok=True)
        lines = [json.dumps({"status": "STARTING", "label": "stub"})
                 for _ in range(n)]
        lines.append(json.dumps({"status": "COMPLETED", "label": "stub"}))
        (hbdir / "heartbeat.ndjson").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")
    if journal_verdicts is not None:
        lines = [json.dumps({"tick": k + 1, "type": "verdict", "verdict": v})
                 for k, v in enumerate(journal_verdicts)]
        (rd / "journal.ndjson").write_text("\n".join(lines) + "\n",
                                           encoding="utf-8")
    return rd


class Uc8TwoRung(unittest.TestCase):
    """UC-8: drive the bundled demo to done at rung 1 (subagent) AND rung 3
    (ticker) against the SAME expected -- so a rung-specific divergence is
    catchable. The live two-rung drive is recorded in outputs/044; here we grade
    that the shared expected accepts a clean demo run and REJECTS a divergence."""

    def test_both_rung_plans_check_and_share_one_expected(self):
        for p in ("uc8_demo_subagent.json", "uc8_demo_floor.json"):
            self.assertEqual(TICK.check_plan(str(_PLANS / p)), [],
                             "%s failed --check" % p)
        # one expected file is graded for BOTH rungs
        self.assertTrue((_PLANS / "uc8_expected.json").is_file())

    def test_clean_demo_run_passes_shared_expected(self):
        rd = _build_run_dir(("completed", "completed", "completed"))
        self.assertEqual(C.check(rd, _plan("uc8_expected.json")), [])

    def test_rung_specific_divergence_is_caught(self):
        # a rung that diverges (one job failed) must FAIL the shared expected --
        # this is what makes "run both rungs against one expected" meaningful.
        rd = _build_run_dir(("completed", "failed", "completed"))
        fails = C.check(rd, _plan("uc8_expected.json"))
        self.assertTrue(any("run-02" in f for f in fails)
                        or any("completed" in f for f in fails), fails)


class Uc9InContextQueue(unittest.TestCase):
    """UC-9: the load-bearing leg is that a FRESH context resumes the in-context
    QUEUE (FR-47/48), not merely the background run-dir. Disk-deterministic:
    selection advances as outputs land, STOP halts mid-queue, and the FR-49
    "busy, not asleep" note renders. The "did the fresh context truly rehydrate
    from disk" judgement is agent-self-reported in outputs/044."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ins = self.tmp / "instructions"
        self.outs = self.tmp / "outputs"
        # use the COMMITTED uc9 instruction folder as the real fixture
        shutil.copytree(_ROOT / "tests" / "acceptance" / "uc9_instructions",
                        self.ins)
        self.outs.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_queue_resumes_across_a_fresh_context(self):
        # context A picks the lowest unprocessed instruction
        first = IC.select_next_instruction(self.ins, self.outs)
        self.assertIsNotNone(first)
        self.assertTrue(first.name.startswith("001"))
        # ...processes it (writes a matching output), then the session dies.
        (self.outs / "001-trivial-note.md").write_text("done", encoding="utf-8")
        # A FRESH context rehydrates and must resume the QUEUE at 002 -- the
        # whole point of FR-48: not restart at 001, not stall.
        second = IC.select_next_instruction(self.ins, self.outs)
        self.assertIsNotNone(second)
        self.assertTrue(second.name.startswith("002"))
        (self.outs / "002-trivial-note.md").write_text("done", encoding="utf-8")
        self.assertIsNone(IC.select_next_instruction(self.ins, self.outs))

    def test_stop_halts_the_queue_mid_stream(self):
        stop = self.tmp / "STOP"
        stop.write_text("", encoding="utf-8")
        # 001/002 unprocessed, yet STOP forces selection to None (read-only halt)
        self.assertIsNone(
            IC.select_next_instruction(self.ins, self.outs, stop_file=stop))

    def test_busy_not_asleep_note_renders(self):
        note = IC.monitoring_pause_note(0.0, 900.0, 2)
        self.assertIn("monitoring paused", note)
        self.assertIn("background change", note)
        self.assertTrue(note.isascii())

    def test_background_run_grades_done(self):
        rd = _build_run_dir(("completed", "completed"))
        self.assertEqual(C.check(rd, _plan("uc9_expected.json")), [])


class Uc10ConversationalBuild(unittest.TestCase):
    """UC-10: for a FIXED NL prompt the host agent assembles a shorthand; the
    builder must expand it to EXACTLY the frozen canonical plan and the saved
    bundle must re-run faithfully. NL comprehension is agent-self-reported; the
    plan-fidelity + bundle-rerun legs are disk-graded here."""

    def setUp(self):
        self.shorthand = _plan("uc10_build.jobs.json")
        self.canonical = _plan("uc10_expected_plan.json")

    def test_assembled_plan_matches_frozen_canonical(self):     # PIN
        assembled = JOBS.expand_jobs(self.shorthand)
        self.assertEqual(assembled, self.canonical,
                         "the builder no longer assembles the committed canonical")

    def test_canonical_is_pool2_three_subagents(self):
        self.assertEqual(self.canonical.get("pool_size"), 2)
        entries = self.canonical.get("entries", [])
        self.assertEqual(len(entries), 3)
        self.assertTrue(all(e.get("dispatch_mode") == "subagent" for e in entries))

    def test_saved_bundle_reruns_faithfully(self):
        bundle = JOBS.session_bundle(self.shorthand)
        self.assertFalse(JOBS.bundle_drifted(bundle),
                         "a freshly-saved bundle must not read as drifted")
        self.assertEqual(bundle["plan"], self.canonical)
        # the bundle's plan is itself runnable (--check clean)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps(bundle["plan"]))
            path = fh.name
        try:
            self.assertEqual(TICK.check_plan(path), [])
        finally:
            Path(path).unlink()


class Uc11AutonomyIntegrity(unittest.TestCase):
    """UC-11: a long stub run holds the continuation contract (every non-final
    tick CONTINUE, ends HALT:done, no CONTINUE-state yield) -- AND the detector
    FIRES on the three deliberate-violation fixtures (the reason FR-55 exists)."""

    def test_long_run_holds_the_contract(self):
        # 4 entries, pool 1 -> several CONTINUE ticks then HALT:done; no yields
        rd = _build_run_dir(
            ("completed",) * 4,
            journal_verdicts=["CONTINUE", "CONTINUE", "CONTINUE", "HALT:done"])
        self.assertEqual(C.check(rd, _plan("uc11_expected.json")), [])

    def test_detector_fires_on_the_three_violations(self):      # PIN
        cases = {
            "continuation_abandon": "silent_abandonment",
            "continuation_false_yield": "illegitimate_yield",
            "continuation_false_halt_claim": "false_halt_claim",
        }
        for scenario, violation in cases.items():
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as d:
                    run_dir = RUNNER.run_scenario(_INT / "scenarios" / scenario, d)
                    sc_expected = json.loads(
                        (_INT / "scenarios" / scenario / "scenario.json")
                        .read_text())["expected"]
                    # grades clean against the fixture's OWN expected (the right
                    # violation fired)...
                    self.assertEqual(C.check(run_dir, sc_expected), [],
                                     "%s: expected violation not detected" % scenario)
                    # ...and the SPECIFIC class is the one detected.
                    self.assertIn(violation,
                                  sc_expected["continuation"]["violations"])
                    # ...and claiming NO violation must FAIL (detector genuinely
                    # fired, not a rubber stamp).
                    fails = C.check(run_dir, {"continuation": {"violations": []}})
                    self.assertTrue(any("continuation violations" in f
                                        for f in fails),
                                    "%s: detector did not fire" % scenario)


class Uc12ActivityPatterns(unittest.TestCase):
    """UC-12: a wrap job with adapter_activity_patterns over noisy output -- the
    ACTIVITY label must show the RELEVANT line, never the noise. Drives the REAL
    wrap adapter with the command + regex taken from the committed uc12 plan."""

    def _hb_inprogress_labels(self, hb):
        out = []
        for ln in Path(hb).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except ValueError:
                continue
            if obj.get("status") == "IN_PROGRESS":
                out.append(obj.get("label", ""))
        return out

    def test_wrap_activity_label_is_relevant_not_noise(self):
        plan = _plan("uc12_activity.json")
        entry = plan["entries"][0]
        command = entry["command"]
        regex = entry["adapter_activity_patterns"][0]
        d = Path(tempfile.mkdtemp())
        hb = d / "hb.ndjson"
        subprocess.run(
            [sys.executable, str(_ROOT / "arunner" / "engine" / "heartbeat.py"),
             "wrap", "--task-id", "act-1", "--heartbeat-path", str(hb),
             "--launch-grace-minutes", "0", "--activity-regex", regex,
             "--"] + command,
            capture_output=True, timeout=40)
        labels = self._hb_inprogress_labels(hb)
        self.assertTrue(labels, "no IN_PROGRESS keepalive fired")
        self.assertTrue(any("step" in lb for lb in labels),
                        "activity label never showed a relevant line: %r" % labels)
        self.assertFalse(any("noise: chatter" in lb for lb in labels),
                         "activity label showed the noise: %r" % labels)


if __name__ == "__main__":
    unittest.main()
