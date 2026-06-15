"""FR-58a -- frequent activity-refresh cadence (engine).

The wrap/tail keepalive interval USED to be min(launch_grace, stall/3) (~10 min
at defaults), so a normal-length job's FR-56 activity patterns never fired and
the ACTIVITY column never moved. FR-58a makes the interval a configurable
``--keepalive-seconds`` (plan field), DEFAULT ~45s, decoupled from stall/3, with
a first-scan IN_PROGRESS right after STARTING. It also fixes the latent bug that
``--launch-grace-minutes``/``--stall-threshold-minutes`` were NEVER synthesized
into the adapter (the plan's grace/stall were inert for adapter jobs).

The FR-56 EndToEnd tests used grace 0 (floored to 1s by the old coupling), which
is exactly why the default-path breakage shipped unnoticed -- so these drive the
DEFAULT path (grace > 0 / the 45s default), per the council.

MUTATION PINS (instr 046):
  * test_default_keepalive_is_45_decoupled  -- the interval no longer derives
    from stall/3 (the whole fix).
  * test_first_scan_at_start                -- a sub-interval job still surfaces
    a line.
  * test_label_moves_across_keepalives       -- the ACTIVITY column actually moves.
  * test_adapter_synthesizes_all_three       -- grace/stall/keepalive now flow to
    the adapter (the inert-knob fix).
  * test_check_rejects_keepalive_over_grace  -- fail-loud pre-gate.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_HB = _ROOT / "arunner" / "engine" / "heartbeat.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


H = _load("hb_cad", _HB)
T = _load("tick_cad", _ROOT / "arunner" / "engine" / "tick.py")


def _args(**kw):
    ns = argparse.Namespace(keepalive_seconds=None, launch_grace_minutes=None,
                            stall_threshold_minutes=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _inprogress_labels(hb):
    out = []
    for ln in Path(hb).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        obj = json.loads(ln)
        if obj.get("status") == "IN_PROGRESS":
            out.append(obj.get("label", ""))
    return out


class Resolver(unittest.TestCase):

    def test_default_keepalive_is_45_decoupled(self):           # PIN
        # default: 45s, NOT min(grace, stall/3) (which would be ~600s at defaults)
        self.assertEqual(H._resolve_keepalive_secs(_args()), 45.0)
        # the OLD coupling would have returned 600 for these grace/stall:
        self.assertEqual(
            H.keepalive_interval_secs(600, 2700), 600.0)         # old formula
        self.assertEqual(H._resolve_keepalive_secs(
            _args(launch_grace_minutes=10, stall_threshold_minutes=45)), 45.0)

    def test_explicit_keepalive_wins_and_floors(self):
        self.assertEqual(H._resolve_keepalive_secs(_args(keepalive_seconds=10)), 10.0)
        self.assertEqual(H._resolve_keepalive_secs(_args(keepalive_seconds=0)), 1.0)  # floor
        self.assertEqual(H._resolve_keepalive_secs(_args(keepalive_seconds=-5)), 1.0)


class FirstScanAndMovement(unittest.TestCase):

    def _ka(self, cap, hb):
        activity = H._compile_activity(["step \\d+"])
        reader = H._LogTail(cap)
        return H._Keepalive(hb_path=hb, task_id="t", capture_path=cap,
                            interval_secs=45.0, start_ts=0.0,
                            activity=activity, activity_reader=reader)

    def test_first_scan_at_start(self):                         # PIN
        d = Path(tempfile.mkdtemp())
        cap = d / "cap.out"; cap.write_text("noise\n", encoding="utf-8")
        hb = d / "hb.ndjson"; hb.touch()
        ka = self._ka(cap, hb)
        # emit_first fires an IN_PROGRESS even though no interval has elapsed
        self.assertFalse(ka.due(0.0))
        ka.emit_first(0.0)
        labels = _inprogress_labels(hb)
        self.assertEqual(len(labels), 1, "first-scan did not emit one IN_PROGRESS")

    def test_label_moves_across_keepalives(self):              # PIN
        d = Path(tempfile.mkdtemp())
        cap = d / "cap.out"; cap.write_text("noise\n", encoding="utf-8")
        hb = d / "hb.ndjson"; hb.touch()
        ka = self._ka(cap, hb)
        ka.emit_first(0.0)                                      # no match yet
        with cap.open("a", encoding="utf-8") as fh:
            fh.write("step 1\n")
        self.assertTrue(ka.maybe_emit(45.0))                   # due -> "step 1"
        with cap.open("a", encoding="utf-8") as fh:
            fh.write("noise\nstep 2\n")
        self.assertTrue(ka.maybe_emit(90.0))                   # due -> "step 2"
        labels = _inprogress_labels(hb)
        self.assertEqual(labels[0], "(running...)")            # first scan, no match
        self.assertTrue(any("step 1" in lb for lb in labels), labels)
        self.assertEqual(labels[-1], "step 2", labels)          # moved to the latest
        self.assertFalse(any("noise" in lb for lb in labels), labels)


class AdapterSynthesis(unittest.TestCase):

    def test_adapter_synthesizes_all_three(self):              # PIN
        cmd = T._adapter_worker_cmd(
            {"adapter": "wrap", "command": ["make"]},
            {"launch_grace_minutes": 20, "stall_threshold_minutes": 60,
             "keepalive_seconds": 30})
        for flag, val in (("--launch-grace-minutes", "20"),
                          ("--stall-threshold-minutes", "60"),
                          ("--keepalive-seconds", "30")):
            self.assertIn(flag, cmd)
            self.assertEqual(cmd[cmd.index(flag) + 1], val, cmd)

    def test_entry_override_wins_over_plan(self):
        cmd = T._adapter_worker_cmd(
            {"adapter": "wrap", "command": ["make"], "keepalive_seconds": 5},
            {"keepalive_seconds": 30})
        self.assertEqual(cmd[cmd.index("--keepalive-seconds") + 1], "5")

    def test_defaults_when_no_plan(self):
        cmd = T._adapter_worker_cmd({"adapter": "tail", "log_path": "x"})
        self.assertEqual(cmd[cmd.index("--keepalive-seconds") + 1],
                         str(T.DEFAULT_KEEPALIVE_SECONDS))
        self.assertEqual(cmd[cmd.index("--launch-grace-minutes") + 1],
                         str(T.DEFAULT_LAUNCH_GRACE_MINUTES))


class CheckGate(unittest.TestCase):

    def _check(self, plan):
        d = Path(tempfile.mkdtemp()) / "p.json"
        d.write_text(json.dumps(plan), encoding="utf-8")
        return T.check_plan(d)

    def _wrap_entry(self):
        return {"task_id": "a", "target_repo": ".", "dispatch_mode": "shell",
                "adapter": "wrap", "command": ["make"]}

    def test_check_rejects_keepalive_over_grace(self):         # PIN
        probs = self._check({"launch_grace_minutes": 10, "keepalive_seconds": 700,
                             "entries": [self._wrap_entry()]})
        self.assertTrue(any("keepalive_seconds" in p and "launch grace" in p
                            for p in probs), probs)

    def test_check_accepts_keepalive_within_grace(self):
        self.assertEqual(
            self._check({"launch_grace_minutes": 10, "keepalive_seconds": 30,
                         "entries": [self._wrap_entry()]}), [])

    def test_check_rejects_entry_level_override_over_grace(self):
        e = self._wrap_entry(); e["keepalive_seconds"] = 999
        probs = self._check({"launch_grace_minutes": 5, "entries": [e]})
        self.assertTrue(any("entries[0].keepalive_seconds" in p for p in probs), probs)


class RealDefaultGracePath(unittest.TestCase):
    """Drive the REAL wrap adapter on the DEFAULT grace path (grace > 0, NOT the
    grace-0 the FR-56 tests used) with a fast keepalive, proving first-scan +
    moving ACTIVITY end-to-end through the subprocess."""

    def test_wrap_default_grace_label_moves(self):
        d = Path(tempfile.mkdtemp())
        hb = d / "hb.ndjson"
        stub = _ROOT / "tests" / "integration" / "stub_worker.py"
        # NO --launch-grace-minutes 0 here: default grace (10 min) + a fast
        # keepalive. The OLD code would keepalive every min(600, 900)=600s and
        # surface nothing; FR-58a decouples the interval.
        subprocess.run(
            [sys.executable, str(_HB), "wrap", "--task-id", "w",
             "--heartbeat-path", str(hb), "--keepalive-seconds", "0.3",
             "--activity-regex", r"step \d+",
             "--", sys.executable, str(stub), "--emit", "log", "--noise", "2",
             "--steps", "6", "--sleep", "0.4"],
            capture_output=True, timeout=40)
        labels = _inprogress_labels(hb)
        self.assertTrue(labels, "no IN_PROGRESS fired on the default grace path")
        self.assertTrue(any("step" in lb for lb in labels),
                        "ACTIVITY never moved to a relevant line: %r" % labels)
        self.assertFalse(any("noise: chatter" in lb for lb in labels), labels)


if __name__ == "__main__":
    unittest.main()
