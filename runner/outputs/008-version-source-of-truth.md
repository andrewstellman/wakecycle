# Output for 008-version-source-of-truth.md
**Status:** completed

FR-34 hardened: a single WRITTEN version source, `pyproject.toml` made dynamic
(derives from `arunner.__version__`), the line bumped to **1.1.0**, a console
startup banner reading the source, and the stale `wakecycle.egg-info` removed. One
focused commit on `version-source-of-truth` (off `main` `ca268d7`), **local only —
not pushed/merged**. Single-panel self-Council: **SHIP**. Suite **485 → 486 passed,
1 skipped**, Python **3.14.6**. Derivation verified live in a throwaway venv.

## Pre-flight
- `main` at `ca268d7` (as the instruction states). Created worktree
  `~/Documents/arunner-version` on new branch `version-source-of-truth` off `main`.

## The pyproject-dynamic change (single source)
**Before:** `arunner/__init__.py:__version__ = "0.1.0"` **and** a literal
`version = "0.1.0"` under `[project]` in `pyproject.toml` — two written copies, a
drift point (bump one, they diverge).
**After:** `[project]` declares `dynamic = ["version"]`; a new
`[tool.setuptools.dynamic]` table sets `version = {attr = "arunner.__version__"}`.
`arunner/__init__.py` is now the **one** written location; wheel/sdist metadata
derives from it. **Whole-tree grep** confirms exactly one `^__version__ =` source.
**Verified live** (throwaway venv, not assumed): `pip install -e .` →
`importlib.metadata.version("arunner")` → `1.1.0` and `arunner --version` →
`arunner 1.1.0`.

## The `__version__` bump + mirrors
- `arunner/__init__.py:__version__` → **`"1.1.0"`** (FR-34 docstring kept).
- Mirrors moved in lockstep so the existing drift-pin stays green:
  `package.json` `1.1.0`, `plugin.json` `1.1.0`, `SKILL.md` frontmatter `1.1.0`.

## The banner change + entry point
- The engine (`tick.main`, `tick.py:3730`) and ticker (`ticker.main:281`) **already**
  print `arunner <version>` from `_arunner_version()` (reads `__init__.py` by path).
- **Added** a console startup banner at the CLI entry — `cli.main` (`arunner.cli`)
  now prints `arunner %s % __version__` to **stderr** (stdout stays clean for the
  status table / staged-job lines). `--version`/`-h` exit inside `parse_args`
  first, so the banner never double-prints on `--version` (verified: `--version` →
  stdout `arunner 1.1.0`, stderr empty; a verb run → banner on stderr). Reads the
  single source, never a literal. No new US/UC needed (within FR-34's existing
  "startup banner" scope).

## The egg-info cleanup
- `wakecycle.egg-info/` (PKG-INFO `Name: wakecycle`, `Version: 0.0.1` — the
  abandoned name-reservation release) lived in the **main checkout**, was
  **gitignored/untracked** build cruft (`!!` in `git status --ignored`). Removed
  with `rm -rf`; no tracked file touched. End-to-end package identity is `arunner`.
- Also made the `npm-bin/arunner.js` header comment version-agnostic (it carried a
  stale `(v0.1.0)` literal nothing reads — a latent drift point; the launcher execs
  `python -m arunner`).

## Files created / changed
| Path | Lines | Note |
|---|---|---|
| `pyproject.toml` | +13/−1 | `[project]` literal → `dynamic = ["version"]`; new `[tool.setuptools.dynamic] version = {attr = "arunner.__version__"}`. |
| `arunner/__init__.py` | ±1 | `__version__` `0.1.0` → `1.1.0` (the one written source). |
| `arunner/cli.py` | +6 | console startup banner (stderr, reads `__version__`). |
| `package.json` | ±1 | mirror → `1.1.0`. |
| `plugins/arunner/.claude-plugin/plugin.json` | ±1 | mirror → `1.1.0`. |
| `plugins/arunner/skills/arunner/SKILL.md` | ±1 | frontmatter mirror → `1.1.0`. |
| `npm-bin/arunner.js` | ±1 | header comment made version-agnostic. |
| `tests/test_version_single_source.py` | +85/−24 | drift-pin: pyproject-derives assert (no static literal) + installed-metadata surface (skips if not installed) + CLI-banner surface; docstring mutation note updated. |
| `docs/REQUIREMENTS.md` | +2 in-place | FR-34 statement + §9 row record the hardening (derives, banner, egg-info, drift-pin, line=1.1.0). |
| `runner/reviews/008_self_council/SYNTHESIS.md` | new | single-panel self-Council, SHIP. |
| *(removed)* `wakecycle.egg-info/` | — | stale untracked v0.0.1 build cruft in the main checkout. |

## Commits made
- **`91f9b75`** — *FR-34 hardening: single version source + dynamic pyproject + bump 1.1.0 (instr 008)*.
  On `version-source-of-truth`, **local only — not pushed, not merged** (operator lands).

`git log --oneline -4`:
```
91f9b75 FR-34 hardening: single version source + dynamic pyproject + bump 1.1.0 (instr 008)
ca268d7 runner: commit 006+007 record (instr 007, outputs, STATUS)
895da5e FR-73: make output_globs scan Python-3.10+ portable (instr 007)
5695c9f FR-73: gate OUT-AGE scan to in-flight runs (self-Council B-F1) + 006 council artifacts
```

## Acceptance criteria — pass/fail per item
- `pyproject` derives from `arunner.__version__` (dynamic attr), no literal — **PASS**
  (verified live: metadata + `--version` → 1.1.0).
- Exactly one written version source — **PASS** (whole-tree grep → `__init__.py` only).
- `__version__` bumped to `1.1.0`, FR-34 docstring kept — **PASS**.
- Startup banner prints the version, read from the source — **PASS** (CLI banner added;
  engine/ticker banners already read the source).
- `--version` reports `1.1.0` — **PASS** (installed console script in venv).
- Stale `wakecycle.egg-info` removed; identity is `arunner` — **PASS**.
- Drift-pin pins every surface + bites — **PASS** (two mutations bit, restored).
- Full suite green; NFR-3 (stdlib-only, no new dependency) — **PASS** (486 passed/1
  skipped; `importlib.metadata` is stdlib, dynamic attr reads the file, no import).

## Council (required) — single-panel self-Council: **SHIP**
`runner/reviews/008_self_council/SYNTHESIS.md`. Charter (a) pyproject truly derives,
exactly one written source — SHIP; (b) banner reads the source, `--version` = 1.1.0,
drift-pin pins all surfaces + bites (two mutations) — SHIP; (c) identity `arunner`,
no new dependency (NFR-3) — SHIP. Scope decision recorded: README/TOOLKIT release
prose and frozen `reviews/`/`runner/1.5.9` artifacts referencing the historical
`v0.1.0` milestone are not version sources and were left to the operator's
release-notes call; the one packaging-adjacent literal (`npm-bin/arunner.js` comment)
was made version-agnostic.

## Tests
- Baseline (main `ca268d7`): **485 passed** (`python3 -m pytest tests/ -q`, via
  `git stash`).
- Final: **486 passed, 1 skipped** (+1 CLI-banner pin; installed-metadata pin skips
  in the path-based env, PASSES when the package is installed). Python **3.14.6**.
- Live derivation: throwaway venv `pip install -e .` → `importlib.metadata` and
  `arunner --version` both **1.1.0**.
- Mutation bites: pyproject literal reintroduction →
  `test_pyproject_derives_from_canonical` FAIL; `package.json` drift →
  `test_package_json_mirrors_canonical` FAIL; restored → green.

## §9 rows flipped
None added. Updated the existing **FR-34** §9 row (and the FR-34 statement) to record
the hardening: single written source + dynamic pyproject + banner + egg-info removal +
extended drift-pin + line `1.1.0`. No new FR/US/UC (banner is within FR-34's scope).

## Notable observations
- **Derivation proven, not assumed:** the instruction warned a green dev-interpreter
  suite isn't proof; the editable-install venv run (`importlib.metadata` + `--version`
  → 1.1.0) is the real evidence the dynamic attr resolves to the single source.
- **`importlib.metadata` skip-vs-pass:** the metadata drift-pin skips in the
  path-based test env (arunner not installed) and PASSES when installed — both paths
  exercised. Kept the static `dynamic`-config assert as the always-on guard.
- **NFR-3 clean:** setuptools reads the `attr` literal by parsing `__init__.py` at
  build time (no package import), and `importlib.metadata` is stdlib — no runtime
  dependency added to the engine path.

## Next action expected from orchestrator
Operator lands `version-source-of-truth` → `main`, deletes the branch + worktree.
Next single-trunk 1.1.0 step (per `docs/PLANNED_run_robustness.md`): FR-76 → FR-75 →
FR-77 → doc-sync; tag `v1.1.0` when the line completes.
