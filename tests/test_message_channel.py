"""FR-60 -- chat <-> runner message channel (typed inbox/outbox).

A typed, acknowledged, idempotent control channel. The chat drops
<run-dir>/inbox/<id>.json; the engine drains the inbox at the start of each tick
UNDER the .tick.lock (mirroring FR-57's incoming/ absorb), processes each message
idempotently (a processed-ids ledger, mark-FIRST, makes a crash/replay a no-op),
acks every message to the append-only outbox, and emits a result correlating the
message id <-> spawned task_id when the staged work completes. Closed verb set;
read-only verbs mutate no run state; local-disk only (no network listener).

MUTATION PINS (instr 048):
  * test_replay_processed_id_is_a_noop / test_ledger_prevents_reapply -- the
    idempotency guarantee (mark-first ledger): a replayed/crashed id never
    double-applies.
  * test_readonly_verbs_never_write_run_state -- snapshot/note write only the
    outbox/journal, never harness_status.json / plan.json / .tick.lock.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
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


TICK = _load("tick_msg", _TICK)
C = _load("checker_msg", _ROOT / "tests" / "integration" / "checker.py")
import arunner.cli as CLI


def _wrap_entry(tid, msg="ok"):
    return {"task_id": tid, "target_repo": ".", "dispatch_mode": "shell",
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

    def _inbox(self, rd, mid, verb, args=None):
        ib = rd / "inbox"
        ib.mkdir(exist_ok=True)
        (ib / (mid + ".json")).write_text(
            json.dumps({"id": mid, "verb": verb, "args": args or {}}),
            encoding="utf-8")

    def _entries(self, rd):
        return json.loads((rd / "plan.json").read_text())["entries"]

    def _ack(self, rd, mid):
        return json.loads((rd / "outbox" / (mid + ".ack.json")).read_text())

    def _tick(self, rd):
        env = dict(os.environ, ARUNNER_RUNS_DIR=str(self.runs))
        subprocess.run([sys.executable, str(_TICK), str(rd)], env=env,
                       capture_output=True, timeout=60)

    def _drive(self, rd, max_ticks=14):
        env = dict(os.environ, ARUNNER_RUNS_DIR=str(self.runs))
        for _ in range(max_ticks):
            st = json.loads((rd / "harness_status.json").read_text())
            if st.get("done"):
                break
            subprocess.run([sys.executable, str(_TICKER), "--once", str(rd)],
                           env=env, capture_output=True, timeout=60)
        return json.loads((rd / "harness_status.json").read_text())


class Idempotency(_Base):

    def test_replay_processed_id_is_a_noop(self):           # PIN
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        self._inbox(rd, "m1", "enqueue", {"entries": [_wrap_entry("b")]})
        TICK._drain_inbox(rd)
        self.assertEqual(len(self._entries(rd)), 2)
        self.assertEqual(self._ack(rd, "m1")["status"], "applied")
        # replay the SAME id -> idempotent no-op (no second dispatch)
        self._inbox(rd, "m1", "enqueue", {"entries": [_wrap_entry("b")]})
        TICK._drain_inbox(rd)
        self.assertEqual(len(self._entries(rd)), 2, "replay double-dispatched")

    def test_ledger_prevents_reapply(self):                 # PIN (crash-safety)
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        # simulate a crash AFTER the id was marked but BEFORE apply: pre-seed the
        # ledger, then drain -> the message must NOT apply (mark-first guarantee).
        self._inbox(rd, "crash1", "enqueue", {"entries": [_wrap_entry("z")]})
        (rd / "inbox" / ".processed").write_text("crash1\n", encoding="utf-8")
        TICK._drain_inbox(rd)
        self.assertEqual(len(self._entries(rd)), 1, "re-applied a marked id")
        self.assertTrue((rd / "inbox" / "processed" / "crash1.json").exists())


class UnderLockDrain(_Base):

    def test_drain_happens_through_the_locked_tick(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        self._inbox(rd, "u1", "enqueue", {"entries": [_wrap_entry("b")]})
        self._tick(rd)                          # tick.py main holds the .tick.lock
        self.assertEqual(len(self._entries(rd)), 2)
        self.assertEqual(self._ack(rd, "u1")["status"], "applied")


class AckResultLifecycle(_Base):

    def test_enqueue_acked_and_completes_with_result(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        self._inbox(rd, "e1", "enqueue",
                    {"entries": [_wrap_entry("b"), _wrap_entry("c")]})
        final = self._drive(rd)
        self.assertTrue(final["done"])
        ack = self._ack(rd, "e1")
        self.assertEqual(ack["status"], "applied")
        self.assertEqual(ack["task_ids"], ["b", "c"])
        result = json.loads((rd / "outbox" / "e1.result.json").read_text())
        self.assertTrue(result["completed"])
        self.assertEqual(result["task_ids"], ["b", "c"])     # id <-> task_id
        self.assertTrue(all(s == "completed"
                            for s in result["run_states"].values()))

    def test_every_verb_yields_a_well_formed_ack(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        self._inbox(rd, "v-enq", "enqueue", {"entries": [_wrap_entry("b")]})
        self._inbox(rd, "v-dj", "dispatch-job",
                    {"worker_prompt": "HEARTBEAT_PATH={HEARTBEAT_PATH} "
                     "TASK_ID={TASK_ID} RUN_DIR={RUN_DIR} TARGET_REPO={TARGET_REPO} "
                     "HARNESS_BIN={HARNESS_BIN} stub"})
        self._inbox(rd, "v-ctl", "control", {"op": "pause"})
        self._inbox(rd, "v-snap", "snapshot", {})
        self._inbox(rd, "v-note", "note", {"text": "hello from chat"})
        TICK._drain_inbox(rd)
        for mid in ("v-enq", "v-dj", "v-ctl", "v-snap", "v-note"):
            ack = self._ack(rd, mid)
            self.assertEqual(ack["status"], "applied", "%s: %s" % (mid, ack))
            self.assertEqual(ack["message_id"], mid)
        # control pause dropped the PAUSE control file (consumed by the tick)
        self.assertTrue((rd / "PAUSE").exists())
        # note went to the journal
        jr = (rd / "journal.ndjson").read_text()
        self.assertIn("hello from chat", jr)


class MalformedAndCheck(_Base):

    def test_unknown_verb_rejected_tick_continues(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        self._inbox(rd, "bad-verb", "frobnicate", {})
        TICK._drain_inbox(rd)                    # must not raise
        self.assertEqual(self._ack(rd, "bad-verb")["status"], "rejected")
        self.assertEqual(len(self._entries(rd)), 1)   # nothing landed
        # the tick still runs to completion afterwards
        self.assertTrue(self._drive(rd)["done"])

    def test_malformed_json_is_rejected(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        (rd / "inbox").mkdir(exist_ok=True)
        (rd / "inbox" / "junk.json").write_text("{ not json", encoding="utf-8")
        TICK._drain_inbox(rd)                    # must not raise
        self.assertEqual(self._ack(rd, "junk")["status"], "rejected")

    def test_check_rejects_bad_entry_before_landing(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        self._inbox(rd, "badent", "enqueue", {"entries": [
            {"task_id": "x", "target_repo": ".", "dispatch_mode": "shell",
             "adapter": "wrap", "command": "not-an-array"}]})
        TICK._drain_inbox(rd)
        self.assertEqual(self._ack(rd, "badent")["status"], "rejected")
        self.assertEqual(len(self._entries(rd)), 1)   # bad spec never landed


class ReadOnlySafety(_Base):

    def _fingerprint(self, p):
        st = p.stat()
        return (p.read_bytes(), st.st_mtime_ns, st.st_size)

    def test_readonly_verbs_never_write_run_state(self):    # PIN
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        self._inbox(rd, "ro-snap", "snapshot", {})
        self._inbox(rd, "ro-note", "note", {"text": "audit"})
        status_before = self._fingerprint(rd / "harness_status.json")
        plan_before = self._fingerprint(rd / "plan.json")
        TICK._drain_inbox(rd)
        # (bytes, mtime, size) -- a no-op rewrite (write-temp+rename) changes the
        # mtime even for identical content, so this bites ANY write, not just a
        # content change.
        self.assertEqual(self._fingerprint(rd / "harness_status.json"),
                         status_before, "read-only verb wrote harness_status.json")
        self.assertEqual(self._fingerprint(rd / "plan.json"), plan_before,
                         "read-only verb wrote plan.json")
        self.assertFalse((rd / ".tick.lock").exists())
        # but they DID produce the expected outbox/journal artifacts
        self.assertTrue((rd / "outbox" / "ro-snap.result.json").exists())
        self.assertIn("audit", (rd / "journal.ndjson").read_text())

    def test_no_network_listener_in_engine(self):
        # NFR-11: the message channel is local-disk only -- the engine opens no
        # socket / no server. Guard against a regression that adds one.
        src = _TICK.read_text(encoding="utf-8")
        for banned in ("socket.socket", "http.server", "socketserver",
                       "bind((", "listen("):
            self.assertNotIn(banned, src, "engine added a network listener")


class CliRoundTrip(_Base):

    def test_msg_send_check_gate_and_outbox(self):
        rd = self._init({"pool_size": 2, "entries": [_wrap_entry("a")]})
        # a malformed control is rejected BEFORE send (send-side --check)
        rc = CLI.main(["msg", str(rd), "control", "--op", "cadence"])
        self.assertEqual(rc, 1)
        self.assertFalse((rd / "inbox").exists()
                         and list((rd / "inbox").glob("*.json")))
        # a good enqueue from a file lands and acks applied
        f = self.runs / "jobs.json"
        f.write_text(json.dumps({"entries": [_wrap_entry("b")]}), encoding="utf-8")
        rc = CLI.main(["msg", str(rd), "enqueue", "--file", str(f)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(list((rd / "inbox").glob("*.json"))), 1)
        TICK._drain_inbox(rd)
        self.assertEqual(len(self._entries(rd)), 2)


if __name__ == "__main__":
    unittest.main()
