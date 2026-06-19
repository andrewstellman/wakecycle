# Instruction 003 self-council synthesis — shell-gate cwd false-failure fix

*Mandatory 3-panel self-Council on a one-argument bug fix: `_eval_shell_gate` now runs the gate subprocess with `cwd = the step/entry target_repo`, so a relative-path/relative-import gate no longer false-fails from the orchestrator's incidental cwd. Three fresh-context adversarial reviewers verifying on disk + by execution. Date: 2026-06-19. Branch `fix-gate-cwd` off `fr-61-65-impl` (`e6aa7d3`).*

| Panelist | Charter | Verdict |
|----------|---------|---------|
| `panelist_A_correctness.md` | cwd=target_repo fixes it; nonexistent→internal_error preserved; `or None` fallback; sound general default | **SHIP** |
| `panelist_B_regression_safety.md` | diff scope = 3 files only; FR-18 / measurement / persistence untouched; stdlib-only; suite green | **SHIP** |
| `panelist_C_test_sufficiency.md` | sentinel pin bites under mutation; sentinel (not path-equality) form; evidence header; omissions justified | **SHIP** |

## Outcome: unanimous SHIP (round 1)

**A — correctness.** `cwd=(values.get("TARGET_REPO") or None)`; `values["TARGET_REPO"]` is the resolved step/entry target_repo (step override wins). A bad cwd raises `OSError` (FileNotFound/NotADirectory), caught by the unchanged `except` → `internal_error` (fail-closed). `"" or None` → `None` → engine cwd (prior behavior). Sound general semantic.

**B — regression-safety.** Diff is exactly: the cwd kwarg + docstring in `_eval_shell_gate`, the new `ShellGateCwdTests` + mutation header, and the §9 FR-63 row. The exit-code firewall (DEVNULL, returncode-only), outcome mapping, reasoning path, gate.json persist/read-on-resume, and the FR-51/measurement fences are byte-for-byte unchanged. No new imports (NFR-3). Full suite `Ran 379, OK` ×3.

**C — test-sufficiency.** The sentinel test passes; removing the cwd kwarg makes it FAIL (`halt` != `continue`) — the pin bites. Sentinel form avoids macOS `/tmp`→`/private/tmp` path-equality flakiness. Evidence header present. The cwd=None fallback (Python truthiness idiom) and the nonexistent-target_repo path (pre-existing fail-closed `except`) are justified omissions, not gaps.

## Disposition
No FIX-REQUIRED. The fix ships. §9 FR-63 row updated (cwd=target_repo, 14 tests / 3 PINs). Full suite `Ran 379, OK` ×3 (Python 3.14.5). Recorded sibling for a SEPARATE instruction (out of scope here): `_run_auth_check` has the same no-cwd pattern — its auth_check subprocess also runs from the orchestrator's incidental cwd.
