#!/usr/bin/env python3
"""arunner CLI — the lifecycle verbs (FR-53) + persist (FR-52.4).

Every verb is a THIN deterministic wrapper over an existing, tested entry point
in ``bin/`` — no new engine state:

  run <plan>       expand (FR-43) -> --check (FR-42) -> init_run + ticker
  status <run-dir> read harness_status.json + plan.json, print _format_table
                   (bin/tick.py) READ-ONLY — never advances a tick
  stop <run-dir>   drop the STOP control file (FR-10)
  resume <run-dir> run the ticker loop (rung 3) against the run-dir (FR-13);
                   --once for a single tick
  summary <run-dir> print SUMMARY.md (FR-45), or a "not done yet" notice
  new              pointer to the interactive builder (FR-52, next iteration)
  expand <file>    expand the jobs shorthand; --out writes the plan, --save
                   writes the my_run.json bundle (FR-52.4)

Invoke as ``python -m arunner <verb> …``. Stdlib only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TICKER = _ROOT / "bin" / "ticker.py"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TICK = _load("arunner_cli_tick", "bin/tick.py")
JOBS = _load("arunner_cli_jobs", "bin/jobs.py")


def _resolve_plan(doc):
    """Resolve a loaded plan doc to a canonical plan, warning (not blocking) on
    a my_run.json that has drifted from its shorthand. Returns the canonical
    plan dict."""
    if isinstance(doc, dict) and "plan" in doc and "jobs" in doc:   # my_run.json
        if JOBS.bundle_drifted(doc):
            print("arunner: WARNING - the saved plan no longer matches a fresh "
                  "expansion of its jobs (hand-edit drift); running the saved "
                  "plan as-is.", file=sys.stderr)
        return doc["plan"]
    if isinstance(doc, dict) and "jobs" in doc:                     # shorthand
        return JOBS.expand_jobs(doc)
    return doc                                                      # canonical


def prepare_run(plan_path):
    """expand -> --check -> init_run. Returns (run_dir, problems): run_dir is
    None when --check finds problems (the caller prints them). Deterministic;
    drives no ticks (so tests can drive via the settle path)."""
    plan_path = Path(plan_path).resolve()
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    plan = _resolve_plan(doc)
    # write the resolved canonical plan to a temp file for --check + init_run
    tmp = Path(tempfile.mkdtemp()) / "plan.json"
    tmp.write_text(json.dumps(plan), encoding="utf-8")
    problems = TICK.check_plan(tmp)
    if problems:
        return None, problems
    return TICK.init_run(tmp), []


def cmd_run(args) -> int:
    run_dir, problems = prepare_run(args.plan)
    if problems:
        print(TICK._format_check_report(args.plan, problems))
        return 1
    print("arunner: initialized %s" % run_dir)
    if args.no_drive:
        return 0
    ticker_args = [sys.executable, str(_TICKER)]
    ticker_args += (["--once", str(run_dir)] if args.once else [str(run_dir)])
    return subprocess.run(ticker_args).returncode


def cmd_status(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    sp = run_dir / "harness_status.json"
    pp = run_dir / "plan.json"
    if not sp.is_file():
        print("arunner: no run at %s (no harness_status.json)" % run_dir,
              file=sys.stderr)
        return 2
    status = json.loads(sp.read_text(encoding="utf-8"))
    plan = json.loads(pp.read_text(encoding="utf-8")) if pp.is_file() else {}
    terminal = bool(status.get("done"))
    # READ-ONLY: _format_table only reads (heartbeats/age for display); it never
    # advances a tick or writes the run-dir.
    print(TICK._format_table(run_dir, status, plan, terminal=terminal))
    return 0


def cmd_stop(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    (run_dir / "STOP").write_text("", encoding="utf-8")     # FR-10
    print("arunner: wrote STOP to %s; the next tick halts cleanly." % run_dir)
    return 0


def cmd_resume(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    ticker_args = [sys.executable, str(_TICKER)]
    ticker_args += (["--once", str(run_dir)] if args.once else [str(run_dir)])
    return subprocess.run(ticker_args).returncode


def cmd_summary(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    sm = run_dir / "SUMMARY.md"
    if sm.is_file():
        print(sm.read_text(encoding="utf-8"), end="")
        return 0
    print("arunner: not done yet - no SUMMARY for %s (the capstone is written "
          "on the transition into done). Try `status` or `resume`." % run_dir)
    return 0


def cmd_new(args) -> int:
    print("arunner new - interactive build. Load the arunner skill in your "
          "agent and describe your run in plain language; it assembles, "
          "previews (--check), runs, and saves the session. See TOOLKIT.md. "
          "(The full interactive builder lands in the next release.)")
    return 0


def cmd_expand(args) -> int:
    jargs = ["expand", args.file]
    if args.save:
        jargs += ["--save", args.save]
    elif args.out:
        jargs += ["--out", args.out]
    return JOBS.main(jargs)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arunner", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="verb")

    r = sub.add_parser("run", help="launch a plan / shorthand / my_run.json")
    r.add_argument("plan")
    r.add_argument("--once", action="store_true", help="single tick after init")
    r.add_argument("--no-drive", action="store_true",
                   help="init only; don't run the ticker (resume later)")

    for verb, helptext in (("status", "print the status table (read-only)"),
                           ("stop", "drop the STOP control file"),
                           ("summary", "print the SUMMARY capstone")):
        sp = sub.add_parser(verb, help=helptext)
        sp.add_argument("run_dir")

    rs = sub.add_parser("resume", help="continue an interrupted run (ticker loop)")
    rs.add_argument("run_dir")
    rs.add_argument("--once", action="store_true", help="single tick")

    sub.add_parser("new", help="interactive build (pointer; FR-52 next release)")

    ex = sub.add_parser("expand", help="expand a jobs shorthand")
    ex.add_argument("file")
    ex.add_argument("--out", default=None, help="write the expanded plan here")
    ex.add_argument("--save", default=None,
                    help="write a my_run.json (jobs + expanded plan) here")
    return p


_DISPATCH = {"run": cmd_run, "status": cmd_status, "stop": cmd_stop,
             "resume": cmd_resume, "summary": cmd_summary, "new": cmd_new,
             "expand": cmd_expand}


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.verb:
        _build_parser().print_help(sys.stderr)
        return 64
    return _DISPATCH[args.verb](args)


if __name__ == "__main__":
    sys.exit(main())
