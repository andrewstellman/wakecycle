"""13b honesty gate (FR-50 / FR-54 / NFR-12) — mechanical guards on the §9
evidence ledger and the cross-agent / unattended positioning.

These are STRUCTURAL doc-consistency checks (no engine behavior). They keep the
§9 evidence map honest — the cadence/Windows floor row and the FR-55 row stay
PENDING, and no VERIFIED row leans on dogfooding/always-on as its evidence — and
keep the README/SKILL/TOOLKIT lead framing on the cross-agent identity and the
unattended-reliability split that FR-54/FR-50 require. This is the test cited by
the §9 FR-50/FR-54 rows; it must bite if any of those guarantees regress.
"""
from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REQ = (_ROOT / "docs" / "REQUIREMENTS.md").read_text(encoding="utf-8")


def _section9_rows():
    """Parse the §9 table into (claim, evidence) pairs."""
    after = _REQ.split("## 9. Validation evidence map", 1)[1]
    rows = []
    for raw in after.splitlines():
        line = raw.strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 2 and cells[0] != "Claim":
            rows.append((cells[0], cells[1]))
    return rows


class Section9HonestyGate(unittest.TestCase):

    def setUp(self):
        self.rows = _section9_rows()
        self.assertTrue(self.rows, "no §9 rows parsed")

    def _evidence_for(self, claim_substr):
        hits = [ev for (claim, ev) in self.rows if claim_substr in claim]
        self.assertEqual(len(hits), 1,
                         "expected exactly one §9 row matching %r, got %d"
                         % (claim_substr, len(hits)))
        return hits[0]

    def test_floor_windows_row_stays_pending(self):
        ev = self._evidence_for("Windows floor")
        self.assertIn("PENDING", ev)
        self.assertNotIn("VERIFIED**", ev)   # not flipped to a VERIFIED status

    def test_fr55_row_stays_pending(self):
        ev = self._evidence_for("Continuation contract")
        self.assertIn("PENDING", ev)
        self.assertNotIn("VERIFIED**", ev)

    def test_no_verified_row_cites_dogfooding_or_alwayson(self):
        # the load-bearing honesty rule: a flip to VERIFIED rests on real
        # tests/evidence, never on dogfooding or always-on running.
        for claim, ev in self.rows:
            if "**VERIFIED**" in ev:   # the status marker, not prose "verified"
                low = ev.lower()
                self.assertNotIn("dogfood", low,
                                 "VERIFIED row cites dogfooding: %s" % claim)
                self.assertNotIn("always-on", low,
                                 "VERIFIED row cites always-on: %s" % claim)

    def test_built_rows_are_verified(self):
        # the rows 13b flips on green evidence — guard against silent regression
        for claim in ("(FR-34)", "(FR-35..39)", "(FR-40/41)", "(FR-42..44)",
                      "(FR-45)", "(FR-46..49", "(FR-51)", "(FR-50)", "(FR-54)",
                      "(FR-52, UC-10)", "(FR-53)"):
            self.assertIn("VERIFIED", self._evidence_for(claim),
                          "expected VERIFIED: %s" % claim)


class PositioningLeadFraming(unittest.TestCase):
    """FR-54/FR-50: the docs lead with the cross-agent identity and the
    unattended split. Light substring guards so the lead can't silently drift
    back to a single-vendor or agent-rung-reliability framing."""

    def _read(self, *parts):
        return _ROOT.joinpath(*parts).read_text(encoding="utf-8")

    def test_readme_leads_cross_agent_and_splits_roles(self):
        r = self._read("README.md")
        head = r[:2200]
        self.assertIn("any", head.lower())
        self.assertIn("agentic coding system", head)
        # the worker-vs-orchestrator role split is present
        self.assertIn("worker", r)
        self.assertIn("orchestrator", r)
        # builder honesty: DESIGNED any host / VERIFIED Claude Code only
        self.assertIn("Claude Code only", r)

    def test_skill_and_toolkit_lead_cross_agent(self):
        for path in (("plugins", "arunner", "skills", "arunner", "SKILL.md"),
                     ("TOOLKIT.md",)):
            t = self._read(*path)
            self.assertIn("agentic coding system", t,
                          "missing cross-agent lead: %s" % (path,))

    def test_unattended_steers_to_deterministic_rungs(self):
        # FR-50: README + TOOLKIT steer unattended -> deterministic rungs,
        # framing the agent rung as interactive + safety tick (not reliability).
        for path in (("README.md",), ("TOOLKIT.md",)):
            t = self._read(*path).lower()
            self.assertIn("unattended", t, "%s" % (path,))
            self.assertIn("safety tick", t, "%s" % (path,))


if __name__ == "__main__":
    unittest.main()
