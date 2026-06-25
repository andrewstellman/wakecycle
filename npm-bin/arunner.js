#!/usr/bin/env node
"use strict";
// arunner npm launcher (version tracks the Python package -- FR-34 single source).
//
// The arunner ENGINE is a Python package (`arunner` on PyPI) — pipx/pip is the
// PRIMARY install channel. This npm package is only a SECONDARY convenience: a
// thin launcher that locates a Python interpreter and execs `python -m arunner`,
// passing every argument through. It deliberately does NOT reimplement the CLI
// in Node. If the Python package isn't installed, it points you at pipx and
// exits non-zero — it never pretends to be a working standalone Node CLI.
const { spawnSync } = require("child_process");

const HINT =
  "arunner's engine is a Python package and was not found.\n" +
  "Install it (Python 3.10+):\n" +
  "  pipx install arunner            # recommended\n" +
  "  python3 -m pip install arunner\n" +
  "(The npm package is only a thin launcher for the Python CLI.)";

const args = process.argv.slice(2);

for (const py of ["python3", "python"]) {
  // Probe: this interpreter exists AND the arunner package imports.
  const probe = spawnSync(py, ["-c", "import arunner"], { stdio: "ignore" });
  if (probe.error || probe.status !== 0) continue;
  const run = spawnSync(py, ["-m", "arunner", ...args], { stdio: "inherit" });
  if (run.error) {
    console.error(HINT);
    process.exit(1);
  }
  process.exit(run.status === null ? 1 : run.status);
}

console.error(HINT);
process.exit(127);
