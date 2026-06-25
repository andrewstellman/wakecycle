# Output for 006-continue-past-stall.md
**Status:** completed

FR-74 (continue-past-stall) + FR-73 (`OUT-AGE` output-activity) implemented
together on a short-lived branch off `main`, self-Council to **unanimous SHIP**,
two commits, **local only — not pushed/merged**. Suite **463 → 483 passed ×3**,
Python 3.14.6. stdlib-only engine preserved (NFR-3).

- Worktree: `~/Documents/arunner-fr74`, branch **`fr74-continue-past-stall`** off `main` (`9845735`).
- Commits: **`68a3cc3`** (FR-74 + FR-73 impl) + **`5695c9f`** (self-Council B-F1 fix + council artifacts).
- Prereq confirmed: `git branch --contains 366a5bd` → `main` (FR-72 merged).

## Step-0 — gen-007 calibration finding (concern #2, decided the design)

From the frozen run dirs (`harness_runs/20260622T193939Z/` — the 18-tick
`HALT:stalled`; `claimed/` held `job-00011`=defu + `job-00015`=goshs; `queue/`=43)
and the target-repo mtimes in `~/Documents/QPB/repos/secbench2_widenet/`
(TZ = EDT, −0400). HALT was tick 18 @ **2026-06-23T00:53:03Z**.

| worker | claimed | last heartbeat | last OUTPUT write | at HALT | verdict |
|---|---|---|---|---|---|
| **defu** (job-00011, run-11) | 22:28:06Z | **22:42:05Z** (phase 2 keepalive) | **~23:00Z** (19:00 EDT) then silent | HB-AGE ~2h11m, **OUT-AGE ~1h53m** | **genuinely hung** → reclaim is right |
| **goshs** (job-00015, run-15) | 23:54:03Z | **00:04:47Z** (phase 1 keepalive) | kept WRITING **after** HALT (BUG writeups 22:37 EDT=02:37Z; log dir `20260623T014440Z`) | HB-AGE only **~48m** | **alive-but-quiet false-stall** → a time-only reclaim would abandon a live worker |

**Conclusion (recorded):** the gen-007 HALT was the exact pool-2 wedge the spec
predicted (2 stalled slots, 43 queued, `pool − inflight == 0` → `HALT:stalled`),
and it was a **mixed** case — one genuinely hung (defu, output stale ~1h53m) and
one alive-but-quiet (goshs, still writing). **So the output-freshness guard is
load-bearing, not optional** — a time-only reclaim with a low threshold would
have abandoned goshs. This is why FR-74 + FR-73 ship together.

**Calibration → defaults:** `stall_reclaim_minutes` = **90** (2× the 45m stall
threshold; ≫ stall, ≪ FR-72's 720-min cap). Output-freshness window = the stall
threshold (45m), reusing the engine's existing "gone quiet" horizon — source-
controller's ~34m read-gap (gen-007, first run) sits under it → protected. With
these: defu crosses 90m heartbeat-silence at ~00:12Z with stale output →
reclaimed → **one freed slot drains the 43-queue before the 00:53 HALT**; goshs
at 48m isn't reclaim-eligible and, being output-fresh, is held even past 90m.

## Before / after

**Before (`main`):** a heartbeat-quiet worker past `stall_threshold` is `stalled`
— non-terminal, non-killable, yet counted as inflight (`_INFLIGHT_STATES`). Once
both pool-2 slots are stalled, `pool − inflight == 0`, no queued job dispatches,
and `_halt_reason` returns `"stalled"` → **`HALT:stalled`**. One hung worker per
slot wedges an entire unattended batch (gen-007: 43 jobs stranded).

**After:** a run heartbeat-silent past `stall_reclaim_minutes` **AND** whose
newest OUTPUT write is older than `stall_threshold_minutes` (the FR-73 OUT-AGE
data signal) transitions to terminal **`abandoned`** (the idempotent
`_synthesize_failure` path CANCEL uses) → leaves `_INFLIGHT_STATES` → a slot
opens → `_dispatch` sends a queued job → the run **CONTINUEs**. A stalled-but-
output-FRESH worker is **never** reclaimed; an unmeasurable output area HOLDS
(the FR-72 720-min hard cap remains the backstop). `HALT:stalled` is reserved for
the genuinely-unrecoverable wedge.

## Files created / changed
| Path | Lines | Note |
|---|---|---|
| `arunner/engine/tick.py` | +~300 | FR-73 data layer (`_output_root`/`_output_globs`/`_newest_output_mtime`/`_output_age_secs`/`_out_age_str`, bounded + memoized); `_reclaim_stalled`; FR-74 reclaim in `_advance` (single-prompt) + `_advance_multistep`; `OUT-AGE` column in `_format_table` (in-flight-gated, B-F1); `check_plan` ordering (`stall<reclaim<cap`) + `stall_retries`/`output_globs` validation; new defaults/consts; docstring state-diagram edge |
| `schemas/plan.schema.json` (+ plugins copy, **identical**) | +~12 | `stall_reclaim_minutes` (default 90), `stall_retries` (≥0, default 0), `output_globs` (plan + per-job, all modes) |
| `docs/REQUIREMENTS.md` | +~30 | FR-73 + FR-74 (§5), US-19/US-20 (§3), UC-15/UC-16 (§4), two VERIFIED §9 rows; FR-40/FR-72 lineage; OUT-AGE display-only invariant |
| `references/STATE_MACHINE.md` (+ plugins copy, **identical**) | +~20 | `abandoned` state row; the `stalled → abandoned` reclaim edge + diagram; reversibility-below-threshold note |
| `TOOLKIT.md` | +~12 | `stall_reclaim_minutes`/`output_globs` knob rows; the HB-AGE-vs-OUT-AGE operator reading note |
| `tests/test_run_robustness.py` | +~430 (new) | 20 tests (the two load-bearing pins mutation-verified + supporting) |
| `runner/reviews/006_self_council/{A,B,C,SYNTHESIS}.md` | new | the 3-panel self-Council (committed) |

## Commits made
- **`68a3cc3`** — *FR-74 continue-past-stall + FR-73 OUT-AGE output-activity (instr 006)* — engine + schemas (both) + docs + tests.
- **`5695c9f`** — *FR-73: gate OUT-AGE scan to in-flight runs (self-Council B-F1) + 006 council artifacts*.

Both on `fr74-continue-past-stall`, **local only — not pushed, not merged** (operator lands + deletes the branch).

## Acceptance criteria — pass/fail per item
- Pool-saturating stall DRAINS instead of HALTing (reclaim → dispatch) — **PASS** (`test_pool2_two_stalled_with_queue_drains_not_halt`, mutation-verified).
- Stalled-but-output-FRESH worker NOT reclaimed (quiet-but-working guard) — **PASS** (`test_stalled_but_output_fresh_is_NOT_reclaimed`, mutation-verified).
- Reclaim → terminal `abandoned`, frees slot, queue dispatches — **PASS**.
- Reclaimed worker's late terminal does NOT resurrect / double-count / double-dispatch — **PASS**.
- `HALT:stalled` still reachable for the genuine wedge (reclaim disabled / output-fresh-pinned) — **PASS**.
- `OUT-AGE` data layer bounded + stdlib + memoized; column in the one renderer (engine/monitor/tui) — **PASS**.
- `OUT-AGE` display-only — never a lifecycle input for doneness — **PASS** (`test_out_age_is_display_only_not_lifecycle`).
- `--check` enforces `stall_threshold < stall_reclaim < subagent_hard_cap`; `stall_retries ≥ 0`; `output_globs` string-list — **PASS**.
- Shell-mode parity (FR-74 applies; FR-72 launch path unchanged) — **PASS**.
- Schemas + STATE_MACHINE copies identical — **PASS** (`diff` clean).
- FR-73/FR-74 + US-19/20 + UC-15/16 added, no number reuse — **PASS**.

## Council (required) — 3-panel self-Council: **UNANIMOUS SHIP**
`runner/reviews/006_self_council/SYNTHESIS.md` (+ A/B/C).
- **A (state-machine/correctness):** SHIP — transition correct + idempotent; output-fresh guard holds; reversibility intact; comeback reconciles. Ratified tradeoff: 45m output-freshness window (gen-007-calibrated).
- **B (regression-safety):** SHIP after **B-F1** — `_format_table` was scanning OUT-AGE for every run; gated to in-flight runs (per-render cost ~pool_size, "no full recursive walk per render" holds at scale). HALT-still-reachable / shell parity / FR-72-unchanged / display-only / stdlib-bounded all OK.
- **C (tests/honesty):** SHIP — both pins bite; no number reuse; §9 honest; the calibration finding recorded (this file). Ratified: `stall_retries` is a validated-but-reserved **FR-75** knob (FR-74 abandons — the signals-free engine can't safely requeue a subagent without a heartbeat-collision); the requeue-vs-abandon default the instruction delegated to the Council.
- **Process:** committed BEFORE the Council (`68a3cc3`) — and re-confirmed the lesson the hard way (a mutation-test `git checkout` reverted uncommitted engine work; restored from a side backup, then re-ran the bites against the committed tree).

## Tests
Baseline **463** (`main`) → **483 passed**, run **×3** identical on the final tree
(post-B-F1), `python3 -m pytest tests/ -q`, Python **3.14.6**, `__pycache__`
purged before each post-restore re-verify. New file `tests/test_run_robustness.py`
(20). Two mutation-verified PINs (drain + output-fresh guard); both bite-executed
in-tree and restored to green.

## §9 rows flipped
Two new **VERIFIED** rows added (no existing row changed):
1. FR-73 / US-20 / UC-16 — `OUT-AGE` output-activity (bounded data layer + column; display-only invariant pinned).
2. FR-74 / US-19 / UC-15 — continue-past-stall (drain-not-HALT + output-fresh guard + idempotent comeback + HALT-reserved), both load-bearing pins mutation-verified.

## STATE_MACHINE delta
Added the terminal `abandoned` state row and the one-way `stalled → abandoned`
reclaim edge (`hb-silent > stall_reclaim AND OUTPUT stale (OUT-AGE)`), with the
note that `stalled ↔ running` reversibility holds **below** the reclaim threshold
and an output-fresh stall is held, never reclaimed. Synced to the plugins copy
(identical).

## Notable observations
- **Reclaim reuses the CANCEL synthesis path** (`_synthesize_failure → abandoned`)
  rather than inventing a new terminal, so comeback-idempotency is inherited: a
  terminal run is skipped by `_advance`, `_dispatch` only dispatches `queued`, and
  the display reconciler only overlays a heartbeat-terminal onto an *inflight*
  state — a late `COMPLETED` cannot resurrect an `abandoned` run.
- **Multistep parity** included (a stalled pipeline step reclaims the run), even
  though gen-007 was single-prompt — a stalled step pins a slot identically.
- **`stall_retries` shipped as a forward-declared FR-75 knob** (validated, default
  0, FR-74 always abandons). Honest rationale documented in schema + REQUIREMENTS
  + commit: the signals-free engine cannot kill a stuck worker, and a subagent
  reclaim is an accounting free not a kill, so safe requeue (resume-not-restart +
  heartbeat isolation) belongs to FR-75.
- **`cp` alias hazard:** the environment aliases `cp` to interactive `-i`; use
  `/bin/cp -f` for non-interactive copies (hit twice this tick).

## Next action expected from orchestrator
Independent verification, then operator merges `fr74-continue-past-stall` →
`main` and deletes the branch + worktree. **HARD CHECKPOINT (PLANNED §8):** prove a
pool-saturating stall drains instead of HALTing before filing the next instruction
(007 = version source-of-truth → FR-76 → FR-75 → FR-77). No follow-ups filed beyond
that; `stall_retries` is the seam FR-75 will consume.
