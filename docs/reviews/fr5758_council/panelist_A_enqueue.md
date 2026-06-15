# Panelist A — SOUNDNESS & ENGINE-FIT of FR-57 (live enqueue)

**Charter:** adversarial review of FR-57's soundness and fit against the actual tick engine.
**Scope read:** `docs/REQUIREMENTS.md` (FR-57 @ L364; FR-2 @ L228; FR-6/NFR-6 @ L234/L377; FR-7 @ L235; FR-11 @ L239; FR-12 @ L240; FR-13 @ L241; FR-21a @ L256; FR-42 @ L1389-area; FR-46/47 @ L312-313); `arunner/engine/tick.py` (`init_run` L203-255; `tick` L880-989; `_dispatch` L1120-1193; `_TickLock` L1322-1359; `main` L1663-1694).

## VERDICT: REVISE-REQUIRED

FR-57's *intent* (a first-class `add` verb, staggered claim under the existing pool, idempotent, distinct from FR-47) is sound and well-motivated. But the spec is written against a mental model of the engine that does not match the code. As written, a naive implementation that "appends entries to the queue and resets `done`" will either silently drop the new entries or corrupt the positional `run-NN`↔`entries` mapping, and it omits the concurrency protocol entirely. The FR text needs to specify the append protocol at the level of detail the engine actually requires before this is buildable. Findings below, then the safe-append protocol I would require in the FR.

---

## Finding 1 — CRITICAL: the dispatch unit is `plan.json["entries"]`, NOT the `queue/` directory. FR-57's "append to the queue" is the wrong mutation target.

This is the load-bearing finding and it invalidates the FR's one-sentence implementation sketch.

The spec says (L364): *"appends the entries to the queue, resets `done` to false."* That describes writing files into `queue/`. But the engine never enumerates `queue/` to decide what to dispatch. Every tick rebuilds the entry table **positionally from `plan.json`**:

- `tick.py:894-895` — `entries = {"run-%02d" % i: e for i, e in enumerate(plan.get("entries") or [], start=1)}`. The source of truth for *what jobs exist* is `plan.json["entries"]`, indexed by position.
- `tick.py:1138` — `_dispatch` does `entry = entries[name]`, looking the entry up by the positional `run-NN` key.
- The per-run lifecycle state lives in `harness_status.json["runs"]`, also keyed `run-NN` (`init_run:238`, `tick.py:883`).
- The `queue/job-NNNNN.json` files are NOT a work list the tick scans — they are *claim tokens*. `_dispatch` consumes one by renaming `queue/<job>.json` → `claimed/<job>.json` (`tick.py:1148-1151`), and it only does so for a run that is already present in `status["runs"]` with `state == "queued"` (`tick.py:1132-1135`).

Consequence: dropping new `queue/*.json` files (the literal reading of "append to the queue") accomplishes **nothing** — the tick never iterates that directory to discover work. For the engine to dispatch an added job, FR-57 must append to **three** coupled structures atomically:

1. `plan.json["entries"]` — append the new entry objects (this is what the tick reads).
2. `harness_status.json["runs"]` — add a `run-NN` record with `state: "queued"`, `claimed_at: null`, `last_hb_status: null`, plus `task_id`/`job_id`/`target_repo` (mirror `init_run:238-245`).
3. The run-dir scaffold per new entry — `run-NN/` dir, `run-NN/heartbeat.ndjson` (touched), `run-NN/manifest.json`, and the `queue/job-NNNNN.json` claim token (mirror `init_run:220-237`).

The FR currently specifies none of this. **REVISE-REQUIRED:** rewrite the mechanism sentence to name `plan.json["entries"]` as the primary append target and enumerate all three structures. "Append to the queue" is a category error against this engine.

## Finding 2 — CRITICAL: positional `run-NN` / `job-NNNNN` assignment is fragile and under-specified; collision and renumbering are live hazards.

FR-57 says (L364) *"assigns each a fresh `task_id`/`run-NN`."* `task_id` is a UUID (FR-2), so freshness there is trivially satisfiable. `run-NN` is the problem.

`run-NN` and `job-NNNNN` are **positional, contiguous, 1-based** (`init_run:217-219`: `enumerate(..., start=1)`, `"run-%02d" % i`, `"job-%05d" % i`). The tick *re-derives* this mapping every tick from `plan.json` order (`tick.py:894-895`). This creates two distinct hazards the FR must close:

- **Index-base collision.** If `add` numbers new runs by counting current `entries` length, that is correct ONLY if no entry has ever been removed and the existing `run-NN` keys are exactly `run-01..run-0N` contiguous. The new run number MUST be `len(plan["entries"]) + 1 ... + k` computed from the **plan array length**, appended in array order — never from the count of *live* (non-terminal) runs, and never from a max over `status["runs"]` keys that could diverge from plan length. The FR must pin "append in `plan.json` array order; new index = prior array length + 1" so the positional zip at `tick.py:894-895` stays consistent. A mismatch between `len(plan["entries"])` and the `run-NN` keys in `status["runs"]` is silently corrupting: `_dispatch`'s `entries[name]` (`tick.py:1138`) would `KeyError` (caught and swallowed by the `except Exception` at `tick.py:934`, so the failure is *invisible* — the added job just never dispatches and the tick logs a generic TICK ERROR).
- **No renumbering, ever.** Because `done`/state are keyed on `run-NN` (`status["runs"]`), `add` must be strictly append-only on the index space. It must never re-pack or renumber existing runs to fill gaps, or it will reassign a `run-NN` that already has a claimed/results sentinel and heartbeat history. FR-57 should state append-only-numbering explicitly.

Heartbeat/results/claimed dirs: `init_run` makes `run-NN/heartbeat.ndjson` per entry but `claimed/` and `results/` are **shared** dirs with per-`job-NNNNN` files (`init_run:214`, `_move_to_results:417`). So `add` needs only to (a) create the new `run-NN/` + heartbeat + manifest, and (b) write the `queue/job-NNNNN.json` token; `claimed/`/`results/` need no new subdirs, only that the new `job-NNNNN` id not collide with an existing result/claim sentinel — which the append-only numbering rule guarantees. **REVISE-REQUIRED:** FR-57 must specify append-only positional numbering from plan-array length and explicitly forbid renumbering.

## Finding 3 — CRITICAL: no concurrency protocol. `add` races the tick; it MUST take the same `.tick.lock` (FR-12).

FR-57 is completely silent on concurrency, and the engine's whole safety model is "one writer per state file, serialized by the per-run-dir lock" (FR-12; C-1). A live run is, by definition, being ticked. `add` mutates `plan.json` AND `harness_status.json` AND `queue/` — exactly the files a concurrent tick reads at `tick.py:881-882` and rewrites at `tick.py:967`. The races:

- **Lost update on `harness_status.json`.** Tick reads status (`L881`), `add` reads-modifies-writes status, tick writes status (`L967`) — `add`'s new `run-NN` record is clobbered. Or the reverse: `add` overwrites the tick's freshly-advanced states (a just-reaped `completed` reverts to in-flight). Both are silent corruption.
- **Torn read of `plan.json` vs `status.runs`.** A tick that reads `plan.json` *after* `add` appended the entry but `harness_status.json` *before* `add` added the `run` record gets a positional mismatch → the `entries[name]` `KeyError` path of Finding 2, OR a `runs` entry with no matching plan entry (the zip at `L894` only produces keys present in `plan`, so an orphan `runs` key would simply never dispatch and never count toward `done` correctly).
- **`done` reset racing the SUMMARY write.** If `add` flips `done`→false concurrently with a tick that is taking the done-transition and writing `SUMMARY.md` (`tick.py:948-951`), the capstone guard (`was_done`/SUMMARY-exists) can be left in an inconsistent state.

The required protocol: **`add` MUST acquire the same `.tick.lock` the tick uses** (`tick.py:1329`, `run_dir/.tick.lock`, `fcntl`/`msvcrt` non-blocking, FR-12) before reading or mutating any run-dir state, and hold it across the whole read-modify-write. Note the lock is currently *non-blocking* and the caller skips on contention (`main:1687-1690`); `add` should **block** (or retry-with-backoff) on that lock rather than skip, because skipping would silently no-op the operator's `add`. This is a spec-level decision FR-57 must make.

A cleaner, race-免 alternative worth surfacing in the FR (see Finding 6): a **staging-area absorb** model where `add` only writes an `incoming/` drop file (atomic single-file write, no lock needed) and the *next tick* — already under the lock — absorbs it into `plan.json`/`status`. That trades immediacy for never touching live state outside the lock. **REVISE-REQUIRED:** FR-57 must specify one of {lock-and-mutate, stage-and-absorb}; it currently specifies neither.

## Finding 4 — `done`-reset re-activation is *probably* fine, but only because `done` is recomputed every tick — and the FR overstates `add`'s role.

FR-57 says `add` "resets `done` to false." Good news for soundness: `add` does **not actually need to write `done` at all**. The tick recomputes `done` unconditionally from run states every tick: `tick.py:941` — `status["done"] = all(r["state"] in _TERMINAL_STATES for r in runs.values())`. The moment a new `run-NN` with `state:"queued"` exists in `status["runs"]`, the next tick's `all(...)` is false and the run re-activates correctly, including back-filling the freed pool slots (`_dispatch`, `tick.py:933`). So an idle (all-terminal, `done:true`) run *does* re-activate correctly when a queued entry appears — **this part of FR-57 is sound.**

Two caveats the FR should note:
- Writing `done:false` in `add` is at best belt-and-suspenders and at worst a race surface (Finding 3). The real activator is the new `queued` run record. FR-57 should say "the added `queued` entry causes the next tick to recompute `done:false`" rather than implying `add` owns the `done` field.
- **SUMMARY staleness.** A prior done-transition already wrote `SUMMARY.md`/`summary.json` (`tick.py:948-951`, FR-45). After `add`, the run re-runs and reaches `done` again. The transition guard (`L948`) only rewrites SUMMARY if it's a *fresh* transition (`not was_done`) — and after `add` reset `done`, `was_done` will be false on the re-completion tick, so SUMMARY **is** rewritten. That's the desired outcome, but it depends on `done` actually having been false at re-completion. This is a second reason `add` (or the absorbing tick) must ensure `done` is false once the new entry lands — and a reason the stage-and-absorb model is cleaner (the absorbing tick sets `done:false` under the lock as a natural consequence of adding the `queued` run). FR-57 should call out that SUMMARY is correctly regenerated on the second done-transition.

## Finding 5 — DISTINCTNESS from FR-47 is correct and the spec draws the line well. (No revision needed here.)

FR-57 (L364) and FR-47 (L313) are genuinely different surfaces, and the FR's "Distinct from FR-47" paragraph is accurate:

- **FR-47** (streaming instruction queue) feeds the *in-context worker* — `NNN-`prefixed instruction files the agent processes *itself in its own context*, output-matched by stem. It is rung-1-only (FR-46/C-7), no pool, no `run-NN`, no heartbeat lifecycle. It is about the agent doing work.
- **FR-57** (live enqueue) feeds the *harness pool* — subagent/shell entries the engine claims, dispatches, and reaps via the `run-NN`/`queue`/`claimed`/`results` state machine. It is about *workers* doing work, at any cadence rung.

Different folders (`instruction_folder` vs the run-dir's `queue`/`plan`), different lifecycle (output-stem match vs heartbeat terminal sentinel), different concurrency model. The spec's framing — "Both are live-submission surfaces; this is the one for the worker pool" — is the right boundary. **SHIP on distinctness.** One small ask: the FR could note they can *co-exist* in one in-context session (FR-46's superset retains harness features), so `add` and the instruction folder are both live in a rung-1 in-context run — that's a feature, not a conflict.

## Finding 6 — Validation: "validated exactly as `--init` entries (FR-2/FR-42)" is the right intent but needs a concrete `--check`-before-land gate.

FR-57 says appended entries are "validated exactly as `--init` entries (FR-2/FR-42)." Sound in principle. Specifics the FR should pin:

- **`--check` must run on the add-input BEFORE it lands**, not after. The whole point of FR-42 (`tick.py:1389+`, `check_plan`) is to catch config errors *before* launch spend. An `add` that appends an invalid entry to live `plan.json` and only then validates has already corrupted the live plan and may have triggered a tick that hit the swallowed-`KeyError`/`AUTH_OR_LAUNCH_FAILED` path. The protocol must be: validate the add-input as a standalone mini-plan (`--check`), and only on clean validation perform the atomic append. This composes naturally with the stage-and-absorb model (validate the staged file before the absorbing tick consumes it) — another argument for Finding 3's staging alternative.
- **FR-21a path substitution.** FR-57 (L364) says paths are substituted mechanically (FR-21a). Confirmed sound: `_dispatch` resolves placeholders at dispatch time (`tick.py:1141-1147`, `_resolve_template`), and it does so from the entry + run-dir — *not* from anything `add` needs to pre-bake. So `add` must store the entry with its `{HEARTBEAT_PATH}`/`{TASK_ID}`/etc placeholders **unresolved**, exactly as `--init` stores them (`init_run:237` stores the raw `entry`), and let the dispatch-time substitution at `tick.py:1141` handle it. FR-57 should state that `add` stores placeholder-bearing entries verbatim and does NOT pre-resolve paths (pre-resolving would re-introduce the FR-21a transcription hazard for any field `add` touched).
- **`task_id` validation.** FR-2 requires a UUID `task_id`. If the add-input omits it (the FR-43 shorthand path may), `add` must mint one — and `--check`/FR-2 conformance must be enforced on the minted result.

**REVISE-REQUIRED (minor):** make the validation a hard pre-land gate and state the placeholders-stay-unresolved rule explicitly.

---

## The safe-append protocol I would require FR-57 to specify

Whichever of the two models is chosen, these invariants are non-negotiable for engine-fit:

**Model A — lock-and-mutate (immediate):**
1. Acquire `run_dir/.tick.lock` **blocking** (same lock as FR-12 / `tick.py:1329`); do not use the non-blocking skip path — block or retry so the operator's `add` is never silently dropped.
2. Read `plan.json` + `harness_status.json` under the lock.
3. Validate each new entry as a `--init` entry via FR-42 `check_plan` (FR-2 conformance, placeholder/dispatch-mode checks). Abort the whole `add` on any problem — never partial-append.
4. Compute new indices append-only from `len(plan["entries"])`; `run-NN`/`job-NNNNN` = next contiguous numbers. Never renumber existing runs.
5. For each new entry: append to `plan["entries"]` (preserve array order); add a `queued` `run-NN` record to `status["runs"]` mirroring `init_run:238-245`; create `run-NN/`, touch `heartbeat.ndjson`, write `manifest.json`, write `queue/job-NNNNN.json` token (mirror `init_run:220-237`); mint `task_id` UUID if absent.
6. Recompute `status["counts"]` (`_recount`), set `status["done"]` = false implicitly (it will be, since a queued run exists — or write it explicitly for clarity).
7. Atomic write-temp-rename both `plan.json` and `harness_status.json` (the engine's `_write_json` convention), then release the lock.
8. The next tick (already lock-serialized) dispatches the new `queued` runs into free pool slots, staggered (FR-7), idempotently (FR-6).

**Model B — stage-and-absorb (race-免, preferred):**
1. `add` validates the input (`check_plan`) and writes a single atomic file to a staging dir (e.g. `incoming/<ts>.json`) — **no lock, no live-state mutation.**
2. The next tick, **at the top of its locked section**, absorbs any `incoming/*.json`: re-validates, performs steps 4-6 above under the lock it already holds, deletes the absorbed staging file, then proceeds with the normal advance/dispatch. `done:false` falls out naturally.
3. This keeps ALL live-state mutation inside the existing tick lock, requires no new locking discipline in `add`, and makes `--check`-before-land trivial (the staged file is validated twice — at `add` and at absorb). The cost is one tick of latency, which is acceptable for "grow a running batch."

I recommend the FR adopt **Model B** as the normative protocol and mention Model A only as a rejected alternative — it confines all the Finding 3 races to zero by construction and aligns with the engine's existing "next tick absorbs whatever changed" idiom (the same property FR-46/FR-8 already lean on).

## Summary of required revisions

- **F1 (CRITICAL):** "append to the queue" is wrong; the append target is `plan.json["entries"]` + `status["runs"]` + run-dir scaffold. Rewrite the mechanism.
- **F2 (CRITICAL):** specify append-only positional `run-NN`/`job-NNNNN` from plan-array length; forbid renumbering.
- **F3 (CRITICAL):** specify a concurrency protocol (lock-and-mutate, or stage-and-absorb); the FR currently has none.
- **F4 (clarify):** `done` re-activation is sound; reframe so the queued run — not an `add`-written `done` field — is the activator; note SUMMARY correctly regenerates.
- **F5 (SHIP):** FR-47 distinctness is correct as written.
- **F6 (minor revise):** make `--check` a hard pre-land gate; state that entries are stored with placeholders unresolved (FR-21a), task_id minted if absent.

Net: the requirement is the right requirement, but it is not yet buildable from the FR text without re-deriving the entire append protocol — which is exactly the kind of "looks rigorous, confidently misaligned with the engine" gap a soundness review exists to catch.
