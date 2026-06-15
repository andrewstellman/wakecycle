"""FR-57 live enqueue -- STAGE-AND-ABSORB.

`arunner add` validates the new entries (--check, FR-42) and writes them to
`<run-dir>/incoming/` -- it NEVER mutates the live plan.json / harness_status.json
a concurrent tick reads/writes. The tick engine absorbs `incoming/` at the start
of a tick, under the `.tick.lock` it already holds: append to plan.json["entries"],
scaffold the new run-NN (mirroring init_run) + a `queued` record, retire the
absorbed file. Race-free by construction.

MUTATION PINS (instr 046):
  * test_append_only_numbering -- new run-NN continue from the current entry
    count; a renumber (or len(entries)<->runs mismatch) is silently swallowed by
    the tick's except and drops jobs. The load-bearing invariant.
  * test_add_never_touches_live_files -- the race-free guarantee: add writes ONLY
    incoming/.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TICK = _ROOT / "arunner" / "engine" / "tick.py"
_TICKER = _ROOT / "arunner" / "engine" / "ticker.py"
_PLANS = _ROOT / "tests" / "acceptance" / "plans"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TICK = _load("tick_enq", _TICK)
C = _load("checker_enq", _ROOT / "tests" / "integration" / "checker.py")
import arunner.cli as CLI


def _wrap_entry(task_id, msg="ok"):
    return {"task_id": task_id, "target_repo": ".", "dispatch_mode": "shell",
            "adapter": "wrap", "command": ["python3", "-c", "print('%s')" % msg]}


class _Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.runs = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def _init(self, plan):
        p = self.runs / "plan_src.json"
        p.write_text(json.dumps(plan), encoding="utf-8")
        os.environ["ARUNNER_RUNS_DIR"] = str(self.runs)
        try:
            return TICK.init_run(p)
        finally:
            os.environ.pop("ARUNNER_RUNS_DIR", None)

    def _add(self, run_dir, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = CLI.main(["add", str(run_dir)] + argv)
        return rc, out.getvalue()

    def _stage_file(self, entries, pool=None):
        doc = {"entries": entries}
        if pool is not None:
            doc["pool_size"] = pool
        f = self.runs / "addsrc.json"
        f.write_text(json.dumps(doc), encoding="utf-8")
        return f

    def _drive(self, run_dir, max_ticks=14):
        env = dict(os.environ, ARUNNER_RUNS_DIR=str(self.runs))
        for _ in range(max_ticks):
            st = json.loads((run_dir / "harness_status.json").read_text(encoding="utf-8"))
            if st.get("done"):
                break
            subprocess.run([sys.executable, str(_TICKER), "--once", str(run_dir)],
                           env=env, capture_output=True, timeout=60)
        return json.loads((run_dir / "harness_status.json").read_text(encoding="utf-8"))


class StageAndAbsorb(_Base):

    def test_add_stages_to_incoming(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        rc, msg = self._add(rd, [str(self._stage_file([_wrap_entry("b")]))])
        self.assertEqual(rc, 0, msg)
        staged = list((rd / "incoming").glob("*.json"))
        self.assertEqual(len(staged), 1, "add did not stage to incoming/")
        self.assertIn("entries", json.loads(staged[0].read_text()))

    def test_add_never_touches_live_files(self):            # PIN (race-free)
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        before_plan = (rd / "plan.json").read_bytes()
        before_status = (rd / "harness_status.json").read_bytes()
        self._add(rd, [str(self._stage_file([_wrap_entry("b")]))])
        self.assertEqual((rd / "plan.json").read_bytes(), before_plan,
                         "add mutated the live plan.json (not race-free)")
        self.assertEqual((rd / "harness_status.json").read_bytes(), before_status,
                         "add mutated the live harness_status.json")

    def test_tick_absorbs_and_new_run_completes(self):
        rd = self._init({"pool_size": 2,
                         "entries": [_wrap_entry("a"), _wrap_entry("b")]})
        self._add(rd, [str(self._stage_file([_wrap_entry("c"), _wrap_entry("d")]))])
        final = self._drive(rd)
        self.assertTrue(final["done"])
        self.assertEqual(final["counts"]["completed"], 4)
        self.assertEqual(len(json.loads((rd / "plan.json").read_text())["entries"]), 4)
        # the staged file was retired
        self.assertEqual(list((rd / "incoming").glob("*.json")), [])
        self.assertEqual(C.check(rd, {"done": True, "counts": {"completed": 4},
                                      "no_double_dispatch": True}), [])

    def test_append_only_numbering(self):                   # PIN
        rd = self._init({"pool_size": 1,
                         "entries": [_wrap_entry("a"), _wrap_entry("b")]})
        self._add(rd, [str(self._stage_file([_wrap_entry("c")]))])
        # absorb under one tick (subprocess so the .tick.lock is held)
        env = dict(os.environ, ARUNNER_RUNS_DIR=str(self.runs))
        subprocess.run([sys.executable, str(_TICK), str(rd)], env=env,
                       capture_output=True, timeout=60)
        status = json.loads((rd / "harness_status.json").read_text())
        runs = status["runs"]
        # existing runs keep their numbers; the new one APPENDS as run-03
        self.assertIn("run-01", runs)
        self.assertIn("run-02", runs)
        self.assertIn("run-03", runs)
        self.assertEqual(runs["run-03"]["task_id"], "c")
        # positional rebuild stays correct: len(entries) == len(runs)
        entries = json.loads((rd / "plan.json").read_text())["entries"]
        self.assertEqual(len(entries), len(runs))
        self.assertEqual(entries[2]["task_id"], "c")

    def test_check_rejects_bad_add_before_landing(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        bad = self._stage_file([{"task_id": "bad", "target_repo": ".",
                                 "dispatch_mode": "shell", "adapter": "wrap",
                                 "command": "not-an-array"}])
        rc, msg = self._add(rd, [str(bad)])
        self.assertEqual(rc, 1, msg)
        self.assertIn("FAILED", msg)
        # nothing landed in incoming/
        self.assertFalse((rd / "incoming").exists()
                         and list((rd / "incoming").glob("*.json")))

    def test_placeholders_stored_unresolved(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        entry = {"task_id": "ph", "target_repo": "{TARGET_REPO}",
                 "dispatch_mode": "subagent",
                 "worker_prompt": "HEARTBEAT_PATH={HEARTBEAT_PATH}\nTASK_ID={TASK_ID}"
                                  "\nRUN_DIR={RUN_DIR}\nTARGET_REPO={TARGET_REPO}"
                                  "\nHARNESS_BIN={HARNESS_BIN}\nstub"}
        f = self.runs / "ph.json"
        f.write_text(json.dumps({"entries": [entry]}), encoding="utf-8")
        # --check the bare placeholder entry would fail on target_repo existence,
        # so stage it directly (the absorb stores it verbatim) and absorb.
        (rd / "incoming").mkdir(exist_ok=True)
        (rd / "incoming" / "add-x.json").write_text(
            json.dumps({"entries": [entry]}), encoding="utf-8")
        env = dict(os.environ, ARUNNER_RUNS_DIR=str(self.runs))
        subprocess.run([sys.executable, str(_TICK), str(rd)], env=env,
                       capture_output=True, timeout=60)
        entries = json.loads((rd / "plan.json").read_text())["entries"]
        self.assertEqual(entries[-1]["target_repo"], "{TARGET_REPO}")  # UNRESOLVED
        self.assertIn("{HEARTBEAT_PATH}", entries[-1]["worker_prompt"])

    def test_add_to_a_done_run_reactivates(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        final = self._drive(rd)
        self.assertTrue(final["done"])                       # run is DONE
        self._add(rd, [str(self._stage_file([_wrap_entry("b")]))])
        # a done run needs one tick to absorb the add (the ticker loop stopped);
        # that absorb tick flips done->False (the new queued run re-activates).
        env = dict(os.environ, ARUNNER_RUNS_DIR=str(self.runs))
        subprocess.run([sys.executable, str(_TICKER), "--once", str(rd)],
                       env=env, capture_output=True, timeout=60)
        absorbed = json.loads((rd / "harness_status.json").read_text())
        self.assertFalse(absorbed["done"], "add to a done run did not re-activate")
        final2 = self._drive(rd)
        self.assertTrue(final2["done"])
        self.assertEqual(final2["counts"]["completed"], 2)

    def test_stop_tick_does_not_absorb(self):               # PIN (STOP read-only)
        # FR-10/FR-35: a STOP tick is fully read-only -- a staged add must wait
        # UNTOUCHED in incoming/ while STOP is present (absorbing on a STOP tick
        # would mutate plan.json/harness_status.json + consume the staged file).
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        self._add(rd, [str(self._stage_file([_wrap_entry("b")]))])
        (rd / "STOP").write_text("", encoding="utf-8")
        plan_before = (rd / "plan.json").read_bytes()
        status_before = (rd / "harness_status.json").read_bytes()
        env = dict(os.environ, ARUNNER_RUNS_DIR=str(self.runs))
        subprocess.run([sys.executable, str(_TICK), str(rd)], env=env,
                       capture_output=True, timeout=60)
        # nothing mutated, the staged add survives
        self.assertEqual((rd / "plan.json").read_bytes(), plan_before,
                         "STOP tick absorbed (mutated plan.json) -- not read-only")
        self.assertEqual((rd / "harness_status.json").read_bytes(), status_before)
        self.assertEqual(len(list((rd / "incoming").glob("*.json"))), 1,
                         "STOP tick consumed the staged add")
        # clearing STOP lets the NEXT tick absorb it
        (rd / "STOP").unlink()
        subprocess.run([sys.executable, str(_TICK), str(rd)], env=env,
                       capture_output=True, timeout=60)
        self.assertEqual(
            len(json.loads((rd / "plan.json").read_text())["entries"]), 2)

    def test_two_staged_adds_absorb_in_order(self):
        # concurrent-add safety: multiple staged files all absorb under the lock,
        # append-only, none clobbered.
        rd = self._init({"pool_size": 3, "entries": [_wrap_entry("a")]})
        self._add(rd, [str(self._stage_file([_wrap_entry("b")]))])
        # a second add stages a DISTINCT file (unique name)
        f2 = self.runs / "addsrc2.json"
        f2.write_text(json.dumps({"entries": [_wrap_entry("c")]}), encoding="utf-8")
        self._add(rd, [str(f2)])
        self.assertEqual(len(list((rd / "incoming").glob("*.json"))), 2)
        final = self._drive(rd)
        self.assertTrue(final["done"])
        self.assertEqual(final["counts"]["completed"], 3)
        self.assertEqual(len(json.loads((rd / "plan.json").read_text())["entries"]), 3)

    def test_command_form_stages_a_wrap_job(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        rc, msg = self._add(rd, ["--command", "python3 -c \"print(1)\""])
        self.assertEqual(rc, 0, msg)
        staged = json.loads(next((rd / "incoming").glob("*.json")).read_text())
        e = staged["entries"][0]
        self.assertEqual(e["adapter"], "wrap")
        self.assertEqual(e["command"], ["python3", "-c", "print(1)"])
        self.assertTrue(e["task_id"])                        # minted


if __name__ == "__main__":
    unittest.main()
