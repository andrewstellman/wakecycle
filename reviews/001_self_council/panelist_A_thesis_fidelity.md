# Panelist A — Thesis fidelity (FR-61..65, arunner 0.2.0 spec)

**Charter:** Verify the new spec text preserves arunner's founding thesis — disk-truth/determinism (FR-4/6, NFR-6), the FR-18 single-`status` boundary, FR-51 "never grades its own homework," tokens reporting-only, and no "engine-parses-text" hazard reintroduced via the gate.

**What I traced (on disk, `/Users/andrewstellman/Documents/arunner-fr61-65/`):**

1. **Determinism / disk-truth (NFR-6).** FR-62 and FR-63 both state gates are "recorded as `step-MM/gate.json` and read on resume, never recomputed"; FR-63's reasoning verdict is "computed at most once, persisted, and never recomputed." Step state is genuinely just more disk (`run-NN/steps/step-MM/` reusing FR-18). Completed steps are "reaped, never re-run." Schema confirms: `gate.json` recorded; `step_index`/`step_count` on the manifest. PASS.

2. **FR-51 / reasoning-gate fencing.** FR-63 mechanically enforces a separate judging context (same-context judge = `--check` error), excludability requiring `allow_reasoning_gates:true`, and rejection in any `measurement`/FR-51/FR-55 run. `plan.schema.json` backs both flags with `default:false`. The shell gate is "the only kind allowed in measurement/benchmark runs." PASS.

3. **Token boundary (FR-18).** FR-65 — "the engine reads **exactly `data.usage`** (and nothing else in `data`, which stays opaque)"; tokens "never change the FR-5 `{done, stop}` outcome." `result.schema.json` places `input_tokens`/`output_tokens` as **top-level** siblings of `summary`/`synthesized` (Council FIX #6 honored — not under `claimed`), nullable, `—` on absence (NFR-12). PASS.

4. **Engine-parses-text hazard (FIX #5).** FR-63: shell gate is "**exit-code only … There is no stdout/regex interpretation … excluded by construction.**" `plan.schema.json` mirrors this; the `argv` property documents exit-code→outcome with no stdout/regex field present. The regression is foreclosed by absence, not merely discouraged. PASS.

**Cross-checks:** FR-64 `halt` maps to an existing FR-55 closed-set verdict (`failed`/`blocked:<id>`), not a new terminal (FIX #4) — auditor classifies from disk. `behavior-flag:<name>` is operator/plan-declared, never judge-chosen. §9 PENDING rows correctly mark all five as spec-only PENDING.

**FIX-REQUIRED items:** None. All thesis invariants preserved with explicit, quotable guarantees.

**VERDICT: SHIP**
