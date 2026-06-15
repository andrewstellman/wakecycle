# Panelist C — honesty & regression (FR-57 / FR-58a / FR-58b)

Charter: FR-58b stays DESIGNED and is graded separately from FR-58a; §9 flips cite real tests; no
regression.

1. **FR-58b stays DESIGNED, graded separately.** §9 row (REQUIREMENTS.md) reads
   `PENDING (per-host agent-rung, DESIGNED, NFR-12) … graded separately from FR-58a`. The SKILL edit
   states verbatim: *"This is a per-host agent-rung behavior (DESIGNED, NFR-12) — it is NOT verified
   by any engine test, and a green FR-58a engine test does not satisfy it."* BOOTSTRAP echoes it. No
   conflation with 58a (the FR-50/FR-54 overclaim pattern the council flagged is avoided).
2. **§9 flips cite real, passing tests.** FR-57 cites `test_live_enqueue.py` (9→10 tests, PINs
   present); FR-58a cites `test_activity_cadence.py` (11 tests, 5 PINs). Both run green. `cmd_add` /
   `add` subparser and `_adapter_worker_cmd(entry, plan)` synthesis are real, not phantom.
3. **Honesty guard intact.** `test_positioning_honesty.py` 7 passed; the Windows-floor row stays
   PENDING; FR-55 row unchanged. FR-57/58a flipping to VERIFIED did not disturb them.
4. **No regression.** Full suite 310 passed (290 → +9 FR-57 +11 FR-58a), then 311 after the STOP
   regression test. The FR-56/UC-12 fixtures updated honestly (each adds an explicit
   `--keepalive-seconds 0.3` because the default is now 45s — no assertion loosened, no expected value
   silently changed).
5. **Diff scope clean.** Engine confined to `tick.py`/`heartbeat.py`/`cli.py`; docs to
   REQUIREMENTS/TRACEABILITY/SKILL/BOOTSTRAP; tests added/updated as described. No stray mutation.

VERDICT: SHIP
