# Self-Council — instr 008: version source-of-truth (harden FR-34) + bump to 1.1.0

**Scope:** single-panel self-Council (small, deterministic packaging/version
change), as the instruction specifies. Branch `version-source-of-truth` off `main`
(`ca268d7`). **Verdict: SHIP.**

## Charter (from instr 008)

(a) `pyproject` truly **derives** from `arunner.__version__` — grep the whole tree;
exactly **one written source**.
(b) The banner reads the source, `--version` reports **1.1.0**, the drift-pin pins
all surfaces + **bites**.
(c) Package identity is **arunner** (no `wakecycle` residue); **no new runtime
dependency** (NFR-3).

## (a) One written source; pyproject derives — SHIP

- `pyproject.toml` `[project]` now declares `dynamic = ["version"]` and a
  `[tool.setuptools.dynamic]` table `version = {attr = "arunner.__version__"}`. The
  literal `version = "0.1.0"` is gone — pyproject no longer *writes* a version.
- **Live derivation verified** (not assumed): a throwaway venv, `pip install -e .`
  → `importlib.metadata.version("arunner")` → **`1.1.0`** and
  `arunner --version` → **`arunner 1.1.0`**, both tracing back to
  `arunner/__init__.py:__version__`.
- **Whole-tree grep** for `^__version__\s*=` → exactly **one** hit:
  `arunner/__init__.py:9 = "1.1.0"`. The mirror surfaces (`package.json`,
  `SKILL.md`, `plugin.json`) carry `1.1.0` and are drift-pinned to the source.
- **Residual `0.1.0`/`0.0.1` strings audited and judged out-of-scope:** README/TOOLKIT
  release prose and `tests/*` milestone comments describe the historical *v0.1.0
  milestone* (not a version source, not machine-read, not drift-pinned); `reviews/`
  + `runner/1.5.9/` are frozen council/output artifacts. The one packaging-adjacent
  literal — the `npm-bin/arunner.js` header comment `(v0.1.0)` — was made
  version-agnostic to kill a latent drift point (the launcher already execs
  `python -m arunner`, so it carries no real version). Updating marketing/release
  prose is the operator's release-notes call, deliberately not done here.

## (b) Banner reads the source; `--version` = 1.1.0; drift-pin pins + bites — SHIP

- **Banners read the source:** the engine (`tick.main`) and ticker
  (`ticker.main`) already printed `arunner %s` from `_arunner_version()` (reads
  `__init__.py` by path). Added a matching **console startup banner** at the CLI
  entry (`cli.main`), printing `arunner %s % __version__` to **stderr** so stdout
  stays clean for the status table / staged-job lines. `--version`/`-h` exit inside
  `parse_args` first, so the banner never double-prints on `--version` (verified:
  `--version` → stdout `arunner 1.1.0`, stderr empty; a verb run → banner on stderr).
- **`--version` reports 1.1.0** — verified via the installed console script in the venv.
- **Drift-pin extended and value-agnostic** (never hardcodes the number):
  `test_pyproject_derives_from_canonical` (dynamic declared + attr points at the
  source + NO static literal), `test_installed_metadata_matches_canonical`
  (skips when not installed; PASSED == 1.1.0 when the editable install was present),
  `test_cli_startup_banner_reads_canonical` (new), plus the retained
  package.json/SKILL/plugin.json/`--version`/engine-banner pins.
- **Mutation-verified to bite (two bites, both restored):**
  (1) reintroduced `version = "9.9.9"` under `[project]` →
  `test_pyproject_derives_from_canonical` FAILed (the no-static-literal assert);
  (2) drifted `package.json` to `9.9.9` → `test_package_json_mirrors_canonical`
  FAILed. Restored tree: `test_version_single_source.py` → 9 passed, 1 skipped.

## (c) Package identity `arunner`; no new dependency — SHIP

- **`wakecycle.egg-info` (v0.0.1) removed** — it lived in the main checkout, was
  **gitignored/untracked** build cruft (`PKG-INFO` Name: `wakecycle`, the abandoned
  name-reservation release). `rm -rf`; no tracked file touched. End-to-end identity
  is `arunner` (pyproject `name`, package, console scripts, all mirrors).
- **No new runtime dependency (NFR-3):** `importlib.metadata` is stdlib; the
  `attr` dynamic reads the `__init__.py` literal at build time without importing the
  package. The engine/ticker/CLI path adds nothing.

## Tests

- Full suite (Python **3.14.6**): baseline (main `ca268d7`) **485 passed** → final
  **486 passed, 1 skipped** (+1 the new CLI-banner pin; the installed-metadata pin
  skips in the path-based env, PASSES when installed).
- Live derivation: venv `pip install -e .` → metadata + `--version` both **1.1.0**.

**Verdict: SHIP.** All three charter clauses satisfied; derivation verified live;
drift-pin extended and mutation-bitten; no `wakecycle` residue; NFR-3 intact.
