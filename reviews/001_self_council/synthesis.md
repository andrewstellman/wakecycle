# Instruction 001 self-council synthesis — FR-61..65 (multi-step / gates / prompt-from-file / token reporting), arunner 0.2.0 spec

*Mandatory 3-panel self-Council. Three fresh-context, role-locked, adversarial reviewers verifying on disk — tracing the FR text against the founding invariants, biting the schema `oneOf`/enum/top-level-token contracts, and checking each of the seven Council FIX-REQUIRED items actually landed (woven into the FR bodies, not appended). Date: 2026-06-18. This is a SPEC/docs change; the five FRs remain PENDING in §9 (no implementation, no tests yet).*

| Panelist | Charter | Verdict |
|----------|---------|---------|
| `panelist_A_thesis_fidelity.md` | determinism/disk-truth (NFR-6); reasoning gate upholds FR-51; tokens reporting-only + FR-18 `data.usage`-only boundary; no engine-parses-text via the shell gate | **SHIP** |
| `panelist_B_schema_contract.md` | FR numbering; `oneOf` relaxation correct + breaks no existing plan; top-level token fields + `data.usage` fit real schemas; per-step manifest enum widened; JSON well-formed; heartbeat byte-pin intact | **SHIP** |
| `panelist_C_internal_consistency.md` | all 7 FIX-REQUIRED integrated; concerns folded as prose; five §9 PENDING rows (none VERIFIED); all FR/NFR cross-refs resolve | **SHIP** |

## Outcome: unanimous SHIP (round 1)

### Panelist A — thesis fidelity (SHIP)
Gates are recorded to `step-MM/gate.json` and read-on-resume, never recomputed (NFR-6); step state is just more disk reusing FR-18. The reasoning gate is fenced (separate judging context = `--check`-enforced, `allow_reasoning_gates` + `measurement` flags default false, rejected in measurement/FR-51/FR-55 runs) — FR-51 holds. Tokens are reporting-only: the engine reads **exactly `data.usage`**, top-level `input_tokens`/`output_tokens` are siblings of `summary`/`synthesized`, and tokens never touch `{done,stop}`. The shell gate is **exit-code-only with no stdout/regex field present** — the engine-parses-text hazard is foreclosed by construction.

### Panelist B — schema/contract (SHIP)
FR-61..65 continue cleanly from FR-60; five §9 rows present, all PENDING/0.2.0; v1.4 status+footer consistent. All three schema files parse; `heartbeat.schema.json` untouched (byte-identical PIN holds). The 4-way `oneOf` (worker_prompt | worker_prompt_file | steps | adapter) gives exactly-one semantics under draft-07 and breaks no existing plan — verified that no canonical/test entry carries both `adapter` and `worker_prompt`, and that `.jobs.json` files use the FR-40/41 shorthand, not direct plan validation. Result token fields are top-level (not under `claimed`); manifest enum widened to `["subagent","shell"]` additively.

### Panelist C — internal consistency (SHIP)
All seven FIX-REQUIRED items are integrated into the FR bodies and the schema files (quoted per item): #1 Phase-3-skip = worker-written-sentinel `behavior-flag` (Phase 4 always runs) with `skip-to-next` kept generic; #2 designated replace-key pass + literal-single-brace pin; #3 `required` relaxed to `oneOf` and named a constraint relaxation; #4 `halt`→existing FR-55 `failed`/`blocked:<id>`; #5 shell gate exit-code-only; #6 top-level result token fields; #7 manifest enum widened. All five "concerns to fold in as prose" present. Five §9 PENDING rows, none VERIFIED. Every FR/NFR cross-reference resolves; the draft's FIX-REQUIRED appendix does not appear in the landed text (woven in, not bolted on).

## Disposition
No FIX-REQUIRED items raised by any panel. The integration ships as the arunner 0.2.0 spec for FR-61..65. Full test suite green at the time of review: **334 passed** (Python 3.14.5) — confirming the spec/schema edits introduced no regression. Implementation of each FR lands in later instructions; §9 rows flip to VERIFIED only when those tests exist and pass.
