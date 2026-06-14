# Instruction 041 self-council synthesis — Acceptance-layer foundation

*Mandatory 3-panel (establishes the acceptance mechanism). Three fresh-context, role-locked, adversarial reviewers, each verifying on disk (re-running the suite, independently driving the rung-1 mechanism, mutation-biting the pin). Date: 2026-06-14. (B and C were re-spawned after the first pair hit stream-idle timeouts mid-work.)*

| Panelist | Charter | Verdict |
|----------|---------|---------|
| `panelist_A_checker_parity.md` | durable-vs-meta parity + CLI + independence | **SHIP** |
| `panelist_B_in_agent_fidelity.md` | in-agent rung-1 fidelity | **SHIP** |
| `panelist_C_honesty_regression.md` | honesty & regression | **SHIP** |

## Outcome: unanimous SHIP (round 1)

### Panelist A — checker parity + CLI + independence (SHIP)
Durable and meta paths reach the same verdict where both apply: the core keys (done/counts/run_states/paused/summary_present/results_for_terminal) read durable disk regardless of meta; the meta-dependent keys fall back correctly — `stopped`→STOP file, `stop_readonly`→`_before_snapshot.json`, `verdict_present`→`journal.ndjson`, `final_done`→`status.done`. The four meta-only keys are FLAGGED, not silently passed (`max_inflight_*` emits an explicit "requires the runner" failure). The CLI exits 0/1/2 correctly. `test_checker_independence.py` green (the CLI + `_read_journal` are stdlib-only). The load-bearing pin bites (no-op'ing the run_states check fails `test_durable_grading_detects_wrong_run_state`; restored via shutil.copy2).

### Panelist B — in-agent fidelity (SHIP)
The plan is a genuine subagent plan (all 3 entries `dispatch_mode: subagent`, a `worker_prompt`, zero `worker_cmd`/`adapter`/`command`; `preview` renders all SUBAGENT, `--check: OK`). The stub is trivial (emit STARTING + terminal COMPLETED, return one line) and carries all five placeholders so the engine substitutes the paths — no `{...}` survives, no model-transcribed paths (FR-21a holds). The reviewer **independently drove the rung-1 loop**: staggered pool-2 dispatch (2 → 1 → 0), `done: true` at tick 3, the checker exits 0 (CHECK PASSED), and `_check_meta.json` is absent (graded as a live run from durable artifacts). The drive is the agent-self-reported rung-1 path with an objective disk grade — nothing attributes it to the ticker.

### Panelist C — honesty & regression (SHIP)
The checker docstring's three honesty tiers (disk-gradeable / needs-before-snapshot / needs-meta) match the code and `docs/ACCEPTANCE_TESTS.md`. "Cheap, not free" is stated honestly (zero worker API spend via stubs; the in-agent path spends the agent's own tick tokens + a trivial subagent call per job). No overclaim: 041 is the foundation (one UC demonstrated); Cursor/Copilot stay DESIGNED; a macOS pass doesn't clear the Windows floor — `test_positioning_honesty.py` green with the floor row PENDING. No regression: 267 passed (257 + 10); the `checker.py` diff is purely additive (the meta path is unchanged where meta is present). The new test is sound (a real mutation-pinned assertion).

## Net
The acceptance layer's foundation is in place: the independent stdlib checker now grades a LIVE run from its durable artifacts (harness_status.json / journal.ndjson / results/ / heartbeats) and is exposed as `checker.py <run-dir> <expected.json>`, ADDITIVELY (meta where it exists — the 257 suite — else durable), with an honest disk-gradeable / before-snapshot / runner-meta split. A subagent-dispatch stub plan (`uc1_multijob.json`, trivial heartbeat-emit prompt, `--check`-clean) is the in-agent substrate. And one in-agent acceptance test was demonstrated end-to-end: the worker drove rung-1 (3 stub subagents, NOT the ticker) to `done` and graded it objectively via the CLI (exit 0), with the UC-3 snapshot/STOP/read-only pattern also demonstrated. Suite 257 → 267. The remaining per-UC suite + the AGENTS.md bootstrap + the cross-platform/per-agent runs are later work.
