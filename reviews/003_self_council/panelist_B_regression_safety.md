# Panelist B — Regression-safety (instr 003: shell-gate cwd fix)

**Charter:** no behavioral change beyond the intended one; FR-18 / measurement / persistence untouched; stdlib-only; no regression.

**Diff scope confirmed (vs base `e6aa7d3` / `fr-61-65-impl`): exactly 3 files, nothing else touched.**
- `arunner/engine/tick.py`: only the `cwd=(values.get("TARGET_REPO") or None)` kwarg added to the existing `subprocess.run` in `_eval_shell_gate`, plus its docstring note. The exit-code→outcome mapping, `outcomes`/`default` handling, the reasoning-gate path, gate.json persist/read-on-resume, and the FR-51/measurement fences are byte-for-byte unchanged.
- `docs/REQUIREMENTS.md`: single §9 FR-63 row (13→14 tests, 2→3 PINs).
- `tests/test_gates.py`: new `ShellGateCwdTests` class + mutation-evidence header only.

**FR-18 firewall intact:** stdout/stderr still `subprocess.DEVNULL`; the gate still reads only `proc.returncode`; no stdout parsing introduced.

**stdlib-only (NFR-3):** no new imports (`subprocess` already imported). The kwarg reuses the existing `_gate_values` map; empty target_repo → `None` → engine cwd, as before.

**Suite:** `python3 -m unittest discover tests` → **Ran 379, OK** (3× deterministic). `test_gates` alone passes. Mutation check: removing the cwd kwarg made `ShellGateCwdTests` FAIL (`halt` vs `continue`); restored → OK. The new test pins the fix and does not duplicate/weaken existing coverage.

No FIX-REQUIRED.

**VERDICT: SHIP**
