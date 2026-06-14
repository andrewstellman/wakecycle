# Arunner — Software Development Lifecycle

*How arunner is built. This document is the methodology of record: the roles, the artifact flow, the test tiers, the review protocol, the honesty discipline, and the release gate. It is arunner-specific; a generalized version may be extracted later.*

Arunner is built with an **AI-driven development** process: a human operator directs the work, an orchestrating agent plans and verifies, a worker agent implements, and panels of reviewer agents critique. The process is designed so that no single agent's judgment is load-bearing — correctness is established by deterministic tests, independent review, and an auditable evidence ledger, not by any agent's say-so. There is no deadline; correctness and coherence are prioritized over speed.

## Roles

**Operator (human).** Directs scope and priorities, makes the decisions that are genuinely the operator's, and is the *only* party that pushes to a remote or publishes. Every push, tag, and package upload is an operator action.

**Orchestrator (Cowork agent).** Owns the specification and planning documents, writes the numbered instructions the worker executes, independently verifies each landed iteration, runs its own review councils on gates, and maintains the evidence ledger. The orchestrator never pushes or publishes. The orchestrator drives the build loop continuously and only relinquishes control on a real condition (an operator decision, an operator-only action, a genuine blocker recorded as state, or completion) — never on its own judgment that "this is a good place to stop." That discipline is the working-process mirror of FR-55 (below).

**Worker (Claude Code agent).** Implements one numbered instruction at a time in the repository, writes failing tests first where the logic is deterministic, runs its own self-Council review, commits natively in focused commits, writes an output file, and stops. The worker never pushes or publishes.

**Reviewers (sub-agent councils).** Independent agent panels that critique a specification or an implementation against explicit charters. Reviews are adversarial by design and write durable artifacts to disk.

## Artifact flow

```
REQUIREMENTS.md            the contract: FR / NFR / US / UC + the §9 evidence ledger
   │
ITERATION_PLAN.md          the locked scope turned into ordered, individually-tested increments
   │
NNN-<name>.md (instruction) one increment, handed to the worker
   │
worker: red→green tests → self-Council → native commit(s) → outputs/NNN-<name>.md
   │
orchestrator: independent verification (+ its own council on gates)
   │
operator: push / publish
```

`REQUIREMENTS.md` is the single source of truth. The iteration plan and summary documents are *derived* from it; when they disagree, the requirements win. Before authoring any planning content about a versioned change, the canonical design/requirements documents for that version are read end-to-end first — summaries are never treated as specifications.

Requirements evolve **incident-first and council-reviewed**: a real failure (a hallucinated path; an unjustified overnight stop) becomes a dated, traceable functional requirement with a matching user story, use case, and §9 row, vetted by a council before it is built. FR-21a and FR-55 are the canonical examples.

## Tests: the necessary condition vs. the acceptance tests

Two distinct things. Conflating them — calling the first "the acceptance tests" or "the regression net" — is the trap.

**The necessary-condition suite (`python -m pytest tests/` / `unittest discover`).** Unit tests (red/green, mutation-pinned, with an injected `ARUNNER_NOW` clock seam so nothing sleeps) plus deterministic scenarios that drive the *real engine* through the ticker (`ticker.py --once`, **never the agent loop**), with the AI worker replaced by a stub, graded by an **independent** stdlib-only checker (the harness never grades its own homework). This suite checks the engine in isolation. It is a **necessary condition, not a regression test** — it can be fully green while the actual use cases regress, because it never exercises the agent driving arunner. It runs cross-platform in CI; green here is the floor you stand on, not proof the product works.

**The acceptance tests.** The agent — from Claude Code, acting as the operator's seat — drives arunner through the **use-case scenarios at their proper rungs**: in-agent (rung 1) for the agent-driven cases (UC-1, UC-2, UC-3, UC-4, UC-9, UC-10, UC-11), and the ticker/terminal floor for the no-agent cases (UC-5, UC-6, UC-7). Canned plans use stub workers (cross-platform, no API spend) so the whole set runs back to back. Each acceptance test mirrors a user story / use case. **This is where regressions actually surface** — running arunner the way it is used. An agent bootstraps from `AGENTS.md` and runs them all.

**What a regression test is.** The **acceptance tests *and* the necessary-condition suite, run together.** Neither alone is one: the pytest suite catches engine-level breakage but is blind to in-agent regressions; the acceptance tests catch use-case regressions but assume the engine floor. Only the two together are a regression test.

(Dogfooding — using arunner for real development work — is separate from both: it *measures* which wake-up modes survive real use and never *validates* a §9 row, NFR-12.)

## Traceability

Every acceptance test mirrors the specific user story / use case it covers; the necessary-condition suite is the engine floor underneath, not the acceptance layer. The traceability artifact is a US/UC → acceptance-test matrix. Because the acceptance tests run at different rungs and on different hosts, the matrix also records *where* each must run to be complete — a per-OS run (minimally Windows and macOS) for the platform-sensitive cases, and a per-agent run (Claude Code, Cursor, Copilot) for the in-agent orchestrator cases (UC-10's "any host" is verified only where an agent has actually driven it). Coverage is claimed only after a **council review concludes every US/UC is mirrored by an acceptance test**, with each test's required run-contexts named. That review is the traceability gate; it keeps "we cover every use case" from quietly overclaiming.

## The §9 evidence ledger

`REQUIREMENTS.md` §9 maps every claim to its evidence and a status: **VERIFIED** (an evidence-linked test or recorded run), **PENDING** (not yet built/validated), or **DESIGNED** (built but not yet validated on a given host/platform). The hard rules:

- A row flips to VERIFIED only on a real, linked artifact (a named test + scenario, or a dated run-dir).
- **No row flips on dogfooding or always-on running** — those measure survival, they do not validate a floor.
- The cadence/scheduler/Windows floor and the in-session agent rung stay PENDING/DESIGNED until a recorded cross-platform matrix run exists.
- The ledger is guarded **mechanically** (`test_positioning_honesty.py`): the floor row must stay PENDING, no VERIFIED row may cite dogfooding/always-on, the lead messaging must keep its honest framing. When a feature is genuinely built, its guard assertion is updated in the same iteration that flips its row.

This ledger is the honesty surface the whole process exists to protect.

## Review protocol

**Worker self-Council.** Before filing an iteration as done, the worker spawns a panel of reviewer subagents (three for load-bearing / shared-state / timing-coupled / honesty-surface work; a single reviewer for small deterministic changes), each with an explicit charter, each writing a durable verdict file. The worker iterates to a unanimous SHIP before the orchestrator sees it.

**Orchestrator independent council.** On gates — release packaging, the §9 honesty reconciliation, and load-bearing features — the orchestrator runs its *own* independent panel, with reviewers that read and exercise the landed code (build the wheel, install it, run the gates), not the worker's claims. Independence is the point: the implementer's context does not review its own work.

**Review norms.** Reviewers are adversarial and cite file:line evidence. Confidence is calibrated — a position that folds under a single challenge should have been hedged from the start. Findings are incorporated before the gate is declared passed, and the synthesis is kept as an artifact.

## Commit, push, and the operator boundary

- Commits are **native** to the repository and **focused** (one concern each), made by the worker (or, for pending specification edits, folded into a worker baseline commit — the "commit pending spec first" pattern).
- **The worker and orchestrator never push or publish.** Pushing to a remote, tagging, and uploading a package are operator-only actions.
- **Verify before claiming a remote state.** Nothing is reported as pushed/tagged/published without directly observing the end state (e.g. `git ls-remote`), never inferred from having issued the command.

## Autonomy integrity (FR-55) — in the product and in the process

Arunner's core promise is *unattended* autonomy, so the decision to keep running versus relinquish control is externalized to disk (a deterministic per-tick verdict) and audited (yield accounting + a violation detector), never left to the agent's discretion. The same principle governs how the process itself runs: the orchestrator does not stop the build on its own judgment ("it's late," "a tidy checkpoint") — it continues until a terminal state, an explicit operator control, or a genuine blocker recorded as state. An unjustified stop is treated as a defect, the same way the product treats a `CONTINUE`-state yield as a violation.

## The release gate

A release is gated on **both**, because neither substitutes for the other:

1. **The necessary-condition suite is green cross-platform in CI** — the full pytest/unittest suite on Windows, macOS, and Linux across the supported Python versions (stdlib-only, no agent, no secrets). Necessary, not sufficient.
2. **The acceptance tests pass** — the agent, from Claude Code, drives arunner through the use-case scenarios on each target platform (minimally Windows and macOS) and on each agent claimed as an orchestrator host. These are the runs that actually exercise the product; recorded, they are what lets a §9 host/floor row flip from DESIGNED to VERIFIED.

Together (1) + (2) are the regression test. Plus clean release-gate councils on the packaging and the §9 ledger. Only then does the operator publish.
