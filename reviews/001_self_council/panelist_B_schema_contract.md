# Panelist B — Schema / contract correctness (FR-61..65)

**Charter:** Schema/contract correctness of instruction 001 (FR-61..65, three `.json` edits, REQUIREMENTS.md). Note: arunner schemas are NOT enforced by a `jsonschema` library at runtime (NFR-3 forbids that dependency) — the hand-rolled stdlib `--check` validator (`tests/test_check_plan.py`) is the runtime enforcer; these `.json` files are the documented contract. `tests/test_schemas.py` pins ONLY `heartbeat.schema.json` (byte-identical across two skill copies), which was intentionally NOT modified.

**Verified:**

1. **FR numbering** — FR-61..65 follow FR-60 cleanly: no collision, no gap. All five §9 rows present, each PENDING/spec-only and tagged "arunner 0.2.0," matching the block prose. Status line and closing note bumped to v1.4 consistently.

2. **All three `.json` files are well-formed** (`python json.load` passes). **Only the four expected files are in the diff; `heartbeat.schema.json` is untouched** (`git diff` confirms), so `test_schemas.py`'s byte-identical PIN holds.

3. **The `oneOf` is correct.** The four subschemas constrain only `required:[X]`, so under draft-07 `oneOf` an entry satisfies exactly-one iff exactly one source key is present: inline-only / adapter-only / file-only / steps-only all PASS (n=1); both-present (`worker_prompt`+`adapter`, or `worker_prompt`+`steps`) correctly REJECTS (n=2); none correctly REJECTS (n=0). **No existing example or test plan violates it:** the canonical plan entry carries only `worker_prompt`; every adapter job carries only `adapter` (no `worker_prompt`); no single entry anywhere carries both. The `.jobs.json` files use the FR-40/41 shorthand (`jobs` key), not direct `plan.schema.json` validation — unaffected regardless.

4. **Token fields fit the real schema.** `result.schema.json`: `input_tokens`/`output_tokens` are top-level siblings of `summary`/`synthesized`, NOT nested under `claimed` — exactly as FR-65 specifies. Typed `["integer","null"]`, `minimum:0`, reporting-only. Heartbeat correctly needs no change: usage rides in the already-open `data` object.

5. **Manifest enum widened** to `["subagent","shell"]`; optional `step_index`(≥0)/`step_count`(≥1) added — additive, `schema_version` unchanged. Matches `plan.schema.json`.

No FIX-REQUIRED items.

**VERDICT: SHIP**
