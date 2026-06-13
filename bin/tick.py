#!/usr/bin/env python3
"""wakecycle tick — the deterministic harness state-machine stepper.

The deterministic Python half of the harness-as-skill. The orchestrator
SKILL.md runs this once per tick; the script reads disk state, advances
the state machine, and prints a JSON envelope the agent acts on. ALL the
state logic lives here — the agent's per-tick prose is small and fixed
(run script, dispatch listed jobs, print table, schedule the next tick).

Usage:
    tick.py --init <plan-path>   # scaffold a run-dir; print its path
    tick.py <run-dir>            # run one tick; print JSON to stdout

Tick stdout JSON: {dispatch_list, status_table, next_tick_minutes, done, stop}.

Design: docs/REQUIREMENTS.md (the worker contract, the tick contract FR-4..13,
the state machine) and references/STATE_MACHINE.md. The proven patterns:
atomic writes, state-guarded transitions, cycle-as-witness, STOP read-only,
the {dispatch_list,...} dispatch JSON shape.

State machine (per run-NN; see references/STATE_MACHINE.md):

    queued ── dispatch ──▶ claimed ── heartbeat STARTING/IN_PROGRESS ──▶ running
       │                     │                                            │
       │                     │ no heartbeat past launch_grace             │ terminal sentinel
       │                     ▼                                            ▼
       │            auth_or_launch_failed (terminal)            completed | failed (terminal)
       │                                                                  │
       └── (pool slot frees on any terminal) ◀───────────────────────────┘
                              running/claimed ── heartbeat mtime > stall_threshold ──▶ stalled

`stalled` is NON-terminal and NON-killable in the MVP (no kill semantics —
documented in STATE_MACHINE.md): the slot stays held, the run keeps being
watched, and a late heartbeat can move it back to running. `done` is true
only when every run is terminal (completed / failed / auth_or_launch_failed
/ abandoned-in-results).

Idempotency is mandatory: every transition checks "already done?" before
mutating disk. Running the same tick twice in a row changes nothing but
the `cycle` witness counter. A STOP file makes the tick fully read-only.

Stdlib only. Cross-platform (no process forking, no signals).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# --- defaults (a plan may override any of these top-level keys) -------------
DEFAULT_POOL_SIZE = 3
DEFAULT_TICK_INTERVAL_MINUTES = 10
DEFAULT_STALL_THRESHOLD_MINUTES = 45      # design §Open questions #5 / Risks
DEFAULT_LAUNCH_GRACE_MINUTES = 10         # claimed + no heartbeat past this ⇒ launch failed
DEFAULT_IDLE_TICK_MULTIPLIER = 1          # >1 lengthens cadence when nothing is running

TAIL_LINES = 20
_TERMINAL_HB = ("COMPLETED", "FAILED", "ABANDONED")
# FR-21a (instruction 010): EVERY harness-known path a worker needs is
# substituted MECHANICALLY by the engine before dispatch — a worker prompt
# must NEVER ask a model to transcribe a literal path. {HARNESS_BIN} (the
# engine's own bin directory) is the placeholder for the demo-worker /
# helper location, replacing the hand-copied <HARNESS_BIN> that silently
# killed run-02 in 20260612T005833Z (username hallucinated on transcription).
_PLACEHOLDERS = ("HEARTBEAT_PATH", "TASK_ID", "RUN_DIR", "TARGET_REPO",
                 "HARNESS_BIN")
# v1.5.9 Phase 2B: shell dispatch adds {PROMPT_FILE} (the per-job prompt
# written to queue/job-NNNNN.prompt.txt for quoting/arg-length safety).
_SHELL_PLACEHOLDERS = _PLACEHOLDERS + ("PROMPT_FILE",)
# The engine knows its own bin directory (FR-21a {HARNESS_BIN}); a worker
# never transcribes it.
_HARNESS_BIN = str(Path(__file__).resolve().parent)
# Heartbeat lines: current emit is "2" (label/data); the reader still
# accepts "1" (phase/step) — Postel (FR-18/19).
SCHEMA_VERSION = "2"
# A wall-clock jump larger than this multiple of the tick interval means the
# machine slept/hibernated (E2): heartbeat ages are inflated, so stall
# marking is suppressed for one tick rather than false-STALLING.
_WALLCLOCK_JUMP_FACTOR = 4

# Terminal run states (occupy no pool slot; count toward `done`).
_TERMINAL_STATES = ("completed", "failed", "auth_or_launch_failed", "abandoned")
# States that hold a pool slot (in-flight).
_INFLIGHT_STATES = ("claimed", "running", "stalled")

# FR-21b: AUTH_OR_LAUNCH_FAILED covers more causes than auth (transcribed
# path, missing helper, bad worker_cmd) — the synthesized result + the table
# footnote carry this actionable hint, not a bare "auth failed".
_LAUNCH_FAIL_HINT = ("no heartbeat received within launch grace - check "
                     "worker-side launch: auth, helper availability, paths")
# FR-21b: long internal state names must not overflow the status-table
# column. Display-only abbreviation (the on-disk state is unchanged).
_STATE_DISPLAY = {"auth_or_launch_failed": "LAUNCH-FAIL"}


def _now() -> float:
    """Wall-clock seconds. Overridable via WAKECYCLE_NOW (epoch float)
    so stall / launch-grace logic is testable without sleeping."""
    override = os.environ.get("WAKECYCLE_NOW")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    return time.time()


def _pid_alive(pid) -> bool:
    """Cross-platform 'is this PID still running?' (Council A-5: lets stall
    detection tell a dead shell-worker process from a slow one). POSIX:
    os.kill(pid, 0). Windows: OpenProcess + exit-code probe. Unknown/bad
    pid ⇒ treated as NOT alive (conservative — a vanished process should
    free its slot)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":  # Windows
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            k = ctypes.windll.kernel32
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                k.CloseHandle(h)
        except Exception:
            return False
    # POSIX
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return False


def _lock_pid(run_dir: Path, job_id: str):
    """Return the PID recorded in claimed/<job>.lock (shell dispatch),
    or None. The lock is written by the tick engine at claim and updated
    with the real PID by the spawning tier (ticker) per A-5."""
    lock = run_dir / "claimed" / (job_id + ".lock")
    if not lock.is_file():
        return None
    try:
        return json.loads(lock.read_text(encoding="utf-8", errors="replace")).get("pid")
    except (OSError, ValueError):
        return None


def _utc_iso() -> str:
    # Derive the ISO stamp from _now() so the WAKECYCLE_NOW clock seam is
    # UNIFORM across the engine (epoch + ISO): claimed_ts/reaped_ts honor the
    # same injected clock as claimed_at, making FR-45 durations deterministic in
    # tests. No production effect without WAKECYCLE_NOW.
    return datetime.fromtimestamp(_now(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, data) -> None:
    """Atomic whole-file write (temp + rename) so a reader never sees a
    half-written status file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _log(run_dir: Path, message: str) -> None:
    """Append a line to the per-run-dir tick log (best-effort; never
    raises into the tick)."""
    try:
        with (run_dir / "harness_tick.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{_utc_iso()} {message}\n")
    except OSError:
        pass


# --- plan / config access ---------------------------------------------------

def _cfg(plan: dict, key: str, default):
    val = plan.get(key, default)
    return val if isinstance(val, type(default)) else default


# --- init -------------------------------------------------------------------

def init_run(plan_path: Path) -> Path:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    entries = plan.get("entries") or []
    if not entries:
        raise ValueError(f"plan {plan_path} has no entries[]")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Base dir is <repo>/harness_runs by default; WAKECYCLE_RUNS_DIR
    # overrides it (tests point this at a tmp dir to stay hermetic).
    base = os.environ.get("WAKECYCLE_RUNS_DIR")
    runs_root = Path(base) if base else Path(__file__).resolve().parent.parent / "harness_runs"
    run_dir = runs_root / stamp
    for sub in ("queue", "claimed", "results"):
        (run_dir / sub).mkdir(parents=True)
    runs: dict[str, dict] = {}
    for i, entry in enumerate(entries, start=1):
        run_name = "run-%02d" % i
        job_id = "job-%05d" % i
        rd = run_dir / run_name
        rd.mkdir()
        (rd / "heartbeat.ndjson").touch()
        manifest = {
            "task_id": entry.get("task_id"),
            "target_repo": entry.get("target_repo"),
            "dispatch_mode": entry.get("dispatch_mode", "subagent"),
            "run": run_name,
            "job_id": job_id,
        }
        # FR-20: a plan entry MAY point the harness at a heartbeat file the
        # job already writes (absolute). Recorded here so _heartbeat_path
        # watches it instead of the run-dir default.
        if entry.get("heartbeat_path"):
            manifest["heartbeat_path"] = entry["heartbeat_path"]
        _write_json(rd / "manifest.json", manifest)
        _write_json(run_dir / "queue" / (job_id + ".json"),
                    {"job_id": job_id, "run": run_name, "entry": entry})
        runs[run_name] = {
            "task_id": entry.get("task_id"),
            "job_id": job_id,
            "target_repo": entry.get("target_repo"),
            "state": "queued",
            "last_hb_status": None,
            "claimed_at": None,
        }
    _write_json(run_dir / "plan.json", plan)
    _write_json(run_dir / "harness_status.json", {
        "cycle": 0,
        "pool_size": _cfg(plan, "pool_size", DEFAULT_POOL_SIZE),
        "counts": _recount(runs),
        "done": False,
        "runs": runs,
    })
    _log(run_dir, f"init: {len(runs)} run(s) from {plan_path}")
    return run_dir


def _recount(runs: dict) -> dict:
    counts = {"queued": 0, "claimed": 0, "running": 0, "stalled": 0,
              "completed": 0, "failed": 0, "auth_or_launch_failed": 0,
              "abandoned": 0}
    for r in runs.values():
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return counts


# --- heartbeat reading ------------------------------------------------------

def _heartbeat_path(run_dir: Path, run_name: str) -> Path:
    """The file the engine watches for this run. Default: the run-dir's own
    ``run-NN/heartbeat.ndjson``. FR-20 (specifiable heartbeat file): if the
    plan entry declared an absolute ``heartbeat_path`` (recorded in the
    per-run manifest at init), watch THAT file instead — so the harness can
    point at a status file a pre-existing job already writes, with no change
    to the job. The result/manifest layout is unaffected."""
    default = run_dir / run_name / "heartbeat.ndjson"
    mf = run_dir / run_name / "manifest.json"
    if mf.is_file():
        try:
            override = json.loads(
                mf.read_text(encoding="utf-8", errors="replace")
            ).get("heartbeat_path")
        except (OSError, ValueError):
            override = None
        if override:
            return Path(override)
    return default


def _tail(hb: Path) -> list[str]:
    # v1.5.9 [Phase 2 prep] / 189-class: heartbeat.ndjson is EXTERNAL
    # content (worker-written), so the read uses errors="replace" — a
    # stray non-UTF-8 byte from a worker must not crash the tick on a
    # Windows cp1252 host (the 185/189/190 hazard chain). The substring
    # liveness matching downstream is unaffected by a replacement char.
    if not hb.exists():
        return []
    return [ln for ln in hb.read_text(encoding="utf-8", errors="replace")
            .splitlines()[-TAIL_LINES:]
            if ln.strip()]


def _status_of_line(ln: str):
    """The ``status`` FIELD of one heartbeat line (the ONLY field the harness
    interprets, FR-18), JSON-parsed — NEVER a substring scan of the raw line.

    This is load-bearing for the FR-40/41 adapters: they surface arbitrary
    child output (a wrapped build that prints "FAILED", a tailed log line
    containing "COMPLETED") as the display ``label``. A substring scan would
    mistake that text for a terminal sentinel and mis-reap the run; reading the
    status FIELD keeps doneness sourced from the worker's declared status only.
    None for a malformed line (Postel: skipped) or one with no string status."""
    try:
        obj = json.loads(ln)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict):
        st = obj.get("status")
        return st if isinstance(st, str) else None
    return None


def _hb_observe(hb: Path):
    """Return (has_any, last_status_keyword, activity, mtime) for a
    heartbeat file. The status keyword is the ``status`` FIELD of the last
    line (JSON-parsed, never substring-matched — so a display ``label`` that
    happens to contain a sentinel word is never read as a status). ``activity``
    is the v2 ``label`` of that line, falling back to the v1 ``phase`` (Postel:
    read both) — display-only, never interpreted."""
    lines = _tail(hb)
    if not lines:
        return (False, None, None, None)
    last = lines[-1]
    status = _status_of_line(last)
    if status not in (*_TERMINAL_HB, "IN_PROGRESS", "STARTING"):
        status = None
    activity = None
    if '"label":' in last or '"phase":' in last:
        try:
            obj = json.loads(last)
            activity = obj.get("label") or obj.get("phase")
        except (ValueError, TypeError):
            activity = None
    try:
        mtime = hb.stat().st_mtime
    except OSError:
        mtime = None
    return (True, status, activity, mtime)


def _count_malformed(hb: Path) -> int:
    """Count non-empty heartbeat lines that aren't valid JSON. The reader
    SKIPS these (Postel: liberal in what it accepts; a malformed line is
    never fatal) — this is only for the non-fatal warning in _advance."""
    bad = 0
    for ln in _tail(hb):
        try:
            json.loads(ln)
        except ValueError:
            bad += 1
    return bad


def _terminal_status_of(hb: Path):
    """If any heartbeat line's STATUS FIELD is a terminal sentinel, return it
    (COMPLETED / FAILED / ABANDONED); else None. Scans the whole tail so a
    terminal line followed by nothing is still caught. Reads the status field
    (not a substring) so an adapter's free-text label never mis-reaps a run."""
    for ln in _tail(hb):
        st = _status_of_line(ln)
        if st in _TERMINAL_HB:
            return st
    return None


def _result_meta(hb: Path) -> dict:
    """Best-effort parse of the terminal sentinel line (located by its status
    FIELD) for result_file / summary (display + results sidecar). Never
    raises."""
    for ln in reversed(_tail(hb)):
        if _status_of_line(ln) in _TERMINAL_HB:
            try:
                obj = json.loads(ln)
                return {"result_file": obj.get("result_file"),
                        "summary": obj.get("summary"),
                        "status": obj.get("status")}
            except (ValueError, TypeError):
                return {}
    return {}


# --- the tick ---------------------------------------------------------------

def _dispatch_prompt(entry: dict, run_dir: Path, run_name: str) -> str:
    values = {
        "HEARTBEAT_PATH": str(_heartbeat_path(run_dir, run_name)),
        "TASK_ID": str(entry.get("task_id", "")),
        "RUN_DIR": str(run_dir / run_name),
        "TARGET_REPO": str(entry.get("target_repo", "")),
        "HARNESS_BIN": _HARNESS_BIN,
    }
    prompt = entry.get("worker_prompt", "")
    for key in _PLACEHOLDERS:
        prompt = prompt.replace("{%s}" % key, values[key])
    return prompt


def _move_to_results(run_dir: Path, run: dict, terminal_status: str,
                     hb: Path) -> bool:
    """Move the claimed job file to results/ as a terminal sentinel.
    Idempotent + GUARDED (carry-forward A-F6): if the claimed file is
    externally absent, we DON'T silently pretend success — we synthesize
    the result record from the heartbeat and log the anomaly, but the
    transition still completes (the heartbeat is the source of truth for
    terminal-ness). Returns True if a result file now exists."""
    job_id = run["job_id"]
    result_path = run_dir / "results" / (job_id.replace("job-", "result-") + ".json")
    if result_path.exists():
        return True  # already reaped — idempotent no-op
    src = run_dir / "claimed" / (job_id + ".json")
    meta = _result_meta(hb)
    record = {
        "job_id": job_id,
        "run": run.get("job_id") and run_name_of(run),
        "task_id": run.get("task_id"),
        "terminal_status": terminal_status,
        "result_file": meta.get("result_file"),
        "summary": meta.get("summary"),
        "reaped_ts": _utc_iso(),
    }
    if src.exists():
        # Capture the claimed manifest, then replace the file with the
        # terminal record at the results path.
        try:
            record["claimed"] = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record["claimed"] = None
        _write_json(result_path, record)
        src.unlink()
    else:
        # GUARD: claimed file vanished out from under us. Don't fabricate a
        # clean success — record the anomaly, but honor the heartbeat's
        # terminal verdict so the run can still leave the in-flight set.
        record["anomaly"] = "claimed_job_file_absent_at_reap"
        _write_json(result_path, record)
    # best-effort lock cleanup
    lock = run_dir / "claimed" / (job_id + ".lock")
    if lock.exists():
        try:
            lock.unlink()
        except OSError:
            pass
    return True


def run_name_of(run: dict) -> str:
    return run.get("_run_name", "")


# --- control-file convention (FR-35) ----------------------------------------
# A fixed, CLOSED set read at the run-dir root at the top of each tick. STOP is
# handled by the not-stopped gate in tick() (so STOP stays fully read-only,
# FR-10); the rest are dispatched here in a FIXED precedence. Only PAUSE/RESUME
# have handlers this iteration; CANCEL/CADENCE/POOL/POLL-NOW are recognized
# (reserved) and slot in as handlers in later iterations -- recognized-but-
# unhandled, never crashing or mis-firing.
_CONTROL_ORDER = ("CANCEL", "PAUSE", "RESUME", "CADENCE", "POOL", "POLL-NOW")
_ONE_SHOT_CONTROLS = ("POLL-NOW", "CANCEL")      # consumed after firing once
_STICKY_CONTROLS = ("PAUSE", "RESUME", "CADENCE", "POOL")  # persist to status
_ALL_CONTROLS = ("STOP",) + _CONTROL_ORDER
# a "control-style" filename: ALL-CAPS, no extension (the naming convention) --
# used only to WARN on a stray look-alike (Postel), never to act on it.
_CONTROL_NAME_RE = __import__("re").compile(r"^[A-Z][A-Z0-9-]*$")


def _consume_control(run_dir: Path, name: str) -> None:
    """Delete a control file after its value has been applied/persisted
    (sticky) or it has fired (one-shot). Best-effort; never raises."""
    try:
        (run_dir / name).unlink()
    except OSError:
        pass


def _read_control_value(run_dir: Path, name: str):
    """The VALUE CHANNEL (FR-37): a value-carrying control (CADENCE/POOL, and
    CANCEL in Iter 5) reads its argument from the control file's BODY -- e.g. a
    `CADENCE` file containing `5`. Returns the stripped body, or None if the
    file is unreadable/empty. Never raises (Postel)."""
    try:
        text = (run_dir / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _parse_positive_int(raw):
    """Parse a control value as a positive integer, or None if missing,
    unparseable, or non-positive (the caller warns + retains the prior)."""
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _effective_interval(status: dict, plan: dict) -> int:
    """FR-37 cadence asymmetry: a CADENCE override LAYERS OVER the per-tick
    plan re-read -- the override wins when present, else the plan value. This
    is read both before controls (to honor a *persisted* override from an
    earlier tick) and again after (in case CADENCE was set *this* tick)."""
    override = status.get("tick_interval_override")
    if isinstance(override, int) and override > 0:
        return override
    return _cfg(plan, "tick_interval_minutes", DEFAULT_TICK_INTERVAL_MINUTES)


def _ctl_pause(run_dir: Path, status: dict, warnings: list) -> None:
    status["paused"] = True                      # sticky: persisted to status
    _consume_control(run_dir, "PAUSE")


def _ctl_resume(run_dir: Path, status: dict, warnings: list) -> None:
    status["paused"] = False
    _consume_control(run_dir, "RESUME")


def _ctl_cadence(run_dir: Path, status: dict, warnings: list) -> None:
    """FR-37: persist a tick-interval override that LAYERS OVER the plan
    re-read (never edits plan.json). Non-positive/unparseable -> warn, retain
    prior."""
    raw = _read_control_value(run_dir, "CADENCE")
    n = _parse_positive_int(raw)
    if n is None:
        warnings.append("CADENCE value %r invalid (want a positive integer of "
                        "minutes); cadence unchanged" % (raw,))
    else:
        status["tick_interval_override"] = n     # sticky: layered in tick()
    _consume_control(run_dir, "CADENCE")


def _ctl_pool(run_dir: Path, status: dict, warnings: list) -> None:
    """FR-37: write back the sticky `pool_size` (same field --init sets).
    Raising it back-fills dispatch next tick (capped at the new pool); lowering
    below the in-flight count is honored as slots drain -- dispatch is gated by
    pool but reaping never is, so a running worker is NEVER killed.
    Non-positive/unparseable -> warn, retain prior."""
    raw = _read_control_value(run_dir, "POOL")
    n = _parse_positive_int(raw)
    if n is None:
        warnings.append("POOL value %r invalid (want a positive integer); "
                        "pool_size unchanged" % (raw,))
    else:
        status["pool_size"] = n
    _consume_control(run_dir, "POOL")


_POLL_NOW_CADENCE_MINUTES = 1   # the immediate/minimum cadence POLL-NOW forces


def _ctl_poll_now(run_dir: Path, status: dict, warnings: list) -> None:
    """FR-38: a one-shot forced tick -- the file-based "run another tick now".
    Signals tick() (via a TRANSIENT flag, popped before persist, never sticky)
    to collapse next_tick_minutes to the immediate minimum, then consumes the
    file. Does NOT pierce PAUSE (FR-35 precedence; applied last): while paused
    it is inert and LEFT on disk to fire after RESUME -- paused dominates."""
    if status.get("paused"):
        return                          # inert; wait for RESUME (not consumed)
    status["_poll_now"] = True          # transient: read+popped in tick()
    _consume_control(run_dir, "POLL-NOW")


_RUN_ID_RE = __import__("re").compile(r"^(?:run-)?0*([0-9]+)$", __import__("re").I)


def _parse_run_id(raw):
    """Normalize a CANCEL value to a canonical ``run-NN`` id, or None if
    unparseable (Postel). Accepts ``run-02`` / ``run-2`` / a bare ``2``."""
    if raw is None:
        return None
    m = _RUN_ID_RE.match(str(raw).strip())
    if not m:
        return None
    return "run-%02d" % int(m.group(1))


def _ctl_cancel(run_dir: Path, status: dict, warnings: list) -> None:
    """FR-39: mark a named run ``abandoned`` (terminal) via the SAME synthesis
    path the genuine FAILED reap uses (`_synthesize_failure`) -- so the run
    stays auditable in results/ and frees its pool slot (it leaves
    _INFLIGHT_STATES). One-shot + value-carrying (run id in the file body).

    Safety (this is the load-bearing control):
      * NEVER un-terminals a finished run -- an already-terminal target
        (completed/failed/abandoned) is a consumed no-op with a warning. This
        guard runs BEFORE _synthesize_failure, and _synthesize_failure's own
        result_path early-return is a second layer (idempotent second CANCEL).
      * Unknown run id / unparseable value -> consumed no-op, warned (Postel).
      * The worker is NOT killed (§8): we free the slot and stop watching; a
        detached orphan, if any, runs to its own terminal. Because the run is
        already `abandoned` (terminal), a later orphan heartbeat does not
        re-reap or resurrect it (_advance skips terminal states). So the true
        running-process count may briefly exceed pool_size by design."""
    raw = _read_control_value(run_dir, "CANCEL")
    run_id = _parse_run_id(raw)
    runs = status.get("runs", {})
    if run_id is None:
        warnings.append("CANCEL value %r unparseable (want a run id like "
                        "'run-02'); ignored" % (raw,))
    elif run_id not in runs:
        warnings.append("CANCEL %s: unknown run; no-op" % run_id)
    elif runs[run_id]["state"] in _TERMINAL_STATES:
        warnings.append("CANCEL %s: already %s (terminal); no-op -- not "
                        "un-terminaled" % (run_id, runs[run_id]["state"]))
    else:
        r = runs[run_id]
        _synthesize_failure(run_dir, r, "abandoned",
                            "cancelled by CANCEL control")
        r["state"] = "abandoned"        # leaves _INFLIGHT_STATES -> frees a slot
        _log(run_dir, "%s: ABANDONED (CANCEL control)" % run_id)
    _consume_control(run_dir, "CANCEL")  # one-shot: always consumed


# The handler registry IS the extension point: Iterations 3-5 register CANCEL /
# CADENCE / POOL / POLL-NOW here without touching tick(). A name in
# _CONTROL_ORDER with no handler is recognized-but-unhandled (left on disk).
_CONTROL_HANDLERS = {"PAUSE": _ctl_pause, "RESUME": _ctl_resume,
                     "CADENCE": _ctl_cadence, "POOL": _ctl_pool,
                     "POLL-NOW": _ctl_poll_now, "CANCEL": _ctl_cancel}


def _apply_controls(run_dir: Path, status: dict) -> list:
    """Read + apply the closed control set in fixed precedence. MUST be called
    only inside the not-stopped path (the caller's STOP gate guarantees a STOP
    tick reads/consumes nothing -- FR-10 read-only). Mutations to ``status``
    are persisted by the caller's single atomic write. Returns warning strings
    (Postel: unknown / unhandled never wedge the machine)."""
    warnings: list = []
    for name in _CONTROL_ORDER:                  # precedence order, explicit
        if not (run_dir / name).is_file():
            continue
        handler = _CONTROL_HANDLERS.get(name)
        if handler is None:
            # reserved for a later iteration; recognize, don't act, don't crash
            continue
        handler(run_dir, status, warnings)
    # Postel: a stray control-style file that isn't a recognized control is
    # ignored with a warning -- never acted on, never wedges.
    try:
        for p in run_dir.iterdir():
            if (p.is_file() and _CONTROL_NAME_RE.match(p.name)
                    and p.name not in _ALL_CONTROLS):
                warnings.append("unknown control file %r ignored" % p.name)
    except OSError:
        pass
    return warnings


# --- FR-45 SUMMARY roll-up --------------------------------------------------
SUMMARY_SCHEMA_VERSION = "1"


def _iso_to_epoch(iso):
    """Parse a _utc_iso() timestamp back to epoch seconds, or None."""
    if not iso:
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _read_result_record(run_dir: Path, job_id):
    """The results/result-NNNNN.json record for a job_id, or None."""
    if not job_id:
        return None
    rp = run_dir / "results" / (str(job_id).replace("job-", "result-") + ".json")
    try:
        return json.loads(rp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _md_cell(v) -> str:
    if v is None:
        return "-"
    return str(v).replace("|", "\\|").replace("\n", " ")


def _render_summary_md(payload: dict) -> str:
    lines = ["# wakecycle run summary",
             "",
             "Run-dir: `%s`  -  generated %s"
             % (payload["run_dir"], payload["generated_ts"]),
             "",
             "| Run | Task | State | Duration | Result file | Summary |",
             "|-----|------|-------|----------|-------------|---------|"]
    for j in payload["jobs"]:
        dur = "-" if j["duration_seconds"] is None else "%ss" % j["duration_seconds"]
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            j["run"], _md_cell(j["task_id"]), j["state"], dur,
            _md_cell(j["result_file"]), _md_cell(j["summary"])))
    c = payload["counts"]
    lines += ["",
              "**Totals** - completed: %d, failed: %d, abandoned: %d, "
              "auth/launch-failed: %d (of %d job(s))" % (
                  c.get("completed", 0), c.get("failed", 0),
                  c.get("abandoned", 0), c.get("auth_or_launch_failed", 0),
                  len(payload["jobs"]))]
    return "\n".join(lines) + "\n"


def _write_summary(run_dir: Path, status: dict) -> None:
    """FR-45: write SUMMARY.md (human) + summary.json (machine, schema_version'd)
    capstones to the run-dir, sourced ENTIRELY from on-disk records (no new
    tracking): per-job terminal state from status["runs"], result_file/summary
    from results/, durations from claimed_at -> the result's reaped_ts, counts
    from the recount. The CALLER guards this to the done-transition so a
    post-done re-tick stays cycle-only (FR-6)."""
    runs = status.get("runs", {})
    jobs = []
    for name in sorted(runs):
        r = runs[name]
        rec = _read_result_record(run_dir, r.get("job_id"))
        claimed_at = r.get("claimed_at")
        reaped = _iso_to_epoch((rec or {}).get("reaped_ts"))
        # reaped_ts is second-granular (_utc_iso truncates) while claimed_at is
        # a sub-second float; floor the claim to the second so both share
        # granularity and a sub-second run reads 0.0s, never a spurious negative.
        duration = (max(0.0, round(reaped - int(claimed_at), 1))
                    if isinstance(claimed_at, (int, float)) and reaped is not None
                    else None)
        jobs.append({
            "run": name,
            "task_id": r.get("task_id"),
            "state": r.get("state"),
            "result_file": (rec or {}).get("result_file"),
            "summary": (rec or {}).get("summary"),
            "duration_seconds": duration,
        })
    payload = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "run_dir": run_dir.name,
        "done": True,
        "generated_ts": _utc_iso(),
        "counts": dict(status.get("counts", {})),
        "jobs": jobs,
    }
    _write_json(run_dir / "summary.json", payload)
    (run_dir / "SUMMARY.md").write_text(_render_summary_md(payload), encoding="utf-8")


def tick(run_dir: Path) -> dict:
    status = json.loads((run_dir / "harness_status.json").read_text(encoding="utf-8"))
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    runs = status["runs"]
    for name, r in runs.items():
        r["_run_name"] = name  # transient, stripped before write

    pool_size = status.get("pool_size") or _cfg(plan, "pool_size", DEFAULT_POOL_SIZE)
    tick_interval = _effective_interval(status, plan)  # honors a persisted CADENCE override
    stall_secs = _cfg(plan, "stall_threshold_minutes",
                      DEFAULT_STALL_THRESHOLD_MINUTES) * 60
    grace_secs = _cfg(plan, "launch_grace_minutes",
                      DEFAULT_LAUNCH_GRACE_MINUTES) * 60
    idle_mult = _cfg(plan, "idle_tick_multiplier", DEFAULT_IDLE_TICK_MULTIPLIER)
    entries = {"run-%02d" % i: e
               for i, e in enumerate(plan.get("entries") or [], start=1)}
    now = _now()

    stop = (run_dir / "STOP").exists()
    dispatch_list: list[dict] = []
    poll_now = False                     # FR-38: set by a POLL-NOW control (not paused)

    # E2: a wall-clock gap since the last tick much larger than the cadence
    # means the machine slept/hibernated — heartbeat ages are inflated, so
    # suppress STALLED marking for this one tick.
    last_wall = status.get("last_tick_wall")
    suppress_stall = bool(
        last_wall is not None
        and (now - last_wall) > max(stall_secs, tick_interval * 60)
        * _WALLCLOCK_JUMP_FACTOR)

    if not stop:
        # Controls are read/consumed ONLY here (inside the not-stopped path),
        # so a STOP tick stays fully read-only (FR-10). PAUSE/RESUME persist a
        # `paused` flag to status; while paused, no NEW dispatch happens but
        # in-flight workers keep running and are still reaped (FR-36).
        for w in _apply_controls(run_dir, status):
            _log(run_dir, "CONTROL WARN: " + w)
        paused = bool(status.get("paused"))
        # FR-37: CADENCE/POOL are the first VALUE-carrying controls -- their
        # effect lives in fields tick() itself reads. Re-read after applying so
        # THIS tick honors them: POOL back-fills dispatch up to the new pool;
        # the CADENCE override governs next_tick_minutes. (PAUSE/RESUME needed
        # no such wiring -- their `paused` effect already gated _dispatch.)
        pool_size = status.get("pool_size") or pool_size
        tick_interval = _effective_interval(status, plan)
        # FR-38: POLL-NOW signalled a forced tick (only when NOT paused -- the
        # handler is inert under PAUSE). Read+pop the transient flag here, BEFORE
        # the persist below, so it never becomes sticky (one-shot).
        poll_now = bool(status.pop("_poll_now", False))
        try:
            _advance(run_dir, runs, now, stall_secs, grace_secs, suppress_stall)
            if not paused:
                dispatch_list = _dispatch(run_dir, runs, entries, pool_size, now)
        except Exception:  # never let a transition crash the loop
            _log(run_dir, "TICK ERROR:\n" + traceback.format_exc())
        if suppress_stall:
            _log(run_dir, f"E2: wall-clock jump ({int(now - last_wall)}s since "
                          f"last tick) — stall marking suppressed this tick")
        was_done = bool(status.get("done"))     # the persisted (pre-tick) value
        status["counts"] = _recount(runs)
        status["done"] = all(r["state"] in _TERMINAL_STATES for r in runs.values())
        status["cycle"] = status.get("cycle", 0) + 1
        status["last_tick_wall"] = now
        # FR-45: write the SUMMARY capstones on the TRANSITION into done (or to
        # backfill if a prior write was lost). The guard keeps a post-done
        # idempotent re-tick cycle-only (FR-6): an already-done run with SUMMARY
        # present is NOT rewritten.
        if status["done"] and (not was_done or not (
                (run_dir / "SUMMARY.md").exists()
                and (run_dir / "summary.json").exists())):
            _write_summary(run_dir, status)
        # FR-38: POLL-NOW collapses the next cadence to the immediate minimum,
        # overriding whatever _next_cadence would return (idle multiplier OR a
        # CADENCE override from Iter 3) for this one envelope. Persist it so the
        # chosen cadence is auditable on disk (and observable by the checker).
        next_minutes = (_POLL_NOW_CADENCE_MINUTES if poll_now
                        else _next_cadence(status, tick_interval, idle_mult))
        status["next_tick_minutes"] = next_minutes
        # strip transient field, then persist
        for r in runs.values():
            r.pop("_run_name", None)
        _write_json(run_dir / "harness_status.json", status)
    else:
        for r in runs.values():
            r.pop("_run_name", None)
        # STOP tick is read-only: don't re-persist. Report the cadence the
        # already-persisted state implies (no POLL-NOW on a STOP tick).
        next_minutes = _next_cadence(status, tick_interval, idle_mult)

    done = status["done"]
    table = _format_table(run_dir, status, plan, terminal=(done or stop))
    return {
        "dispatch_list": dispatch_list,
        "status_table": table,
        "next_tick_minutes": next_minutes,
        "done": done,
        "stop": stop,
        "paused": bool(status.get("paused")),
    }


def _advance(run_dir, runs, now, stall_secs, grace_secs, suppress_stall=False):
    """Reap terminals, detect stalls and launch failures. Mutates run
    state + disk. Every transition is guarded for idempotency.

    ``suppress_stall`` (E2): set when this tick detected a wall-clock jump
    (machine slept/hibernated) — heartbeat ages are inflated this tick, so
    STALLED marking is skipped to avoid false stalls."""
    for name, r in runs.items():
        state = r["state"]
        if state in _TERMINAL_STATES:
            continue
        hb = _heartbeat_path(run_dir, name)
        has_any, last_status, _activity, mtime = _hb_observe(hb)
        if has_any:
            r["last_hb_status"] = last_status
        # Postel: a malformed (non-JSON) heartbeat line is SKIPPED by the
        # reader, never fatal — but surface it as a non-fatal warning so an
        # operator can see a worker is writing garbage (FR-19).
        bad = _count_malformed(hb)
        if bad:
            _log(run_dir, f"{name}: WARN {bad} malformed heartbeat line(s) "
                          f"skipped (Postel: liberal accept, non-fatal)")
        # 1. terminal sentinel ⇒ reap
        terminal = _terminal_status_of(hb)
        if terminal is not None:
            if _move_to_results(run_dir, r, terminal, hb):
                r["state"] = "failed" if terminal in ("FAILED", "ABANDONED") else "completed"
                _log(run_dir, f"{name}: reaped → {r['state']} ({terminal})")
            continue
        # 1b. shell dispatch with a recorded-but-dead PID and no terminal
        #     heartbeat ⇒ the worker process died without finishing (A-5).
        #     Checked BEFORE the time-based grace/stall paths so a dead
        #     process is failed fast and precisely (no waiting out the
        #     launch grace). A recorded pid of None (just claimed, not yet
        #     spawned) is skipped.
        pid = _lock_pid(run_dir, r["job_id"])
        if pid is not None and not _pid_alive(pid):
            _synthesize_failure(run_dir, r, "failed",
                                f"shell worker process {pid} exited without a "
                                f"terminal heartbeat")
            r["state"] = "failed"
            _log(run_dir, f"{name}: FAILED (dead shell PID {pid}, no terminal)")
            continue
        # 2. claimed + no heartbeat past launch grace ⇒ launch failed
        if state == "claimed" and not has_any:
            claimed_at = r.get("claimed_at")
            if claimed_at is None:
                # Self-heal a claimed run with no claimed_at (hand-edit /
                # partial-advance anomaly, panelist A-F4): start the grace
                # clock now instead of being permanently immune to it.
                r["claimed_at"] = now
            elif (now - claimed_at) > grace_secs:
                _synthesize_failure(run_dir, r,
                                    "auth_or_launch_failed",
                                    _LAUNCH_FAIL_HINT)
                r["state"] = "auth_or_launch_failed"
                _log(run_dir, f"{name}: AUTH_OR_LAUNCH_FAILED (no heartbeat)")
            continue
        # 3. heartbeat present ⇒ running; stale heartbeat ⇒ stalled
        if has_any:
            if mtime is None:
                # Heartbeat exists but couldn't be stat'd (transient OS
                # race, panelist B-F3). Be CONSERVATIVE: never recover to
                # running off an unknowable mtime — leave the state as-is so
                # a genuine stall isn't masked. The next tick re-evaluates.
                pass
            elif (now - mtime) > stall_secs and not suppress_stall:
                if r["state"] != "stalled":
                    r["state"] = "stalled"
                    _log(run_dir, f"{name}: STALLED (heartbeat age "
                                  f"{int(now - mtime)}s > stall threshold "
                                  f"{int(stall_secs)}s)")
            else:
                # fresh heartbeat (or stall suppressed by an E2 wall-clock
                # jump this tick) — (re)mark running, recovering from stalled
                if r["state"] in ("claimed", "stalled"):
                    r["state"] = "running"


def _resolve_template(tpl: str, values: dict) -> str:
    out = tpl
    for key, val in values.items():
        out = out.replace("{%s}" % key, val)
    return out


_VALID_ADAPTERS = ("wrap", "tail")


def _adapter_worker_cmd(entry: dict):
    """FR-41 selector: synthesize the shell worker_cmd for an entry that
    declares ``adapter: "wrap"|"tail"`` — so the operator declares intent in
    ONE place (the command for wrap, or the log path + optional markers for
    tail) and never hand-wires the heartbeat.py invocation. Returns a token
    list (with placeholders the dispatch resolver fills), or None when no
    adapter is declared."""
    adapter = entry.get("adapter")
    if not adapter:
        return None
    helper = ["python3", "{HARNESS_BIN}/heartbeat.py", str(adapter),
              "--task-id", "{TASK_ID}", "--heartbeat-path", "{HEARTBEAT_PATH}"]
    if adapter == "wrap":
        return helper + ["--"] + [str(t) for t in (entry.get("command") or [])]
    if adapter == "tail":
        out = helper + ["--log-file", str(entry.get("log_path", "")),
                        "--lock-file", "{LOCK_FILE}"]
        if entry.get("success_regex"):
            out += ["--success-regex", str(entry["success_regex"])]
        if entry.get("failure_regex"):
            out += ["--failure-regex", str(entry["failure_regex"])]
        if entry.get("sentinel_file"):
            out += ["--sentinel-file", str(entry["sentinel_file"])]
        if entry.get("pid") is not None:
            out += ["--pid", str(entry["pid"])]
        if entry.get("command"):
            out += ["--"] + [str(t) for t in entry["command"]]
        return out
    return None


def _dispatch(run_dir, runs, entries, pool_size, now) -> list[dict]:
    """Emit dispatch entries for queued runs while a pool slot is free.
    Guarded: a run already past queued is never re-dispatched.

    dispatch_mode == "subagent" (default): the entry carries a resolved
    ``worker_prompt`` for the orchestrator agent to launch via its
    subagent tool. dispatch_mode == "shell": the prompt is written to
    queue/job-NNNNN.prompt.txt (quoting/arg-length safety, FR-15) and the
    entry carries a resolved ``worker_cmd`` argv for the ticker to Popen
    detached; the placeholder block adds {PROMPT_FILE}."""
    inflight = sum(1 for r in runs.values() if r["state"] in _INFLIGHT_STATES)
    out: list[dict] = []
    for name in sorted(runs):
        r = runs[name]
        if r["state"] != "queued":
            continue
        if inflight >= pool_size:
            break
        entry = entries[name]
        adapter_cmd = _adapter_worker_cmd(entry)   # FR-41: synthesized if adapter set
        mode = "shell" if adapter_cmd is not None else entry.get("dispatch_mode", "subagent")
        values = {
            "HEARTBEAT_PATH": str(_heartbeat_path(run_dir, name)),
            "TASK_ID": str(entry.get("task_id", "")),
            "RUN_DIR": str(run_dir / name),
            "TARGET_REPO": str(entry.get("target_repo", "")),
            "HARNESS_BIN": _HARNESS_BIN,
        }
        src = run_dir / "claimed" / (r["job_id"] + ".json")
        qsrc = run_dir / "queue" / (r["job_id"] + ".json")
        if qsrc.exists() and not src.exists():
            qsrc.replace(src)
            _write_json(run_dir / "claimed" / (r["job_id"] + ".lock"), {
                "task_id": r["task_id"],
                "claimed_ts": _utc_iso(),
                "dispatched_by": "wakecycle_tick",
                "dispatch_mode": mode,
                "pid": None,
            })
        r["state"] = "claimed"
        r["claimed_at"] = now
        r["dispatch_mode"] = mode
        inflight += 1
        prompt = _resolve_template(entry.get("worker_prompt", ""), values)
        if mode == "shell":
            # Write the prompt to a file (quoting/arg-length safety) and
            # resolve the worker_cmd template; the ticker Popens it detached.
            prompt_file = run_dir / "queue" / (r["job_id"] + ".prompt.txt")
            prompt_file.write_text(prompt, encoding="utf-8")
            lock_file = run_dir / "claimed" / (r["job_id"] + ".lock")
            sh_values = dict(values, PROMPT_FILE=str(prompt_file),
                             LOCK_FILE=str(lock_file))   # FR-41 PID backstop
            cmd_template = (adapter_cmd if adapter_cmd is not None
                            else (entry.get("worker_cmd") or []))
            worker_cmd = [_resolve_template(tok, sh_values)
                          for tok in cmd_template]
            out.append({
                "run": name, "task_id": r["task_id"],
                "dispatch_mode": "shell",
                "worker_cmd": worker_cmd,
                "prompt_file": str(prompt_file),
                "heartbeat_path": values["HEARTBEAT_PATH"],
                "run_dir": values["RUN_DIR"],
                "target_repo": values["TARGET_REPO"],
                "auth_check": entry.get("auth_check"),
            })
        else:
            out.append({
                "run": name, "task_id": r["task_id"],
                "dispatch_mode": "subagent",
                "worker_prompt": prompt,
            })
        _log(run_dir, f"{name}: dispatched (claimed, {mode})")
    return out


def _synthesize_failure(run_dir, r, terminal_state, reason):
    """Write a results sentinel for a run that failed WITHOUT a worker
    terminal heartbeat (e.g. dispatch never launched). Idempotent."""
    job_id = r["job_id"]
    result_path = run_dir / "results" / (job_id.replace("job-", "result-") + ".json")
    if result_path.exists():
        return
    _write_json(result_path, {
        "job_id": job_id,
        "task_id": r.get("task_id"),
        "terminal_status": terminal_state.upper(),
        "result_file": None,
        "summary": reason,
        "reaped_ts": _utc_iso(),
        "synthesized": True,
    })
    for suffix in (".json", ".lock"):
        p = run_dir / "claimed" / (job_id + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


# --- presentation -----------------------------------------------------------

def _next_cadence(status, tick_interval, idle_mult) -> int:
    """tick_interval while any run is actively in flight; lengthened by
    idle_tick_multiplier when nothing is running (all waiting/stalled or
    nearly done) so an idle harness polls less often. Never below 1."""
    any_running = any(r["state"] in ("claimed", "running")
                      for r in status["runs"].values())
    if any_running or status.get("done"):
        return max(1, int(tick_interval))
    return max(1, int(tick_interval) * max(1, int(idle_mult)))


def _hb_age_str(run_dir, name) -> str:
    hb = _heartbeat_path(run_dir, name)
    if not hb.exists():
        return "-"
    try:
        age = int(_now() - hb.stat().st_mtime)
    except OSError:
        return "-"
    return "%dm%02ds" % (age // 60, age % 60)


def _ascii_trunc(value, width: int) -> str:
    """Display-only: ASCII-sanitize (cp1252 console safety, NFR-7) and
    truncate a free-form field to the column width. The raw value is
    untouched on disk (FR-18)."""
    s = "-" if value is None else str(value)
    s = s.encode("ascii", "replace").decode("ascii")
    return s[:width]


def _format_table(run_dir, status, plan, terminal: bool) -> str:
    bar = "-" * 86
    # FR-21b: STATE narrowed + abbreviated (LAUNCH-FAIL) so it can't
    # overflow; ACTIVITY (the free-form label) widened and sanitized.
    fmt = "%-5s%-22s%-8s%-13s%-16s%-13s%s"
    rows = [
        "Run-Dir: %s (cycle %d)" % (run_dir.name, status.get("cycle", 0)),
        bar,
        fmt % ("RUN", "REPO", "MODE", "STATE", "ACTIVITY", "LAST-HB",
               "HB-AGE"),
    ]
    any_launch_fail = False
    for name in sorted(status["runs"]):
        r = status["runs"][name]
        _, _, activity, _ = _hb_observe(_heartbeat_path(run_dir, name))
        st = r["state"]
        if st == "auth_or_launch_failed":
            any_launch_fail = True
        rows.append(fmt % (
            name[4:],
            _ascii_trunc(r.get("target_repo") or "-", 21),
            {"subagent": "subgnt", "shell": "shell"}.get(
                r.get("dispatch_mode"), "-"),
            _STATE_DISPLAY.get(st, st)[:12],
            _ascii_trunc(activity, 15),
            r.get("last_hb_status") or "-",
            _hb_age_str(run_dir, name),
        ))
    c = status["counts"]
    rows.append(bar)
    rows.append(
        "Queue: %d  Claimed: %d  Running: %d  Stalled: %d  "
        "Completed: %d  Failed: %d" % (
            c.get("queued", 0), c.get("claimed", 0), c.get("running", 0),
            c.get("stalled", 0), c.get("completed", 0),
            c.get("failed", 0) + c.get("auth_or_launch_failed", 0)
            + c.get("abandoned", 0)))
    if any_launch_fail:
        # FR-21b: the diagnostic hint travels with the table, not just the
        # result record — LAUNCH-FAIL covers more than auth.
        rows.append("LAUNCH-FAIL: " + _LAUNCH_FAIL_HINT + ".")
    if status.get("paused") and not terminal:
        # FR-36: PAUSED banner while paused (no new dispatch; in-flight workers
        # keep running). Drop RESUME or remove PAUSE to resume.
        rows.append("PAUSED - no new dispatch; drop a RESUME file (or remove "
                    "PAUSE) to resume. In-flight workers keep running.")
    if terminal:
        # ASCII only (no em-dash / box-drawing / arrows) — the status
        # table prints on Windows cp1252 consoles (185 print-path lesson).
        if status.get("done"):
            rows.append("DONE - all runs terminal. No further ticks.")
        else:
            rows.append("STOP - halting. No further ticks.")
    else:
        rows.append("Next tick in %d min" % _next_cadence(
            status, _cfg(plan, "tick_interval_minutes",
                         DEFAULT_TICK_INTERVAL_MINUTES),
            _cfg(plan, "idle_tick_multiplier", DEFAULT_IDLE_TICK_MULTIPLIER)))
    return "\n".join(rows)


def _print_intro() -> None:
    """Self-describing help on no-args / --help (stdlib only; the standalone
    engine carries no external banner dependency)."""
    print(__doc__.strip() if __doc__ else
          "wakecycle tick (--init <plan-path> | <run-dir>)")


class _TickLock:
    """E1 (FR-12): a per-run-dir advisory lock that serializes concurrent
    tick processes (overlapping cron fires / a ticker + a manual --once).
    Non-blocking: ``acquired`` is False if another process holds it, and the
    caller skips the tick cleanly. Stdlib both platforms (fcntl / msvcrt)."""

    def __init__(self, run_dir: Path):
        self._path = run_dir / ".tick.lock"
        self._fh = None
        self.acquired = False

    def __enter__(self):
        try:
            self._fh = open(self._path, "a+")
        except OSError:
            self.acquired = True  # can't lock (read-only dir) — don't block
            return self
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.acquired = False
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                if self.acquired and os.name != "nt":
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._fh.close()


def _locked_skip_output(run_dir: Path) -> dict:
    return {
        "dispatch_list": [],
        "status_table": ("Run-Dir: %s - another tick is already in progress; "
                         "this tick skipped cleanly (E1)." % run_dir.name),
        "next_tick_minutes": 1,
        "done": False,
        "stop": False,
        "skipped": True,
    }


def _wakecycle_version() -> str:
    """The single canonical version (FR-34): wakecycle/__init__.py:__version__.
    bin scripts aren't installed as a package, so read it by repo-relative
    path rather than importing -- one source, every surface reads it."""
    init = Path(__file__).resolve().parent.parent / "wakecycle" / "__init__.py"
    try:
        for line in init.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "unknown"


# --- FR-42 plan pre-flight (--check) ----------------------------------------
# A hand-rolled, stdlib-only validator (NFR-3 forbids a jsonschema dependency).
# It reports ALL problems at once so an adopter fixes config proactively rather
# than discovering it as a reactive AUTH_OR_LAUNCH_FAILED after launch spend.

_VALID_DISPATCH_MODES = ("subagent", "shell")
# Top-level optional knobs that, IF present, must be integers >= 1 (mirrors
# plan.schema.json minimums; defaults live in the engine if omitted).
_PLAN_INT_KEYS = ("tick_interval_minutes", "pool_size", "stall_threshold_minutes",
                  "launch_grace_minutes", "idle_tick_multiplier")
# Reuse the engine's substitution sets so the check can NEVER drift from what
# _dispatch actually substitutes (FR-42). _KNOWN_PLACEHOLDERS catches typos like
# {HEARTBEATPATH}; the subagent prompt must carry the full _PLACEHOLDERS block.
_KNOWN_PLACEHOLDERS = frozenset(_SHELL_PLACEHOLDERS)
_HEARTBEAT_PLACEHOLDER = "HEARTBEAT_PATH"   # the trackability-critical one
assert _HEARTBEAT_PLACEHOLDER in _PLACEHOLDERS   # guard against a tuple rename
_PLACEHOLDER_TOKEN_RE = __import__("re").compile(r"\{([A-Z][A-Z0-9_]*)\}")


def _is_pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _run_auth_check(argv) -> tuple:
    """Run an entry's auth_check argv (opt-in). Returns (rc, detail)."""
    try:
        proc = subprocess.run(argv, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "could not run %r (%s)" % (argv, exc)
    return proc.returncode, "rc=%d" % proc.returncode


def _check_adapter_entry(tag: str, e: dict) -> list:
    """FR-41: validate an `adapter: wrap|tail` entry. The engine synthesizes
    the worker_cmd, so the worker_prompt/placeholder plumbing is NOT required
    here — the operator declares intent in one place."""
    p = []
    adapter = e.get("adapter")
    if adapter not in _VALID_ADAPTERS:
        p.append("%s.adapter: must be one of %s (got %r)"
                 % (tag, list(_VALID_ADAPTERS), adapter))
    if e.get("dispatch_mode") not in (None, "shell"):
        p.append("%s.dispatch_mode: an adapter entry runs as 'shell' "
                 "(got %r)" % (tag, e.get("dispatch_mode")))
    if adapter == "wrap":
        cmd = e.get("command")
        if not (isinstance(cmd, list) and cmd and all(isinstance(t, str) for t in cmd)):
            p.append("%s.command: wrap requires a non-empty array of strings" % tag)
    elif adapter == "tail":
        if not (isinstance(e.get("log_path"), str) and e.get("log_path")):
            p.append("%s.log_path: tail requires a non-empty string" % tag)
        if "command" in e and not (isinstance(e["command"], list)
                                   and all(isinstance(t, str) for t in e["command"])):
            p.append("%s.command: must be an array of strings" % tag)
        for key in ("success_regex", "failure_regex", "sentinel_file"):
            if key in e and not isinstance(e[key], str):
                p.append("%s.%s: must be a string" % (tag, key))
        if "pid" in e and not (isinstance(e["pid"], int) and not isinstance(e["pid"], bool)):
            p.append("%s.pid: must be an integer" % tag)
    return p


def _check_entry(i: int, e, run_auth: bool) -> list:
    tag = "entries[%d]" % i
    if not isinstance(e, dict):
        return ["%s: must be a JSON object" % tag]
    p = []
    # task_id / target_repo always required; worker_prompt required EXCEPT for
    # an adapter entry (the adapter synthesizes its own plumbing, FR-41).
    is_adapter = bool(e.get("adapter"))
    required = ("task_id", "target_repo") if is_adapter else (
        "task_id", "target_repo", "worker_prompt")
    for key in required:
        if not (isinstance(e.get(key), str) and e.get(key)):
            p.append("%s.%s: required non-empty string" % (tag, key))
    # optional typed fields
    if "heartbeat_path" in e and not isinstance(e["heartbeat_path"], str):
        p.append("%s.heartbeat_path: must be a string" % tag)
    for key in ("worker_cmd", "auth_check"):
        if key in e and not (isinstance(e[key], list)
                             and all(isinstance(t, str) for t in e[key])):
            p.append("%s.%s: must be an array of strings" % (tag, key))

    if is_adapter:
        # The engine builds the worker_cmd from the adapter config; validate
        # that config and SKIP the manual worker_prompt/placeholder checks.
        p.extend(_check_adapter_entry(tag, e))
    else:
        mode = e.get("dispatch_mode")
        if mode not in _VALID_DISPATCH_MODES:
            p.append("%s.dispatch_mode: must be one of %s (got %r)"
                     % (tag, list(_VALID_DISPATCH_MODES), mode))
        prompt = e.get("worker_prompt") if isinstance(e.get("worker_prompt"), str) else ""
        cmd = e.get("worker_cmd") if isinstance(e.get("worker_cmd"), list) else []
        cmd_text = " ".join(t for t in cmd if isinstance(t, str))
        # placeholder presence -- reuse the engine tuples so it can't drift
        if mode == "subagent":
            for ph in _PLACEHOLDERS:
                if ("{%s}" % ph) not in prompt:
                    p.append("%s.worker_prompt: missing placeholder {%s}" % (tag, ph))
        elif mode == "shell":
            if not cmd:
                p.append("%s.worker_cmd: required (non-empty) for shell dispatch" % tag)
            else:
                hb = "{%s}" % _HEARTBEAT_PLACEHOLDER
                via_cmd = hb in cmd_text
                via_prompt = ("{PROMPT_FILE}" in cmd_text) and (hb in prompt)
                if not (via_cmd or via_prompt):
                    p.append("%s: shell entry has no route for %s -- put it in "
                             "worker_cmd, or reference {PROMPT_FILE} in worker_cmd "
                             "with the prompt carrying it" % (tag, hb))
        # typo / drift catch: any placeholder-shaped token that isn't a known one
        for tok in sorted(set(_PLACEHOLDER_TOKEN_RE.findall(prompt + " " + cmd_text))):
            if tok not in _KNOWN_PLACEHOLDERS:
                p.append("%s: unknown placeholder {%s} (known: %s)"
                         % (tag, tok, ", ".join(sorted(_KNOWN_PLACEHOLDERS))))
        # optional auth_check (opt-in: external commands only run with --run-auth)
        if run_auth and mode == "shell" and isinstance(e.get("auth_check"), list) and e["auth_check"]:
            rc, detail = _run_auth_check(e["auth_check"])
            if rc != 0:
                p.append("%s.auth_check: failed (%s)" % (tag, detail))

    # target_repo existence (both paths)
    tr = e.get("target_repo")
    if isinstance(tr, str) and tr and not Path(tr).is_dir():
        p.append("%s.target_repo: not an existing directory: %s" % (tag, tr))
    return p


def check_plan(plan_path, run_auth: bool = False) -> list:
    """Validate a plan and return a list of ALL problems (empty == clean).
    Never launches anything (auth_check runs only when run_auth=True)."""
    try:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except OSError as exc:
        return ["plan: cannot read %s (%s)" % (plan_path, exc)]
    except ValueError as exc:
        return ["plan: not valid JSON (%s)" % exc]
    if not isinstance(plan, dict):
        return ["plan: top level must be a JSON object"]
    problems = []
    for k in _PLAN_INT_KEYS:
        if k in plan and not _is_pos_int(plan[k]):
            problems.append("plan.%s: must be an integer >= 1 (got %r)" % (k, plan[k]))
    if "schema_version" in plan and not isinstance(plan["schema_version"], str):
        problems.append("plan.schema_version: must be a string")
    entries = plan.get("entries")
    if not isinstance(entries, list) or not entries:
        problems.append("plan.entries: a non-empty array is required")
        return problems                      # nothing per-entry to check
    for i, e in enumerate(entries):
        problems.extend(_check_entry(i, e, run_auth))
    return problems


def _format_check_report(plan_path, problems) -> str:
    if not problems:
        return "plan OK: %s -- no problems found" % plan_path
    head = "plan FAILED: %s -- %d problem(s):" % (plan_path, len(problems))
    return "\n".join([head] + ["  - " + p for p in problems])


def main(argv) -> int:
    args = list(argv[1:])
    # FR-34 banner: the running version is always visible. To stderr so the
    # --init run-dir path and the per-tick JSON stay clean on stdout.
    print("wakecycle %s" % _wakecycle_version(), file=sys.stderr)
    if not args or args in (["-h"], ["--help"]):
        _print_intro()
        return 0
    if args and args[0] == "--check":
        # FR-42: pre-flight validation. `--check <plan> [--run-auth]`.
        rest = args[1:]
        run_auth = "--run-auth" in rest
        rest = [a for a in rest if a != "--run-auth"]
        if len(rest) != 1:
            print("usage: tick.py --check <plan> [--run-auth]", file=sys.stderr)
            return 64
        problems = check_plan(Path(rest[0]).resolve(), run_auth=run_auth)
        print(_format_check_report(rest[0], problems))
        return 1 if problems else 0
    if len(args) == 2 and args[0] == "--init":
        print(init_run(Path(args[1]).resolve()))
        return 0
    if len(args) == 1 and args[0] != "--init":
        run_dir = Path(args[0]).resolve()
        with _TickLock(run_dir) as lock:
            if not lock.acquired:
                print(json.dumps(_locked_skip_output(run_dir), indent=2))
                return 0
            print(json.dumps(tick(run_dir), indent=2))
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))
