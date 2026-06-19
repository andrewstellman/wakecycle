# Panelist C — Test-sufficiency (instr 003: shell-gate cwd fix)

**Charter:** the new test passes, the pin bites under mutation, the sentinel form is used, the mutation-evidence header is present, omitted cases justified.

**Execution (pass → mutate → FAIL → revert → pass):**
1. `test_shell_gate_runs_in_target_repo_cwd` passes with the fix.
2. **Mutation bites** — removing `cwd=(values.get("TARGET_REPO") or None)` from `_eval_shell_gate`'s `subprocess.run` → the test FAILS with `AssertionError: 'halt' != 'continue'` (gate runs from the engine's incidental cwd, the relative `sentinel.txt` is absent → exit 1 → unmapped nonzero → `halt`). A genuine, non-tautological pin discriminating on the cwd argument.
3. Reverted by re-adding the exact argument (NOT via git — git checkout would discard the uncommitted fix). Re-ran → passes. `grep -c 'cwd=(values.get("TARGET_REPO") or None)'` → 1.
4. Full gate suite: 15 passed.

**Charter checks:**
- **Sentinel form (not path-equality):** confirmed — writes `sentinel.txt` into target_repo and the gate argv probes `os.path.exists('sentinel.txt')` (relative); no `os.getcwd()==EXPECT` anywhere (avoids the macOS `/tmp`→`/private/tmp` symlink flakiness).
- **Mutation-verify evidence header:** present, matches the file's existing Pin/Mutation/Observed/Restored format, with the real-world QPB origin.
- **Omitted cases justified:** the `cwd=None` fallback is the idiom itself (`"" or None` → `None` = inherit-cwd default) — a dedicated test would only re-assert Python truthiness; the nonexistent-target_repo path raises `OSError`, already caught by the existing fail-closed `except`. Neither is a gap.

**VERDICT: SHIP**
