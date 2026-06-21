"""FR-72 — subagent-mode liveness: a live (or merely silent) subagent worker
must never be marked ``auth_or_launch_failed`` (terminal) just because the
engine saw no heartbeat within launch grace.

Re-derived from the (stale, pre-format-collapse) ``fr-61-subagent-liveness``
reference onto the unified one-format (``jobs``/``id``/``repo``/``mode``) engine
on ``main``. A ``mode: agent`` job dispatches as the runtime ``dispatch_mode:
subagent``; the behavior keys on that runtime mode.

This extends the FR-40 invariant the wrap adapter already guarantees for shell
commands ("a long-running quiet command never false-trips LAUNCH-FAIL or
STALLED; doneness comes from the exit code, never from parsing output") to
subagent dispatch, where the engine does NOT own the worker (the orchestrator's
Task does) and so has no authority to declare it dead on heartbeat absence.

Coverage:
  * REGRESSION PIN (mutation-verified): a claimed subagent that produces NO
    heartbeat is, past launch grace, ADVISORY (NO-HEARTBEAT) and NON-terminal --
    never auth_or_launch_failed -- and reconciles to ``completed`` when its
    terminal arrives.
  * SHELL-MODE PARITY: a shell/Popen worker (mode: shell) with no heartbeat past
    grace still goes auth_or_launch_failed (the engine owns it).
  * HARD CAP: a subagent silent past the long hard cap IS reclaimed terminal.
  * LIFECYCLE EMIT (Layer B): the engine writes STARTING on the subagent's
    behalf at dispatch; a simulated return writes the terminal.

Time-dependent transitions are driven by the ARUNNER_NOW clock seam (no sleeps).

MUTATION-VERIFY EVIDENCE (in-tree, instr 005) -- the regression pin BITE-executed:
  Pin: SubagentAdvisoryTests.test_no_heartbeat_past_grace_is_advisory_not_terminal
  Mutation: in tick.py `_advance`, neuter the subagent fork so the `claimed +
    no heartbeat past grace` branch falls through to the shell-mode terminal:
        if _record_dispatch_mode_of(...) == "subagent":  ->  if False:
  Observed: test FAILs -- run-01 is marked auth_or_launch_failed and goes `done`.
  Restored -> OK. (Verified 2026-06-21, Python 3.14.6.)
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_tick():
    spec = importlib.util.spec_from_file_location(
        "tick_subagent_liveness", str(_REPO_ROOT / "arunner" / "engine" / "tick.py"))
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["tick_subagent_liveness"] = mod
    spec.loader.exec_module(mod)
    return mod


T = _load_tick()


def _plan(jobs, **top):
    p = {"tick_interval_minutes": 5, "pool_size": top.pop("pool_size", 1),
         "jobs": jobs}
    p.update(top)
    return p


def _subagent_job(tid, repo="/tmp/x"):
    # mode: agent -> runtime dispatch_mode: subagent. Bare prompt (the engine
    # auto-injects the placeholder preamble at dispatch).
    return {"id": tid, "repo": repo, "mode": "agent",
            "prompt": "do the work on this repository"}


def _shell_job(tid, repo="/tmp/x"):
    # mode: shell -> runtime dispatch_mode: shell; the raw command carries the
    # heartbeat route (the engine owns the Popen here).
    return {"id": tid, "repo": repo, "mode": "shell",
            "command": ["python3", "w.py", "--hb", "{HEARTBEAT_PATH}",
                        "--tid", "{TASK_ID}", "--rd", "{RUN_DIR}"]}


def _status(run_dir: Path) -> dict:
    return json.loads((run_dir / "harness_status.json").read_text())


def _hb_lines(run_dir: Path, run="run-01") -> list:
    p = run_dir / run / "heartbeat.ndjson"
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _write_terminal(run_dir: Path, tid, status="COMPLETED", run="run-01"):
    """Stand in for the orchestrator/worker recording the subagent's RETURN:
    append a terminal heartbeat line (the Layer-B contract)."""
    line = {"ts": "t", "task_id": tid, "schema_version": "2",
            "status": status, "result_file": "out.json", "summary": "done"}
    with (run_dir / run / "heartbeat.ndjson").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _clear_hb(run_dir: Path, run="run-01"):
    """Empty the heartbeat file -> simulate a subagent that produced NO heartbeat
    at all (the incident: an ad-hoc gather prompt that never self-heartbeat, and
    -- to isolate the engine's grace logic -- without even the engine's STARTING
    marker)."""
    (run_dir / run / "heartbeat.ndjson").write_text("", encoding="utf-8")


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.environ["ARUNNER_RUNS_DIR"] = str(self.tmp / "harness_runs")
        os.environ.pop("ARUNNER_NOW", None)

    def tearDown(self):
        os.environ.pop("ARUNNER_NOW", None)
        os.environ.pop("ARUNNER_RUNS_DIR", None)
        self._tmp.cleanup()

    def _init(self, plan):
        pf = self.tmp / "plan.json"
        pf.write_text(json.dumps(plan))
        return Path(T.init_run(pf))


class SubagentAdvisoryTests(_Base):
    """REGRESSION PIN: a no-heartbeat subagent is advisory, never terminal."""

    def test_no_heartbeat_past_grace_is_advisory_not_terminal(self):
        rd = self._init(_plan([_subagent_job("t-1")], pool_size=1,
                              launch_grace_minutes=10))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)                                  # dispatch -> claimed
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "claimed")
        # The incident: the subagent does work but emits NO heartbeat. Clear the
        # file to isolate the engine's grace logic from the Layer-B STARTING.
        _clear_hb(rd)
        # 11 minutes later, still no heartbeat. SHELL mode would now be
        # auth_or_launch_failed; SUBAGENT mode must stay advisory + non-terminal.
        os.environ["ARUNNER_NOW"] = str(1000000 + 11 * 60)
        out = T.tick(rd)
        s = _status(rd)
        r = s["runs"]["run-01"]
        self.assertEqual(r["state"], "claimed")          # NON-terminal
        self.assertNotEqual(r["state"], "auth_or_launch_failed")
        self.assertEqual(r.get("launch_advisory"), "NO-HEARTBEAT")
        self.assertFalse(out["done"])                    # the loop keeps going
        # no synthesized failure record was written
        self.assertFalse((rd / "results" / "result-00001.json").exists())
        # the table surfaces NO-HEARTBEAT as an advisory, not a failure
        self.assertIn("NO-HEARTBEAT", out["status_table"])
        self.assertNotIn("LAUNCH-FAIL", out["status_table"])

    def test_advisory_subagent_reconciles_to_completed_on_return(self):
        rd = self._init(_plan([_subagent_job("t-1")], pool_size=1,
                              launch_grace_minutes=10))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)
        _clear_hb(rd)
        os.environ["ARUNNER_NOW"] = str(1000000 + 11 * 60)
        T.tick(rd)
        self.assertEqual(_status(rd)["runs"]["run-01"].get("launch_advisory"),
                         "NO-HEARTBEAT")
        # the subagent RETURNS (orchestrator records the terminal) -> completed
        _write_terminal(rd, "t-1", "COMPLETED")
        out = T.tick(rd)
        s = _status(rd)
        self.assertEqual(s["runs"]["run-01"]["state"], "completed")
        self.assertIsNone(s["runs"]["run-01"].get("launch_advisory"))
        self.assertTrue(out["done"])

    def test_advisory_clears_when_a_heartbeat_arrives(self):
        rd = self._init(_plan([_subagent_job("t-1")], pool_size=1,
                              launch_grace_minutes=10))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)
        _clear_hb(rd)
        os.environ["ARUNNER_NOW"] = str(1000000 + 11 * 60)
        T.tick(rd)
        self.assertEqual(_status(rd)["runs"]["run-01"].get("launch_advisory"),
                         "NO-HEARTBEAT")
        # a late IN_PROGRESS arrives -> demonstrably alive: advisory clears,
        # run moves to running.
        line = {"ts": "t", "task_id": "t-1", "schema_version": "2",
                "label": "working", "status": "IN_PROGRESS"}
        with (rd / "run-01" / "heartbeat.ndjson").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
        # advance the heartbeat mtime to "now" so it isn't read as stale
        future = (rd / "run-01" / "heartbeat.ndjson").stat().st_mtime
        os.environ["ARUNNER_NOW"] = str(future)
        T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "running")
        self.assertIsNone(r.get("launch_advisory"))


class ShellParityTests(_Base):
    """PARITY PIN: shell mode is unchanged -- the engine owns the Popen, so no
    heartbeat past grace IS a launch failure."""

    def test_shell_no_heartbeat_past_grace_is_terminal(self):
        rd = self._init(_plan([_shell_job("t-1")], pool_size=1,
                              launch_grace_minutes=10))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "claimed")
        os.environ["ARUNNER_NOW"] = str(1000000 + 11 * 60)
        out = T.tick(rd)
        s = _status(rd)
        self.assertEqual(s["runs"]["run-01"]["state"], "auth_or_launch_failed")
        self.assertTrue(out["done"])
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertTrue(rec.get("synthesized"))


class SubagentHardCapTests(_Base):
    """A genuinely-dead silent subagent is still eventually reclaimed via the
    long hard cap -- a hang can't pin a slot forever."""

    def test_no_heartbeat_past_hard_cap_is_reclaimed_terminal(self):
        rd = self._init(_plan([_subagent_job("t-1")], pool_size=1,
                              launch_grace_minutes=10,
                              subagent_hard_cap_minutes=120))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)
        _clear_hb(rd)
        # just past grace but well within the cap -> still advisory
        os.environ["ARUNNER_NOW"] = str(1000000 + 30 * 60)
        T.tick(rd)
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "claimed")
        # 121 minutes in -> past the hard cap -> reclaimed terminal, slot freed
        os.environ["ARUNNER_NOW"] = str(1000000 + 121 * 60)
        out = T.tick(rd)
        s = _status(rd)
        r = s["runs"]["run-01"]
        self.assertEqual(r["state"], "auth_or_launch_failed")
        self.assertIsNone(r.get("launch_advisory"))
        self.assertTrue(out["done"])                    # slot freed -> run terminal
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertTrue(rec.get("synthesized"))
        self.assertIn("hard cap", rec["summary"])

    def test_default_hard_cap_is_generous(self):
        # The default cap is >> launch grace, so a subagent silent for an hour
        # (well past the 10-min default grace) is still only advisory.
        rd = self._init(_plan([_subagent_job("t-1")], pool_size=1))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)
        _clear_hb(rd)
        os.environ["ARUNNER_NOW"] = str(1000000 + 60 * 60)   # 1h later
        T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "claimed")
        self.assertEqual(r.get("launch_advisory"), "NO-HEARTBEAT")


class MultistepSubagentLivenessTests(_Base):
    """FR-72 extends to pipeline (mode:pipeline) STEPS: an `agent` step is a
    subagent the engine can't observe either, so it must not false-fail at grace;
    a `shell` step keeps grace-terminal parity."""

    def _pipe(self, steps, **top):
        return _plan([{"id": "p", "repo": "/tmp/x", "mode": "pipeline",
                       "steps": steps}], **top)

    def test_agent_step_no_heartbeat_past_grace_is_advisory(self):
        rd = self._init(self._pipe(
            [{"mode": "agent", "prompt": "one"}, {"mode": "agent", "prompt": "two"}],
            pool_size=1, launch_grace_minutes=10))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)
        (rd / "run-01" / "steps" / "step-01" / "heartbeat.ndjson").write_text("")
        os.environ["ARUNNER_NOW"] = str(1000000 + 11 * 60)
        out = T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "claimed")              # NON-terminal
        self.assertEqual(r.get("launch_advisory"), "NO-HEARTBEAT")
        self.assertFalse(out["done"])

    def test_agent_step_emits_starting(self):
        rd = self._init(self._pipe([{"mode": "agent", "prompt": "one"}], pool_size=1))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)
        lines = _hb_lines(rd, "run-01/steps/step-01")
        self.assertEqual([l["status"] for l in lines], ["STARTING"])

    def test_agent_step_past_hard_cap_is_reclaimed_terminal(self):
        rd = self._init(self._pipe([{"mode": "agent", "prompt": "one"}],
                                   pool_size=1, launch_grace_minutes=10,
                                   subagent_hard_cap_minutes=120))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)
        (rd / "run-01" / "steps" / "step-01" / "heartbeat.ndjson").write_text("")
        os.environ["ARUNNER_NOW"] = str(1000000 + 121 * 60)
        out = T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "auth_or_launch_failed")
        self.assertTrue(out["done"])

    def test_shell_step_no_heartbeat_past_grace_is_terminal(self):
        rd = self._init(self._pipe(
            [{"mode": "shell", "command": ["python3", "w.py", "--hb", "{HEARTBEAT_PATH}"]}],
            pool_size=1, launch_grace_minutes=10))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)
        os.environ["ARUNNER_NOW"] = str(1000000 + 11 * 60)
        out = T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "auth_or_launch_failed")  # shell parity
        self.assertTrue(out["done"])


class HardCapCheckTests(_Base):
    """FR-72: --check rejects a hard cap that is not > launch grace (it would fire
    before the advisory window, inverting the design)."""

    def test_cap_not_above_grace_rejected(self):
        # use the engine's check_plan directly (repo need not exist for this row)
        pf = self.tmp / "p.json"
        pf.write_text(json.dumps({"launch_grace_minutes": 10,
                                  "subagent_hard_cap_minutes": 10,
                                  "jobs": [_subagent_job("t", str(self.tmp))]}))
        probs = T.check_plan(pf)
        self.assertTrue(any("subagent_hard_cap_minutes" in p and "must be >" in p
                            for p in probs), probs)

    def test_generous_cap_above_grace_clean(self):
        pf = self.tmp / "p2.json"
        pf.write_text(json.dumps({"launch_grace_minutes": 10,
                                  "subagent_hard_cap_minutes": 720,
                                  "jobs": [_subagent_job("t", str(self.tmp))]}))
        self.assertEqual(T.check_plan(pf), [])


class LifecycleEmitTests(_Base):
    """Layer B (the FR-40 analogue): the engine emits STARTING at subagent
    dispatch and a simulated return supplies the terminal -- completion never
    depends on the worker self-heartbeating."""

    def test_dispatch_emits_starting_heartbeat(self):
        rd = self._init(_plan([_subagent_job("t-1")], pool_size=1))
        os.environ["ARUNNER_NOW"] = "1000000"
        out = T.tick(rd)
        # the dispatch entry is a subagent hand-off
        self.assertEqual(out["dispatch_list"][0]["dispatch_mode"], "subagent")
        # ... and the engine wrote a STARTING on the worker's behalf
        lines = _hb_lines(rd)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["status"], "STARTING")
        self.assertEqual(lines[0]["task_id"], "t-1")

    def test_shell_dispatch_does_not_emit_starting(self):
        # shell mode's lifecycle is the adapter/worker's (heartbeat.py wrap),
        # NOT the engine's -- the engine must not double-write STARTING there.
        rd = self._init(_plan([_shell_job("t-1")], pool_size=1))
        os.environ["ARUNNER_NOW"] = "1000000"
        out = T.tick(rd)
        self.assertEqual(out["dispatch_list"][0]["dispatch_mode"], "shell")
        self.assertEqual(_hb_lines(rd), [])

    def test_starting_then_simulated_return_completes_without_self_heartbeat(self):
        rd = self._init(_plan([_subagent_job("t-1")], pool_size=1,
                              launch_grace_minutes=10))
        os.environ["ARUNNER_NOW"] = "1000000"
        T.tick(rd)                          # engine STARTING -> claimed
        # next tick observes the engine's STARTING -> running (no worker beat)
        future = (rd / "run-01" / "heartbeat.ndjson").stat().st_mtime
        os.environ["ARUNNER_NOW"] = str(future)
        T.tick(rd)
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "running")
        # the orchestrator records the subagent's return terminal -> completed
        _write_terminal(rd, "t-1", "COMPLETED")
        out = T.tick(rd)
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "completed")
        self.assertTrue(out["done"])

    def test_starting_emit_failure_does_not_crash_dispatch(self):
        # Best-effort: if the heartbeat write fails, dispatch still proceeds and
        # the advisory/hard-cap path backstops liveness. Point _heartbeat_path
        # at a path UNDER an existing file so mkdir(parents) raises OSError --
        # the guard must swallow it and the run must still claim.
        rd = self._init(_plan([_subagent_job("t-1")], pool_size=1))
        os.environ["ARUNNER_NOW"] = "1000000"
        orig_hp = T._heartbeat_path
        # the per-run heartbeat.ndjson already exists as a FILE; a path beneath
        # it can't be mkdir'd.
        bad = rd / "run-01" / "heartbeat.ndjson" / "nested" / "hb.ndjson"
        T._heartbeat_path = lambda run_dir, name: bad
        try:
            T.tick(rd)                    # must not raise
        finally:
            T._heartbeat_path = orig_hp
        # dispatch still happened (run claimed) despite the failed STARTING emit
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "claimed")


if __name__ == "__main__":
    unittest.main()
