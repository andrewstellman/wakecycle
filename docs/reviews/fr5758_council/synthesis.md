# FR-57 / FR-58 — council synthesis

*2026-06-15. 3-panel review of the two live-ops requirements born from the first real stub-worker pressure test. All three REVISE-REQUIRED; both FRs are the right requirements, the text needed real corrections. All incorporated.*

## FR-57 (live enqueue) — Panelist A's decisive finding

The engine **rebuilds the work table positionally from `plan.json["entries"]`** each tick; `queue/` files are *claim tokens*, not a work list. So "append to the queue" was a category error. **Fix: stage-and-absorb** — `add` writes validated entries to `incoming/`, the **next tick absorbs them under the lock it already holds** (appending to `plan.json`, scaffolding `run-NN` + a `queued` record, mirroring `init_run`). Race-free by construction. Plus: append-only numbering (a renumber/length-mismatch silently drops jobs through the swallowed `except`); placeholders stored **unresolved**; `--check` a hard pre-land gate; **no `done` write** (the queued entry is the activator — `done` is recomputed each tick). Distinctness from FR-47 confirmed clean (SHIP).

## FR-58 (monitoring) — split into 58a (engine) + 58b (SKILL)

- **Panelist C's load-bearing finding:** FR-58 was two FRs in one number, and a single §9 row would let an engine test masquerade as fixing the orchestrator-behavior half — the exact FR-50/FR-54 overclaim pattern. **Split: FR-58a (engine, verifiable) / FR-58b (orchestrator+SKILL, per-host DESIGNED), two evidence bars.**
- **Panelist B (engine design):** the activity re-scan and IN_PROGRESS emit are **already one event** — do NOT split them; decouple the *interval* from `stall/3`, not re-scan from emit. Add `--keepalive-seconds` (default ~45 s). **First-scan-at-start** so sub-interval jobs still surface a line. **Synthesize the knob in `_adapter_worker_cmd`** — and fix the latent bug that grace/stall are *never* synthesized into the adapter today (inert plan knobs). `--check` rejects `keepalive > grace`. The byte-ceiling worry is backwards (finer scans = smaller deltas). **Acceptance test must drive the DEFAULT grace path** — the FR-56 EndToEnd tests used grace 0, which is why this shipped broken.
- **FR-58b** framed as *hardening FR-27/UC-2*, not new ground; ~100% SKILL/runbook; per-host DESIGNED.

## Traceability

US-13→FR-57→**UC-1** (alt-path: grow a running batch); US-14→FR-58→**UC-2** (alt-path: ACTIVITY moves during a run). FR-52 scope note updated (live-add is now FR-57 *or* FR-47). TRACEABILITY.md to gain these alt-paths.

## Disposition

Both buildable now with the corrections baked in. One worker iteration: FR-57 (stage-and-absorb `add` + CLI) + FR-58a (keepalive cadence + adapter synthesis fix) as focused commits; FR-58b is a SKILL/runbook edit (orchestrator prints the table each tick). 3-panel, given the engine-state hazards Panelist A flagged.
