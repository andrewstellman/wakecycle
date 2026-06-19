# Panelist A — Correctness (instr 003: shell-gate cwd fix)

**Charter:** the `cwd=target_repo` fix resolves the false-failure; the nonexistent-target_repo path stays fail-closed.

**What I traced / repro'd:**
1. **Fix resolves the bug.** `_eval_shell_gate` now passes `cwd=(values.get("TARGET_REPO") or None)`. `values` comes from `_gate_values` = `str(step.get("target_repo") or entry.get("target_repo",""))` — step override wins, falls back to entry. A gate with a relative import (`python3 -m bin.validate_phase_artifacts`) now runs from target_repo. Correct.
2. **Nonexistent/deleted cwd stays fail-closed.** Repro confirmed: `subprocess.run(cwd="/no/such/dir")` → `FileNotFoundError`; `cwd=<a file>` → `NotADirectoryError`; both are `OSError`. The unchanged `except (OSError, subprocess.SubprocessError): return "internal_error"` catches both → `internal_error` (fail-closed). Correct.
3. **`or None` fallback.** `"" or None` → `None` → subprocess inherits the engine cwd (prior behavior). Correct.
4. **Sound general default.** cwd = where the worker operated / artifacts live is a defensible general semantic, not QPB-specific; the docstring states it generically.

Gate tests pass; the diff is exactly the one-argument change + docstring. No new failure modes.

**VERDICT: SHIP**
