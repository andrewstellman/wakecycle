# Panelist C — SCOPE, COHERENCE & HONESTY review of FR-57 / FR-58

**Charter:** adversarial scope/overlap/honesty review of the two 2026-06-15 live-ops additions.
**Sources read:** REQUIREMENTS.md FR-57/FR-58 (L360–368), US-13/14 (L45–46), §9 PENDING rows (L426–427), FR-23 (L261), FR-26a (L265), FR-46/47 (L312–313), FR-13 (L241), FR-40 keepalive math (L300), §8 out-of-scope (L398), FR-52/53 builder scope (L328–336), UC list (L50–207).

## Overall verdict: **REVISE-REQUIRED** (small, surgical revisions — neither FR should ship as written, but both are legitimate)

Both FRs trace to a genuine real-use gap from the 2026-06-15 stub-worker pressure test, not gold-plating. FR-57 is well-bounded. FR-58 is two features wearing one number, and the honesty split between them is asserted in prose but not load-bearing in the structure. Findings below are ordered by severity.

---

## Findings

### Finding 1 — FR-58 is two FRs stapled together; the honesty split is real but under-structured (REVISE-REQUIRED)
FR-58 explicitly contains "Two parts": (a) **engine** — a configurable finer keepalive/activity-refresh interval (`--keepalive-seconds`, default ~30–60s), and (b) **orchestrator + SKILL** — surface the table as a visible message each tick.

These are different *kinds* of requirement with different *evidence bars*:
- Part (a) is a buildable, deterministic, unit-testable engine change (feed fixed clock values to the keepalive seam — the same seam FR-56 already exercises, L358). It can be VERIFIED.
- Part (b) is **orchestrator behavior + documentation** — "the rung-1 agent prints `status_table` each tick as a visible message (not captured/suppressed)." This is a host-agent-rung concern, the *same* honesty bucket as FR-50/FR-52/FR-54: per NFR-12 and FR-54's "honesty split," anything that lives in the agent's natural-language layer is DESIGNED, VERIFIED only on the host that actually did it. A `test_positioning_honesty.py`-style mechanical guard can pin that the SKILL *says* it, but cannot pin that an arbitrary host agent *does* it.

**The spec names this split but does not make it structural.** The §9 row (L427) collapses both into one PENDING line ("`heartbeat.py` keepalive decoupled... + SKILL/runbook table-surfacing"), which means at verification time someone can mark the whole row VERIFIED on the strength of the engine test alone — overclaiming an engine fix for what is half an orchestrator-behavior issue. That is exactly the dishonesty pattern FR-50/FR-54 exist to prevent.

**Recommended trim:** Split FR-58 into **FR-58a (engine: finer keepalive/activity-refresh interval — buildable, VERIFIABLE)** and **FR-58b (orchestrator+SKILL: per-tick visible table — DESIGNED guidance, mechanically guarded only for the SKILL text, VERIFIED per-host like FR-52)**. Give them two §9 rows with two evidence bars. If a full renumber is unwanted, at minimum the single §9 row must carry the FR-54-style honesty caveat verbatim ("engine part VERIFIABLE; the visible-table behavior is per-host DESIGNED, the SKILL text is the only mechanically-guarded surface"). As written, the row is *honestly* PENDING today (so not yet a violation) but is structured to permit a dishonest VERIFIED later.

### Finding 2 — "Regular visible table" partially re-litigates UC-2 / FR-27, which already claim the table is the UI (CONCERN)
FR-27 (L268) and UC-2 (L67–77) already require the status table printed verbatim each tick by the relaying tier, with UC-1 step 5 ("agent prints the status table verbatim and schedules the next tick," L61). FR-58b's "print `status_table` each tick as a visible message (not captured/suppressed, not buried in one long ticker bash block)" is **not a new capability** — it is a re-statement of FR-27/UC-2 plus a *failure-mode patch* ("don't suppress it"). 

This is honest as far as it goes (the 2026-06-15 test found the table *was* being collapsed out of view), but the spec should say so: FR-58b is a **guidance/guardrail tightening of an existing requirement, not a new one.** Framing a re-assertion of FR-27 as a brand-new functional requirement inflates the apparent novelty. Recommend FR-58b explicitly reference FR-27/UC-2 as the requirement it hardens, and frame itself as "make the existing per-tick-table contract non-suppressible," not as new ground.

### Finding 3 — FR-57 scope is correctly minimal; append-only is the right call, but the §8 omission is a coherence gap (CONCERN)
FR-57 (`add` verb) is appropriately minimal and does **not** balloon: append-only, fresh `task_id`/`run-NN` per entry, mechanical path substitution (FR-21a), validated as `--init` entries (FR-2/FR-42), idempotent claim by the existing pool. It deliberately does *not* add remove/edit — correct, because:
- Removing/editing *queued* entries is already partly covered by the control-file convention (FR-39 CANCEL frees a slot, L398) and FR-35..39 "finer live control."
- Editing entries before launch is FR-52 step 5 (L333), explicitly bounded to pre-launch.

So append-only is the coherent minimal verb. **However:** FR-52's "Scope" note (L335) and step 5 (L333) both currently say "dropping new jobs into a *live* run is the streaming-instruction-queue path (FR-47), not the builder." **That statement is now false** — FR-57 just created a *second* live-job path (the harness-pool one). Those two FR-52 sentences must be updated to read "FR-47 (in-context) or FR-57 (harness pool)," or they actively misdirect a reader to FR-47 for a harness-batch add. This is a coherence regression FR-57 introduces and must fix in the same change.

Separately: §8 out-of-scope (L398) lists "harness resume/iterate strategies (deferred)." FR-57's "reset `done` to false" on a *completed* run is arguably an iterate-strategy. The spec should either (a) confirm FR-57 only targets a *running* run-dir (the text says "running" but then says "resets `done` to false," implying it can revive a done run), or (b) explicitly carve the done-revival case as in or out of scope. As written it's ambiguous whether `add` to an already-`done` run is supported, and that ambiguity touches the §8 deferral.

### Finding 4 — FR-57 vs FR-47 distinction is clean and correctly stated (SHIP this part)
The "Distinct from FR-47" paragraph (L364) draws the line correctly: FR-47 streams *instructions* to an *in-context* worker (instruction folder, `NNN-` files, one agent's context); FR-57 grows the *harness batch* (pool-dispatched subagent/shell entries the engine claims). These are genuinely two surfaces — different dispatch mode (in-context vs pool), different artifact (instruction file vs queue entry), different concurrency model. This is **not** two names for one feature. The only fix needed is propagating the distinction back into FR-52 (Finding 3), because FR-52 currently implies FR-47 is the *only* live-add path.

### Finding 5 — FR-58a (finer keepalive) does NOT overlap FR-23 cadence or FR-26a safety tick — but the spec should pre-empt the confusion (CONCERN, mostly satisfied)
The three "cadences" in play are genuinely orthogonal and FR-58 is mostly careful:
- **FR-23 cadence rungs** = *what triggers the next tick* (in-session timer / OS scheduler / ticker loop / manual). Tick-level.
- **FR-26a safety tick** = a low-frequency *external redundant tick* (~3× plan cadence) to rescue a dropped rung-1 loop. Tick-level, reliability.
- **FR-58a keepalive interval** = *intra-job heartbeat/activity-refresh frequency* (the wrap/tail adapter emitting IN_PROGRESS), decoupled from the stall threshold. **Worker/heartbeat-level, not tick-level.**

FR-58a explicitly says "independent of the stall threshold (which stays coarse for genuine stall detection)" — good, that pre-empts the FR-40 stall-math overlap (L300, keepalive currently `min(launch_grace, stall/3)`). What it does **not** pre-empt: the reader will reasonably ask "if the keepalive now fires every 30–60s but the *tick* still fires on its own cadence, does a 30s keepalive do anything visible if the table only refreshes per tick?" The answer is that FR-58a (more heartbeats on disk) only becomes *visible* via FR-58b (table printed per tick reads the freshest heartbeat) — i.e. **FR-58a is inert without FR-58b**, and a tick cadence longer than the keepalive interval means the operator still only sees updates per tick. The spec should state this coupling explicitly so no one builds FR-58a, tests the heartbeat file, marks it done, and is surprised the operator-visible ACTIVITY still only moves per tick. This is the "finer keepalive could be confused with tick cadence" risk the charter flagged — present, and worth one clarifying sentence.

NFR-13 ("tick is sub-second; over-polling always safe," L384) backs the safety of a finer keepalive, but note keepalive frequency is bounded by *tick* frequency for *visibility*; that bound deserves a line.

### Finding 6 — No UC-13/UC-14; traceability matrix WILL need updating (REVISE-REQUIRED, mechanical)
US-13/US-14 exist (L45–46) and §9 has rows for FR-57/FR-58, but there is **no UC-13/UC-14** (UC list ends at UC-12, L207). Every other recent FR got a UC: FR-55→UC-11, FR-56→UC-12, FR-46..49→UC-9, FR-52→UC-10. FR-57/FR-58 break that pattern.

- **FR-57** does NOT need a new UC — it cleanly *extends UC-1* (run a batch): add an alternative path "(d) operator runs `add` against the live run-dir; the next tick claims the appended entries under the existing pool." A standalone UC would be gold-plating.
- **FR-58** does NOT need a new UC — it *extends UC-2* (monitor): the keepalive/visible-table behavior is the realization of UC-2's "reads per-tick status tables." Add an alternative-path or a postcondition note that ACTIVITY moves intra-run.

**Required:** either add the two alternative paths to UC-1/UC-2, or the §9 rows and US-13/14 will reference FRs that touch UC-1/UC-2 without the matrix saying so. Recommend the lightweight extension (no new UCs). Flag for the doc owner: the FR↔UC↔US traceability is currently incomplete for these two — US-13→FR-57→UC-1 and US-14→FR-58→UC-2 must be made explicit.

---

## Scope-trim summary (what to cut / split before ship)

1. **Split FR-58 → FR-58a (engine, verifiable) + FR-58b (orchestrator+SKILL, DESIGNED/per-host).** Two §9 rows, two evidence bars. (Finding 1)
2. **FR-58b: reframe as a hardening of existing FR-27/UC-2, not a new requirement** ("make the per-tick table non-suppressible"). (Finding 2)
3. **FR-57: fix the now-false FR-52 statements (L333, L335)** that name FR-47 as the only live-add path; add "or FR-57." (Finding 3)
4. **FR-57: disambiguate `add`-to-a-`done`-run** vs the §8 "iterate strategies deferred" line. (Finding 3)
5. **FR-58a: add one sentence** that it is inert for operator-visibility without FR-58b and bounded by tick cadence. (Finding 5)
6. **Add UC-1 alt-path (FR-57) and UC-2 alt-path (FR-58); do NOT create UC-13/UC-14.** Make the US→FR→UC trace explicit. (Finding 6)

## What is genuinely fine (no change)
- FR-57 append-only minimality and the FR-47-vs-FR-57 distinction (Finding 4) — clean, ship as-is once Findings 3 propagate.
- FR-58a's decoupling from the stall threshold (Finding 5) — correctly orthogonal to FR-23/FR-26a.
- Both §9 rows are *honestly* PENDING today; the FR-58 dishonesty risk is latent (structure permits a future overclaim), not present.
- Provenance is honest: both cite the specific 2026-06-15 test and the specific failure (hand-editing the run-dir; ACTIVITY stuck at STARTING). Not gold-plating.
