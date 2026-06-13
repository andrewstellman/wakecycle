"""FR-46..49 in-context worker mode -- the DETERMINISTIC core + honesty
(instr 030, Iteration 12).

The deterministic, unit-testable parts: the FR-47 streaming-queue selection
(lowest NNN- with no matching output; STOP halts, including mid-queue) and the
FR-49 monitoring-pause note (a pure function of disk-derived state). The
agent-behavior parts (doing the work, the FR-48 ERR rehydrate-on-resume
discipline) live in the SKILL and get dogfood/integration coverage, NOT unit
assertions -- stated honestly.

Plus the FALSIFIABLE honesty check (FR-50/C-7): the shipped in-context docs must
NOT imply Class-C recovery / auto-relaunch / unattended in-context reliability,
and the C-7 limitation MUST appear in README + TOOLKIT + SKILL + the in-context
FR docstring.

MUTATION-VERIFY EVIDENCE (DEVELOPMENT_PROCESS Mutation-test), instr 030:
  Pin 1: test_selection_skips_processed.
    Mutation: in select_next_instruction, ignore the `done` set (treat every
      instruction as unprocessed).
    Observed: a processed instruction (001, output present) is re-picked instead
      of 002 -> the test FAILs. Restored OK.
  Pin 2: test_stop_halts_mid_queue.
    Mutation: drop the `if stop_file ... return None` guard.
    Observed: with STOP present and 003 unprocessed, 003 is picked instead of
      None -> the test FAILs. Restored OK.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


IC = _load("incontext_fr46", "bin/incontext.py")


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.ins = self.tmp / "instructions"
        self.outs = self.tmp / "outputs"
        self.ins.mkdir(); self.outs.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _instr(self, *names):
        for n in names:
            (self.ins / n).write_text("x")

    def _out(self, name):
        (self.outs / name).write_text("done")

    def _next(self, stop_file=None):
        nxt = IC.select_next_instruction(self.ins, self.outs, stop_file=stop_file)
        return nxt.name if nxt is not None else None


class SelectionTests(_Base):

    def test_lowest_unprocessed_is_chosen(self):
        self._instr("002-b.md", "001-a.md", "003-c.md", "not-a-number.md")
        self.assertEqual(self._next(), "001-a.md")        # lowest, ignores non-numeric

    def test_selection_skips_processed(self):
        self._instr("001-a.md", "002-b.md", "003-c.md")
        self._out("001-a.md")                              # 001 processed
        self.assertEqual(self._next(), "002-b.md")
        self._out("002-DIFFERENT-NAME.md")                # match by NNN stem, not name
        self.assertEqual(self._next(), "003-c.md")

    def test_queue_idles_when_all_processed(self):
        self._instr("001-a.md", "002-b.md")
        self._out("001-x.md"); self._out("002-y.md")
        self.assertIsNone(self._next())

    def test_empty_queue_is_none(self):
        self.assertIsNone(self._next())

    def test_zero_padding_normalizes(self):
        self._instr("010-ten.md", "009-nine.md")
        self.assertEqual(self._next(), "009-nine.md")     # 9 < 10 numerically
        self._out("9-anything.md")                         # bare 9 matches 009
        self.assertEqual(self._next(), "010-ten.md")

    def test_stop_halts_mid_queue(self):
        # the negative case: STOP present + an unprocessed instruction -> None.
        self._instr("001-a.md", "002-b.md")
        self._out("001-a.md")                              # 002 still unprocessed
        stop = self.tmp / "STOP"; stop.write_text("")
        self.assertIsNone(self._next(stop_file=stop))      # halts, even mid-queue
        # ... and resumes once STOP is gone
        stop.unlink()
        self.assertEqual(self._next(stop_file=stop), "002-b.md")


class CliTests(_Base):
    """End-to-end via the CLI (no agent) -- the deterministic queue surface."""

    def test_next_cli_prints_lowest_unprocessed(self):
        import io
        from contextlib import redirect_stdout
        self._instr("001-a.md", "002-b.md")
        self._out("001-a.md")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = IC.main(["next", str(self.ins), str(self.outs)])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), str(self.ins / "002-b.md"))

    def test_next_cli_with_stop_prints_nothing(self):
        import io
        from contextlib import redirect_stdout
        self._instr("001-a.md")
        stop = self.tmp / "STOP"; stop.write_text("")
        out = io.StringIO()
        with redirect_stdout(out):
            IC.main(["next", str(self.ins), str(self.outs), "--stop-file", str(stop)])
        self.assertEqual(out.getvalue().strip(), "")     # halted -> no pick printed


class MonitoringPauseNoteTests(unittest.TestCase):

    def test_note_rendering_is_deterministic(self):
        note = IC.monitoring_pause_note(1000000000, 1000003600, 3)
        self.assertEqual(
            note,
            "monitoring paused 01:46-02:46 for in-context work; "
            "3 background change(s) since last poll")

    def test_zero_changes_still_renders_the_gap(self):
        note = IC.monitoring_pause_note(1000000000, 1000000300, 0)
        self.assertIn("monitoring paused", note)
        self.assertIn("0 background change(s)", note)


class HonestyTests(unittest.TestCase):
    """Falsifiable (FR-50/C-7): the in-context docs must not over-claim, and the
    C-7 limitation must appear in all four surfaces."""

    _DOCS = {
        "README.md": _ROOT / "README.md",
        "TOOLKIT.md": _ROOT / "TOOLKIT.md",
        "SKILL.md": _ROOT / "plugins/wakecycle/skills/wakecycle/SKILL.md",
        "incontext.py": _ROOT / "bin/incontext.py",
    }

    @staticmethod
    def _norm(path):
        return " ".join(path.read_text(encoding="utf-8").split()).lower()

    def test_c7_limitation_present_everywhere(self):
        for label, path in self._DOCS.items():
            text = self._norm(path)
            self.assertIn("does not fix class-c", text, "%s lacks C-7 disclaimer" % label)
            self.assertIn("not the unattended-reliability path", text,
                          "%s lacks the unattended-path disclaimer" % label)

    def test_no_class_c_overclaim(self):
        # phrasing that would only appear in an over-claim (the honest negations
        # use "does not fix" / "no auto-recovery" / "not auto-relaunched").
        forbidden = ("fixes class-c", "fixes the class-c", "auto-recovers",
                     "auto-relaunches", "automatically recovers",
                     "automatically relaunches", "unattended in-context",
                     "in-context unattended")
        for label, path in self._DOCS.items():
            text = self._norm(path)
            for bad in forbidden:
                self.assertNotIn(bad, text, "%s over-claims: %r" % (label, bad))


if __name__ == "__main__":
    unittest.main()
