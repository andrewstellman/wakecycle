"""FR-56 — activity-pattern extraction for the wrap/tail adapters.

DISPLAY-ONLY: the matcher selects the ACTIVITY label from operator regexes; it
never touches doneness (the FR-18 producer/reader firewall). Covers the matcher
(most-recent match / staleness hint / placeholder / truncation / byte ceiling),
the `--check` validation (non-list / non-string / empty / >16 / uncompilable /
complexity screen + the success/failure retrofit), the both-adapter synthesis,
and the doneness-unaffected firewall.

MUTATION PINS (instr 038): `test_most_recent_match_wins` (the whole point — the
relevant line, not the noise), `test_staleness_age_hint` (a stale pin must look
stale), and `test_never_matching_pattern_does_not_affect_doneness` (the
display!=doneness firewall) are the load-bearing bits.
"""
from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = _load("tick_fr56", "arunner/engine/tick.py")
H = _load("hb_fr56", "arunner/engine/heartbeat.py")


def _m(*patterns):
    return H._ActivityMatcher([re.compile(p) for p in patterns])


class Matcher(unittest.TestCase):

    def test_most_recent_match_wins(self):          # PIN
        m = _m(r"Step \d+")
        m.feed(["noise", "Step 1", "noise", "Step 2 ok", "noise"], 10.0)
        self.assertEqual(m.label(10.0, 5.0), "Step 2 ok")

    def test_no_match_placeholder(self):
        m = _m("Step")
        m.feed(["chatter", "more chatter"], 10.0)
        self.assertEqual(m.label(10.0, 5.0), "(running...)")

    def test_no_patterns_returns_none(self):
        self.assertIsNone(H._ActivityMatcher([]).label(0.0, 5.0))

    def test_staleness_age_hint(self):              # PIN
        m = _m("Step")
        m.feed(["Step 7/12"], 100.0)
        self.assertEqual(m.label(100.0, 10.0), "Step 7/12")      # fresh: no hint
        stale = m.label(580.0, 10.0)                             # 480s later
        self.assertTrue(stale.startswith("Step 7/12 ("))
        self.assertIn("ago)", stale)

    def test_placeholder_and_hint_are_ascii(self):  # NFR-7 cp1252 safety
        m = _m("Step")
        m.feed(["Step 1"], 0.0)
        self.assertTrue(m.label(9999.0, 5.0).isascii())
        self.assertTrue(_m("never").label(0.0, 5.0).isascii())

    def test_line_truncated_before_matching(self):
        m = _m("END")
        m.feed(["x" * 10000 + "END"], 0.0)          # match is past the 4 KiB cap
        self.assertEqual(m.label(0.0, 5.0), "(running...)")

    def test_byte_ceiling_stops_and_retains_last(self):  # protects the tick loop
        m = _m("HIT")
        m.feed(["HIT early"], 0.0)
        flood = ["x" * 4096] * 70 + ["HIT late"]    # > 256 KiB before the late hit
        m.feed(flood, 0.0)
        self.assertTrue(m.label(0.0, 5.0).startswith("HIT early"))


class CheckValidation(unittest.TestCase):

    def _act(self, patterns):
        p = []
        T._check_activity_patterns("e", {"adapter_activity_patterns": patterns}, p)
        return p

    def test_non_list(self):
        self.assertTrue(any("must be an array" in x for x in self._act("nope")))

    def test_non_string_element(self):
        self.assertTrue(any("must be a string" in x for x in self._act(["ok", 5])))

    def test_empty_element_defeats_filter(self):
        self.assertTrue(any("empty pattern" in x for x in self._act([""])))

    def test_over_16_cap(self):
        self.assertTrue(any("at most 16" in x for x in self._act(["x"] * 17)))

    def test_uncompilable(self):
        self.assertTrue(any("not a valid regex" in x for x in self._act(["(oops"])))

    def test_complexity_screen_rejects_nested_quantifier(self):
        self.assertTrue(any("backtracking" in x for x in self._act(["(a+)+"])))

    def test_valid_patterns_pass(self):
        self.assertEqual(self._act([r"Step \d+", "BUILD (OK|DONE)"]), [])

    def test_retrofit_compiles_success_regex(self):
        probs = T._check_adapter_entry(
            "entries[0]", {"adapter": "tail", "log_path": "x",
                           "success_regex": "(a+)+"})
        self.assertTrue(any("success_regex" in x and "backtracking" in x
                            for x in probs))

    def test_retrofit_bad_failure_regex(self):
        probs = T._check_adapter_entry(
            "entries[0]", {"adapter": "tail", "log_path": "x",
                           "failure_regex": "(unclosed"})
        self.assertTrue(any("failure_regex" in x and "not a valid" in x
                            for x in probs))


class ComplexityScreenPortability(unittest.TestCase):
    """instr 039 regression: the ReDoS complexity screen must work on BOTH stdlib
    parser modules -- `re._parser` (3.11+) and `sre_parse` (<=3.10). An earlier
    version did `re._parser` unconditionally and swallowed the AttributeError on
    3.10 into a silent pass, disabling the screen on a supported Python. This
    forces the <=3.10 fallback path and confirms `(a+)+` is still detected."""

    def test_screen_detects_via_sre_parse_fallback(self):
        import re as _re
        import warnings
        had = hasattr(_re, "_parser")
        saved = getattr(_re, "_parser", None)
        try:
            if had:
                del _re._parser          # simulate Python <=3.10 (no re._parser)
            self.assertFalse(hasattr(_re, "_parser"))
            self.assertEqual(_re.compile("x").pattern, "x")  # re.compile survives
            with warnings.catch_warnings():
                # sre_parse is deprecated on 3.11+ but is the real module on <=3.10
                # (where the fallback actually runs); silence it when forcing the
                # path on a newer Python.
                warnings.simplefilter("ignore", DeprecationWarning)
                self.assertTrue(T._regex_complexity_problem("(a+)+"),
                                "the sre_parse fallback failed to detect (a+)+")
                self.assertIsNone(T._regex_complexity_problem(r"BUILD (OK|DONE)"),
                                  "the fallback false-rejected a safe pattern")
        finally:
            if had:
                _re._parser = saved      # restore for the rest of the suite
        self.assertEqual(hasattr(_re, "_parser"), had)

    def test_screen_works_on_the_native_parser(self):
        # whatever parser this Python resolves, (a+)+ is rejected, safe patterns pass
        self.assertTrue(T._regex_complexity_problem("(a*)*"))
        self.assertIsNone(T._regex_complexity_problem(r"\d{2,5}"))


class Synthesis(unittest.TestCase):

    def test_wrap_synthesizes_activity_regex_before_command(self):
        cmd = T._adapter_worker_cmd({"adapter": "wrap", "command": ["make"],
                                     "adapter_activity_patterns": [r"Step \d+", "BUILD"]})
        self.assertEqual(cmd.count("--activity-regex"), 2)
        self.assertLess(cmd.index("--activity-regex"), cmd.index("--"))

    def test_tail_synthesizes_activity_regex(self):
        cmd = T._adapter_worker_cmd({"adapter": "tail", "log_path": "x",
                                     "adapter_activity_patterns": ["P"]})
        self.assertIn("--activity-regex", cmd)


class DonenessFirewall(unittest.TestCase):

    def test_never_matching_pattern_does_not_affect_doneness(self):   # PIN
        log = Path(tempfile.mkdtemp()) / "j.log"
        log.write_text("compiling step 1\nBUILD OK\n", encoding="utf-8")
        m = _m("NEVER-MATCHES-ANYTHING")
        w = H._TailWatcher(log_file=str(log),
                           success_re=re.compile("BUILD OK"), activity=m)
        # doneness still fires via success_regex; activity shows nothing matched
        self.assertEqual(w.poll(now=100.0), "COMPLETED")
        self.assertEqual(m.label(100.0, 5.0), "(running...)")


class EndToEnd(unittest.TestCase):
    """Drive the REAL wrap/tail adapter subprocess (grace 0 -> a 1s keepalive)
    with the cross-platform simulator emitting relevant lines amid --noise, and
    assert the emitted ACTIVITY label is the RELEVANT line, never the noise."""

    def _hb_inprogress_labels(self, hb):
        import json
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
        import subprocess
        import sys
        d = Path(tempfile.mkdtemp())
        hb = d / "hb.ndjson"
        stub = _ROOT / "tests" / "integration" / "stub_worker.py"
        subprocess.run(
            [sys.executable, str(_ROOT / "arunner" / "engine" / "heartbeat.py"),
             "wrap", "--task-id", "w", "--heartbeat-path", str(hb),
             "--launch-grace-minutes", "0", "--keepalive-seconds", "0.3",
             "--activity-regex", r"step \d+",
             "--", sys.executable, str(stub), "--emit", "log", "--noise", "2", "--steps", "6", "--sleep", "0.5"],
            capture_output=True, timeout=40)
        labels = self._hb_inprogress_labels(hb)
        self.assertTrue(labels, "no IN_PROGRESS keepalive fired")
        self.assertTrue(any("step" in lb for lb in labels),
                        "activity label never showed a relevant line: %r" % labels)
        self.assertFalse(any("noise: chatter" in lb for lb in labels),
                         "activity label showed the noise: %r" % labels)

    def test_tail_activity_label_is_relevant_not_noise(self):
        import subprocess
        import sys
        d = Path(tempfile.mkdtemp())
        hb = d / "hb.ndjson"
        log = d / "job.log"
        stub = _ROOT / "tests" / "integration" / "stub_worker.py"
        subprocess.run(
            [sys.executable, str(_ROOT / "arunner" / "engine" / "heartbeat.py"),
             "tail", "--task-id", "t", "--heartbeat-path", str(hb),
             "--launch-grace-minutes", "0", "--keepalive-seconds", "0.3",
             "--log-file", str(log),
             "--success-regex", r"\[COMPLETED\]", "--activity-regex", r"step \d+",
             "--", sys.executable, str(stub), "--emit", "log",
             "--log-file", str(log), "--noise", "2", "--steps", "4",
             "--sleep", "0.4"],
            capture_output=True, timeout=40)
        labels = self._hb_inprogress_labels(hb)
        self.assertTrue(labels, "no IN_PROGRESS keepalive fired")
        self.assertTrue(any("step" in lb for lb in labels),
                        "activity label never showed a relevant line: %r" % labels)
        self.assertFalse(any("noise: chatter" in lb for lb in labels),
                         "activity label showed the noise: %r" % labels)


if __name__ == "__main__":
    unittest.main()
