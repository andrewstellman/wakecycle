# Panelist C — Honesty (adversarial) — instr 030, Iteration 12 (FR-46..49 in-context worker mode)

Role: independent adversarial reviewer, HONESTY charter. Reviewed the four in-context
surfaces in full, `docs/REQUIREMENTS.md` (FR-46/47/48/49/50, C-6, C-7, §8, UC-9),
`tests/test_incontext.py`, and `bin/incontext.py`. All checks run live against
`/Users/andrewstellman/Documents/wakecycle`. Full suite: **176 passed in 11.73s**.

## 1. Adversarial over-claim hunt (read the prose, not just the substring list)

Ran my own case-insensitive regex sweep for over-claim phrasings across all four surfaces
(`fix(es)? class-c`, `auto.?recover`, `auto.?relaunch`, `automatically (recover|relaunch)`,
`unattended.*in-context`, `in-context.*unattended`, `reliable.*in-context`, `immune`):

- Every hit is a **negation** of the over-claim, not a claim. README/TOOLKIT/SKILL/docstring
  all say "**does not fix Class-C**", "no auto-recovery", "not auto-relaunched".
- The one `immune` hit (README) is correctly attributed to the **deterministic ticker/cron
  rungs (2–4)** ("…use the deterministic ticker/cron rungs (2–4), which are immune to the
  agent-loop drop"), NOT to in-context mode. Honest.
- The two `deterministic` hits are correctly scoped: SKILL applies it to the *selection*
  (which-instruction-next), and `incontext.py` to the *core* queue-selection/note functions.
  Neither claims in-context **mode** is deterministically reliable or recoverable. The
  docstring's LIMITATION block (lines 20–27) explicitly negates auto-relaunch and unattended
  reliability. No subtle determinism/reliability over-claim slips past the substring test.

Verdict on (1): **no over-claim present** in any of the four surfaces. Prose read in full.

## 2. C-7 limitation present in all four surfaces + the honesty tests genuinely enforce it

Live-confirmed the normalized text of all four surfaces contains both required disclaimers
("does not fix class-c" AND "not the unattended-reliability path"). The `_norm` helper does
`" ".join(text.split()).lower()`, which collapses **all** whitespace including newlines — so
README's line-wrapped `**does not fix\nClass-C**` normalizes to `does not fix class-c**` and
the substring still matches (markdown `**` markers do not break it; verified by printing the
40-char context window in each file). Whitespace-normalization claim **holds**.

Falsification experiments I ran myself (tree restored via `shutil.copy2` from a pre-mutation
backup after each):
- **Inject an over-claim** ("…automatically recovers its queue and fixes class-c drops, the
  unattended in-context path." appended to README) → `test_no_class_c_overclaim` **FAILS**
  (`'fixes class-c' unexpectedly found`). The test catches it.
- **Delete the disclaimer** from SKILL.md (redact "does not fix" + "not the
  unattended-reliability path") → `test_c7_limitation_present_everywhere` **FAILS**
  (`SKILL.md lacks C-7 disclaimer`). The test catches it.

Both honesty tests are genuinely falsifiable, not coverage theater. They fail exactly when an
over-claim is added OR a disclaimer is removed. The `forbidden` list also covers
`auto-recovers`, `auto-relaunches`, `automatically recovers/relaunches`, `unattended
in-context`, `in-context unattended` — a reasonable adversarial spread.

## 3. Cross-axis coupling STATED, not implied

All four surfaces state it explicitly, not by inference: the in-context **tasks themselves**
need a live agent and ride on **rung-1** cadence, and the deterministic floor (ticker/cron)
rescues **only the background/harness portion, never the in-context tasks**. SKILL has a
dedicated "Cross-axis coupling — state it, don't imply otherwise" paragraph; the
`incontext.py` docstring repeats it; README/TOOLKIT both carry the "rescues only the
background/harness portion" clause. Matches FR-46/C-7/§8 and REQUIREMENTS §6.4 line 193.

## 4. ERR discipline (FR-48) honestly framed as agent behavior

SKILL documents externalize-recognize-rehydrate as a **discipline the agent MUST follow**
("Externalize progress to disk… recognize loss on resume… rehydrate from disk"), not as
engine logic. The `incontext.py` docstring and the test module docstring both state plainly
that the ERR/agent-behavior parts "live in the SKILL… and get dogfood/integration coverage,
NOT unit assertions." The iteration does not fake agent-loop behavior as a unit assertion —
it is honest that only the FR-47 selection and FR-49 note rendering are pure/unit-tested. The
mutation-verify evidence in the test docstring (Pin 1 selection-skips-processed, Pin 2
stop-halts-mid-queue) is real and matches the shipped tests.

## 5. Adversarial meta — is the iteration's own framing over-claiming determinism/reliability?

No. The module is titled "the DETERMINISTIC core" but scopes that precisely to the two pure
functions, and its load-bearing LIMITATION block (lines 20–27) is the strongest disclaimer of
the four surfaces. The README capability-ladder entry frames in-context as "the
interactive/convenient mode, backed by the safety tick" and steers unattended needs to rungs
2–4. The §8 out-of-scope line ("automatic recovery of the in-context queue from a Class-C
drop") is consistent. I found nothing in the docs+code+tests that over-claims reliability or
auto-recovery.

## Tree integrity

Left the working tree exactly as found: `README.md`, `TOOLKIT.md`,
`plugins/wakecycle/skills/wakecycle/SKILL.md` modified (+81 insertions); `bin/incontext.py`,
`tests/test_incontext.py` untracked. No source edited — every mutation was reverted from a
backup and re-verified (12/12 in-context tests pass post-restore; full suite 176 passed). I
edited no source under review.

VERDICT: SHIP
