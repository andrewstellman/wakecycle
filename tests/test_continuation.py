"""FR-55 — the continuation contract: the per-tick verdict is a PURE FUNCTION
of run-dir state (the closed halt set + CONTINUE), reads persisted status not
control-file presence, carries `next_tick_due`/`monitoring_paused`, and is
persisted + journaled each tick while a STOP tick stays fully read-only.

MUTATION-VERIFY EVIDENCE (instr 036): `test_continue_healthy_midrun` is the
load-bearing pin — the most dangerous engine bug is a verdict that DIVERGES
from state (reporting HALT:done on a still-live run lets a host think it's
finished, or lets an abandonment hide). Mutating `_halt_reason` to return
"done" for a non-terminal run makes this test FAIL; restored -> green.
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = _load("tick_fr55", "arunner/engine/tick.py")


def _status(states, **kw):
    runs = {("run-%02d" % (i + 1)): {"state": s, "job_id": "job-%05d" % (i + 1)}
            for i, s in enumerate(states)}
    st = {"runs": runs, "pool_size": kw.pop("pool_size", 1), "cycle": 1}
    st.update(kw)
    return st


class HaltReasonClosedSet(unittest.TestCase):
    """The verdict is a pure function of disk state across the whole closed set."""

    def setUp(self):
        self.rd = Path(tempfile.mkdtemp())

    def _reason(self, status, stop=False):
        return T._halt_reason(self.rd, status, stop)

    def test_continue_healthy_midrun(self):           # the mutation pin
        self.assertIsNone(self._reason(_status(["running", "queued"], pool_size=1)))

    def test_done_all_completed(self):
        self.assertEqual(self._reason(_status(["completed", "completed"])), "done")

    def test_failed_any_non_clean_terminal(self):
        self.assertEqual(self._reason(_status(["completed", "failed"])), "failed")
        self.assertEqual(self._reason(_status(["abandoned"])), "failed")
        self.assertEqual(self._reason(_status(["auth_or_launch_failed"])), "failed")

    def test_stop_from_file_or_persisted_flag(self):
        self.assertEqual(self._reason(_status(["running"]), stop=True), "stop")
        self.assertEqual(self._reason(_status(["running"], stopped=True)), "stop")

    def test_pause_reads_persisted_status_not_file(self):
        # a consumed PAUSE leaves paused:true and NO file (FR-35) -> HALT:pause
        self.assertEqual(self._reason(_status(["running"], paused=True)), "pause")

    def test_cancel_flag(self):
        self.assertEqual(self._reason(_status(["running"], cancelled=True)), "cancel")

    def test_budget_flag(self):
        self.assertEqual(self._reason(_status(["running"], budget_exhausted=True)),
                         "budget")

    def test_stalled_wedge(self):
        # all non-terminal stalled, pool full, nothing dispatchable -> stalled
        self.assertEqual(self._reason(_status(["stalled"], pool_size=1)), "stalled")

    def test_stalled_not_if_a_run_is_progressing(self):
        self.assertIsNone(self._reason(_status(["stalled", "running"], pool_size=2)))

    def test_stalled_not_if_a_free_slot_can_dispatch(self):
        self.assertIsNone(self._reason(_status(["stalled", "queued"], pool_size=3)))

    def test_precedence_stop_beats_done(self):
        self.assertEqual(self._reason(_status(["completed"]), stop=True), "stop")

    def test_precedence_terminal_beats_stale_pause(self):
        # a finished run with a stale paused flag is done, not pause
        self.assertEqual(self._reason(_status(["completed"], paused=True)), "done")

    def test_reason_is_always_in_the_closed_set(self):
        for st, stop in ((_status(["completed"]), False),
                         (_status(["failed"]), False),
                         (_status(["running"]), True),
                         (_status(["running"], paused=True), False),
                         (_status(["running"], cancelled=True), False),
                         (_status(["stalled"], pool_size=1), False)):
            r = self._reason(st, stop)
            self.assertIn(r, T._CONTINUATION_REASONS)


class InternalErrorCatchAll(unittest.TestCase):
    """`internal_error` is a closed-set reason and must be REACHABLE: a fault
    computing the verdict (e.g. a malformed `runs` record) routes to
    HALT:internal_error rather than crashing the tick or escaping the set.
    (Instr 037 — gap found by the independent FR-55 council.)"""

    def test_malformed_run_record_routes_to_internal_error(self):
        rd = Path(tempfile.mkdtemp())
        # `runs` is not a dict -> _halt_reason raises -> _continuation's
        # try/except routes to the catch-all that keeps the set closed.
        cont = T._continuation(rd, {"runs": 123}, False, 1000.0, 1)
        self.assertEqual(cont["verdict"], "HALT")
        self.assertEqual(cont["reason"], "internal_error")
        self.assertIn(cont["reason"], T._CONTINUATION_REASONS)


class BlockerLifecycle(unittest.TestCase):

    def setUp(self):
        self.rd = Path(tempfile.mkdtemp())

    def _blocker(self, bid, cleared=None):
        bdir = self.rd / "blockers"
        bdir.mkdir(exist_ok=True)
        (bdir / (bid + ".json")).write_text(json.dumps(
            {"id": bid, "created_at": "t0", "reason": "operator decision",
             "cleared_at": cleared}), encoding="utf-8")

    def test_open_blocker_halts_blocked(self):
        self._blocker("b1")
        self.assertEqual(T._halt_reason(self.rd, _status(["running"]), False),
                         "blocked")
        cont = T._continuation(self.rd, _status(["running"]), False, 1000.0, 1)
        self.assertEqual(cont["verdict"], "HALT")
        self.assertEqual(cont["reason"], "blocked")
        self.assertEqual(cont["blocker_id"], "b1")

    def test_cleared_blocker_resumes_continue(self):
        self._blocker("b1", cleared="t9")
        self.assertIsNone(T._halt_reason(self.rd, _status(["running"]), False))


class ContinuationObject(unittest.TestCase):

    def setUp(self):
        self.rd = Path(tempfile.mkdtemp())

    def test_next_tick_due_and_monitoring_paused(self):
        cont = T._continuation(self.rd,
                               _status(["running"], monitoring_paused=True),
                               False, 1000.0, 5)
        self.assertEqual(cont["verdict"], "CONTINUE")
        self.assertEqual(cont["next_tick_due"], 1000 + 5 * 60)
        self.assertTrue(cont["monitoring_paused"])

    def test_verdict_str_canonical(self):
        self.assertEqual(T._verdict_str({"verdict": "CONTINUE"}), "CONTINUE")
        self.assertEqual(T._verdict_str({"verdict": "HALT", "reason": "done"}),
                         "HALT:done")
        self.assertEqual(T._verdict_str({"verdict": "HALT", "reason": "blocked"}),
                         "HALT:blocked")


class TickIntegration(unittest.TestCase):
    """End-to-end through tick(): the verdict is persisted + journaled, and a
    STOP tick emits HALT:stop while writing nothing (read-only)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.runs_root = Path(self.tmp) / "runs"
        self.runs_root.mkdir(parents=True)
        os.environ["ARUNNER_RUNS_DIR"] = str(self.runs_root)

    def tearDown(self):
        os.environ.pop("ARUNNER_RUNS_DIR", None)

    def _init(self):
        plan = {"schema_version": "1", "pool_size": 1, "tick_interval_minutes": 1,
                "entries": [{"task_id": "c-1", "target_repo": self.tmp,
                             "dispatch_mode": "subagent",
                             "worker_prompt": "stub"}]}
        pf = Path(self.tmp) / "plan.json"
        pf.write_text(json.dumps(plan), encoding="utf-8")
        return T.init_run(pf)

    def _journal(self, rd):
        p = rd / "journal.ndjson"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
                if x.strip()]

    def test_continuation_persisted_and_journaled(self):
        rd = self._init()
        out = T.tick(rd)                      # dispatch -> claimed, non-terminal
        self.assertEqual(out["continuation"]["verdict"], "CONTINUE")
        st = json.loads((rd / "harness_status.json").read_text(encoding="utf-8"))
        self.assertEqual(st["continuation"]["verdict"], "CONTINUE")
        jl = self._journal(rd)
        self.assertTrue(jl)
        self.assertEqual(jl[-1]["type"], "verdict")
        self.assertEqual(jl[-1]["verdict"], "CONTINUE")

    def test_done_verdict_on_all_terminal(self):
        rd = self._init()
        T.tick(rd)
        st = json.loads((rd / "harness_status.json").read_text(encoding="utf-8"))
        for r in st["runs"].values():
            r["state"] = "completed"
        (rd / "harness_status.json").write_text(json.dumps(st), encoding="utf-8")
        out = T.tick(rd)
        self.assertEqual(out["continuation"]["verdict"], "HALT")
        self.assertEqual(out["continuation"]["reason"], "done")

    def test_stop_tick_emits_halt_stop_but_writes_nothing(self):
        rd = self._init()
        T.tick(rd)
        before_status = (rd / "harness_status.json").read_bytes()
        before_journal = (rd / "journal.ndjson").read_bytes()
        (rd / "STOP").write_text("", encoding="utf-8")
        out = T.tick(rd)
        self.assertEqual(out["continuation"]["verdict"], "HALT")
        self.assertEqual(out["continuation"]["reason"], "stop")
        self.assertEqual((rd / "harness_status.json").read_bytes(), before_status,
                         "STOP tick mutated harness_status.json (not read-only)")
        self.assertEqual((rd / "journal.ndjson").read_bytes(), before_journal,
                         "STOP tick appended to the journal (not read-only)")


if __name__ == "__main__":
    unittest.main()
