#!/usr/bin/env python3
"""wakecycle heartbeat  -  the payload-agnostic heartbeat emit helper.

The generic heartbeat surface (FR-18..21): ``label`` is a FREE STRING
(FR-18) with no coupling to any particular payload  -  this helper is the
optional convenience SDK for the worker contract, *a job is anything that
appends JSON lines to a file*. (A vendored integration may map its own
identity into ``label`` e.g. ``2:generation``; that coupling never enters
this core.)

A worker appends single-line JSON heartbeat records to a known file so the
tick engine can track progress and detect stalls. The contract: ``status``
is the ONLY field the harness interprets; ``label`` is a short free string
it displays (column ACTIVITY) but never reads; ``data`` is an opaque object
it never reads. Subcommands:

  emit       progress record: --label <str>
             --status STARTING|IN_PROGRESS|COMPLETED|FAILED [--message]
             [--data <json-object>]
  keepalive  mid-work liveness ping: reads the CURRENT label from the LAST
             heartbeat line (the canonical position in the generic core  -
             there is no run_state.jsonl) and appends an IN_PROGRESS line,
             so the ping's label can't drift. --label overrides; no-op if
             no prior label and no --label.
  terminal   last line: --status COMPLETED|FAILED|ABANDONED
             --result-file <path> --summary <text>

Disciplines (FR-19): every value is JSON-encoded via ``json.dumps`` (never
printf-interpolated, so %/"/\\ are safe); appends are atomic ``O_APPEND``;
every line carries ``schema_version="2"`` (FR-18). Postel: the harness
READER also accepts v1 lines (``phase``/``step``); this helper only EMITS
v2.

E6 (FR-21): if the append FAILS, the helper exits NONZERO loudly (it never
swallows the error)  -  a silent worker must never look healthy. Worker
guidance: on a nonzero heartbeat exit, abort the job with a FAILED
terminal (the orchestrator will reap it as failed rather than stall).

Mode A: when neither ``--heartbeat-path`` / ``HARNESS_HEARTBEAT_PATH`` /
``WAKECYCLE_HEARTBEAT_PATH`` nor ``--task-id`` / the TASK_ID env is set AND
``--mode-a-noop`` is passed, the call silently exits 0.

Exit codes: 0 ok / mode-A no-op; 2 missing heartbeat-path or task-id;
3 bad status; 5 heartbeat write failure (E6); 64 argparse error.

Stdlib only. Cross-platform (NFR-1/3). ASCII-safe diagnostics (NFR-7).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_NAME = "wakecycle-heartbeat"
_PROGRESS_STATES = ("STARTING", "IN_PROGRESS", "COMPLETED", "FAILED")
_TERMINAL_STATES = ("COMPLETED", "FAILED", "ABANDONED")
SCHEMA_VERSION = "2"

# Env aliases (either name works).
_HB_ENV = ("WAKECYCLE_HEARTBEAT_PATH", "HARNESS_HEARTBEAT_PATH")
_TID_ENV = ("WAKECYCLE_TASK_ID", "HARNESS_TASK_ID")


def _utc_iso() -> str:
    # Clock seam (instr 018): mirror the tick engine's WAKECYCLE_NOW override
    # (epoch float) so heartbeat timestamps -- and the future wrap-adapter
    # keepalive cadence (Iteration 7) -- are unit-testable without sleeping.
    override = os.environ.get("WAKECYCLE_NOW")
    if override:
        try:
            return datetime.fromtimestamp(
                float(override), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OverflowError, OSError):
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env(names) -> Optional[str]:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def append_line(heartbeat_path: Path, obj: dict) -> None:
    """Atomic O_APPEND of one JSON-encoded NDJSON line. Raises OSError on
    a write failure (the caller turns that into the E6 loud nonzero exit)."""
    line = json.dumps(obj, separators=(",", ":"))
    if "\n" in line:
        raise ValueError("encoded heartbeat line contains a literal newline")
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(heartbeat_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def last_label(heartbeat_path: Path) -> Optional[str]:
    """Return the ``label`` of the most recent heartbeat line carrying one
    (the generic 'current position'), or None. Postel: a v1 line carries
    ``phase`` instead of ``label`` — fall back to it so the keepalive reuses
    a v1 position too. Reads with errors='replace' (NFR-7  -  external
    worker content); a malformed line is skipped, never fatal."""
    if not heartbeat_path.is_file():
        return None
    try:
        lines = heartbeat_path.read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue  # Postel: skip a malformed line, keep scanning
        if isinstance(obj, dict) and (obj.get("label") or obj.get("phase")):
            return str(obj.get("label") or obj.get("phase"))
    return None


def build_progress(*, label: str, task_id: str, status: str,
                   message: Optional[str] = None,
                   data: Optional[dict] = None,
                   ts: Optional[str] = None) -> dict:
    if status not in _PROGRESS_STATES:
        raise ValueError(
            f"status {status!r} must be one of {_PROGRESS_STATES}")
    obj = {"ts": ts or _utc_iso(), "task_id": task_id,
           "schema_version": SCHEMA_VERSION, "label": str(label),
           "status": status}
    if message is not None:
        obj["message"] = message
    if data is not None:
        obj["data"] = data
    return obj


def build_terminal(*, task_id: str, status: str, result_file: str,
                   summary: str, ts: Optional[str] = None) -> dict:
    if status not in _TERMINAL_STATES:
        raise ValueError(
            f"terminal status {status!r} must be one of {_TERMINAL_STATES}")
    return {"ts": ts or _utc_iso(), "task_id": task_id,
            "schema_version": SCHEMA_VERSION, "status": status,
            "result_file": result_file, "summary": summary}


def _resolve_io(args):
    hb = args.heartbeat_path or _env(_HB_ENV)
    tid = args.task_id or _env(_TID_ENV)
    return (hb, tid)


def _require_io(args):
    hb, tid = _resolve_io(args)
    if not hb or not tid:
        if getattr(args, "mode_a_noop", False):
            sys.exit(0)
        missing = []
        if not hb:
            missing.append("--heartbeat-path / HARNESS_HEARTBEAT_PATH")
        if not tid:
            missing.append("--task-id / HARNESS_TASK_ID")
        print(f"{_NAME}: missing required {', '.join(missing)} "
              f"(or pass --mode-a-noop)", file=sys.stderr)
        sys.exit(2)
    return (Path(hb), tid)


def _append_or_die(hb: Path, obj: dict) -> int:
    """E6: append, or exit 5 loudly. Never swallow a heartbeat failure."""
    try:
        append_line(hb, obj)
    except (OSError, ValueError) as exc:
        print(f"{_NAME}: HEARTBEAT WRITE FAILED ({exc})  -  the worker must "
              f"abort this job with a FAILED terminal; a silent worker must "
              f"never look healthy (E6).", file=sys.stderr)
        return 5
    return 0


def _parse_data(raw: Optional[str]):
    """Parse the optional --data JSON-object string. Returns None if unset;
    raises ValueError if it isn't a JSON object."""
    if raw is None:
        return None
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("--data must be a JSON object")
    return obj


def _cmd_emit(args) -> int:
    hb, tid = _require_io(args)
    try:
        data = _parse_data(getattr(args, "data", None))
        obj = build_progress(label=args.label, task_id=tid,
                             status=args.status, message=args.message,
                             data=data)
    except ValueError as exc:
        print(f"{_NAME} emit: {exc}", file=sys.stderr)
        return 3
    return _append_or_die(hb, obj)


def _cmd_keepalive(args) -> int:
    hb, tid = _require_io(args)
    label = args.label or last_label(hb)
    if label is None:
        return 0  # nothing to ping yet  -  not an error
    obj = build_progress(label=label, task_id=tid, status="IN_PROGRESS")
    return _append_or_die(hb, obj)


def _cmd_terminal(args) -> int:
    hb, tid = _require_io(args)
    try:
        obj = build_terminal(task_id=tid, status=args.status,
                            result_file=args.result_file, summary=args.summary)
    except ValueError as exc:
        print(f"{_NAME} terminal: {exc}", file=sys.stderr)
        return 3
    return _append_or_die(hb, obj)


# --- FR-40 wrap-and-run adapter ---------------------------------------------
# `wrap` turns ANY command into a conformant wakecycle job with no change to
# the command: it launches the command as its OWN CHILD (the adapter is the
# parent), redirects the child's stdout+stderr to a capture file it owns and
# tails, emits STARTING at launch, IN_PROGRESS keepalives on a TIMER-DRIVEN
# floor (NOT output-driven, so a silent job never false-STALLs), and the
# terminal COMPLETED/FAILED straight from the child's EXIT CODE (doneness is
# exit-code-only, never parsed from output).

_DEFAULT_LAUNCH_GRACE_MIN = 10
_DEFAULT_STALL_THRESHOLD_MIN = 45


def _grace_stall_secs(args) -> tuple:
    """Resolve (launch_grace_secs, stall_secs) from the args, honoring an
    EXPLICIT value via `is not None` -- an explicit 0 must not be silently
    replaced by the default (it floors to a 1s keepalive interval via
    keepalive_interval_secs, the Postel 'reject non-positive, retain floor'
    posture used for CADENCE/POOL). Iteration-7 review fold-in."""
    g = (args.launch_grace_minutes if args.launch_grace_minutes is not None
         else _DEFAULT_LAUNCH_GRACE_MIN)
    s = (args.stall_threshold_minutes if args.stall_threshold_minutes is not None
         else _DEFAULT_STALL_THRESHOLD_MIN)
    return g * 60, s * 60


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID exists (stdlib signal-0 probe).
    PermissionError means it exists but isn't ours (still alive)."""
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def _now() -> float:
    """Wall-clock epoch seconds, honoring the WAKECYCLE_NOW seam (instr 018) so
    the keepalive cadence is unit-testable without real sleeps."""
    override = os.environ.get("WAKECYCLE_NOW")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    return time.time()


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _last_output_line(capture_path: Path) -> Optional[str]:
    """The most recent non-empty line of the child's captured output, or None.
    errors='replace' (NFR-7: arbitrary external output)."""
    try:
        text = Path(capture_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for ln in reversed(text.splitlines()):
        ln = ln.strip()
        if ln:
            return ln
    return None


def keepalive_interval_secs(launch_grace_secs: float, stall_secs: float) -> float:
    """The keepalive floor (FR-40): a single interval that is BOTH within
    launch_grace (first IN_PROGRESS lands before LAUNCH-FAIL) AND <= 1/3 of the
    stall threshold (subsequent pings keep the heartbeat well under STALLED).
    Never below 1s."""
    return max(1.0, min(float(launch_grace_secs), float(stall_secs) / 3.0))


class _Keepalive:
    """Timer-driven IN_PROGRESS keepalive scheduler (FR-40).

    The decision to emit is a PURE function of (now, last_emit, interval) -- it
    does NOT depend on whether the child produced output, so a silent command
    still keepalives on cadence and never false-STALLs. ``maybe_emit(now)`` is
    the synchronous 'advance the clock to `now`, emit a keepalive if one is
    due' entry point, so the cadence is deterministically testable by feeding
    explicit clock values (no real sleeps)."""

    def __init__(self, *, hb_path: Path, task_id: str, capture_path: Path,
                 interval_secs: float, start_ts: float):
        self.hb_path = hb_path
        self.task_id = task_id
        self.capture_path = capture_path
        self.interval = max(1.0, float(interval_secs))
        self.last_emit = float(start_ts)   # STARTING was emitted at start_ts
        self.count = 0

    def due(self, now: float) -> bool:
        return (now - self.last_emit) >= self.interval

    def maybe_emit(self, now: float) -> bool:
        """Emit ONE IN_PROGRESS keepalive if due at ``now``. Returns True if it
        emitted. Label = the child's most recent output line, or a neutral
        fallback when the child has been quiet (the ping still fires)."""
        if not self.due(now):
            return False
        label = _last_output_line(self.capture_path) or "(running, no output yet)"
        append_line(self.hb_path, build_progress(
            label=label, task_id=self.task_id, status="IN_PROGRESS",
            ts=_iso_from_epoch(now)))
        self.last_emit = now
        self.count += 1
        return True


def _cmd_wrap(args) -> int:
    hb, tid = _require_io(args)
    cmd = list(getattr(args, "command", None) or [])
    if cmd and cmd[0] == "--":              # argparse REMAINDER keeps the '--'
        cmd = cmd[1:]
    if not cmd:
        print(f"{_NAME} wrap: no command given (use: wrap ... -- <cmd> [args])",
              file=sys.stderr)
        return 64
    grace_secs, stall_secs = _grace_stall_secs(args)   # honors an explicit 0
    interval = keepalive_interval_secs(grace_secs, stall_secs)
    capture_path = (Path(args.capture_file) if args.capture_file
                    else Path(hb).parent / "wrap.out")
    try:
        capture_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"{_NAME} wrap: cannot create capture dir {capture_path.parent} "
              f"({exc})", file=sys.stderr)
        return 5

    start = _now()
    # STARTING immediately. If even this can't be written, fail loud (E6): an
    # untrackable worker must never look healthy.
    if _append_or_die(hb, build_progress(
            label="wrap: %s" % " ".join(cmd), task_id=tid, status="STARTING",
            ts=_iso_from_epoch(start))):
        return 5

    # The child writes to a FILE we own (never a PIPE) -- so a chatty child can
    # never fill a pipe buffer and deadlock; we tail the file independently.
    try:
        cap = open(capture_path, "wb")
    except OSError as exc:
        print(f"{_NAME} wrap: cannot open capture file {capture_path} ({exc})",
              file=sys.stderr)
        return 5
    try:
        try:
            proc = subprocess.Popen(cmd, stdout=cap, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL)
        except OSError as exc:
            # The command itself couldn't launch -> FAILED terminal (the engine
            # reaps it as failed rather than waiting out the launch grace).
            append_line(hb, build_terminal(
                task_id=tid, status="FAILED", result_file=str(capture_path),
                summary="wrap: could not launch %r (%s)" % (cmd, exc)))
            return 1
        ka = _Keepalive(hb_path=Path(hb), task_id=tid, capture_path=capture_path,
                        interval_secs=interval, start_ts=start)
        while True:
            try:
                proc.wait(timeout=interval)
                break                       # child exited
            except subprocess.TimeoutExpired:
                ka.maybe_emit(_now())       # timer-driven; fires even if silent
    finally:
        cap.close()

    rc = proc.returncode
    terminal = "COMPLETED" if rc == 0 else "FAILED"   # doneness = EXIT CODE only
    if _append_or_die(hb, build_terminal(
            task_id=tid, status=terminal, result_file=str(capture_path),
            summary="wrap: %r exited %d" % (cmd[0], rc))):
        return 5
    return 0 if rc == 0 else 1              # adapter mirrors the child's status


# --- FR-41 tail-existing-log adapter ----------------------------------------
# For a job that writes its OWN log, `tail` watches that log (it does NOT
# capture the process's stdout), surfaces the most-recent line as the
# IN_PROGRESS label, and decides doneness by PRECEDENCE:
#   1. optional overlay -- a success/failure regex matched in NEW log lines, or
#      a sentinel file the job touches (for jobs that signal only in their log);
#   2. authoritative -- process exit (default COMPLETED on a clean exit), always
#      available when the adapter owns/watches the process.
# The engine never guesses a terminal from text; the ADAPTER emits it.


class _LogTail:
    """Incremental line reader over a growing log file (stdlib seek/tell).
    ``new_lines()`` yields only COMPLETE lines appended since the last call; a
    partial trailing line is buffered until its newline arrives, so a marker is
    never matched against half a line."""

    def __init__(self, path):
        self.path = str(path)
        self.pos = 0
        self._buf = ""

    def new_lines(self) -> list:
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self.pos)
                chunk = fh.read()
                self.pos = fh.tell()
        except OSError:
            return []
        self._buf += chunk
        out = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            out.append(line)
        return out


class _TailWatcher:
    """Doneness decision for the tail adapter. ``poll()`` is ONE synchronous
    step (no sleep, no blocking) returning 'COMPLETED'/'FAILED'/None, so the
    precedence is deterministically unit-testable by driving the inputs (write
    log lines, touch the sentinel, hand it a fake/real process)."""

    def __init__(self, *, log_file, success_re=None, failure_re=None,
                 sentinel=None, proc=None, pid=None):
        self.tail = _LogTail(log_file)
        self.success_re = success_re
        self.failure_re = failure_re
        self.sentinel = Path(sentinel) if sentinel else None
        self.proc = proc        # a Popen we own (.poll())
        self.pid = pid          # an external PID we only watch

    def poll(self):
        # (1) overlay: scan NEW log lines (failure wins over success on a line)
        for line in self.tail.new_lines():
            if self.failure_re and self.failure_re.search(line):
                return "FAILED"
            if self.success_re and self.success_re.search(line):
                return "COMPLETED"
        if self.sentinel is not None and self.sentinel.exists():
            return "COMPLETED"
        # (2) authoritative: process exit (default COMPLETED on a clean exit)
        if self.proc is not None:
            rc = self.proc.poll()
            if rc is not None:
                return "COMPLETED" if rc == 0 else "FAILED"
        elif self.pid is not None and not _pid_alive(self.pid):
            return "COMPLETED"
        return None


def _record_pid_in_lock(lock_path, pid) -> None:
    """Record the adapter's SUPERVISED child/watched PID in the run's claim
    lock, so the engine's existing dead-PID reap (A-5) backstops a sentinel/
    regex that never arrives. Best-effort; never raises."""
    try:
        lock = Path(lock_path)
        obj = json.loads(lock.read_text(encoding="utf-8")) if lock.is_file() else {}
        if not isinstance(obj, dict):
            obj = {}
        obj["pid"] = int(pid)
        tmp = lock.with_suffix(lock.suffix + ".tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        tmp.replace(lock)
    except (OSError, ValueError, TypeError):
        pass


_TAIL_POLL_SECONDS = 0.05


def _cmd_tail(args) -> int:
    hb, tid = _require_io(args)
    if not args.log_file:
        print(f"{_NAME} tail: --log-file is required", file=sys.stderr)
        return 64
    log_file = Path(args.log_file)
    cmd = list(getattr(args, "command", None) or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    grace_secs, stall_secs = _grace_stall_secs(args)
    interval = keepalive_interval_secs(grace_secs, stall_secs)
    success_re = __import__("re").compile(args.success_regex) if args.success_regex else None
    failure_re = __import__("re").compile(args.failure_regex) if args.failure_regex else None

    start = _now()
    if _append_or_die(hb, build_progress(
            label="tail: %s" % log_file.name, task_id=tid, status="STARTING",
            ts=_iso_from_epoch(start))):
        return 5

    proc = None
    watched_pid = None
    if cmd:
        # The adapter OWNS the job (it launches it). The job writes its own log;
        # we do NOT capture its stdout -- we tail --log-file.
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
        except OSError as exc:
            append_line(hb, build_terminal(
                task_id=tid, status="FAILED", result_file=str(log_file),
                summary="tail: could not launch %r (%s)" % (cmd, exc)))
            return 1
        watched_pid = proc.pid
    elif args.pid is not None:
        watched_pid = int(args.pid)

    # PID backstop: record the supervised PID in the claim lock.
    if watched_pid is not None and args.lock_file:
        _record_pid_in_lock(args.lock_file, watched_pid)

    watcher = _TailWatcher(log_file=log_file, success_re=success_re,
                           failure_re=failure_re, sentinel=args.sentinel_file,
                           proc=proc, pid=watched_pid)
    ka = _Keepalive(hb_path=Path(hb), task_id=tid, capture_path=log_file,
                    interval_secs=interval, start_ts=start)
    terminal = None
    # A pure-tail watch with no process and no sentinel/regex has no doneness
    # signal -- guard against an unbounded loop (the engine's dead-PID backstop
    # or an operator STOP handles such a job; the adapter shouldn't spin forever).
    has_signal = (proc is not None or watched_pid is not None
                  or args.sentinel_file or success_re or failure_re)
    if not has_signal:
        print(f"{_NAME} tail: no doneness signal (need a command, --pid, "
              f"--sentinel-file, or a marker regex)", file=sys.stderr)
        return 64
    while terminal is None:
        terminal = watcher.poll()
        if terminal is not None:
            break
        ka.maybe_emit(_now())
        time.sleep(_TAIL_POLL_SECONDS)

    if _append_or_die(hb, build_terminal(
            task_id=tid, status=terminal, result_file=str(log_file),
            summary="tail: %s via %s" % (
                terminal, "marker" if watcher.proc is None and watcher.pid is None
                else "process-exit/marker"))):
        return 5
    return 0 if terminal == "COMPLETED" else 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=_NAME, description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--task-id", default=None)
        sp.add_argument("--heartbeat-path", default=None)
        sp.add_argument("--mode-a-noop", action="store_true")

    em = sub.add_parser("emit", help="progress heartbeat")
    common(em)
    em.add_argument("--label", required=True,
                    help="free-string activity label (displayed, not interpreted)")
    em.add_argument("--status", required=True, choices=list(_PROGRESS_STATES))
    em.add_argument("--message", default=None)
    em.add_argument("--data", default=None,
                    help="optional opaque JSON object (harness never reads it)")

    ka = sub.add_parser("keepalive", help="liveness ping (reuses last label)")
    common(ka)
    ka.add_argument("--label", default=None,
                    help="override; default = last heartbeat's label")

    tm = sub.add_parser("terminal", help="terminal sentinel (last line)")
    common(tm)
    tm.add_argument("--status", required=True, choices=list(_TERMINAL_STATES))
    tm.add_argument("--result-file", required=True)
    tm.add_argument("--summary", required=True)

    wr = sub.add_parser("wrap", help="run a command as a child and emit its "
                                     "heartbeat stream (FR-40)")
    common(wr)
    wr.add_argument("--launch-grace-minutes", type=int, default=None,
                    help="first keepalive lands within this (default 10)")
    wr.add_argument("--stall-threshold-minutes", type=int, default=None,
                    help="keepalives fire at <= 1/3 of this (default 45)")
    wr.add_argument("--capture-file", default=None,
                    help="where to capture child stdout+stderr "
                         "(default <heartbeat-dir>/wrap.out)")
    wr.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- <command> [args...] to run")

    ta = sub.add_parser("tail", help="tail a log a job already writes and emit "
                                     "its heartbeat stream (FR-41)")
    common(ta)
    ta.add_argument("--log-file", default=None, required=False,
                    help="the log file to tail (its last line is the label)")
    ta.add_argument("--success-regex", default=None,
                    help="optional: a log line matching this -> COMPLETED")
    ta.add_argument("--failure-regex", default=None,
                    help="optional: a log line matching this -> FAILED")
    ta.add_argument("--sentinel-file", default=None,
                    help="optional: this file existing -> COMPLETED")
    ta.add_argument("--pid", type=int, default=None,
                    help="optional: watch an external PID's exit (authoritative)")
    ta.add_argument("--lock-file", default=None,
                    help="optional: claim lock to record the supervised PID in "
                         "(the engine's dead-PID reap backstop)")
    ta.add_argument("--launch-grace-minutes", type=int, default=None)
    ta.add_argument("--stall-threshold-minutes", type=int, default=None)
    ta.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- <command> [args...] to launch+own (optional)")
    return p


def main(argv=None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    parser = _build_parser()
    args = parser.parse_args(args_list)
    if args.cmd == "emit":
        return _cmd_emit(args)
    if args.cmd == "keepalive":
        return _cmd_keepalive(args)
    if args.cmd == "terminal":
        return _cmd_terminal(args)
    if args.cmd == "wrap":
        return _cmd_wrap(args)
    if args.cmd == "tail":
        return _cmd_tail(args)
    parser.print_help(sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
