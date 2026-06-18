# Panelist C — Internal consistency (FR-61..65)

**Charter:** Confirm all SEVEN Council FIX-REQUIRED items are *integrated* into the final FR text/schemas (not merely appended), the "Concerns to fold in as prose" are present, the §9 rows are five PENDING (none VERIFIED), and all FR/NFR cross-references resolve.

**Per-item checklist (each quoted from the landed files):**

- **FIX #1 — Phase-3-skip is `behavior-flag` (sentinel), NOT `skip-to-next` — PRESENT.** FR-64 `behavior-flag` bullet: "the Phase-3 skip is worker-decided (the worker writes a `_phase3_skipped_sentinel`; downstream steps only read it…) and Phase 4 always runs — a `behavior-flag` exactly, not a gate-driven `skip-to-next`." `skip-to-next` kept generic with a non-QPB example (lint step skipped when no source changed). Acceptance pin asserts the sentinel-write/sentinel-read mechanism, not a gate-driven skip.
- **FIX #2 — designated replace-key pass, no stray-brace scan; literal-brace pin — PRESENT.** FR-61: "a literal `str.replace` of each `{key}` … does not scan for or reject stray braces, so a prompt may carry literal single-brace JSON blocks … that survive substitution unmangled." Acceptance pin present; mirrored in `plan.schema.json` `vars` description.
- **FIX #3 — `required` relaxed via `oneOf`; called a relaxation; file updated — PRESENT.** Entry `required` is now `["task_id","target_repo","dispatch_mode"]` + a 4-way `oneOf`; description: "FR-61 constraint relaxation … mirroring heartbeat.schema.json's oneOf." FR text: "a deliberate constraint relaxation, not a mere additive field." `heartbeat.schema.json`'s `oneOf` confirmed real.
- **FIX #4 — `halt`/`internal_error` maps to an EXISTING FR-55 verdict — PRESENT.** FR-64 `halt`: "maps to an existing FR-55 closed-set verdict (terminal `failed`, or `blocked:<id>` …), never a new ad-hoc terminal." `blocked:<id>`/`failed` are the real FR-55 vocabulary (confirmed at UC-11 + FR-55).
- **FIX #5 — shell-gate EXIT-CODE-ONLY (stdout-regex dropped) — PRESENT.** FR-63: "exit-code only … There is no stdout/regex interpretation … excluded by construction." No `success_regex`/stdout field on the gate object — dropped, not deprecated.
- **FIX #6 — token fields TOP-LEVEL (not under `claimed`); file updated — PRESENT.** `result.schema.json` adds both as top-level props; descriptions state "a sibling of summary/synthesized, NOT nested under claimed." FR-65 agrees.
- **FIX #7 — manifest `dispatch_mode` enum widened to include `shell`; file updated — PRESENT.** `["subagent","shell"]`; description: "Additive enum value; schema_version unchanged." FR-62 agrees.

**Concerns folded in as prose — all PRESENT:** reasoning-verdict computed-at-most-once + persisted (NFR-6 on resume); `behavior-flag <name>` operator-declared; engine reads exactly `data.usage`; a concrete QPB-precondition reference gate (`test -f <path>`); run_playbook won't emit `data.usage` → TOKENS render `—`.

**§9 matrix — CORRECT.** Exactly five new rows (FR-61..65), each **PENDING** ("spec only (instr 001)"), each tagged **arunner 0.2.0**, each naming its schema delta and deferred tests. NONE VERIFIED.

**Cross-references — ALL RESOLVE.** FR-2/3/5/18/27/40/41/45/51/55/56/59 and NFR-6/9/11/12 all have real definitions (grep-verified). No dangling reference. The source draft's "Council FIX-REQUIRED" block does NOT appear in the landed REQUIREMENTS.md (woven in, not appended).

**Adversarial findings:** None rising to FIX-REQUIRED.

**VERDICT: SHIP**
