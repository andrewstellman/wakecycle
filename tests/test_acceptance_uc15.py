"""instr 045 -- driving gradeable-leg tests for UC-1..5 + the UC-6/7 legs.

The coverage council found UC-1..5 had plans + `expected` that `--check` clean
but NO driving test (only UC-8..12 got those), and UC-6/7 had no distinct leg.
This closes the DETERMINISTIC, disk-objective part: each plan is driven to its
`expected` state and graded by the independent checker -- proving the plan
actually reaches its end-state, not merely that it parses.

How the rungs are driven deterministically (no live agent, reproducible):
  * subagent plans (UC-1/2/3/4) -- a tiny in-test driver ticks the engine and,
    for each subagent the tick dispatches, emits the STARTING + COMPLETED
    heartbeats a real stub subagent would (exactly the UC-8 rung-1 drive the
    worker did live, automated). The ENGINE still does all dispatch/reap/idem-
    potency/STOP/resume logic; the driver only stands in for the worker turn.
  * shell/floor plan (UC-5) -- driven by the REAL ticker (`ticker.py --once`),
    the genuine rung-3 path (as in instr 043).
The LIVE in-agent / real-scheduler runs remain the operator's recorded action
(ACCEPTANCE_TESTS.md); these are the deterministic legs underneath.

MUTATION PINS (instr 045) -- each load-bearing engine invariant, verified to
bite (revert -> fail -> restore) and recorded in outputs/045:
  * UC-1/UC-5  reaches `done` with every job completed (the reaper).
  * UC-2/UC-4  `no_double_dispatch` across an idempotent re-tick / a resume
               (the `_dispatch` past-queued guard).
  * UC-3       the STOP tick is read-only (the `if not stop:` gate).
  * UC-6       exactly one tick per `--once` fire (cycle advances by 1).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PLANS = _ROOT / "tests" / "acceptance" / "plans"
_TICK = _ROOT / "arunner" / "engine" / "tick.py"
_TICKER = _ROOT / "arunner" / "engine" / "ticker.py"
_INT = _ROOT / "tests" / "integration"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("checker_uc15", _INT / "checker.py")
HB = _load("hb_uc15", _ROOT / "arunner" / "engine" / "heartbeat.py")


def _plan(name):
    return json.loads((_PLANS / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Deterministic drivers
# --------------------------------------------------------------------------- #
def _init(plan_name, runs_root):
    env = dict(os.environ, ARUNNER_RUNS_DIR=str(runs_root))
    out = subprocess.run([sys.executable, str(_TICK), "--init",
                          str(_PLANS / plan_name)],
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return Path(out.stdout.strip().splitlines()[-1])


def _tick(target, runs_root, now=None):
    """Run one engine tick; return the parsed JSON result (dispatch_list etc.)."""
    env = dict(os.environ, ARUNNER_RUNS_DIR=str(runs_root))
    if now is not None:
        env["ARUNNER_NOW"] = str(now)
    out = subprocess.run([sys.executable, str(_TICK), str(target)],
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _emit_worker(run_dir, run, task_id):
    """Stand in for a stub subagent turn: STARTING + COMPLETED heartbeats, the
    exact two lines the UC plans' worker_prompt instructs a real subagent to
    write. The engine reaps the COMPLETED terminal on the next tick."""
    hb = Path(run_dir) / run / "heartbeat.ndjson"
    HB.append_line(hb, HB.build_progress(label="stub", task_id=task_id,
                                         status="STARTING"))
    rf = Path(run_dir) / run / "result.txt"
    rf.write_text("stub %s done\n" % task_id, encoding="utf-8")
    HB.append_line(hb, HB.build_terminal(task_id=task_id, status="COMPLETED",
                                         result_file=str(rf),
                                         summary="stub acceptance worker done"))


def _drive_subagent(plan_name, runs_root, max_ticks=12, emit=True):
    """Drive a subagent plan to `done` (or until max_ticks): tick, emit the
    worker heartbeats for everything the tick dispatched, repeat. Returns the
    run-dir. With emit=False the workers are NOT run (used for the STOP leg)."""
    run_dir = _init(plan_name, runs_root)
    for _ in range(max_ticks):
        res = _tick(run_dir, runs_root)
        if emit:
            for d in res.get("dispatch_list", []):
                if d.get("dispatch_mode") == "subagent":
                    _emit_worker(run_dir, d["run"], d["task_id"])
        if res.get("done"):
            break
    return run_dir


def _drive_floor_ticker(plan_name, runs_root, max_ticks=12):
    """Drive a shell/floor plan to done via the REAL ticker (rung 3)."""
    env = dict(os.environ, ARUNNER_RUNS_DIR=str(runs_root))
    subprocess.run([sys.executable, str(_TICKER), "--once", str(_PLANS / plan_name)],
                   env=env, capture_output=True, timeout=60)
    run_dir = sorted(p for p in Path(runs_root).iterdir() if p.is_dir())[0]
    for _ in range(max_ticks):
        st = json.loads((run_dir / "harness_status.json").read_text(encoding="utf-8"))
        if st.get("done"):
            break
        subprocess.run([sys.executable, str(_TICKER), "--once", str(run_dir)],
                       env=env, capture_output=True, timeout=60)
    return run_dir


def _status(run_dir):
    return json.loads((Path(run_dir) / "harness_status.json").read_text(
        encoding="utf-8"))


import unittest


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.runs = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()


class Uc1Multijob(_Tmp):
    """UC-1: the multijob subagent plan driven to all-completed / done."""

    def test_multijob_drives_to_done(self):                     # PIN (reaper)
        rd = _drive_subagent("uc1_multijob.json", self.runs)
        self.assertEqual(C.check(rd, _plan("uc1_expected.json")), [])
        self.assertTrue(_status(rd)["done"])


class Uc2Monitor(_Tmp):
    """UC-2: the tick-now idempotency leg -- an EXTRA tick after done moves only
    the cycle counter; never a re-dispatch (no_double_dispatch)."""

    def test_tick_now_is_idempotent(self):                      # PIN (dispatch guard)
        rd = _drive_subagent("uc2_monitor.json", self.runs)
        self.assertEqual(C.check(rd, _plan("uc2_expected.json")), [])
        before = _status(rd)
        # an on-demand "tick now" after the run settled
        self._extra = _tick(rd, self.runs)
        after = _status(rd)
        self.assertEqual(after["cycle"], before["cycle"] + 1)   # only the cycle moved
        self.assertEqual(after["counts"], before["counts"])     # no state churn
        self.assertEqual(C.check(rd, _plan("uc2_expected.json")), [])  # still no double-dispatch


class Uc3Halt(_Tmp):
    """UC-3: snapshot -> STOP -> tick -> compare. The STOP tick is read-only:
    it changes nothing (not even the cycle)."""

    def test_stop_is_read_only(self):                           # PIN (not-stop gate)
        # one tick dispatches both (pool 2); emit their heartbeats but do NOT
        # let a reaping tick run -- snapshot the claimed state, then STOP.
        rd = _init("uc3_halt.json", self.runs)
        res = _tick(rd, self.runs)
        for d in res.get("dispatch_list", []):
            _emit_worker(rd, d["run"], d["task_id"])
        # snapshot BEFORE the control action (the runbook's read-only proof)
        shutil.copy2(Path(rd) / "harness_status.json",
                     Path(rd) / "_before_snapshot.json")
        (Path(rd) / "STOP").write_text("", encoding="utf-8")
        self._stop_tick = _tick(rd, self.runs)                  # the read-only STOP tick
        self.assertEqual(C.check(rd, _plan("uc3_expected.json")), [])


class Uc4Resume(_Tmp):
    """UC-4: pool 1 -> uc4-b is queued while uc4-a runs. The resume continues
    with NO double-dispatch; the final reap rides a wall-clock JUMP (hibernate)
    without a false STALL."""

    def test_resume_no_double_dispatch_and_wallclock_jump(self):  # PIN (dispatch guard)
        rd = _init("uc4_resume.json", self.runs)
        # tick 1: dispatch uc4-a (run-01); uc4-b queued (pool 1)
        res = _tick(rd, self.runs)
        for d in res.get("dispatch_list", []):
            _emit_worker(rd, d["run"], d["task_id"])
        # tick 2 (the RESUME): reap run-01, dispatch run-02 -- continuity
        res = _tick(rd, self.runs)
        for d in res.get("dispatch_list", []):
            _emit_worker(rd, d["run"], d["task_id"])
        # final tick rides a large wall-clock jump (sleep/hibernate): the run
        # must reap to done with NO false STALL (E2 wall-clock-jump guard).
        jump = _status(rd).get("last_tick_wall", 0) + 999999
        self._final = _tick(rd, self.runs, now=jump)
        st = _status(rd)
        self.assertEqual(st["counts"].get("stalled", 0), 0, "false STALL on resume")
        self.assertEqual(C.check(rd, _plan("uc4_expected.json")), [])


class Uc5Floor(_Tmp):
    """UC-5: the shell floor plan driven by the REAL ticker to done (rung 3)."""

    def test_floor_drives_to_done_via_real_ticker(self):        # PIN (reaper)
        rd = _drive_floor_ticker("uc5_floor.json", self.runs)
        self.assertEqual(C.check(rd, _plan("uc5_expected.json")), [])
        self.assertTrue(_status(rd)["done"])


class Uc6Uc7SchedulerManual(_Tmp):
    """UC-6 (scheduled) / UC-7 (manual): both share the `ticker.py --once`
    mechanism; their distinguishing leg is the real scheduler / manual
    environment, which is OPERATOR-RECORDED. The deterministic part graded here
    is one-tick-per-fire idempotency: each `--once` does exactly one tick (the
    cycle advances by 1, no double-dispatch), and N fires drive the run to done."""

    def _ticker_once(self, target):
        env = dict(os.environ, ARUNNER_RUNS_DIR=str(self.runs))
        subprocess.run([sys.executable, str(_TICKER), "--once", str(target)],
                       env=env, capture_output=True, timeout=60)

    def test_one_tick_per_fire_advances_to_done(self):          # PIN (one-tick-per-fire)
        # UC-6: simulate N scheduler fires, one --once each. Each fire must
        # advance the cycle by EXACTLY 1 (one tick per fire) and never
        # double-dispatch; the run reaches done over the fires.
        self._ticker_once(str(_PLANS / "uc5_floor.json"))       # fire 1 = init + tick
        run_dir = sorted(p for p in self.runs.iterdir() if p.is_dir())[0]
        last_cycle = _status(run_dir)["cycle"]
        fires = 1
        while not _status(run_dir).get("done") and fires < 12:
            self._ticker_once(run_dir)                          # one scheduler fire
            fires += 1
            cyc = _status(run_dir)["cycle"]
            self.assertEqual(cyc, last_cycle + 1,
                             "a single --once fire did more than one tick")
            last_cycle = cyc
        self.assertTrue(_status(run_dir)["done"])
        # one-tick-per-fire never double-dispatches (UC-7 manual = same mechanism)
        self.assertEqual(C.check(run_dir, _plan("uc5_expected.json")), [])

    def test_manual_repeat_reaches_done(self):                  # UC-7
        # UC-7: repeated manual --once by hand reaches done (operator-paced; the
        # deterministic equivalent of the recorded manual run).
        rd = _drive_floor_ticker("uc5_floor.json", self.runs)
        self.assertTrue(_status(rd)["done"])
        self.assertEqual(C.check(rd, _plan("uc5_expected.json")), [])


if __name__ == "__main__":
    unittest.main()
