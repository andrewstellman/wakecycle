#!/usr/bin/env python3
"""arunner tick — the deterministic harness state-machine stepper.

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
DEFAULT_KEEPALIVE_SECONDS = 45            # FR-58a: activity-refresh cadence (adapter keepalive)

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
# The placeholder preamble the engine AUTO-INJECTS into every `agent` prompt
# (job + pipeline step) so authors never hand-type it (settled decision 3 /
# FR-21a). The TOKENS stay {HEARTBEAT_PATH} etc.; the engine substitutes the
# absolute paths at dispatch. Injected only when the prompt does not already
# carry {HEARTBEAT_PATH} (so a hand-written header is never doubled).
_PLACEHOLDER_HEADER = "".join("%s={%s}\n" % (k, k) for k in _PLACEHOLDERS) + "\n"


def _inject_preamble(prompt: str) -> str:
    """Prepend the reserved-placeholder preamble to an `agent` prompt unless it
    already carries {HEARTBEAT_PATH} (idempotent — never doubles a header)."""
    if "{HEARTBEAT_PATH}" in (prompt or ""):
        return prompt or ""
    return _PLACEHOLDER_HEADER + (prompt or "")
# v1.5.9 Phase 2B: shell dispatch adds {PROMPT_FILE} (the per-job prompt
# written to queue/job-NNNNN.prompt.txt for quoting/arg-length safety).
_SHELL_PLACEHOLDERS = _PLACEHOLDERS + ("PROMPT_FILE",)
# FR-61: the engine's RESERVED placeholder names (substituted mechanically at
# dispatch). A plan/entry/step `vars` key may not be one of these, and a `vars`
# VALUE may not contain one as a token -- so a designated-key {var} pass can
# never spoof a dispatch-time path (both are --check errors). LOCK_FILE is the
# FR-41 adapter PID backstop token, reserved for the same reason.
_RESERVED_NAMES = frozenset(_SHELL_PLACEHOLDERS + ("LOCK_FILE",))
_RESERVED_TOKENS = frozenset("{%s}" % n for n in _RESERVED_NAMES)
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
    """Wall-clock seconds. Overridable via ARUNNER_NOW (epoch float)
    so stall / launch-grace logic is testable without sleeping."""
    override = os.environ.get("ARUNNER_NOW")
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
    # Derive the ISO stamp from _now() so the ARUNNER_NOW clock seam is
    # UNIFORM across the engine (epoch + ISO): claimed_ts/reaped_ts honor the
    # same injected clock as claimed_at, making FR-45 durations deterministic in
    # tests. No production effect without ARUNNER_NOW.
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


# --- the one `mode` discriminator -------------------------------------------
# A job/step declares ONE friendly `mode`; the engine maps it onto its existing
# dispatch machinery. agent -> in-session subagent (auto-injected placeholder
# preamble); command -> shell via the synthesized `wrap` heartbeat helper;
# log -> shell via the synthesized `tail` helper; shell -> shell with the raw
# `command` argv (operator wires the heartbeat); pipeline -> ordered steps.
_AGENT_MODES = ("agent",)
_SHELL_MODES = ("command", "log", "shell")     # dispatch as dispatch_mode:"shell"
_JOB_MODES = ("agent", "command", "log", "pipeline", "shell")
_STEP_MODES = ("agent", "command", "log", "shell")
# command -> the FR-40/41 `wrap` adapter; log -> `tail`. shell/agent synthesize
# no adapter helper (shell is raw argv; agent is a subagent prompt).
_MODE_ADAPTER = {"command": "wrap", "log": "tail"}


def _dispatch_mode_of(entry) -> str:
    """The RUNTIME dispatch_mode (subagent | shell) for a job/step, derived from
    its friendly ``mode``. Recorded in the manifest/lock/record (A2A vocabulary)."""
    return "shell" if (isinstance(entry, dict)
                       and entry.get("mode") in _SHELL_MODES) else "subagent"


def _apply_defaults(job: dict, defaults: dict) -> dict:
    """Shallow-merge a plan-level ``defaults`` map UNDER one job (the job's own
    key always wins). Returns the job unchanged when there is nothing to merge."""
    if not (isinstance(defaults, dict) and defaults and isinstance(job, dict)):
        return job
    merged = dict(defaults)
    merged.update(job)
    return merged


def _merge_defaults(plan: dict) -> list:
    """The plan's jobs with ``defaults`` merged under each (job key wins). Used by
    --check and --init so the effective job (what the engine reads) is validated
    and dispatched. Non-dict jobs pass through untouched."""
    defaults = plan.get("defaults") if isinstance(plan.get("defaults"), dict) else {}
    jobs = plan.get("jobs") or []
    return [_apply_defaults(j, defaults) if isinstance(j, dict) else j for j in jobs]


# --- init -------------------------------------------------------------------

def _scaffold_run(run_dir: Path, run_name: str, job_id: str, entry: dict) -> dict:
    """Scaffold ONE run-NN (heartbeat file, manifest, queue claim token) and
    return its ``queued`` runs-record. The single source of truth for the
    per-run on-disk shape, shared by ``init_run`` (the initial batch) and
    ``_absorb_incoming`` (FR-57 live adds) so they can never drift.

    The job is the friendly mode-discriminated shape (``id``/``repo``/``mode``);
    the runtime RECORD keeps the A2A identity vocabulary (``task_id``/
    ``target_repo``/``dispatch_mode``) — the {TASK_ID}/{TARGET_REPO} placeholder
    tokens stay, so this is the one plan-vocab -> runtime-vocab seam."""
    rd = run_dir / run_name
    rd.mkdir(exist_ok=True)
    (rd / "heartbeat.ndjson").touch()
    manifest = {
        "task_id": entry.get("id"),
        "target_repo": entry.get("repo"),
        "dispatch_mode": _dispatch_mode_of(entry),
        "run": run_name,
        "job_id": job_id,
    }
    # FR-20: a plan job MAY point the harness at a heartbeat file the
    # job already writes (absolute). Recorded here so _heartbeat_path
    # watches it instead of the run-dir default.
    if entry.get("heartbeat_path"):
        manifest["heartbeat_path"] = entry["heartbeat_path"]
    record = {
        "task_id": entry.get("id"),
        "job_id": job_id,
        "target_repo": entry.get("repo"),
        "state": "queued",
        "last_hb_status": None,
        "claimed_at": None,
    }
    if _is_multistep(entry):
        # FR-62: a multi-step entry runs steps in one slot. Scaffold the FIRST
        # step now; record step_index/step_count on the run. Dispatch is
        # per-step (run-level queue token is unused), so we don't write one.
        steps = entry["steps"]
        record["step_index"] = 0
        record["step_count"] = len(steps)
        manifest["step_count"] = len(steps)
        _write_json(rd / "manifest.json", manifest)
        _scaffold_step(run_dir, run_name, 0, entry, steps[0])
        return record
    _write_json(rd / "manifest.json", manifest)
    _write_json(run_dir / "queue" / (job_id + ".json"),
                {"job_id": job_id, "run": run_name, "entry": entry})
    return record


def _absorb_incoming(run_dir: Path) -> int:
    """FR-57 stage-and-absorb: at the START of a tick (under the ``.tick.lock``
    the caller already holds) append any entries staged in ``<run-dir>/incoming/``
    to ``plan.json["jobs"]``, scaffold each new ``run-NN`` + a ``queued``
    record in ``harness_status.json["runs"]`` (mirroring ``init_run``), then
    retire the absorbed file. Race-free by construction: ``add`` only ever
    writes ``incoming/`` -- never the live files a concurrent tick reads/writes.

    Disciplines (FR-57): APPEND-ONLY positional numbering (new ``run-NN`` continue
    from the current entry count -- a renumber or ``len(entries)``<->``runs``
    mismatch is silently swallowed by the tick's ``except``); ``task_id`` minted
    if absent; placeholders stored UNRESOLVED (dispatch-time substitution handles
    them); NO ``done`` write -- the new ``queued`` run re-activates an idle run on
    this very tick (``done`` is recomputed below). Returns the count absorbed."""
    inc = run_dir / "incoming"
    if not inc.is_dir():
        return 0
    staged = sorted(p for p in inc.iterdir()
                    if p.is_file() and p.suffix == ".json")
    if not staged:
        return 0
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    status = json.loads((run_dir / "harness_status.json").read_text(encoding="utf-8"))
    jobs = plan.setdefault("jobs", [])
    plan_defaults = plan.get("defaults") if isinstance(plan.get("defaults"), dict) else {}
    runs = status["runs"]
    added = 0
    for sp in staged:
        try:
            payload = json.loads(sp.read_text(encoding="utf-8"))
        except ValueError:
            _log(run_dir, "absorb: skipping malformed incoming %s" % sp.name)
            sp.unlink()
            continue
        new_jobs = (payload.get("jobs") if isinstance(payload, dict)
                    else payload)
        pool = payload.get("pool_size") if isinstance(payload, dict) else None
        for entry in (new_jobs or []):
            if not isinstance(entry, dict):
                continue
            idx = len(jobs) + 1                         # APPEND-ONLY, positional
            run_name = "run-%02d" % idx
            job_id = "job-%05d" % idx
            entry = _apply_defaults(entry, plan_defaults)
            if not entry.get("id"):
                entry = dict(entry, id="added-%02d" % idx)        # mint if absent
            jobs.append(entry)                           # placeholders UNRESOLVED
            runs[run_name] = _scaffold_run(run_dir, run_name, job_id, entry)
            added += 1
        if pool:
            status["pool_size"] = max(int(status.get("pool_size") or 0), int(pool))
        sp.unlink()                                      # retire the absorbed file
    if added:
        status["counts"] = _recount(runs)
        # NO `done` write: a freshly-queued run re-activates the batch; `done`
        # is recomputed by the tick that called us.
        _write_json(run_dir / "plan.json", plan)
        _write_json(run_dir / "harness_status.json", status)
        _log(run_dir, "absorb: +%d run(s) from incoming/ (now %d job%s)"
             % (added, len(jobs), "" if len(jobs) == 1 else "s"))
    return added


# --- FR-60: chat <-> runner message channel (typed inbox/outbox) -----------
# A typed, acknowledged, idempotent control channel: the chat drops
# <run-dir>/inbox/<id>.json; the engine DRAINS the inbox at the start of each
# tick UNDER the .tick.lock it already holds (mirroring the FR-57 incoming/
# absorb), processes each message idempotently (a processed-ids ledger makes a
# replayed/crashed drain a no-op), and writes an ACK (and later a RESULT) to the
# append-only outbox. Closed verb set only; read-only verbs mutate no run state;
# local-disk only (NO network listener); worker_prompts are passed to subagents
# as DATA, never shell-evaluated. The engine is a generic message->dispatch
# relay -- higher-level loop semantics live in the chat + the job prompts.
_MSG_VERBS = ("enqueue", "control", "dispatch-job", "run-batch", "snapshot", "note")
_CONTROL_OPS = ("pause", "resume", "cadence", "poll-now", "cancel")


def _outbox_dir(run_dir: Path) -> Path:
    ob = run_dir / "outbox"
    ob.mkdir(exist_ok=True)
    return ob


def _write_ack(run_dir: Path, mid: str, status: str, reason=None, task_ids=None):
    """Append-only ack (never mutated in place): received -> applied/rejected."""
    _write_json(_outbox_dir(run_dir) / (mid + ".ack.json"), {
        "message_id": mid, "status": status, "reason": reason,
        "task_ids": list(task_ids or []), "ts": _utc_iso()})


def _processed_ids(run_dir: Path) -> set:
    p = run_dir / "inbox" / ".processed"
    if not p.is_file():
        return set()
    return set(x.strip() for x in p.read_text(encoding="utf-8").splitlines()
               if x.strip())


def _mark_processed(run_dir: Path, mid: str) -> None:
    with (run_dir / "inbox" / ".processed").open("a", encoding="utf-8") as fh:
        fh.write(mid + "\n")


def _load_pending(run_dir: Path) -> dict:
    p = run_dir / "outbox" / ".pending.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _check_message(msg) -> list:
    """--check an inbound message (FR-42): closed verb + well-formed args. A
    malformed/unknown message is rejected in its ack and never crashes the tick."""
    if not isinstance(msg, dict):
        return ["message must be a JSON object"]
    p = []
    if not (isinstance(msg.get("id"), str) and msg["id"]):
        p.append("message missing a string 'id'")
    verb = msg.get("verb")
    if verb not in _MSG_VERBS:
        return p + ["unknown verb %r (allowed: %s)"
                    % (verb, ", ".join(_MSG_VERBS))]
    args = msg.get("args") or {}
    if not isinstance(args, dict):
        return p + ["'args' must be an object"]
    if verb == "enqueue":
        if not (isinstance(args.get("jobs"), list) and args["jobs"]):
            p.append("enqueue requires non-empty args.jobs[]")
    elif verb == "run-batch":
        if not (args.get("batch") or (isinstance(args.get("jobs"), list)
                                      and args["jobs"])):
            p.append("run-batch requires args.batch (name) or args.jobs[]")
    elif verb == "dispatch-job":
        if not (isinstance(args.get("prompt"), str) and args["prompt"]):
            p.append("dispatch-job requires args.prompt (non-empty string)")
    elif verb == "control":
        op = args.get("op")
        if op not in _CONTROL_OPS:
            p.append("control.op must be one of %s" % ", ".join(_CONTROL_OPS))
        elif op == "cadence" and not _is_pos_int(args.get("minutes")):
            p.append("control cadence requires args.minutes (integer >= 1)")
        elif op == "cancel" and not args.get("task"):
            p.append("control cancel requires args.task")
    return p


def _msg_entries(run_dir: Path, msg: dict) -> list:
    """The plan jobs a job-staging verb contributes (placeholders UNRESOLVED)."""
    verb = msg["verb"]
    args = msg.get("args") or {}
    if verb == "enqueue":
        return list(args.get("jobs") or [])
    if verb == "dispatch-job":
        e = {"repo": args.get("repo", "."),
             "mode": "agent", "prompt": args["prompt"]}
        if args.get("id"):
            e["id"] = args["id"]
        return [e]
    if verb == "run-batch":
        if isinstance(args.get("jobs"), list):
            return list(args["jobs"])
        bf = run_dir / "batches" / (str(args.get("batch")) + ".json")
        try:
            doc = json.loads(bf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return list(doc.get("jobs") or [])
    return []


def _process_message(run_dir, msg, plan, status):
    """Apply ONE validated message. Returns (status, reason, task_ids, runs).
    Mutates plan/status in place for job-staging verbs (the caller persists)."""
    verb = msg["verb"]
    args = msg.get("args") or {}
    if verb in ("enqueue", "dispatch-job", "run-batch"):
        jobs = _msg_entries(run_dir, msg)
        if not jobs:
            return "rejected", "no jobs to stage", [], []
        plan_defaults = (plan.get("defaults")
                         if isinstance(plan.get("defaults"), dict) else {})
        jobs = [_apply_defaults(e, plan_defaults) if isinstance(e, dict) else e
                for e in jobs]
        # --check the synthesized jobs against the live plan's knobs BEFORE
        # they touch the work table (FR-42); a bad spec never lands.
        probe = {k: plan[k] for k in _PLAN_INT_KEYS if k in plan}
        probe["jobs"] = jobs
        base = len(plan.get("jobs") or [])
        for j, e in enumerate(jobs):
            if isinstance(e, dict) and not e.get("id"):
                e["id"] = "msg-%02d" % (base + j + 1)
        import tempfile as _tmp
        tp = Path(_tmp.mkdtemp()) / "probe.json"
        tp.write_text(json.dumps(probe), encoding="utf-8")
        probs = check_plan(tp)
        if probs:
            return "rejected", "; ".join(probs), [], []
        pjobs = plan.setdefault("jobs", [])
        runs = status["runs"]
        spawned, task_ids = [], []
        for e in jobs:
            idx = len(pjobs) + 1                       # APPEND-ONLY, positional
            run_name = "run-%02d" % idx
            job_id = "job-%05d" % idx
            pjobs.append(e)                             # placeholders UNRESOLVED
            runs[run_name] = _scaffold_run(run_dir, run_name, job_id, e)
            spawned.append(run_name)
            task_ids.append(e.get("id"))
        return "applied", None, task_ids, spawned
    if verb == "control":
        op = args["op"]
        if op == "pause":
            (run_dir / "PAUSE").write_text("", encoding="utf-8")
        elif op == "resume":
            (run_dir / "RESUME").write_text("", encoding="utf-8")
        elif op == "poll-now":
            (run_dir / "POLL-NOW").write_text("", encoding="utf-8")
        elif op == "cadence":
            (run_dir / "CADENCE").write_text(str(args["minutes"]), encoding="utf-8")
        elif op == "cancel":
            (run_dir / "CANCEL").write_text(str(args["task"]), encoding="utf-8")
        return "applied", "control:%s" % op, [], []
    if verb == "snapshot":
        # READ-ONLY: render the CURRENT (last-tick) state to the outbox; mutate
        # no run state.
        table = _format_table(run_dir, status, plan,
                              terminal=bool(status.get("done")))
        run_states = {n: r.get("state") for n, r in status.get("runs", {}).items()}
        _write_json(_outbox_dir(run_dir) / (msg["id"] + ".result.json"), {
            "message_id": msg["id"], "verb": "snapshot",
            "status_table": table, "counts": status.get("counts", {}),
            "run_states": run_states, "done": bool(status.get("done")),
            "ts": _utc_iso()})
        return "applied", "snapshot emitted", [], []
    if verb == "note":
        # AUDIT only, no action: append to the journal.
        with (run_dir / "journal.ndjson").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "note", "message_id": msg["id"],
                                 "note": args.get("text", ""),
                                 "ts": _utc_iso()}) + "\n")
        return "applied", "note journaled", [], []
    return "rejected", "unhandled verb", [], []


def _drain_inbox(run_dir: Path) -> int:
    """FR-60: drain <run-dir>/inbox/ at the START of a tick, under the caller's
    .tick.lock. Idempotent by id (mark-FIRST against the processed-ids ledger so
    a crash/replay never double-applies); --check each message; ack every one;
    move processed files to inbox/processed/. Returns the count applied."""
    inbox = run_dir / "inbox"
    if not inbox.is_dir():
        return 0
    msgs = sorted(p for p in inbox.iterdir()
                  if p.is_file() and p.suffix == ".json")
    if not msgs:
        return 0
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    status = json.loads((run_dir / "harness_status.json").read_text(encoding="utf-8"))
    processed = _processed_ids(run_dir)
    proc_dir = inbox / "processed"
    pending = _load_pending(run_dir)
    applied = 0
    plan_mutated = False
    for mp in msgs:
        try:
            msg = json.loads(mp.read_text(encoding="utf-8"))
        except ValueError:
            mid = mp.stem
            if mid not in processed:
                _mark_processed(run_dir, mid)
                processed.add(mid)
                _write_ack(run_dir, mid, "rejected", "malformed JSON")
            proc_dir.mkdir(exist_ok=True)
            mp.replace(proc_dir / mp.name)
            continue
        mid = msg.get("id") if isinstance(msg, dict) and msg.get("id") else mp.stem
        if mid in processed:                       # idempotent replay -> no-op
            proc_dir.mkdir(exist_ok=True)
            mp.replace(proc_dir / mp.name)
            continue
        # COMMIT the id to the ledger FIRST: a crash after this never re-applies
        # the message (idempotency over double-apply).
        _mark_processed(run_dir, mid)
        processed.add(mid)
        probs = _check_message(msg)
        if probs:
            _write_ack(run_dir, mid, "rejected", "; ".join(probs))
        else:
            st, reason, task_ids, spawned = _process_message(run_dir, msg, plan, status)
            _write_ack(run_dir, mid, st, reason, task_ids)
            if st == "applied" and spawned:
                pending[mid] = {"task_ids": task_ids, "runs": spawned}
                plan_mutated = True
                applied += 1
            elif st == "applied":
                applied += 1
        proc_dir.mkdir(exist_ok=True)
        mp.replace(proc_dir / mp.name)
    if plan_mutated:
        status["counts"] = _recount(status["runs"])
        _write_json(run_dir / "plan.json", plan)
        _write_json(run_dir / "harness_status.json", status)
    _write_json(_outbox_dir(run_dir) / ".pending.json", pending)
    _log(run_dir, "inbox: drained %d message(s)" % len(msgs))
    return applied


def _emit_ready_results(run_dir: Path, runs: dict) -> None:
    """FR-60: when a message's staged work has all reached a terminal state,
    write the append-only outbox result correlating message id <-> task_id(s)."""
    pending = _load_pending(run_dir)
    if not pending:
        return
    changed = False
    for mid in list(pending):
        spec = pending[mid]
        rnames = spec.get("runs") or []
        states = {n: (runs.get(n) or {}).get("state") for n in rnames}
        if rnames and all(s in _TERMINAL_STATES for s in states.values()):
            _write_json(_outbox_dir(run_dir) / (mid + ".result.json"), {
                "message_id": mid, "task_ids": spec.get("task_ids", []),
                "run_states": states, "completed": True, "ts": _utc_iso()})
            del pending[mid]
            changed = True
    if changed:
        _write_json(run_dir / "outbox" / ".pending.json", pending)


def init_run(plan_path: Path) -> Path:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    # Bake `defaults` into each job so the persisted plan.json (read every tick)
    # is the EFFECTIVE shape; the engine never re-merges downstream.
    jobs = _merge_defaults(plan)
    if not jobs:
        raise ValueError(f"plan {plan_path} has no jobs[]")
    plan["jobs"] = jobs
    plan.pop("defaults", None)                      # consumed into the jobs
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Base dir is <repo>/harness_runs by default; ARUNNER_RUNS_DIR
    # overrides it (tests point this at a tmp dir to stay hermetic).
    base = os.environ.get("ARUNNER_RUNS_DIR")
    runs_root = Path(base) if base else Path(__file__).resolve().parent.parent.parent / "harness_runs"
    run_dir = runs_root / stamp
    for sub in ("queue", "claimed", "results"):
        (run_dir / sub).mkdir(parents=True)
    runs: dict[str, dict] = {}
    plan_dir = plan_path.resolve().parent          # FR-61: prompt-file base dir
    for i, entry in enumerate(jobs, start=1):
        run_name = "run-%02d" % i
        job_id = "job-%05d" % i
        # FR-61/62: snapshot any prompt-from-file (job-level + per-step) into
        # the run-dir so the run is a self-sufficient record (NFR-9); persist the
        # snapshot back into plan.json's job so every downstream path sees a
        # normal inline prompt.
        entry = _snapshot_entry_and_steps(entry, plan_dir, run_dir / run_name)
        jobs[i - 1] = entry
        runs[run_name] = _scaffold_run(run_dir, run_name, job_id, entry)
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


# --- FR-62: multi-step entries (ordered sub-runs) ---------------------------
# A multi-step entry runs its `steps` SEQUENTIALLY within ONE pool slot. Each
# step is a full FR-18 sub-run on disk at run-NN/steps/step-MM/ (own heartbeat,
# manifest, result). The runs-record carries step_index (0-based current) +
# step_count; the engine dispatches only the CURRENT step per tick and advances
# only on the predecessor's clean terminal (modulo a FR-63 gate). Resume reads
# step_index and reaps-not-re-runs completed steps (NFR-6).

def _is_multistep(entry) -> bool:
    return isinstance(entry, dict) and isinstance(entry.get("steps"), list) \
        and bool(entry.get("steps"))


def _step_name(m: int) -> str:
    return "step-%02d" % (m + 1)          # m is 0-based; on-disk is 1-based


def _step_dir(run_dir: Path, name: str, m: int) -> Path:
    return run_dir / name / "steps" / _step_name(m)


def _step_hb(run_dir: Path, name: str, m: int) -> Path:
    return _step_dir(run_dir, name, m) / "heartbeat.ndjson"


def _run_hb_path(run_dir: Path, name: str, r: dict) -> Path:
    """The heartbeat file the engine watches for a run: the reasoning-gate judge's
    while a gate is pending, the CURRENT step's for a multi-step run, else the
    single-prompt default (FR-20 override honored)."""
    if r.get("gate_pending") is not None:
        return _judge_hb(run_dir, name, int(r["gate_pending"]))
    if r.get("step_count"):
        return _step_hb(run_dir, name, int(r.get("step_index", 0)))
    return _heartbeat_path(run_dir, name)


def _scaffold_step(run_dir: Path, name: str, m: int, entry: dict, step: dict) -> None:
    """Create run-NN/steps/step-MM/ with its heartbeat + manifest (FR-18 per
    step, reusing job_manifest.schema.json with the widened dispatch_mode enum).
    Idempotent: an existing step dir/heartbeat is left untouched (resume-safe)."""
    sd = _step_dir(run_dir, name, m)
    sd.mkdir(parents=True, exist_ok=True)
    hb = sd / "heartbeat.ndjson"
    if not hb.exists():
        hb.touch()
    mf = sd / "manifest.json"
    if not mf.is_file():
        _write_json(mf, {
            "task_id": entry.get("id"),
            "target_repo": step.get("repo") or entry.get("repo"),
            "dispatch_mode": _dispatch_mode_of(step),
            "run": name,
            "job_id": "%s-%s" % (entry_job_of(name), _step_name(m)),
            "step_index": m,
            "step_count": len(entry.get("steps") or []),
        })


def entry_job_of(name: str) -> str:
    """The entry-level job id for a run-NN (job-00007 for run-07)."""
    try:
        return "job-%05d" % int(name.split("-")[-1])
    except (ValueError, IndexError):
        return "job-" + name


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

# --- FR-61: prompt-from-file + light {var} templating -----------------------

def _apply_vars(text: str, vars_map: dict) -> str:
    """FR-61: designated-key {var} substitution. A literal ``str.replace`` of
    each DECLARED ``{key}`` -- it does NOT scan for or reject stray braces, so a
    prompt that carries literal single-brace JSON (QPB's phase3/phase5 blocks)
    survives untouched and never trips an unresolved-{name} error. Runs BEFORE
    the engine's reserved placeholders (FR-3/21a)."""
    if not vars_map:
        return text
    for k, v in vars_map.items():
        text = text.replace("{%s}" % k, "" if v is None else str(v))
    return text


def _entry_vars(plan: dict, entry: dict, extra: dict = None) -> dict:
    """The merged {var} map for an entry/step: plan-level ``vars`` < entry/step
    ``vars`` < ``extra`` (FR-64 behavior-flags exposed to the next step). Later
    wins. Non-dict ``vars`` are ignored (defended at --check)."""
    merged = {}
    for src in (plan.get("vars") if isinstance(plan.get("vars"), dict) else None,
                entry.get("vars") if isinstance(entry.get("vars"), dict) else None,
                extra if isinstance(extra, dict) else None):
        if src:
            merged.update(src)
    return merged


def _resolve_prompt_file(wpf: str, base_dir: Path) -> Path:
    """FR-61: resolve ``worker_prompt_file`` -- absolute taken as-is, else
    relative to ``base_dir`` (the plan file's directory at --init)."""
    p = Path(wpf)
    return p if p.is_absolute() else (base_dir / p)


def _snapshot_prompt(entry: dict, base_dir: Path, snap_path: Path) -> dict:
    """FR-61: if ``entry`` sources its prompt from a file, read it (resolved
    relative to ``base_dir``), snapshot the bytes to ``snap_path`` in the
    run-dir (NFR-9 self-sufficient record -- mutating the source after --init
    never changes the run), and return a NEW entry with ``prompt`` set to
    the snapshot and ``prompt_file`` removed, so every downstream path
    (dispatch, --check re-probe) sees a normal inline prompt. A non-file entry is
    returned unchanged. A read error raises (the caller surfaces it)."""
    wpf = entry.get("prompt_file")
    if not wpf:
        return entry
    content = _resolve_prompt_file(wpf, base_dir).read_text(encoding="utf-8")
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(content, encoding="utf-8")
    new = dict(entry)
    new["prompt"] = content
    new.pop("prompt_file", None)
    return new


def _snapshot_entry_and_steps(entry: dict, base_dir: Path, rd: Path) -> dict:
    """FR-61/62: snapshot a job's prompt-from-file AND each of its steps'
    prompt-from-file into the run-dir. Returns a new job with inline
    ``prompt`` filled and ``prompt_file`` removed at both levels.
    A single-prompt job snapshots to ``run-NN/prompt.snapshot.md``; step MM
    snapshots to ``run-NN/steps/step-MM/prompt.snapshot.md``."""
    entry = _snapshot_prompt(entry, base_dir, rd / "prompt.snapshot.md")
    steps = entry.get("steps")
    if isinstance(steps, list) and steps:
        new_steps = []
        for m, step in enumerate(steps, start=1):
            if isinstance(step, dict):
                sdir = rd / "steps" / ("step-%02d" % m)
                step = _snapshot_prompt(step, base_dir, sdir / "prompt.snapshot.md")
                # FR-63: snapshot a reasoning gate's judge prompt-from-file too.
                gate = step.get("gate")
                if isinstance(gate, dict) and gate.get("judge_prompt_file"):
                    content = _resolve_prompt_file(
                        gate["judge_prompt_file"], base_dir).read_text(encoding="utf-8")
                    snap = sdir / "gate" / "judge_prompt.snapshot.md"
                    snap.parent.mkdir(parents=True, exist_ok=True)
                    snap.write_text(content, encoding="utf-8")
                    gate = dict(gate)
                    gate["judge_prompt"] = content
                    gate.pop("judge_prompt_file", None)
                    step = dict(step)
                    step["gate"] = gate
            new_steps.append(step)
        entry = dict(entry)
        entry["steps"] = new_steps
    return entry


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
    usage = _usage_of(hb)                 # FR-65: top-level input/output_tokens
    if usage:
        record.update(usage)
    elif _usage_malformed(hb):
        _log(run_dir, "%s: WARN malformed data.usage skipped (FR-65; tokens "
             "reported as unavailable, not fatal)" % run_name_of(run))
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
    lines = ["# arunner run summary",
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
        if j.get("steps"):                        # FR-62 per-step statuses
            lines.append("|   | steps: %s |" % ", ".join(
                "%s %s" % (s.get("step"), s.get("terminal_status") or "-")
                for s in j["steps"]))
    c = payload["counts"]
    lines += ["",
              "**Totals** - completed: %d, failed: %d, abandoned: %d, "
              "auth/launch-failed: %d (of %d job(s))" % (
                  c.get("completed", 0), c.get("failed", 0),
                  c.get("abandoned", 0), c.get("auth_or_launch_failed", 0),
                  len(payload["jobs"]))]
    tok = payload.get("tokens") or {}        # FR-65: token roll-up (honest)
    ti, to = tok.get("input_tokens"), tok.get("output_tokens")
    lines.append("**Tokens** - input: %s, output: %s -- %s" % (
        "-" if ti is None else ti, "-" if to is None else to,
        tok.get("label", "no token usage reported")))
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
        job = {
            "run": name,
            "task_id": r.get("task_id"),
            "state": r.get("state"),
            "result_file": (rec or {}).get("result_file"),
            "summary": (rec or {}).get("summary"),
            "duration_seconds": duration,
        }
        # FR-62: a multi-step entry lists per-step statuses in the SUMMARY.
        if r.get("step_count"):
            job["steps"] = [{"step": s.get("step"),
                             "terminal_status": s.get("terminal_status")}
                            for s in _collect_step_results(run_dir, name,
                                                           int(r["step_count"]))]
        inp, outp = _run_tokens(run_dir, name, r)        # FR-65 per-job tokens
        job["input_tokens"] = inp
        job["output_tokens"] = outp
        jobs.append(job)
    # FR-65: additive roll-up + honest partial labeling (NFR-12).
    total = [None, None]
    reported = 0
    for j in jobs:
        if j.get("input_tokens") is not None or j.get("output_tokens") is not None:
            reported += 1
            _add_tok(total, j)
    if reported == 0:
        tok_label = "no token usage reported"
    elif reported < len(jobs):
        tok_label = "partial (%d of %d jobs reported)" % (reported, len(jobs))
    else:
        tok_label = "%d of %d jobs reported" % (reported, len(jobs))
    payload = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "run_dir": run_dir.name,
        "done": True,
        "generated_ts": _utc_iso(),
        "counts": dict(status.get("counts", {})),
        "tokens": {"input_tokens": total[0], "output_tokens": total[1],
                   "reported": reported, "jobs": len(jobs), "label": tok_label},
        "jobs": jobs,
    }
    _write_json(run_dir / "summary.json", payload)
    (run_dir / "SUMMARY.md").write_text(_render_summary_md(payload), encoding="utf-8")


# --- FR-55: the continuation contract (autonomy integrity) ------------------
# Every tick the engine emits a deterministic continuation VERDICT = a pure
# function of run-dir state, so the orchestrating host READS the continue/stop
# decision rather than AUTHORING it. The closed halt set keeps "why we stopped"
# auditable; an independent checker (FR-51) cross-checks the host's yield
# records against the engine's recorded verdict. The failure is caught
# POST-HOC, not prevented — see UC-11 / docs/INTEGRATION_TEST_PLAN.md.
_CONTINUATION_REASONS = frozenset((
    "done", "failed", "stop", "pause", "cancel", "blocked",
    "stalled", "budget", "internal_error"))


def _open_blockers(run_dir: Path) -> list:
    """Host-authored blocker records under ``<run_dir>/blockers/*.json``. A
    blocker ``{id, created_at, reason, cleared_at}`` is OPEN while ``cleared_at``
    is null. The engine only READS these (host-authored per FR-55), so its
    per-tick status write can never clobber an operator's blocker."""
    out = []
    bdir = run_dir / "blockers"
    if bdir.is_dir():
        for bf in sorted(bdir.glob("*.json")):
            try:
                obj = json.loads(bf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(obj, dict) and not obj.get("cleared_at"):
                out.append(obj)
    return out


def _halt_reason(run_dir: Path, status: dict, stop: bool):
    """The single HALT reason (closed set ``_CONTINUATION_REASONS``) for this
    tick, or ``None`` for CONTINUE. Reads PERSISTED status (paused / terminal
    states / blocker records), not raw control-file presence — FR-35 consumes
    control files once persisted (a consumed PAUSE leaves ``paused: true`` and
    no file). STOP is the deliberate exception: it is never consumed (a
    read-only gate, FR-10), so the STOP file IS the truth and ``stop`` carries
    it. ``cancel``/``budget`` read persisted flags; ``internal_error`` is the
    catch-all the caller sets on a fault, keeping the set closed."""
    runs = status.get("runs") or {}
    states = [r.get("state") for r in runs.values()]
    # operator control states dominate a still-live run
    if stop or status.get("stopped"):
        return "stop"
    if status.get("cancelled"):
        return "cancel"
    # a finished run is done/failed regardless of any stale pause/blocker
    if states and all(s in _TERMINAL_STATES for s in states):
        return "done" if all(s == "completed" for s in states) else "failed"
    # an open operator-decision gate suspends an otherwise-healthy run
    if _open_blockers(run_dir):
        return "blocked"
    if status.get("paused"):
        return "pause"
    if status.get("budget_exhausted"):
        return "budget"
    # progress still possible? a wedge = non-terminal but nothing can advance:
    # every non-terminal run is stalled AND no free pool slot can dispatch a
    # queued run (FR-55 `stalled` — the non-killable wedge; operator out: CANCEL)
    non_terminal = [s for s in states if s not in _TERMINAL_STATES]
    if non_terminal:
        progressing = any(s in ("running", "claimed") for s in non_terminal)
        queued = sum(1 for s in non_terminal if s == "queued")
        inflight = sum(1 for s in non_terminal
                       if s in ("claimed", "running", "stalled"))
        pool = status.get("pool_size") or 0
        free_slot = queued > 0 and (pool - inflight) > 0
        if (not progressing and not free_slot
                and any(s == "stalled" for s in non_terminal)):
            return "stalled"
    return None


def _continuation(run_dir: Path, status: dict, stop: bool, now: float,
                  next_minutes) -> dict:
    """The per-tick continuation object — a pure function of run-dir state:
    ``{verdict, reason?, blocker_id?, next_tick_due, monitoring_paused}``.
    CONTINUE iff non-terminal ∧ status not stopped/cancelled/paused ∧ no open
    blocker ∧ progress possible; else ``HALT`` with a closed-set ``reason``."""
    try:
        reason = _halt_reason(run_dir, status, stop)
    except Exception:                       # a fault keeps the set closed
        reason = "internal_error"
    cont = {"monitoring_paused": bool(status.get("monitoring_paused", False))}
    try:
        cont["next_tick_due"] = int(round(now + float(next_minutes) * 60))
    except (TypeError, ValueError):
        cont["next_tick_due"] = None
    if reason is None:
        cont["verdict"] = "CONTINUE"
    else:
        cont["verdict"] = "HALT"
        cont["reason"] = reason
        if reason == "blocked":
            opn = _open_blockers(run_dir)
            if opn:
                cont["blocker_id"] = opn[0].get("id")
    return cont


def _verdict_str(cont: dict) -> str:
    """Canonical verdict string for cross-checking a yield: ``CONTINUE`` or
    ``HALT:<reason>`` (the blocker class is ``HALT:blocked``; the id is in the
    record + ``blocker_id``)."""
    if not cont or cont.get("verdict") == "CONTINUE":
        return "CONTINUE"
    return "HALT:" + str(cont.get("reason", "internal_error"))


def _append_journal(run_dir: Path, cont: dict, cycle: int) -> None:
    """Append the engine's per-tick verdict line to the append-only
    ``journal.ndjson``. (The host appends its own ``yield`` records; the engine
    never writes those.) Best-effort: a journal write must never crash a tick."""
    line = {"ts": _utc_iso(), "tick": cycle, "type": "verdict",
            "verdict": _verdict_str(cont),
            "next_tick_due": cont.get("next_tick_due"),
            "monitoring_paused": cont.get("monitoring_paused", False)}
    if cont.get("reason"):
        line["reason"] = cont["reason"]
    try:
        with (run_dir / "journal.ndjson").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except OSError:
        pass


def tick(run_dir: Path) -> dict:
    # FR-57: absorb any live-staged adds FIRST, under the caller's .tick.lock,
    # so the positional work-table rebuild below sees the appended entries.
    # FR-10/FR-35: a STOP tick is fully READ-ONLY -- it mutates nothing and
    # consumes nothing, so the absorb is gated behind STOP-absence (a staged add
    # waits untouched in incoming/ while STOP is present).
    if not (run_dir / "STOP").exists():
        _absorb_incoming(run_dir)
        _drain_inbox(run_dir)            # FR-60: drain the message inbox under the lock
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
               for i, e in enumerate(plan.get("jobs") or [], start=1)}
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
            _advance(run_dir, runs, now, stall_secs, grace_secs, suppress_stall,
                     entries, plan)
            if not paused:
                dispatch_list = _dispatch(run_dir, runs, entries, pool_size, now, plan)
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
        # FR-55: the deterministic continuation verdict, computed from the
        # now-updated status (right after `done` is set), persisted on disk and
        # journaled so an independent reader can audit the continue/stop decision.
        cont = _continuation(run_dir, status, stop, now, next_minutes)
        status["continuation"] = cont
        # FR-60: emit an outbox result for any inbox message whose staged work
        # has now all reached a terminal state (id <-> task_id correlation).
        _emit_ready_results(run_dir, runs)
        # strip transient field, then persist
        for r in runs.values():
            r.pop("_run_name", None)
        _write_json(run_dir / "harness_status.json", status)
        _append_journal(run_dir, cont, status["cycle"])
    else:
        for r in runs.values():
            r.pop("_run_name", None)
        # STOP tick is read-only: don't re-persist. Report the cadence the
        # already-persisted state implies (no POLL-NOW on a STOP tick).
        next_minutes = _next_cadence(status, tick_interval, idle_mult)
        # FR-55: still EMIT the verdict (HALT:stop) for the trace/return, but a
        # STOP tick writes nothing (read-only) — no status persist, no journal.
        cont = _continuation(run_dir, status, stop, now, next_minutes)

    done = status["done"]
    table = _format_table(run_dir, status, plan, terminal=(done or stop))
    if not (done or stop) and status.get("cycle") == 1:
        # First-tick operator watch hints (read-only). Appended to the RETURNED
        # status_table only -- NOT inside _format_table -- so the `monitor` and
        # `status` commands (which re-render _format_table) don't echo them
        # recursively. ASCII only (cp1252 consoles, 185 print-path lesson).
        table += (
            "\nWatch this run (read-only, separate terminal):"
            "\n  snapshot: python3 -m arunner status %s"
            "\n  live:     python3 -m arunner monitor %s" % (run_dir, run_dir))
    return {
        "dispatch_list": dispatch_list,
        "status_table": table,
        "next_tick_minutes": next_minutes,
        "done": done,
        "stop": stop,
        "paused": bool(status.get("paused")),
        "continuation": cont,
    }


def _advance(run_dir, runs, now, stall_secs, grace_secs, suppress_stall=False,
             entries=None, plan=None):
    """Reap terminals, detect stalls and launch failures. Mutates run
    state + disk. Every transition is guarded for idempotency.

    ``suppress_stall`` (E2): set when this tick detected a wall-clock jump
    (machine slept/hibernated) — heartbeat ages are inflated this tick, so
    STALLED marking is skipped to avoid false stalls.

    FR-62: a multi-step run (entry has ``steps``) is advanced by
    ``_advance_multistep`` -- scoped to its current step's sub-run."""
    entries = entries or {}
    plan = plan or {}
    for name, r in runs.items():
        state = r["state"]
        if state in _TERMINAL_STATES:
            continue
        entry = entries.get(name)
        if _is_multistep(entry):
            _advance_multistep(run_dir, name, r, entry, now, stall_secs,
                               grace_secs, suppress_stall, plan)
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


# --- FR-62/63/64: multi-step advance + gate evaluation ----------------------

def _usage_of(hb: Path) -> dict:
    """FR-65: read EXACTLY ``data.usage = {input_tokens, output_tokens}`` from the
    terminal-or-latest heartbeat into top-level token fields. Reporting-only;
    nothing else in the opaque ``data`` is read. Malformed/absent -> {} (the
    caller renders '-' / 'partial', never a fabricated 0)."""
    out = {}
    for ln in reversed(_tail(hb)):
        try:
            obj = json.loads(ln)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        usage = obj.get("data", {}).get("usage") if isinstance(obj.get("data"), dict) else None
        if isinstance(usage, dict):
            for k in ("input_tokens", "output_tokens"):
                v = usage.get(k)
                if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                    out[k] = v
            if out:
                return out
    return out


_GATE_TIMEOUT_SECONDS = 30                 # a shell gate is a quick disk/exit probe


def _judge_dir(run_dir: Path, name: str, m: int) -> Path:
    return _step_dir(run_dir, name, m) / "gate"


def _judge_hb(run_dir: Path, name: str, m: int) -> Path:
    return _judge_dir(run_dir, name, m) / "heartbeat.ndjson"


def _gate_values(run_dir, name, m, step, entry):
    """The reserved-placeholder map for a gate (scoped to the step dir)."""
    sd = _step_dir(run_dir, name, m)
    return {
        "HEARTBEAT_PATH": str(_step_hb(run_dir, name, m)),
        "TASK_ID": str(entry.get("id", "")),
        "RUN_DIR": str(sd),
        "TARGET_REPO": str(step.get("repo") or entry.get("repo", "")),
        "HARNESS_BIN": _HARNESS_BIN,
    }


def _gate_vars(plan, entry, step, r):
    vmap = {}
    for vsrc in (plan.get("vars"), entry.get("vars"), step.get("vars"), r.get("flags")):
        if isinstance(vsrc, dict):
            vmap.update(vsrc)
    return vmap


def _persist_gate(run_dir, name, m, record) -> None:
    _write_json(_step_dir(run_dir, name, m) / "gate.json", record)


def _usage_malformed(hb: Path) -> bool:
    """FR-65: a terminal line carries a ``data.usage`` that yields no valid
    counts -> skipped-with-warning (never fatal)."""
    for ln in reversed(_tail(hb)):
        if _status_of_line(ln) in _TERMINAL_HB:
            try:
                obj = json.loads(ln)
            except (ValueError, TypeError):
                return False
            data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
            if "usage" in data and not _usage_of(hb):
                return True
            return False
    return False


def _evaluate_gate(run_dir, name, m, step, entry, plan, r=None) -> str:
    """FR-63/64: resolve the gate after step ``m`` completed clean -> a closed-set
    FR-64 outcome. No gate -> 'continue'. A persisted gate.json is read on resume
    and NEVER recomputed (NFR-6). A shell gate runs argv and maps the EXIT CODE
    (exit-code only -- no stdout/regex, FR-63 / Council FIX-5) to an outcome. A
    reasoning gate is handled out-of-band (a judge sub-run); this function is
    only reached for the deterministic shell path + persisted-verdict reads."""
    if not (isinstance(step, dict) and isinstance(step.get("gate"), dict)):
        return "continue"
    gp = _step_dir(run_dir, name, m) / "gate.json"
    if gp.exists():                                  # read-on-resume (NFR-6)
        try:
            return json.loads(gp.read_text(encoding="utf-8"))["outcome"]
        except (OSError, ValueError, KeyError):
            return "internal_error"
    gate = step["gate"]
    kind = gate.get("kind", "shell")
    if kind != "shell":
        # a reasoning gate is dispatched as a judge sub-run; never evaluated here
        return "continue"
    outcome = _eval_shell_gate(run_dir, name, m, step, entry, plan, gate, r)
    if not _valid_outcome(outcome):
        outcome = "internal_error"
    _persist_gate(run_dir, name, m, {"step": _step_name(m), "kind": "shell",
                                     "outcome": outcome, "ts": _utc_iso()})
    return outcome


def _eval_shell_gate(run_dir, name, m, step, entry, plan, gate, r) -> str:
    """Run the gate argv; map its EXIT CODE to an FR-64 outcome. exit-code only:
    no stdout is read (avoids the 'engine parses text' hazard, FR-40/41/56).

    The gate runs with cwd = the step/entry ``target_repo`` -- where the worker
    operated and where the artifacts the gate checks live -- so its success never
    depends on the orchestrator's incidental cwd. (Without this a relative-import
    or relative-path gate, e.g. `python3 -m bin.validate_phase_artifacts`, false-
    fails from the engine's cwd; surfaced 2026-06-19 in a QPB regression run.)"""
    argv_tpl = gate.get("argv")
    if not (isinstance(argv_tpl, list) and argv_tpl):
        return "internal_error"
    values = _gate_values(run_dir, name, m, step, entry)
    vmap = _gate_vars(plan, entry, step, r or {})
    argv = [_resolve_template(_apply_vars(str(tok), vmap), values) for tok in argv_tpl]
    try:
        proc = subprocess.run(argv, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=_GATE_TIMEOUT_SECONDS,
                              cwd=(values.get("TARGET_REPO") or None))
        rc = proc.returncode
    except (OSError, subprocess.SubprocessError):
        return "internal_error"
    outcomes = gate.get("outcomes") if isinstance(gate.get("outcomes"), dict) else {}
    if str(rc) in outcomes:
        return outcomes[str(rc)]
    if "default" in gate:
        return gate["default"]
    return "continue" if rc == 0 else "halt"        # convention when unmapped


def _judge_outcome_of(hb: Path):
    """Read the judge's structured verdict (FR-18 ``data.verdict``) from its
    terminal heartbeat -> a closed-set outcome string, or None (fail-closed)."""
    for ln in reversed(_tail(hb)):
        if _status_of_line(ln) in _TERMINAL_HB:
            try:
                obj = json.loads(ln)
            except (ValueError, TypeError):
                return None
            v = obj.get("data", {}).get("verdict") if isinstance(obj.get("data"), dict) else None
            if isinstance(v, dict):
                return v.get("outcome")
            return v if isinstance(v, str) else None
    return None


def _judge_identity_of(hb: Path) -> dict:
    """Record WHO judged (FR-63 rule 1: judge identity recorded)."""
    for ln in reversed(_tail(hb)):
        if _status_of_line(ln) in _TERMINAL_HB:
            try:
                return {"task_id": json.loads(ln).get("task_id"),
                        "verdict_source": "data.verdict"}
            except (ValueError, TypeError):
                break
    return {"verdict_source": "data.verdict"}


def _advance_judge(run_dir, name, r, entry, now, stall_secs, grace_secs,
                   suppress_stall, plan):
    """FR-63 reasoning gate: advance the JUDGE sub-run (a distinct judging
    context at run-NN/steps/step-MM/gate/). On the judge's clean terminal, read
    the structured verdict -> outcome (closed set; malformed/absent/FAILED ->
    internal_error fail-closed), persist gate.json with the judge identity, then
    apply the outcome. Verdict computed at most once, persisted, never recomputed."""
    m = int(r["gate_pending"])
    step = entry["steps"][m]
    hb = _judge_hb(run_dir, name, m)
    has_any, last_status, _a, mtime = _hb_observe(hb)
    if has_any:
        r["last_hb_status"] = last_status
    terminal = _terminal_status_of(hb)

    def _finish(outcome, judge):
        if not _valid_outcome(outcome):
            outcome = "internal_error"
        _persist_gate(run_dir, name, m, {"step": _step_name(m), "kind": "reasoning",
                                         "outcome": outcome, "judge": judge,
                                         "ts": _utc_iso()})
        r.pop("gate_pending", None)
        _apply_gate_outcome(run_dir, name, r, entry, m, outcome, now)

    if terminal is not None:
        if terminal == "COMPLETED":
            _finish(_judge_outcome_of(hb), _judge_identity_of(hb))
        else:                                       # judge FAILED -> fail-closed
            _finish("internal_error", _judge_identity_of(hb))
        return
    pid = _lock_pid_judge(run_dir, name, m)
    if pid is not None and not _pid_alive(pid):
        _finish("internal_error", {"verdict_source": "data.verdict"})
        return
    if r["state"] == "claimed" and not has_any:
        if r.get("claimed_at") is None:
            r["claimed_at"] = now
        elif (now - r["claimed_at"]) > grace_secs:  # judge never launched
            _finish("internal_error", {"verdict_source": "data.verdict"})
        return
    if has_any and mtime is not None:
        if (now - mtime) > stall_secs and not suppress_stall:
            r["state"] = "stalled"
        elif r["state"] in ("claimed", "stalled"):
            r["state"] = "running"


def _lock_pid_judge(run_dir: Path, name: str, m: int):
    lock = _judge_dir(run_dir, name, m) / "claim.lock"
    if not lock.is_file():
        return None
    try:
        return json.loads(lock.read_text(encoding="utf-8", errors="replace")).get("pid")
    except (OSError, ValueError):
        return None


def _dispatch_judge(run_dir, name, r, entry, now, plan) -> dict:
    """FR-63: dispatch the reasoning gate's judge as a DISTINCT subagent context
    (run-NN/steps/step-MM/gate/). The judged step's artifacts are referenced via
    the step dir; the judge writes a structured ``data.verdict`` terminal."""
    m = int(r["gate_pending"])
    step = entry["steps"][m]
    gate = step["gate"]
    jd = _judge_dir(run_dir, name, m)
    jd.mkdir(parents=True, exist_ok=True)
    hbp = jd / "heartbeat.ndjson"
    if not hbp.exists():
        hbp.touch()
    values = {
        "HEARTBEAT_PATH": str(_judge_hb(run_dir, name, m)),
        "TASK_ID": "%s-%s-gate" % (entry.get("id", ""), _step_name(m)),
        "RUN_DIR": str(jd),
        "TARGET_REPO": str(step.get("repo") or entry.get("repo", "")),
        "HARNESS_BIN": _HARNESS_BIN,
        "STEP_DIR": str(_step_dir(run_dir, name, m)),   # the judged artifacts
    }
    vmap = _gate_vars(plan, entry, step, r)
    judge_prompt = gate.get("judge_prompt", "")          # judge_prompt_file snapshotted at init
    prompt = _resolve_template(_apply_vars(judge_prompt, vmap), values)
    r["state"] = "claimed"
    r["claimed_at"] = now
    _log(run_dir, "%s: dispatched %s reasoning-gate judge (claimed)"
         % (name, _step_name(m)))
    return {"run": name, "step": "%s-gate" % _step_name(m),
            "task_id": values["TASK_ID"], "dispatch_mode": "subagent",
            "worker_prompt": prompt}


def _lock_pid_step(run_dir: Path, name: str, m: int):
    lock = _step_dir(run_dir, name, m) / "claim.lock"
    if not lock.is_file():
        return None
    try:
        return json.loads(lock.read_text(encoding="utf-8", errors="replace")).get("pid")
    except (OSError, ValueError):
        return None


def _reap_step(run_dir: Path, name: str, m: int, terminal: str, hb: Path) -> None:
    """Write run-NN/steps/step-MM/result.json from the step's terminal sentinel.
    Idempotent (resume reads it; a reaped step is never re-run -- NFR-6)."""
    rp = _step_dir(run_dir, name, m) / "result.json"
    if rp.exists():
        return
    meta = _result_meta(hb)
    rec = {"step": _step_name(m), "step_index": m, "terminal_status": terminal,
           "result_file": meta.get("result_file"), "summary": meta.get("summary"),
           "reaped_ts": _utc_iso()}
    usage = _usage_of(hb)                              # FR-65 per-step tokens
    if usage:
        rec.update(usage)
    _write_json(rp, rec)


def _reap_step_synth(run_dir: Path, name: str, m: int, terminal: str, summary: str) -> None:
    """Synthesized step result (no worker terminal heartbeat -- launch fail /
    dead PID). Idempotent."""
    rp = _step_dir(run_dir, name, m) / "result.json"
    if rp.exists():
        return
    _write_json(rp, {"step": _step_name(m), "step_index": m,
                     "terminal_status": terminal, "result_file": None,
                     "summary": summary, "reaped_ts": _utc_iso(),
                     "synthesized": True})


def _collect_step_results(run_dir: Path, name: str, count: int) -> list:
    out = []
    for m in range(count):
        rp = _step_dir(run_dir, name, m) / "result.json"
        try:
            out.append(json.loads(rp.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            out.append({"step": _step_name(m), "terminal_status": None})
    return out


def _write_entry_result(run_dir: Path, name: str, r: dict,
                        terminal_status: str, summary: str) -> None:
    """The entry-level results/result-NNNNN.json for a multi-step entry once it
    reaches a terminal state -- SUMMARY (FR-45) + token roll-up (FR-65) source.
    Carries the per-step records under ``steps``. Idempotent."""
    job_id = r["job_id"]
    rp = run_dir / "results" / (job_id.replace("job-", "result-") + ".json")
    if rp.exists():
        return
    steps = _collect_step_results(run_dir, name, int(r.get("step_count") or 0))
    rec = {"job_id": job_id, "task_id": r.get("task_id"),
           "terminal_status": terminal_status, "result_file": None,
           "summary": summary, "reaped_ts": _utc_iso(), "synthesized": True,
           "steps": steps}
    acc = [None, None]                    # FR-65: additive per-step roll-up
    for s in steps:
        _add_tok(acc, s)
    if acc[0] is not None:
        rec["input_tokens"] = acc[0]
    if acc[1] is not None:
        rec["output_tokens"] = acc[1]
    _write_json(rp, rec)


def _apply_gate_outcome(run_dir: Path, name: str, r: dict, entry: dict,
                        m: int, outcome: str, now: float) -> None:
    """FR-64: apply a gate outcome after step ``m`` completed clean. Closed set:
    continue / halt / skip-to-next[ :step-MM] / behavior-flag:<name> /
    internal_error. Advances step_index, synthesizes ``skipped`` terminals,
    records behavior-flags as next-step vars, or terminals the entry."""
    steps = entry["steps"]
    total = len(steps)

    def _advance_to(next_m: int) -> None:
        if next_m >= total:
            r["state"] = "completed"
            _write_entry_result(run_dir, name, r, "COMPLETED",
                                "all %d step(s) completed" % total)
            _log(run_dir, "%s: entry COMPLETED (%d steps)" % (name, total))
        else:
            r["step_index"] = next_m
            r["state"] = "queued"
            r["claimed_at"] = None
            _scaffold_step(run_dir, name, next_m, entry, steps[next_m])
            _log(run_dir, "%s: advance to %s" % (name, _step_name(next_m)))

    if outcome == "halt":
        r["state"] = "failed"
        _write_entry_result(run_dir, name, r, "FAILED",
                            "gate halt after %s" % _step_name(m))
        _log(run_dir, "%s: entry HALTED by gate after %s" % (name, _step_name(m)))
    elif outcome == "internal_error":
        r["state"] = "failed"
        _write_entry_result(run_dir, name, r, "FAILED",
                            "gate internal_error after %s (fail-closed)" % _step_name(m))
        _log(run_dir, "%s: entry FAILED (gate internal_error)" % name)
    elif outcome == "continue":
        _advance_to(m + 1)
    elif outcome.startswith("behavior-flag:"):
        flag = outcome.split(":", 1)[1]
        flags = r.setdefault("flags", {})
        flags[flag] = "1"                 # exposed to the next step as a {var}
        _log(run_dir, "%s: behavior-flag %r set after %s" % (name, flag, _step_name(m)))
        _advance_to(m + 1)
    elif outcome == "skip-to-next" or outcome.startswith("skip-to-next:"):
        target = outcome.split(":", 1)[1] if ":" in outcome else None
        # default: skip the immediately-following step; or skip forward to a
        # named step-MM. Skipped steps get a synthesized auditable `skipped`.
        if target and _STEP_ID_RE.match(target):
            dest = int(target.split("-")[1]) - 1
        else:
            dest = m + 2                  # skip step m+1, resume at m+2
        for s in range(m + 1, min(dest, total)):
            _scaffold_step(run_dir, name, s, entry, steps[s])
            _reap_step_synth(run_dir, name, s, "SKIPPED",
                             "skipped by gate after %s" % _step_name(m))
            _log(run_dir, "%s: %s skipped by gate" % (name, _step_name(s)))
        _advance_to(min(dest, total))
    else:
        # out-of-set -> fail-closed internal_error (defense in depth)
        _apply_gate_outcome(run_dir, name, r, entry, m, "internal_error", now)


def _advance_multistep(run_dir, name, r, entry, now, stall_secs, grace_secs,
                       suppress_stall, plan):
    """FR-62: reap/advance ONE multi-step run. Mirrors _advance but scoped to the
    current step's sub-run, advancing step_index on a clean terminal (via the
    FR-63 gate). Idempotent; a terminal entry is skipped."""
    state = r["state"]
    if state in _TERMINAL_STATES:
        return
    if r.get("gate_pending") is not None:    # FR-63: a reasoning-gate judge is live
        _advance_judge(run_dir, name, r, entry, now, stall_secs, grace_secs,
                       suppress_stall, plan)
        return
    m = int(r.get("step_index", 0))
    steps = entry["steps"]
    if m >= len(steps):                      # defensive: nothing to advance
        return
    step = steps[m]
    if state == "queued":
        return                               # not yet dispatched -> _dispatch
    hb = _step_hb(run_dir, name, m)
    has_any, last_status, _activity, mtime = _hb_observe(hb)
    if has_any:
        r["last_hb_status"] = last_status
    bad = _count_malformed(hb)
    if bad:
        _log(run_dir, "%s/%s: WARN %d malformed heartbeat line(s) skipped"
             % (name, _step_name(m), bad))
    terminal = _terminal_status_of(hb)
    if terminal is not None:
        _reap_step(run_dir, name, m, terminal, hb)
        if terminal in ("FAILED", "ABANDONED"):
            r["state"] = "failed"
            _write_entry_result(run_dir, name, r, "FAILED",
                                "%s %s" % (_step_name(m), terminal))
            _log(run_dir, "%s: entry FAILED at %s (%s)"
                 % (name, _step_name(m), terminal))
            return
        gate = step.get("gate") if isinstance(step.get("gate"), dict) else None
        gp_done = (_step_dir(run_dir, name, m) / "gate.json").exists()
        if gate and gate.get("kind") == "reasoning" and not gp_done:
            # FR-63: enter judging -- a DISTINCT judge sub-run produces the
            # structured verdict; we dispatch it next and read it on a later tick.
            r["gate_pending"] = m
            r["state"] = "queued"
            r["claimed_at"] = None
            jd = _judge_dir(run_dir, name, m)
            jd.mkdir(parents=True, exist_ok=True)
            (jd / "heartbeat.ndjson").touch(exist_ok=True)
            _log(run_dir, "%s: %s reasoning gate -> judging" % (name, _step_name(m)))
            return
        outcome = _evaluate_gate(run_dir, name, m, step, entry, plan, r)
        _apply_gate_outcome(run_dir, name, r, entry, m, outcome, now)
        return
    # no terminal: dead-PID (shell), launch grace, stall -- scoped to the step
    pid = _lock_pid_step(run_dir, name, m)
    if pid is not None and not _pid_alive(pid):
        _reap_step_synth(run_dir, name, m, "FAILED",
                         "shell step process %s exited without a terminal "
                         "heartbeat" % pid)
        r["state"] = "failed"
        _write_entry_result(run_dir, name, r, "FAILED",
                            "%s shell process died" % _step_name(m))
        _log(run_dir, "%s: entry FAILED (%s dead PID %s)" % (name, _step_name(m), pid))
        return
    if state == "claimed" and not has_any:
        claimed_at = r.get("claimed_at")
        if claimed_at is None:
            r["claimed_at"] = now
        elif (now - claimed_at) > grace_secs:
            _reap_step_synth(run_dir, name, m, "FAILED", _LAUNCH_FAIL_HINT)
            r["state"] = "auth_or_launch_failed"
            _write_entry_result(run_dir, name, r, "FAILED",
                                "%s launch failed" % _step_name(m))
            _log(run_dir, "%s: AUTH_OR_LAUNCH_FAILED at %s" % (name, _step_name(m)))
        return
    if has_any:
        if mtime is None:
            pass
        elif (now - mtime) > stall_secs and not suppress_stall:
            if r["state"] != "stalled":
                r["state"] = "stalled"
                _log(run_dir, "%s/%s: STALLED" % (name, _step_name(m)))
        else:
            if r["state"] in ("claimed", "stalled"):
                r["state"] = "running"


def _adapter_worker_cmd(entry: dict, plan: dict = None):
    """Synthesize the shell worker_cmd for a ``command``/``log`` mode job/step —
    so the operator declares intent in ONE place (the ``command`` for `command`
    mode, or the ``log_path`` + optional markers for `log` mode) and never
    hand-wires the heartbeat.py invocation. `command` mode -> the `wrap` helper
    (run+watch, doneness = exit code); `log` mode -> the `tail` helper (watch a
    log a job writes, optionally launching it). Returns a token list (with
    placeholders the dispatch resolver fills), or None for `agent`/`shell` (a
    subagent prompt / a raw operator-wired argv — nothing to synthesize).

    FR-58a: synthesize ``--launch-grace-minutes`` / ``--stall-threshold-minutes``
    / ``--keepalive-seconds`` from the entry (override) or the plan, so all three
    flow to the adapter. Before this they were NEVER synthesized — the adapter
    fell back to hardcoded 10/45 and a ~10-min keepalive, making the plan's
    grace/stall inert for adapter jobs and the FR-56 activity patterns never fire
    on a normal-length job. (Entry-level wins over plan-level wins over default.)"""
    adapter = _MODE_ADAPTER.get(entry.get("mode"))
    if not adapter:
        return None
    plan = plan or {}

    def _knob(name, default):
        v = entry.get(name)
        if v is None:
            v = plan.get(name)
        return default if v is None else v

    grace = _knob("launch_grace_minutes", DEFAULT_LAUNCH_GRACE_MINUTES)
    stall = _knob("stall_threshold_minutes", DEFAULT_STALL_THRESHOLD_MINUTES)
    keepalive = _knob("keepalive_seconds", DEFAULT_KEEPALIVE_SECONDS)
    helper = ["python3", "{HARNESS_BIN}/heartbeat.py", str(adapter),
              "--task-id", "{TASK_ID}", "--heartbeat-path", "{HEARTBEAT_PATH}",
              "--launch-grace-minutes", str(grace),
              "--stall-threshold-minutes", str(stall),
              "--keepalive-seconds", str(keepalive)]
    # FR-56: operator activity patterns -> repeated --activity-regex (BOTH
    # adapters). Passed as argv elements, never through a shell. (Footgun, not
    # injection: a pattern containing a literal {PLACEHOLDER}-shaped token would
    # be rewritten by the engine's template substitution -- documented in FR-56.)
    activity = []
    for pat in (entry.get("adapter_activity_patterns") or []):
        activity += ["--activity-regex", str(pat)]
    if adapter == "wrap":
        return (helper + activity + ["--"]
                + [str(t) for t in (entry.get("command") or [])])
    if adapter == "tail":
        out = helper + activity + ["--log-file", str(entry.get("log_path", "")),
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


def _holds_slot(r: dict) -> bool:
    """FR-62: a run occupies a pool slot if it is in-flight OR a multi-step run
    that has STARTED and is not yet terminal (it keeps its one slot across the
    whole step sequence, including the brief between-steps ``queued`` window)."""
    if r["state"] in _INFLIGHT_STATES:
        return True
    return bool(r.get("started")) and r["state"] not in _TERMINAL_STATES


def _dispatch_step(run_dir, name, r, entry, now, plan) -> dict:
    """FR-62: dispatch the CURRENT step of a multi-step run as its own sub-run
    (run-NN/steps/step-MM/). Mirrors the single-prompt dispatch but scoped to the
    step dir; vars merge plan<entry<step<behavior-flags (FR-61/64)."""
    m = int(r.get("step_index", 0))
    step = entry["steps"][m]
    _scaffold_step(run_dir, name, m, entry, step)        # idempotent
    sd = _step_dir(run_dir, name, m)
    adapter_cmd = _adapter_worker_cmd(step, plan)        # command/log modes
    mode = _dispatch_mode_of(step)                       # subagent | shell
    values = {
        "HEARTBEAT_PATH": str(_step_hb(run_dir, name, m)),
        "TASK_ID": str(entry.get("id", "")),
        "RUN_DIR": str(sd),
        "TARGET_REPO": str(step.get("repo") or entry.get("repo", "")),
        "HARNESS_BIN": _HARNESS_BIN,
    }
    # FR-61/64: plan < entry < step vars < behavior-flags recorded by a prior gate.
    vmap = {}
    for vsrc in (plan.get("vars"), entry.get("vars"), step.get("vars"), r.get("flags")):
        if isinstance(vsrc, dict):
            vmap.update(vsrc)
    raw = _apply_vars(step.get("prompt", ""), vmap)
    # agent steps get the auto-injected placeholder preamble; shell-dispatch
    # steps (command/log/shell) carry no prompt (their {PROMPT_FILE} is empty).
    if step.get("mode") == "agent":
        raw = _inject_preamble(raw)
    prompt = _resolve_template(raw, values)
    r["state"] = "claimed"
    r["claimed_at"] = now
    r["started"] = True
    r["dispatch_mode"] = mode
    if mode == "shell":
        prompt_file = sd / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        lock_file = sd / "claim.lock"
        _write_json(lock_file, {"task_id": r["task_id"], "claimed_ts": _utc_iso(),
                                "dispatched_by": "arunner_tick", "step": _step_name(m),
                                "dispatch_mode": mode, "pid": None})
        sh_values = dict(values, PROMPT_FILE=str(prompt_file), LOCK_FILE=str(lock_file))
        cmd_template = (adapter_cmd if adapter_cmd is not None
                        else (step.get("command") or []))
        worker_cmd = [_resolve_template(tok, sh_values) for tok in cmd_template]
        _log(run_dir, "%s: dispatched %s (claimed, shell)" % (name, _step_name(m)))
        return {"run": name, "step": _step_name(m), "task_id": r["task_id"],
                "dispatch_mode": "shell", "worker_cmd": worker_cmd,
                "prompt_file": str(prompt_file), "heartbeat_path": values["HEARTBEAT_PATH"],
                "run_dir": values["RUN_DIR"], "target_repo": values["TARGET_REPO"],
                "auth_check": step.get("auth_check")}
    _log(run_dir, "%s: dispatched %s (claimed, subagent)" % (name, _step_name(m)))
    return {"run": name, "step": _step_name(m), "task_id": r["task_id"],
            "dispatch_mode": "subagent", "worker_prompt": prompt}


def _dispatch(run_dir, runs, entries, pool_size, now, plan=None) -> list[dict]:
    """Emit dispatch entries for queued runs while a pool slot is free.
    Guarded: a run already past queued is never re-dispatched.

    dispatch_mode == "subagent" (default): the entry carries a resolved
    ``worker_prompt`` for the orchestrator agent to launch via its
    subagent tool. dispatch_mode == "shell": the prompt is written to
    queue/job-NNNNN.prompt.txt (quoting/arg-length safety, FR-15) and the
    entry carries a resolved ``worker_cmd`` argv for the ticker to Popen
    detached; the placeholder block adds {PROMPT_FILE}.

    FR-62: a multi-step run dispatches only its CURRENT step; an already-started
    multi-step run keeps its slot, so advancing to the next step never re-checks
    the pool (only a FRESH run needs a free slot)."""
    plan = plan or {}
    inflight = sum(1 for r in runs.values() if _holds_slot(r))
    out: list[dict] = []
    for name in sorted(runs):
        r = runs[name]
        if r["state"] != "queued":
            continue
        entry = entries[name]
        # A started multi-step run between steps already holds its slot; a fresh
        # run needs a free one. (continue, not break -- a later started run may
        # still be eligible while a fresh one is pool-gated.)
        fresh = not r.get("started")
        if fresh and inflight >= pool_size:
            continue
        if _is_multistep(entry):
            if r.get("gate_pending") is not None:    # FR-63: dispatch the judge
                out.append(_dispatch_judge(run_dir, name, r, entry, now, plan))
            else:
                out.append(_dispatch_step(run_dir, name, r, entry, now, plan))
            if fresh:
                inflight += 1
            continue
        adapter_cmd = _adapter_worker_cmd(entry, plan)   # command/log: synthesized heartbeat wrapper
        mode = _dispatch_mode_of(entry)                  # subagent | shell
        values = {
            "HEARTBEAT_PATH": str(_heartbeat_path(run_dir, name)),
            "TASK_ID": str(entry.get("id", "")),
            "RUN_DIR": str(run_dir / name),
            "TARGET_REPO": str(entry.get("repo", "")),
            "HARNESS_BIN": _HARNESS_BIN,
        }
        src = run_dir / "claimed" / (r["job_id"] + ".json")
        qsrc = run_dir / "queue" / (r["job_id"] + ".json")
        if qsrc.exists() and not src.exists():
            qsrc.replace(src)
            _write_json(run_dir / "claimed" / (r["job_id"] + ".lock"), {
                "task_id": r["task_id"],
                "claimed_ts": _utc_iso(),
                "dispatched_by": "arunner_tick",
                "dispatch_mode": mode,
                "pid": None,
            })
        r["state"] = "claimed"
        r["claimed_at"] = now
        r["dispatch_mode"] = mode
        r["started"] = True
        inflight += 1
        # FR-61: apply the designated {var} pass BEFORE the reserved placeholders.
        raw = _apply_vars(entry.get("prompt", ""), _entry_vars(plan, entry))
        # agent mode gets the auto-injected placeholder preamble (settled
        # decision 3); shell-dispatch modes carry no authored prompt.
        if entry.get("mode") == "agent":
            raw = _inject_preamble(raw)
        prompt = _resolve_template(raw, values)
        if mode == "shell":
            # Write the prompt to a file (quoting/arg-length safety) and
            # resolve the worker_cmd template; the ticker Popens it detached.
            prompt_file = run_dir / "queue" / (r["job_id"] + ".prompt.txt")
            prompt_file.write_text(prompt, encoding="utf-8")
            lock_file = run_dir / "claimed" / (r["job_id"] + ".lock")
            sh_values = dict(values, PROMPT_FILE=str(prompt_file),
                             LOCK_FILE=str(lock_file))   # FR-41 PID backstop
            cmd_template = (adapter_cmd if adapter_cmd is not None
                            else (entry.get("command") or []))
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


def _hb_age_str(run_dir, name, hb=None) -> str:
    hb = hb if hb is not None else _heartbeat_path(run_dir, name)
    if not hb.exists():
        return "-"
    try:
        age = int(_now() - hb.stat().st_mtime)
    except OSError:
        return "-"
    return "%dm%02ds" % (age // 60, age % 60)


# --- FR-65: token reporting (input + output) --------------------------------

def _add_tok(acc, rec):
    """Accumulate input/output_tokens from a record into acc (a 2-list of
    int-or-None). A present int counts; a missing value leaves None so the
    aggregate stays honestly unreported (NFR-12 -- never a fabricated 0)."""
    for i, k in enumerate(("input_tokens", "output_tokens")):
        v = rec.get(k) if isinstance(rec, dict) else None
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            acc[i] = (acc[i] or 0) + v


def _run_tokens(run_dir: Path, name: str, r: dict):
    """(input, output) token totals for a run -- summed across steps for a
    multi-step entry (per-step result, else the step's live heartbeat), or the
    single result record / live heartbeat for a single-prompt run. None where
    unreported (rendered '-', never 0)."""
    acc = [None, None]
    if r.get("step_count"):
        for m in range(int(r["step_count"])):
            rp = _step_dir(run_dir, name, m) / "result.json"
            if rp.exists():
                try:
                    _add_tok(acc, json.loads(rp.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
            else:
                _add_tok(acc, _usage_of(_step_hb(run_dir, name, m)))
    else:
        rec = _read_result_record(run_dir, r.get("job_id"))
        _add_tok(acc, rec if rec else _usage_of(_run_hb_path(run_dir, name, r)))
    return acc[0], acc[1]


def _tokens_cell(inp, out) -> str:
    if inp is None and out is None:
        return "-"
    return "%s/%s" % ("-" if inp is None else inp, "-" if out is None else out)


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
    fmt = "%-5s%-22s%-8s%-13s%-16s%-13s%-9s%s"
    rows = [
        "Run-Dir: %s (cycle %d)" % (run_dir.name, status.get("cycle", 0)),
        bar,
        fmt % ("RUN", "REPO", "MODE", "STATE", "ACTIVITY", "LAST-HB",
               "HB-AGE", "TOKENS"),
    ]
    any_launch_fail = False
    for name in sorted(status["runs"]):
        r = status["runs"][name]
        hb = _run_hb_path(run_dir, name, r)          # FR-62: current step's hb
        _, _, activity, _ = _hb_observe(hb)
        # FR-62: a multi-step run shows "step N of M" alongside the step label.
        if r.get("step_count"):
            tag = "s%d/%d" % (int(r.get("step_index", 0)) + 1, int(r["step_count"]))
            activity = "%s %s" % (tag, activity) if activity else tag
        st = r["state"]
        if st == "auth_or_launch_failed":
            any_launch_fail = True
        inp, outp = _run_tokens(run_dir, name, r)        # FR-65 TOKENS column
        rows.append(fmt % (
            name[4:],
            _ascii_trunc(r.get("target_repo") or "-", 21),
            {"subagent": "subgnt", "shell": "shell"}.get(
                r.get("dispatch_mode"), "-"),
            _STATE_DISPLAY.get(st, st)[:12],
            _ascii_trunc(activity, 15),
            r.get("last_hb_status") or "-",
            _hb_age_str(run_dir, name, hb),
            _tokens_cell(inp, outp),
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
          "arunner tick (--init <plan-path> | <run-dir>)")


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


def _arunner_version() -> str:
    """The single canonical version (FR-34): arunner/__init__.py:__version__.
    The engine ships inside the ``arunner`` package (arunner/engine/), so the
    version file is the package __init__ one level up -- read by path rather
    than importing, so one source feeds every surface identically."""
    init = Path(__file__).resolve().parent.parent / "__init__.py"
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

# Strict-keys (additionalProperties:false) allowed-key sets, per mode. The
# --check validator rejects any unknown job/step key (a typo like `promt`/`repoo`
# fails here, not silently at dispatch), in lockstep with plan.schema.json.
_COMMON_JOB_KEYS = frozenset(("id", "repo", "mode", "description", "_comment"))
_ADAPTER_OPT_KEYS = frozenset(("adapter_activity_patterns", "keepalive_seconds",
                               "launch_grace_minutes", "stall_threshold_minutes"))
_MODE_JOB_KEYS = {
    "agent": frozenset(("prompt", "prompt_file", "vars", "heartbeat_path")),
    "command": frozenset(("command", "auth_check", "vars", "heartbeat_path")) | _ADAPTER_OPT_KEYS,
    "log": frozenset(("log_path", "command", "success_regex", "failure_regex",
                      "sentinel_file", "pid", "vars", "heartbeat_path")) | _ADAPTER_OPT_KEYS,
    "pipeline": frozenset(("steps", "vars")),
    "shell": frozenset(("command", "auth_check", "vars", "heartbeat_path")),
}
_COMMON_STEP_KEYS = frozenset(("mode", "label", "repo", "description", "_comment",
                               "vars", "gate"))
_MODE_STEP_KEYS = {
    "agent": frozenset(("prompt", "prompt_file")),
    "command": frozenset(("command", "auth_check")) | _ADAPTER_OPT_KEYS,
    "log": frozenset(("log_path", "command", "success_regex", "failure_regex",
                      "sentinel_file", "pid")) | _ADAPTER_OPT_KEYS,
    "shell": frozenset(("command", "auth_check")),
}
# Top-level optional knobs that, IF present, must be integers >= 1 (mirrors
# plan.schema.json minimums; defaults live in the engine if omitted).
_PLAN_INT_KEYS = ("tick_interval_minutes", "pool_size", "stall_threshold_minutes",
                  "launch_grace_minutes", "idle_tick_multiplier")
# The closed set of plan-root keys (additionalProperties:false, == plan.schema.json).
_PLAN_KEYS = frozenset(_PLAN_INT_KEYS + (
    "schema_version", "description", "_comment", "keepalive_seconds", "vars",
    "defaults", "allow_reasoning_gates", "measurement", "jobs"))
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


# FR-56: bounds on operator activity patterns (a ReDoS surface — see FR-56).
_ACTIVITY_PATTERN_CAP = 16
_ACTIVITY_PATTERN_MAX_LEN = 500


def _regex_complexity_problem(pattern: str):
    """Conservative ReDoS HEURISTIC (FR-56): reject a pattern with a nested
    unbounded quantifier (a MAX_REPEAT/MIN_REPEAT directly inside another — the
    classic catastrophic-backtracking shape, e.g. ``(a+)+``) or one over a
    length cap. NOT a proof — stdlib `re` has no match timeout and the `regex`
    module is barred (NFR-3); this catches the common shape, not all ReDoS."""
    if len(pattern) > _ACTIVITY_PATTERN_MAX_LEN:
        return "exceeds the %d-char complexity cap" % _ACTIVITY_PATTERN_MAX_LEN
    # Resolve the stdlib regex parser PORTABLY (instr 039): `re._parser` exists
    # only on Python 3.11+; on <=3.10 it is `sre_parse` (re._parser is just the
    # renamed sre_parse). An earlier version did `__import__("re")._parser`
    # unconditionally — on 3.10 that raises AttributeError, which a blanket
    # `except` swallowed into a silent "no problem", DISABLING this ReDoS screen
    # on a supported Python. Resolve the parser without swallowing it; only the
    # parse() call (a genuine compile error) returns None (handled by
    # _regex_problem's re.compile, which always backstops --check).
    try:
        _parser = __import__("re")._parser            # 3.11+
    except AttributeError:
        try:
            import sre_parse as _parser               # <=3.10
        except ImportError:
            return None     # no stdlib parser (impossible on CPython 3.10-3.14);
            #                 re.compile() in _regex_problem still validates the pattern
    try:
        parsed = _parser.parse(pattern)
    except Exception:
        return None          # a genuine compile error — reported by _regex_problem

    def _walk(seq, in_repeat):
        for op, av in seq:
            name = getattr(op, "name", str(op))
            if name in ("MAX_REPEAT", "MIN_REPEAT"):
                if in_repeat:
                    return True
                if _walk(av[2], True):
                    return True
            elif name == "BRANCH":
                for branch in (av[1] or []):
                    if _walk(branch, in_repeat):
                        return True
            elif name == "SUBPATTERN":
                # NB: re._parser.SubPattern is iterable via the sequence
                # protocol (__getitem__/__len__) but has NO __iter__ — so never
                # guard on hasattr(__iter__); recurse and let _walk's for-loop
                # iterate it (a non-sequence av raises, caught by the outer try).
                if _walk(av[-1], in_repeat):
                    return True
            elif name in ("ASSERT", "ASSERT_NOT"):
                if _walk(av[1], in_repeat):
                    return True
        return False
    try:
        if _walk(parsed, False):
            return "nested unbounded quantifier (catastrophic-backtracking shape)"
    except Exception:
        return None
    return None


def _regex_problem(pattern: str):
    """Compile + complexity-screen a regex. Returns a problem phrase or None."""
    import re as _re
    try:
        _re.compile(pattern)
    except _re.error as exc:
        return "not a valid regex (%s)" % exc
    return _regex_complexity_problem(pattern)


def _check_activity_patterns(tag: str, e: dict, problems: list) -> None:
    """FR-56: validate ``adapter_activity_patterns`` (both adapters)."""
    pats = e.get("adapter_activity_patterns")
    if pats is None:
        return
    if not isinstance(pats, list):
        problems.append("%s.adapter_activity_patterns: must be an array of "
                        "regex strings" % tag)
        return
    if len(pats) > _ACTIVITY_PATTERN_CAP:
        problems.append("%s.adapter_activity_patterns: at most %d patterns "
                        "(got %d)" % (tag, _ACTIVITY_PATTERN_CAP, len(pats)))
    for j, pat in enumerate(pats):
        sub = "%s.adapter_activity_patterns[%d]" % (tag, j)
        if not isinstance(pat, str):
            problems.append("%s: must be a string" % sub)
            continue
        if pat == "":
            problems.append("%s: an empty pattern matches every line and "
                            "silently defeats the filter" % sub)
            continue
        prob = _regex_problem(pat)
        if prob:
            problems.append("%s: %s: %r" % (sub, prob, pat))


def _check_command_mode(tag: str, e: dict) -> list:
    """`command` mode: arunner runs+watches an argv and SYNTHESIZES the heartbeat
    plumbing (doneness = exit code; operator wires nothing). Requires a non-empty
    `command` argv. FR-56 activity patterns optional."""
    p = []
    cmd = e.get("command")
    if not (isinstance(cmd, list) and cmd and all(isinstance(t, str) for t in cmd)):
        p.append("%s.command: command mode requires a non-empty array of strings" % tag)
    _check_activity_patterns(tag, e, p)
    return p


def _check_log_mode(tag: str, e: dict) -> list:
    """`log` mode: arunner watches a log a job writes (optionally launching it via
    `command`). Requires `log_path`; optional doneness overlays + activity
    patterns; all regexes compiled + complexity-screened (FR-56)."""
    p = []
    if not (isinstance(e.get("log_path"), str) and e.get("log_path")):
        p.append("%s.log_path: log mode requires a non-empty string" % tag)
    if "command" in e and not (isinstance(e["command"], list)
                               and all(isinstance(t, str) for t in e["command"])):
        p.append("%s.command: must be an array of strings" % tag)
    for key in ("success_regex", "failure_regex", "sentinel_file"):
        if key in e and not isinstance(e[key], str):
            p.append("%s.%s: must be a string" % (tag, key))
    if "pid" in e and not (isinstance(e["pid"], int) and not isinstance(e["pid"], bool)):
        p.append("%s.pid: must be an integer" % tag)
    for key in ("success_regex", "failure_regex"):
        if isinstance(e.get(key), str) and e[key]:
            prob = _regex_problem(e[key])
            if prob:
                p.append("%s.%s: %s: %r" % (tag, key, prob, e[key]))
    _check_activity_patterns(tag, e, p)
    return p


def _check_shell_mode(tag: str, e: dict) -> list:
    """`shell` mode: the raw-argv escape hatch — the operator wires the heartbeat
    themselves. Requires a non-empty `command` that carries the {HEARTBEAT_PATH}
    route (no synthesis happens). auth_check type-checked separately."""
    p = []
    cmd = e.get("command")
    if not (isinstance(cmd, list) and cmd and all(isinstance(t, str) for t in cmd)):
        p.append("%s.command: shell mode requires a non-empty array of strings" % tag)
        return p
    hb = "{%s}" % _HEARTBEAT_PLACEHOLDER
    cmd_text = " ".join(t for t in cmd if isinstance(t, str))
    if hb not in cmd_text:
        p.append("%s: shell mode has no route for %s -- a shell job wires its own "
                 "heartbeat, so its command must carry %s (use command/log mode to "
                 "have arunner synthesize it)" % (tag, hb, hb))
    # typo / drift catch on UPPERCASE {TOKEN}s in the raw argv (FR-61).
    for tok in sorted(set(_PLACEHOLDER_TOKEN_RE.findall(cmd_text))):
        if tok not in _KNOWN_PLACEHOLDERS:
            p.append("%s: unknown placeholder {%s} (known: %s)"
                     % (tag, tok, ", ".join(sorted(_KNOWN_PLACEHOLDERS))))
    return p


# --- FR-63/64: continuation gates --------------------------------------------
_GATE_KINDS = ("shell", "reasoning")
# FR-64 closed outcome set (plus the parametric ``behavior-flag:<name>`` and the
# parametric ``skip-to-next:step-MM`` -- both honored by _apply_gate_outcome).
_GATE_OUTCOME_SET = frozenset(("continue", "halt", "skip-to-next", "internal_error"))
_STEP_ID_RE = __import__("re").compile(r"^step-[0-9]{2,}$")
# The closed set of keys a gate object may carry (additionalProperties:false,
# in lockstep with plan.schema.json's gate definition).
_GATE_KEYS = frozenset(("kind", "argv", "outcomes", "default", "skip_to",
                        "judge_prompt", "judge_prompt_file", "same_context"))


def _valid_outcome(s) -> bool:
    if not isinstance(s, str):
        return False
    if s in _GATE_OUTCOME_SET:
        return True
    if s.startswith("behavior-flag:") and len(s) > len("behavior-flag:"):
        return True
    # parametric skip forward: skip-to-next:step-MM (honored by _apply_gate_outcome)
    if s.startswith("skip-to-next:"):
        return bool(_STEP_ID_RE.match(s.split(":", 1)[1]))
    return False


def _check_gate(tag: str, gate, plan: dict) -> list:
    """FR-63/64: validate a per-step ``gate``. A shell gate is the deterministic
    default (exit-code -> closed-set outcome). A reasoning gate is FENCED: it
    requires plan-level ``allow_reasoning_gates:true``, is rejected in a
    ``measurement:true`` run, and demands a DISTINCT judge (a same-context judge
    is a --check error -- upholds FR-51 'never grades its own homework')."""
    g = "%s.gate" % tag
    if not isinstance(gate, dict):
        return ["%s: must be a JSON object" % g]
    p = []
    for k in gate:                               # strict keys (== schema)
        if k not in _GATE_KEYS:
            p.append("%s: unknown key %r (typo? allowed: %s)"
                     % (g, k, ", ".join(sorted(_GATE_KEYS))))
    kind = gate.get("kind")
    if kind not in _GATE_KINDS:
        p.append("%s.kind: must be one of %s (got %r)"
                 % (g, list(_GATE_KINDS), kind))
    outcomes = gate.get("outcomes")
    if outcomes is not None:
        if not isinstance(outcomes, dict):
            p.append("%s.outcomes: must be an object mapping exit-code strings "
                     "to closed-set outcomes" % g)
        else:
            for code, oc in outcomes.items():
                if not _valid_outcome(oc):
                    p.append("%s.outcomes[%s]: %r is not a closed-set gate "
                             "outcome" % (g, code, oc))
    if "default" in gate and not _valid_outcome(gate["default"]):
        p.append("%s.default: %r is not a closed-set gate outcome"
                 % (g, gate["default"]))
    if "skip_to" in gate and not (isinstance(gate["skip_to"], str)
                                  and _STEP_ID_RE.match(gate["skip_to"])):
        p.append("%s.skip_to: must look like 'step-MM' (got %r)"
                 % (g, gate.get("skip_to")))
    if kind == "shell":
        argv = gate.get("argv")
        if not (isinstance(argv, list) and argv
                and all(isinstance(t, str) for t in argv)):
            p.append("%s.argv: a shell gate requires a non-empty array of "
                     "strings" % g)
    elif kind == "reasoning":
        if not plan.get("allow_reasoning_gates"):
            p.append("%s: a reasoning gate requires plan-level "
                     "allow_reasoning_gates:true (FR-63 fence)" % g)
        if plan.get("measurement"):
            p.append("%s: a reasoning gate is rejected in a measurement run "
                     "(only deterministic shell gates are allowed)" % g)
        has_judge = ((isinstance(gate.get("judge_prompt"), str) and gate.get("judge_prompt"))
                     or (isinstance(gate.get("judge_prompt_file"), str)
                         and gate.get("judge_prompt_file")))
        if gate.get("same_context"):
            p.append("%s: a reasoning gate may NOT judge in the same context as "
                     "the step it judges (FR-51); supply a distinct judge_prompt" % g)
        if not has_judge:
            p.append("%s: a reasoning gate requires a distinct judge_prompt or "
                     "judge_prompt_file (separate judging context, FR-51)" % g)
    return p


def _check_vars(tag: str, vars_map) -> list:
    """FR-61: a `vars` map must be a flat object of string->scalar. A key may not
    be a reserved engine placeholder name, and a value may not contain a reserved
    {TOKEN} -- so the designated-key {var} pass can never spoof a dispatch path."""
    if vars_map is None:
        return []
    if not isinstance(vars_map, dict):
        return ["%s.vars: must be a flat object of string->string" % tag]
    p = []
    for k, v in vars_map.items():
        if not (isinstance(k, str) and k):
            p.append("%s.vars: keys must be non-empty strings" % tag)
            continue
        if k in _RESERVED_NAMES:
            p.append("%s.vars: key %r collides with a reserved engine "
                     "placeholder name" % (tag, k))
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            p.append("%s.vars[%s]: value must be a string or number" % (tag, k))
            continue
        sval = str(v)
        for tok in _RESERVED_TOKENS:
            if tok in sval:
                p.append("%s.vars[%s]: value may not contain a reserved engine "
                         "token %s" % (tag, k, tok))
                break
    return p


def _resolve_check_prompt(tag: str, e: dict, plan_dir: Path):
    """Return (prompt_text, error_or_None) for an inline OR file-sourced agent
    prompt, reading the file (FR-61) so the placeholder typo-check runs over its
    content."""
    wpf = e.get("prompt_file")
    if isinstance(wpf, str) and wpf:
        try:
            return _resolve_prompt_file(wpf, plan_dir).read_text(encoding="utf-8"), None
        except OSError as exc:
            return None, ("%s.prompt_file: cannot read %s (%s)"
                          % (tag, wpf, exc))
    wp = e.get("prompt")
    return (wp if isinstance(wp, str) else ""), None


def _check_agent_prompt(tag: str, e: dict, prompt: str) -> list:
    """`agent` mode: exactly one of prompt/prompt_file. The placeholder preamble
    is AUTO-INJECTED by the engine at dispatch (settled decision 3), so --check
    does NOT require the placeholders be present — it only rejects an unknown
    {TYPO} token the author wrote (a lowercase {var} / single-brace JSON is
    invisible here, FR-61)."""
    p = []
    have = [n for n, ok in (
        ("prompt", isinstance(e.get("prompt"), str) and e.get("prompt")),
        ("prompt_file", isinstance(e.get("prompt_file"), str) and e.get("prompt_file")),
    ) if ok]
    if len(have) == 0:
        p.append("%s: agent mode needs a prompt or prompt_file" % tag)
    elif len(have) > 1:
        p.append("%s: exactly one of prompt / prompt_file (got %s)"
                 % (tag, ", ".join(have)))
    for tok in sorted(set(_PLACEHOLDER_TOKEN_RE.findall(prompt or ""))):
        if tok not in _KNOWN_PLACEHOLDERS:
            p.append("%s: unknown placeholder {%s} (known: %s)"
                     % (tag, tok, ", ".join(sorted(_KNOWN_PLACEHOLDERS))))
    return p


def _check_mode_payload(tag, e, mode, plan_dir, plan, run_auth, allowed):
    """Strict-keys + per-mode required/typed checks for ONE job or step.
    ``allowed`` is the mode's permitted-key set (additionalProperties:false).
    A gate (steps only) is validated by the caller."""
    p = []
    # strict keys (additionalProperties:false) — a typo like `promt`/`repoo`
    # fails HERE, not silently at dispatch.
    for k in e:
        if k not in allowed:
            p.append("%s: unknown key %r (typo? allowed for mode %r: %s)"
                     % (tag, k, mode, ", ".join(sorted(allowed))))
    p.extend(_check_vars(tag, e.get("vars")))
    if "auth_check" in e and not (isinstance(e["auth_check"], list)
                                  and all(isinstance(t, str) for t in e["auth_check"])):
        p.append("%s.auth_check: must be an array of strings" % tag)
    if mode == "agent":
        prompt, perr = _resolve_check_prompt(tag, e, plan_dir)
        if perr:
            p.append(perr)
        else:
            p.extend(_check_agent_prompt(tag, e, prompt))
    elif mode == "command":
        p.extend(_check_command_mode(tag, e))
    elif mode == "log":
        p.extend(_check_log_mode(tag, e))
    elif mode == "shell":
        p.extend(_check_shell_mode(tag, e))
        if (run_auth and isinstance(e.get("auth_check"), list) and e["auth_check"]):
            rc, detail = _run_auth_check(e["auth_check"])
            if rc != 0:
                p.append("%s.auth_check: failed (%s)" % (tag, detail))
    return p


def _check_step(stag: str, step, plan_dir: Path, plan: dict) -> list:
    """Validate ONE pipeline step: a mini-job with its own `mode` (agent/command/
    log/shell), the per-mode required field, strict keys, optional vars + FR-63
    gate."""
    if not isinstance(step, dict):
        return ["%s: must be a JSON object" % stag]
    p = []
    mode = step.get("mode")
    if mode not in _STEP_MODES:
        p.append("%s.mode: must be one of %s (got %r)"
                 % (stag, list(_STEP_MODES), mode))
        return p
    allowed = _COMMON_STEP_KEYS | _MODE_STEP_KEYS[mode]
    p.extend(_check_mode_payload(stag, step, mode, plan_dir, plan, False, allowed))
    if "gate" in step:
        p.extend(_check_gate(stag, step.get("gate"), plan))
    return p


def _check_job(i: int, e, run_auth: bool, plan_dir: Path, plan: dict) -> list:
    tag = "jobs[%d]" % i
    if not isinstance(e, dict):
        return ["%s: must be a JSON object" % tag]
    p = []
    # id / repo / mode always required.
    for key in ("id", "repo"):
        if not (isinstance(e.get(key), str) and e.get(key)):
            p.append("%s.%s: required non-empty string" % (tag, key))
    mode = e.get("mode")
    if mode not in _JOB_MODES:
        p.append("%s.mode: must be one of %s (got %r)" % (tag, list(_JOB_MODES), mode))
        return p                                # without a known mode, stop here
    if "heartbeat_path" in e and not isinstance(e["heartbeat_path"], str):
        p.append("%s.heartbeat_path: must be a string" % tag)
    allowed = _COMMON_JOB_KEYS | _MODE_JOB_KEYS[mode]
    if mode == "pipeline":
        # strict keys + vars, then per-step validation.
        for k in e:
            if k not in allowed:
                p.append("%s: unknown key %r (typo? allowed for mode 'pipeline': %s)"
                         % (tag, k, ", ".join(sorted(allowed))))
        p.extend(_check_vars(tag, e.get("vars")))
        steps = e.get("steps")
        if not (isinstance(steps, list) and steps):
            p.append("%s.steps: pipeline mode requires a non-empty array of step objects" % tag)
        else:
            for m, step in enumerate(steps):
                p.extend(_check_step("%s.steps[%d]" % (tag, m), step, plan_dir, plan))
    else:
        p.extend(_check_mode_payload(tag, e, mode, plan_dir, plan, run_auth, allowed))

    # repo existence (all modes)
    tr = e.get("repo")
    if isinstance(tr, str) and tr and not Path(tr).is_dir():
        p.append("%s.repo: not an existing directory: %s" % (tag, tr))
    return p


def check_plan(plan_path, run_auth: bool = False) -> list:
    """Validate a plan and return a list of ALL problems (empty == clean).
    Never launches anything (auth_check runs only when run_auth=True). The
    runtime enforcer (FR-42, NFR-3 stdlib-only) — agrees field-for-field with
    plan.schema.json. `defaults` are merged under each job before validation."""
    try:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except OSError as exc:
        return ["plan: cannot read %s (%s)" % (plan_path, exc)]
    except ValueError as exc:
        return ["plan: not valid JSON (%s)" % exc]
    if not isinstance(plan, dict):
        return ["plan: top level must be a JSON object"]
    problems = []
    for k in plan:                               # plan-root strict keys (== schema)
        if k not in _PLAN_KEYS:
            problems.append("plan: unknown top-level key %r (typo? allowed: %s)"
                            % (k, ", ".join(sorted(_PLAN_KEYS))))
    if "defaults" in plan and not isinstance(plan["defaults"], dict):
        problems.append("plan.defaults: must be a JSON object")
    for k in _PLAN_INT_KEYS:
        if k in plan and not _is_pos_int(plan[k]):
            problems.append("plan.%s: must be an integer >= 1 (got %r)" % (k, plan[k]))
    if "schema_version" in plan and not isinstance(plan["schema_version"], str):
        problems.append("plan.schema_version: must be a string")
    if "vars" in plan:                                   # FR-61 plan-level vars
        problems.extend(_check_vars("plan", plan["vars"]))
    for bkey in ("allow_reasoning_gates", "measurement"):  # FR-63 fences
        if bkey in plan and not isinstance(plan[bkey], bool):
            problems.append("plan.%s: must be a boolean" % bkey)
    if not (isinstance(plan.get("jobs"), list) and plan.get("jobs")):
        problems.append("plan.jobs: a non-empty array is required")
        return problems                      # nothing per-job to check
    jobs = _merge_defaults(plan)             # effective jobs (defaults merged)
    plan_dir = Path(plan_path).resolve().parent          # FR-61 prompt-file base
    for i, e in enumerate(jobs):
        problems.extend(_check_job(i, e, run_auth, plan_dir, plan))

    # FR-58a: the keepalive/activity-refresh interval must land WITHIN launch
    # grace (else the first IN_PROGRESS never beats LAUNCH-FAIL). Fail-loud at
    # --check, plan-level + per-job override; explicit-override-wins; the 1s
    # floor is applied at runtime (Postel), so only keepalive>grace is rejected.
    plan_grace = plan.get("launch_grace_minutes", DEFAULT_LAUNCH_GRACE_MINUTES)

    def _ka_problem(tag, ka, grace_min):
        try:
            ka = float(ka)
        except (TypeError, ValueError):
            return "%s.keepalive_seconds: must be a number (got %r)" % (tag, ka)
        try:
            gmin = float(grace_min)
        except (TypeError, ValueError):
            return None                      # bad grace is flagged elsewhere
        if ka > gmin * 60:
            return ("%s.keepalive_seconds: %g exceeds launch grace %g min "
                    "(%g s) -- the first keepalive would never beat LAUNCH-FAIL"
                    % (tag, ka, gmin, gmin * 60))
        return None

    if "keepalive_seconds" in plan:
        prob = _ka_problem("plan", plan["keepalive_seconds"], plan_grace)
        if prob:
            problems.append(prob)
    for i, e in enumerate(jobs):
        if isinstance(e, dict) and "keepalive_seconds" in e:
            eg = e.get("launch_grace_minutes", plan_grace)
            prob = _ka_problem("jobs[%d]" % i, e["keepalive_seconds"], eg)
            if prob:
                problems.append(prob)
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
    print("arunner %s" % _arunner_version(), file=sys.stderr)
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
