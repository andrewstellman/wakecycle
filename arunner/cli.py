#!/usr/bin/env python3
"""arunner CLI — the lifecycle verbs (FR-53) + persist (FR-52.4).

Every verb is a THIN deterministic wrapper over an existing, tested entry point
in the ``arunner.engine`` subpackage (shipped inside the wheel so the installed
console script works) — no new engine state:

  run <plan>       expand (FR-43) -> --check (FR-42) -> init_run + ticker
  status <run-dir> read harness_status.json + plan.json, print _format_table
                   (arunner.engine.tick) READ-ONLY — never advances a tick
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
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from arunner import __version__

# The engine lives in the package (arunner/engine/) so the installed console
# script can reach it; load by packaged file path (works in both the source
# tree and an installed wheel) and drive the ticker by that same path.
_ENGINE = Path(__file__).resolve().parent / "engine"
_TICKER = _ENGINE / "ticker.py"


def _load(name, mod):
    spec = importlib.util.spec_from_file_location(name, _ENGINE / mod)
    mod_ = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod_)
    return mod_


TICK = _load("arunner_cli_tick", "tick.py")
JOBS = _load("arunner_cli_jobs", "jobs.py")


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


# --- FR-59: read-only disk monitor (`arunner monitor`) ---------------------
# A strictly read-only sidecar: reload the externalized run state and re-render
# the SHARED `_format_table` every interval, so an operator can watch a run from
# a second terminal even while the orchestrator is blocked on a long synchronous
# subagent (C-6). It writes NOTHING, takes no `.tick.lock`, drops no control
# file, advances no tick -- safe alongside a live engine on any rung.
_ANSI_CLEAR = "\033[H\033[2J"
_MONITOR_MIN_INTERVAL = 0.05


def _monitor_now() -> float:
    """Wall-clock epoch, honoring ARUNNER_NOW (the engine's clock seam) so the
    'as of last tick' age is deterministically testable."""
    override = os.environ.get("ARUNNER_NOW")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    return time.time()


def _age_str(secs: float) -> str:
    s = max(0, int(secs))
    if s < 90:
        return "%ds" % s
    if s < 5400:
        return "%dm" % (s // 60)
    return "%dh" % (s // 3600)


def _status_age_secs(status, status_path, now) -> float:
    """Age of the run *lifecycle* state: prefer the engine's explicit per-tick
    `last_tick_wall` stamp; else the file mtime."""
    ts = status.get("last_tick_wall")
    if not isinstance(ts, (int, float)):
        try:
            ts = Path(status_path).stat().st_mtime
        except OSError:
            return 0.0
    return max(0.0, float(now) - float(ts))


def _monitor_freshness_line(run_dir, status, status_path, interval, now) -> str:
    """The monitor-OWNED honesty line printed AROUND the shared table (never
    edits `_format_table`'s body): the fast display refresh must not imply the
    lifecycle columns are fresher than the last engine tick (NFR-12)."""
    age = _age_str(_status_age_secs(status, status_path, now))
    return ("monitor: refresh %.1fs | run-state as of last tick: %s ago | "
            "ACTIVITY/HB-AGE: live (Ctrl-C to exit)" % (interval, age))


def render_monitor_frame(run_dir, interval=2.0, now=None):
    """Render ONE read-only monitor frame from disk. Returns
    (text, terminal, ok): ok=False on a transient read failure (the caller skips
    the frame and keeps the last good render). STRICTLY READ-ONLY -- the only fs
    ops are reads of harness_status.json / plan.json / heartbeats (the last
    inside `_format_table`) plus a STOP-existence check."""
    run_dir = Path(run_dir)
    sp = run_dir / "harness_status.json"
    pp = run_dir / "plan.json"
    now = _monitor_now() if now is None else now
    try:
        status = json.loads(sp.read_text(encoding="utf-8"))
        plan = json.loads(pp.read_text(encoding="utf-8")) if pp.is_file() else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, False, False                 # skip this frame, never crash
    terminal = (bool(status.get("done")) or bool(status.get("stop"))
                or (run_dir / "STOP").exists())
    table = TICK._format_table(run_dir, status, plan, terminal=terminal)
    header = _monitor_freshness_line(run_dir, status, sp, interval, now)
    return header + "\n" + table, terminal, True


def cmd_monitor(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    interval = max(_MONITOR_MIN_INTERVAL, float(args.interval))
    clear = not args.no_clear
    sp = run_dir / "harness_status.json"
    if args.once and not sp.is_file():
        print("arunner: no run at %s (no harness_status.json)" % run_dir,
              file=sys.stderr)
        return 2
    last_good = None
    waited = 0.0
    try:
        while True:
            text, terminal, ok = render_monitor_frame(run_dir, interval=interval)
            if ok:
                last_good = text
                frame = text
                waited = 0.0
            elif last_good is not None:
                frame = last_good                 # keep the last good render
            else:
                frame = "arunner monitor: waiting for run state at %s ..." % run_dir
                waited += interval
            if clear:
                sys.stdout.write(_ANSI_CLEAR)
            else:
                sys.stdout.write("\n" + ("-" * 60) + "\n")
            sys.stdout.write(frame + "\n")
            sys.stdout.flush()
            if ok and terminal:
                return 0                          # final frame rendered; exit
            if args.once:
                return 0 if ok else 0             # one frame (waiting msg is fine)
            time.sleep(interval)
    except KeyboardInterrupt:
        # leave the cursor sane; no traceback
        sys.stdout.write("\n")
        sys.stdout.flush()
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


def _job_summary(entry):
    """Per-job dispatch mode + prompt/command source, derived deterministically
    from the (expanded) entry — the confirm-gate echo the host agent renders.
    No inference here: the agent already chose the mode (FR-52 intent ladder);
    this only RENDERS it."""
    adapter = entry.get("adapter")
    if adapter == "wrap":
        return "SHELL (wrap)", "wraps: %s" % " ".join(entry.get("command") or [])
    if adapter == "tail":
        return "SHELL (tail)", "tails: %s" % (entry.get("log_path") or "")
    if entry.get("dispatch_mode") == "shell":
        return "SHELL", "worker_cmd: %s" % " ".join(entry.get("worker_cmd") or [])
    return "SUBAGENT", "in-session agent prompt"


def cmd_preview(args) -> int:
    """FR-52 step 2 (deterministic): print, per job, the dispatch mode + prompt/
    command source and the --check verdict. Exit 1 if --check fails (no clean
    'go' signal) so the host agent never confirms a broken plan."""
    plan_path = Path(args.file).resolve()
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    plan = _resolve_plan(doc)
    entries = plan.get("entries", []) if isinstance(plan, dict) else []
    print("arunner preview: %s - %d job(s), pool %s"
          % (plan_path.name, len(entries), plan.get("pool_size", "default")))
    for i, e in enumerate(entries, start=1):
        disp, src = _job_summary(e)
        print("  job %d [%s]: %s  %s" % (i, e.get("task_id", "?"), disp, src))
    import tempfile as _t
    tmp = Path(_t.mkdtemp()) / "plan.json"
    tmp.write_text(json.dumps(plan), encoding="utf-8")
    problems = TICK.check_plan(tmp)
    if problems:
        print("--check: FAILED - %d problem(s) (fix before running):" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    print("--check: OK - no problems found. Safe to run.")
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


# Plan-level config carried into the add --check probe so the new entries are
# validated against the SAME knobs the live run uses (keepalive>grace etc.).
_ADD_PROBE_KEYS = ("schema_version", "tick_interval_minutes", "pool_size",
                   "stall_threshold_minutes", "launch_grace_minutes",
                   "idle_tick_multiplier", "keepalive_seconds")


def cmd_add(args) -> int:
    """FR-57 live enqueue (stage-and-absorb): validate the new entries (--check,
    FR-42) and STAGE them to <run-dir>/incoming/ -- never mutate the live
    plan.json / harness_status.json a concurrent tick reads/writes. The next
    tick absorbs incoming/ under the lock it already holds."""
    run_dir = Path(args.run_dir)
    if not (run_dir / "harness_status.json").is_file():
        print("arunner add: not a run-dir (no harness_status.json): %s" % run_dir,
              file=sys.stderr)
        return 2
    live_plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    source = args.file or "--command"

    if args.command:
        import shlex
        cmd = shlex.split(args.command)
        if not cmd:
            print("arunner add: --command needs a command", file=sys.stderr)
            return 2
        entries = [{"target_repo": args.repo, "dispatch_mode": "shell",
                    "adapter": "wrap", "command": cmd}]
    elif args.file:
        doc = json.loads(Path(args.file).read_text(encoding="utf-8"))
        entries = list(_resolve_plan(doc).get("entries") or [])
    else:
        print("arunner add: give a plan/jobs file or --command <cmd ...>",
              file=sys.stderr)
        return 2
    if not entries:
        print("arunner add: no entries to add", file=sys.stderr)
        return 2

    # Mint task_id for any entry lacking one, numbered append-only from the live
    # entry count, so the --check pre-gate (which requires task_id) passes and
    # the absorb keeps the same ids.
    base = len(live_plan.get("entries") or [])
    for j, e in enumerate(entries):
        if isinstance(e, dict) and not e.get("task_id"):
            e["task_id"] = "added-%02d" % (base + j + 1)

    # --check PRE-GATE (FR-42): validate the new entries against the live plan's
    # knobs BEFORE anything lands in incoming/. A bad add never reaches a tick.
    probe = {k: live_plan[k] for k in _ADD_PROBE_KEYS if k in live_plan}
    probe["entries"] = entries
    if args.pool is not None:
        probe["pool_size"] = args.pool
    tmp = Path(tempfile.mkdtemp()) / "add_probe.json"
    tmp.write_text(json.dumps(probe), encoding="utf-8")
    problems = TICK.check_plan(tmp)
    if problems:
        print(TICK._format_check_report(source, problems))
        return 1

    # Stage to incoming/ (the tick absorbs it). Unique filename so concurrent
    # adds never clobber one another.
    inc = run_dir / "incoming"
    inc.mkdir(exist_ok=True)
    payload = {"entries": entries}
    if args.pool is not None:
        payload["pool_size"] = args.pool
    n = len(list(inc.glob("add-*.json")))
    staged = inc / ("add-%03d-%d.json" % (n, abs(hash(source)) % 100000))
    staged.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("arunner: staged %d entr%s to %s (absorbed on the next tick)"
          % (len(entries), "y" if len(entries) == 1 else "ies", staged))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arunner", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version="arunner %s" % __version__,
                   help="print the single-source version (arunner/__init__.py) and exit")
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

    pv = sub.add_parser("preview", help="per-job dispatch + source + --check (FR-52)")
    pv.add_argument("file")

    ad = sub.add_parser("add", help="live enqueue: stage new jobs into a running "
                                    "run-dir, absorbed next tick (FR-57)")
    ad.add_argument("run_dir")
    ad.add_argument("file", nargs="?", default=None,
                    help="a plan / jobs shorthand whose entries to add")
    ad.add_argument("--command", default=None,
                    help="add a single wrap-adapter job from a shell command "
                         "string, e.g. --command 'make test'")
    ad.add_argument("--pool", type=int, default=None,
                    help="raise the live pool_size as part of this add")
    ad.add_argument("--repo", default=".",
                    help="target_repo for a --command job (default: .)")

    mon = sub.add_parser("monitor", help="read-only sidecar: re-render the status "
                                         "table from disk on an interval (FR-59)")
    mon.add_argument("run_dir")
    mon.add_argument("--interval", type=float, default=2.0,
                     help="seconds between frames (default 2.0; floored small). "
                          "Refreshes the DISPLAY -- ACTIVITY/HB-AGE are live from "
                          "the heartbeat files, but lifecycle/counts are only as "
                          "fresh as the last engine tick (shown in the header).")
    mon.add_argument("--once", action="store_true",
                     help="render one snapshot and exit")
    mon.add_argument("--no-clear", action="store_true",
                     help="append frames with a separator instead of an ANSI "
                          "clear (dumb-terminal / piped fallback)")
    return p


_DISPATCH = {"run": cmd_run, "status": cmd_status, "stop": cmd_stop,
             "resume": cmd_resume, "summary": cmd_summary, "new": cmd_new,
             "expand": cmd_expand, "preview": cmd_preview, "add": cmd_add,
             "monitor": cmd_monitor}


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.verb:
        _build_parser().print_help(sys.stderr)
        return 64
    return _DISPATCH[args.verb](args)


if __name__ == "__main__":
    sys.exit(main())
