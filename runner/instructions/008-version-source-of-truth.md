# Instruction 008 — version source-of-truth (harden FR-34) + bump to 1.1.0

## What this is
Establish a single source of truth for arunner's version and bump the line to **1.1.0** (operator decision). FR-34 already declares `arunner/__init__.py:__version__` canonical, and `--version` (`cli.py`) + the engine reader (`tick.py`) read it — but **`pyproject.toml` carries its own literal `version = "0.1.0"`** (a drift point: bump one without the other and they diverge), there is **no startup banner** printing the version, and a stale **`wakecycle.egg-info`** (former package name, v0.0.1) lingers. Harden FR-34 so the version is written in exactly one place, the banner shows it, and a test pins every surface together.

## Prerequisite / branch (single-trunk)
Short-lived branch off `main` (now `ca268d7`): `git worktree add ~/Documents/arunner-version -b version-source-of-truth main`. Implement, self-Council to SHIP, commit. **Worker does NOT push/merge** — operator lands onto `main` + deletes the branch/worktree.

## The work
1. **Single source — `pyproject.toml` derives from `arunner.__version__`.** Replace the literal `version = "0.1.0"` (~line 7) under `[project]` with `dynamic = ["version"]`, and add `[tool.setuptools.dynamic]` with `version = {attr = "arunner.__version__"}`. After this, `arunner/__init__.py:__version__` is the ONE written location; wheel/sdist metadata derives from it. Verify the derivation actually works: `pip install -e . && arunner --version`, and/or `python3 -c "import importlib.metadata as m; print(m.version('arunner'))"` → must report the `__init__` value.
2. **Bump `__version__` → `"1.1.0"`** in `arunner/__init__.py` (the single source). Keep the FR-34 docstring.
3. **Startup banner prints the version, read from the source.** arunner should print an identity banner at startup that includes the version, read from `__version__` (never a literal). Find the current startup/entry path (CLI `main` in `cli.py`, the REPL/ticker start, the orchestrator bootstrap announce). If a banner exists, make it read `__version__`; if none does, add a one-line `arunner <__version__>` banner at the appropriate entry point. Confirm `--version` now reports `1.1.0`.
4. **Remove the stale `wakecycle.egg-info`.** If it is gitignored build cruft, delete the directory; if tracked, `git rm -r`. Confirm the package identity is `arunner` (not `wakecycle`) end to end.

## Tests
- **Drift-pin (the heart of this instruction):** a test asserting the version is identical across every surface — `arunner.__version__` == the packaging metadata (`importlib.metadata.version("arunner")`, or a parse of the `pyproject` dynamic config) == `arunner --version` output == the startup banner string. Bumping `__version__` alone must keep them in lockstep; introducing a second hardcoded copy must make this test fail. **Mutation-verify:** change one surface to a literal, watch the pin bite, restore.
- Confirm `--version` reports `1.1.0`.
- Full suite green: `python3 -m pytest tests/ -q`. **Report your Python version.** stdlib-only engine preserved (NFR-3 — `importlib.metadata` is stdlib; no new runtime dependency).

## Council
Single-panel self-Council (small, deterministic packaging/version change), charter: (a) `pyproject` truly derives from `arunner.__version__` — grep the whole tree for stray `0.1.0` / version literals, there must be exactly one written source; (b) the banner reads the source, `--version` reports `1.1.0`, the drift-pin pins all surfaces + bites; (c) package identity is `arunner` (no `wakecycle` residue), no new runtime dependency. Write `runner/reviews/008_self_council/`. Iterate to SHIP.

## §9 / requirements
Update the FR-34 §9 row to record the hardening (`pyproject` now derives from `__version__`; banner reads it; drift-pin test added; line = `1.1.0`). If adding a startup banner warrants a small US/UC, assign next-free (no reuse); otherwise none.

## Commit / output
Focused commit(s) on `version-source-of-truth` (do NOT push/merge — operator lands). Output → `outputs/008-version-source-of-truth.md`: the pyproject-dynamic change, the `__version__` bump, the banner change + its entry point, the egg-info cleanup, the drift-pin test + its mutation bite, full-suite count + your Python version, the single-panel verdict, `git log --oneline`.
