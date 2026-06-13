# Panelist A — Selection / Queue Correctness (instr 030, Iteration 12, FR-46..49)

Charter: adversarially review the FR-47 streaming-queue selection in
`bin/incontext.py` (`select_next_instruction` / `_nnn`) against `docs/REQUIREMENTS.md`
FR-47 and `tests/test_incontext.py`. Live verification only; no product/test source
edited (all mutation experiments ran in temp scratch copies).

## Environment / baseline
- `python3 -m pytest tests/test_incontext.py -v` → **12 passed** (Python 3.14.5).
- Working tree at start and end identical: `README.md`/`TOOLKIT.md`/`SKILL.md`
  modified, `bin/incontext.py` + `tests/test_incontext.py` untracked. md5 of both
  source files unchanged after all experiments.

## 1. Lowest-unprocessed pick (FR-47) — VERIFIED live
Crafted my own folders and called `select_next_instruction` directly:
- Lowest `NNN-` chosen; non-numeric files (`not-a-number.md`, `readme.md`,
  `-leading-dash.md`) ignored → idle (None) when only non-numeric present.
- Processed-skip is by **normalized NNN integer stem, not by name**: an output named
  `001-x.md` / `9-out.md` marks instruction `001-...` / `009-...` done even though the
  filenames differ. Confirmed `done` is keyed off `_nnn(output.name)` (lines 76–80).
- Idle (None) when all processed and when the queue is empty.
- Zero-padding normalized: `009 < 010` numerically; bare `9` output matches `009`
  instruction (`bare 9 output matches 009 instr → None`, i.e. 009 correctly counted done).
- Missing instructions dir → None (no crash); missing outputs dir → treated as empty
  `done` set, lowest instruction returned (no crash).

## 2. STOP halts mid-queue (read-only invariant) — VERIFIED + mutation BITES
- STOP present + unprocessed instruction → None (early return, lines 60–61). STOP
  removed → resumes and returns `002-b.md`. Matches FR-10/FR-47.
- `stop_file` given but the path does not exist → normal selection (not treated as halt).
- **Mutation pin `test_stop_halts_mid_queue` BITES** (scratch copy mirroring repo
  layout): removing the `if stop_file ... return None` guard → under STOP, `002-b.md`
  is picked → `AssertionError: '002-b.md' is not None` → test FAILS. Restored/discarded
  (scratch only).

## 3. Selection mutation pin — VERIFIED BITES
- **`test_selection_skips_processed` BITES**: replacing `if num not in done:` with an
  unconditional branch (treat every instruction as unprocessed) → processed `001` is
  re-picked instead of `002` → test FAILS. Reproduced in a fresh scratch copy.

## 4. Adversarial — deterministic, never crashes
All ran live without exception:
- **Ties (two files same NNN)**: `001-aaa.md` chosen over `001-zzz.md`; stable across
  repeated calls. `sorted(idir.iterdir())` + `setdefault` makes "first sorted wins"
  deterministic. OK.
- **Gaps (001, 003, no 002)**: with 001 done → returns `003-c.md`. OK.
- **Huge number** (50-digit prefix): Python big-int; `002-small.md` still chosen as
  lowest. No overflow. OK.
- **Outputs dir absent**: empty done set, lowest instruction returned. OK.
- **Instruction that is a directory** (`001-adir/`): `p.is_file()` skips it → `002-real.md`
  chosen. OK.
- **Instructions dir absent**: None. OK.

## Observations (non-blocking)
- **Asymmetry: an output that is a *directory* with an `NNN` prefix does NOT mark the
  instruction done** (outputs require `is_file`, line 77), so `001-a.md` is still picked
  even though `outs/001-asdir/` exists. This is the defensible reading of FR-47
  ("an output **file** … exists"), consistent with instructions also requiring `is_file`.
  Worth a one-line doc/test note someday, but it matches the spec wording and is not a
  defect. Not ship-blocking.
- `_nnn` matches any leading digit run, so a file like `12abc.md` (no dash) also gets
  stem 12. FR-47 says `NNN-...`; the looser match is harmless for selection (still
  deterministic, still numeric) and the test `not-a-number.md` confirms non-numeric is
  ignored. Acceptable.

## Conclusion
Selection is correct per FR-47, deterministic and crash-free across every adversarial
case I could construct, the lowest-unprocessed/NNN-stem/STOP-mid-queue semantics all hold
live, and both required mutation pins genuinely bite. No correctness defects found in the
selection/queue surface. The directory-output asymmetry is a documentation nicety, not a
bug. Working tree left exactly as found.

VERDICT: SHIP
