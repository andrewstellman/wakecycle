# Panelist B — Engine-fit & Bounded Context (instr 030, Iteration 12, FR-46..49)

Role: independent code reviewer, 3-person self-Council. One change, one verdict.
Repo: `/Users/andrewstellman/Documents/wakecycle`. Suite: `python3 -m pytest`.

## Scope of the change (verified live)

`git status` + `git diff` confirm the iteration is:
- **New code:** `bin/incontext.py` (untracked) + `tests/test_incontext.py` (untracked).
- **Docs only:** `README.md`, `TOOLKIT.md`, `plugins/wakecycle/skills/wakecycle/SKILL.md` (diff is purely additive — `+81` lines, no deletions, no engine edits).

## Charter 1 — The engine needed no change it shouldn't have. CONFIRMED.

- `git status --porcelain bin/tick.py bin/ticker.py bin/heartbeat.py` → **empty**. The three engine
  files are byte-for-byte unchanged by this iteration. The only code addition is `bin/incontext.py`.
- **No coupling, either direction.** `bin/incontext.py` imports only stdlib (`re`, `sys`, `datetime`,
  `pathlib`, `typing`); it does NOT import `tick`/`ticker`/`heartbeat`/`jobs` (the only `tick`/`heartbeat`
  hits are in prose docstrings). Conversely, `grep incontext bin/{tick,ticker,heartbeat,jobs}.py` →
  **none**: the engine has zero dependency on the new module. In-context is a genuinely orthogonal,
  bolt-on deterministic helper, not a hook into the loop.
- **The "engine already tolerates the irregular spacing" claim (FR-46 / FR-8 / E2) is correct.** I read
  the suppress-stall logic in `tick.py`:
  - L780–783: `suppress_stall = last_wall is not None and (now - last_wall) > max(stall_secs,
    tick_interval*60) * _WALLCLOCK_JUMP_FACTOR` (`_WALLCLOCK_JUMP_FACTOR = 4`, L84). A tick gap ≫ cadence
    is treated as a sleep/hibernate.
  - L922: STALLED marking is gated `... and not suppress_stall`; L928–932 (re)marks `running` on the
    suppressed tick. A long in-context pause is observationally identical to a slept machine: inflated
    heartbeat ages are treated as suspect for exactly one tick, then re-evaluated.
  - This property is **pinned by a standing regression test**: `test_wall_clock_jump_suppresses_stall`
    (10h jump → stays `running`) with its control `test_normal_cadence_stale_heartbeat_still_stalls`
    (50-min normal gap → `stalled`). The tolerance is real and tested, not asserted.
  - **Disk-truth / idempotency / stall detection are NOT corrupted by a sparse cadence:** every
    `_advance` transition is mtime-/sentinel-/PID-driven off disk (terminal sentinel L879, dead-PID
    L891, grace L907, stall L922), each guarded for idempotency. A gap only delays observation; it never
    rewrites a transition that disk evidence wouldn't independently justify on the next tick.
  - **Verdict on Charter 1: in-context correctly required no engine change.**

## Charter 4 (adversarial, handled here because it qualifies Charter 1) — long-pause edge

I specifically probed the one transition the charter names: **launch-grace.** Transition #2 in `_advance`
(L899–913) — `state == "claimed" and not has_any` → AUTH_OR_LAUNCH_FAILED when `(now - claimed_at) >
grace_secs` — is **NOT** gated by `suppress_stall`. So a worker that is `claimed` but whose first
heartbeat has not yet landed at the start of a very long in-context pause CAN be marked
AUTH_OR_LAUNCH_FAILED on resume.

This is **not a defect introduced or newly exposed by FR-46**, and it does **not** falsify the
"no engine change" claim, for three independently sufficient reasons:
1. **Pre-existing, identical to the slept-machine case.** A machine that sleeps immediately after
   claiming and wakes past grace false-fails in exactly the same way today. In-context introduces no new
   failure mode — it inherits an already-shipped engine characteristic. Asking FR-46 to fix it would be
   gold-plating an unrelated pre-existing behavior.
2. **The spec already accounts for it as agent-side, not engine-side.** REQUIREMENTS.md FR-46 alt-path
   (L165b): "a single in-context task longer than ~4× the stall threshold looks like a slept machine to
   the engine → the orchestrator passes a 'busy, not asleep' hint." The resolution is deliberately an
   agent behavior (SKILL), consistent with "the engine needs no change."
3. **In practice the window is narrow and disk-recoverable:** the launch heartbeat (STARTING) is emitted
   at spawn, before any in-context burst the same single-threaded turn would begin; and even a false
   LAUNCH-FAIL is a visible, diagnosable disk state (FR-21b hint), not silent corruption.
   The stall path — the common long-pause case — IS correctly E2-suppressed.

I checked the other time-based paths under a long pause and found no corruption: terminal-sentinel reap
(L879) and dead-PID fail (L891) are disk-evidence transitions that are *more* correct after a gap, not
less; the `claimed_at is None` self-heal (L902–906) restarts the grace clock rather than failing
immediately. `_next_cadence` (L1079) is a pure read of run states and cannot be skewed by a gap.

## Charter 2 — Monitoring-pause note is a pure function of disk state (FR-49). CONFIRMED.

`monitoring_pause_note(paused_from_epoch, paused_to_epoch, background_changes)` (L92–101):
- **No wall-clock read inside.** Both timestamps are arguments (the two tick `last_tick_wall` stamps, a
  disk-derived count); `_hm` formats the *passed* epoch via `datetime.fromtimestamp(..., timezone.utc)`.
  Verified live: two calls 50 ms apart with identical args produce byte-identical output (deterministic
  across real time), exactly like FR-27 rendering.
- **Inputs are genuinely disk-derived:** two tick wall-clock stamps + an integer change count — no live
  `time()`/`now()`, no global state.
- **Rendering stable & ASCII-safe:** `"monitoring paused 01:46-02:46 for in-context work; 3 background
  change(s) since last poll"` round-trips through `ascii`+`strict`; `0` changes still renders the gap
  (`int(background_changes)` coerces). Stable and console-safe (NFR-7-compatible).

## Charter 3 — Bounded context / disk-truth (NFR-5). CONFIRMED.

- `select_next_instruction` (L47–85) is a **pure filesystem scan**: it reads `instructions_dir` /
  `outputs_dir` each call, holds no module/instance state between calls, and returns a `Path` or `None`.
  Verified live across successive calls — selection responds only to on-disk changes (drop an output →
  next pick advances; create `STOP` → `None`; remove `STOP` → resumes). Missing dirs → `None`, no crash.
- **STOP halts even mid-queue** (L60–61, read-only FR-10): with an unprocessed instruction present and
  `STOP` on disk, returns `None`; verified live.
- **NNN normalization is disk-keyed, not name-keyed:** match is by leading integer (`_nnn`, L40), so
  `9-anything.md` output satisfies `009-...` instruction; bare/zero-padded share a stem. Verified live.
- **ERR discipline is documented as AGENT behavior, not faked as engine logic.** FR-48's
  externalize-recognize-rehydrate lives in SKILL.md ("Disk is the ERR substrate"; re-read
  `harness_status.json` + folders on resume) and the `incontext.py` docstring explicitly states the
  agent-behavior parts "are not pure functions of disk state" and "get dogfood/integration coverage, not
  unit assertions." No hidden per-tick state is introduced; the disk-is-truth model is intact.

## Honesty surface (FR-50 / C-7) — spot-checked, consistent

The C-7 limitation ("does not fix Class-C", "not the unattended-reliability path", no auto-recovery of
the in-context queue, rung-1 only) appears in all four surfaces (README, TOOLKIT, SKILL,
`incontext.py` docstring), and `HonestyTests` pins both presence and the absence of over-claim phrasing.
The framing matches the engine reality I verified: the deterministic floor rescues only the
background/harness portion, never the in-context tasks.

## Test verification

Full suite run **twice**, both green: **176 passed** (11.79s, 12.31s). Live-verified the deterministic
core (selection purity, STOP mid-queue halt, NNN normalization, missing-dir safety, note determinism /
ASCII-safety) outside the test harness.

## Working tree

Left as found — read-only review. No product/test source edited; no files staged or committed in the
wakecycle repo. (`__pycache__` regenerated by pytest is incidental and untracked.)

## Assessment

In-context mode is a correctly-scoped, orthogonal, bolt-on deterministic helper. The engine was rightly
left untouched; the "long in-context gap == slept machine, already handled" claim is accurate and pinned
for the stall path. The one residual launch-grace edge under a very long pause is a pre-existing,
spec-acknowledged, agent-side concern — not a regression and not a reason to touch the engine. The
deterministic core is a pure disk scan with no hidden state; FR-49's note is a pure function of
disk-derived args; ERR is honestly placed in the SKILL, not faked as engine logic.

VERDICT: SHIP
