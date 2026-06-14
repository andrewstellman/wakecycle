"""Ticker-driven scenario runner for the integration suite (FR-51).

Drives a scenario with the DETERMINISTIC ticker (`arunner/engine/ticker.py --once` in a
loop) -- never the agent loop -- so a run is reproducible and the flaky
Class-C path never enters the regression net. Records a ``_check_meta.json``
in the run-dir (per-tick trace + the pre-STOP snapshot + the stopped flag) for
the INDEPENDENT checker to read. The runner may use repo code freely; only the
checker is constrained to stdlib (that boundary is the whole point).

Scenario format (documented in tests/integration/README.md): a folder with a
single ``scenario.json`` = {description, plan, control?, expected}. The plan is
a normal arunner plan; the runner substitutes ``{STUB}`` (this dir's
stub_worker.py) and ``{SCENARIO_DIR}`` before --init. The engine substitutes
its own {HEARTBEAT_PATH}/{TASK_ID}/{RUN_DIR}/{HARNESS_BIN} block at dispatch.
"""
from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_TICKER = _ROOT / "arunner" / "engine" / "ticker.py"
_ENGINE = _ROOT / "arunner" / "engine" / "tick.py"
_STUB = _HERE / "stub_worker.py"
_MAX_TICKS = 60


def _verdict_str(cont):
    """Canonical verdict string from a continuation object (FR-55)."""
    if not cont or cont.get("verdict") == "CONTINUE":
        return "CONTINUE"
    return "HALT:" + str(cont.get("reason", "internal_error"))


def _substitute(obj, mapping):
    if isinstance(obj, str):
        for k, v in mapping.items():
            obj = obj.replace(k, v)
        return obj
    if isinstance(obj, list):
        return [_substitute(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: _substitute(v, mapping) for k, v in obj.items()}
    return obj


def _only_run_dir(runs_root: Path):
    subs = [p for p in runs_root.iterdir() if p.is_dir()]
    return subs[0] if len(subs) == 1 else None


def _read_status(run_dir: Path):
    try:
        return json.loads((run_dir / "harness_status.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _kill_workers(run_dir: Path):
    """Kill any still-running detached workers (the double-fork reparents them
    to init, so a held stub survives the runner -- clean them up)."""
    claimed = run_dir / "claimed"
    if not claimed.is_dir():
        return
    for lock in claimed.glob("*.lock"):
        try:
            pid = json.loads(lock.read_text(encoding="utf-8")).get("pid")
            if pid:
                os.kill(int(pid), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def _heartbeat_terminal(run_dir, name):
    """True once a run's worker has written a terminal heartbeat -- detected by
    the STATUS FIELD (JSON-parsed), mirroring the engine's _terminal_status_of.
    Never a substring scan: an adapter's free-text label (a wrapped build that
    prints 'FAILED') must not look terminal to the settle loop."""
    hb = Path(run_dir) / name / "heartbeat.ndjson"
    try:
        text = hb.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("status") in ("COMPLETED", "FAILED", "ABANDONED"):
            return True
    return False


def _settle(run_dir, entries, timeout=20.0):
    """Deterministic settle (FR-51): after a tick, WAIT until every in-flight,
    NON-HELD worker has written its terminal heartbeat, so the next tick reaps
    it without a wall-clock race against process-startup speed -- the
    regression net must be environment-independent, not pass on a fast machine
    and fail on a slow one. HELD workers (`--hold-file` in their worker_cmd)
    never terminate and are EXCLUDED (the wait is conditional/bounded, not a
    blanket sleep). On timeout we proceed (the tick handles whatever exists)."""
    held = set()
    for i, e in enumerate(entries, start=1):
        if "--hold-file" in (e.get("worker_cmd") or []):
            held.add("run-%02d" % i)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _read_status(run_dir)
        if status is None:
            return
        pending = [name for name, r in status.get("runs", {}).items()
                   if name not in held
                   and r.get("state") in ("claimed", "running")
                   and not _heartbeat_terminal(run_dir, name)]
        if not pending:
            return
        time.sleep(0.05)


def run_scenario(scenario_dir, work_dir):
    scenario_dir = Path(scenario_dir)
    work_dir = Path(work_dir)
    scn = json.loads((scenario_dir / "scenario.json").read_text(encoding="utf-8"))
    plan = _substitute(scn["plan"], {"{STUB}": str(_STUB),
                                     "{HEARTBEAT_BIN}": str(_ROOT / "arunner" / "engine" / "heartbeat.py"),
                                     "{SCENARIO_DIR}": str(scenario_dir)})
    plan_path = work_dir / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    runs_root = work_dir / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, ARUNNER_RUNS_DIR=str(runs_root))

    # FR-42: dogfood the pre-flight -- a malformed scenario plan must fail
    # loudly HERE, not produce a confusing run. (target_repo is {SCENARIO_DIR},
    # an existing dir; worker_cmd carries {HEARTBEAT_PATH} -- valid scenarios
    # pass.)
    chk = subprocess.run([sys.executable, str(_ENGINE), "--check", str(plan_path)],
                         env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, timeout=60)
    if chk.returncode != 0:
        raise RuntimeError("scenario plan failed --check:\n%s"
                           % chk.stdout.decode("utf-8", "replace"))

    control = scn.get("control") or {}
    stop_after = control.get("write_stop_after_tick")
    pause_after = control.get("write_pause_after_tick")
    resume_after = control.get("write_resume_after_tick")
    pool_after = control.get("write_pool_after_tick")     # FR-37: value-carrying
    pool_value = control.get("pool_value")
    cadence_after = control.get("write_cadence_after_tick")
    cadence_value = control.get("cadence_value")
    pollnow_after = control.get("write_pollnow_after_tick")  # FR-38: one-shot
    cancel_after = control.get("write_cancel_after_tick")    # FR-39: value-carrying
    cancel_value = control.get("cancel_value")
    max_ticks = int(control.get("max_ticks", _MAX_TICKS))

    # FR-55 stub-host knobs: the runner stands in for the LLM orchestrator. These
    # make it STOP driving on command and optionally write a yield/blocker —
    # categorically different from the control-file knobs (which write a file the
    # ENGINE reacts to); these script the HOST's behaviour.
    stop_host_after = control.get("stop_host_after_tick")
    yield_cited = control.get("yield_cited")          # None ⇒ no yield (abandon)
    resume_after_stop = bool(control.get("resume_after_stop"))
    past_due = bool(control.get("past_due"))           # eval_now past next_tick_due?
    block_after = control.get("block_after_tick")
    clear_block_after = control.get("clear_block_after_tick")
    cadence_secs = int((plan.get("tick_interval_minutes") or 1)) * 60

    run_dir = None
    trace = []
    pre_stop = None
    stopped = False
    results_snapshot = None       # FR-39: results/ bytes captured just before CANCEL
    host_stopped_after = None     # FR-55: the tick after which the host went away
    resumed = False               # FR-55: did the host resume (FR-13) after stopping?
    eval_now = None               # FR-55: wall-clock at which abandonment is judged
    for tick_no in range(1, max_ticks + 1):
        target = str(plan_path) if run_dir is None else str(run_dir)
        subprocess.run([sys.executable, str(_TICKER), "--once", target],
                       env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=120)
        if run_dir is None:
            run_dir = _only_run_dir(runs_root)
            if run_dir is None:
                raise RuntimeError("no run-dir created under %s" % runs_root)
        status = _read_status(run_dir)
        if status is None:
            continue
        cont = status.get("continuation") or {}
        trace.append({"counts": dict(status.get("counts", {})),
                      "paused": bool(status.get("paused")),
                      "next_tick_minutes": status.get("next_tick_minutes"),
                      # FR-55: the engine's per-tick verdict (canonical string)
                      # + when the next tick is due, for the abandonment detector
                      "verdict": _verdict_str(cont),
                      "next_tick_due": cont.get("next_tick_due")})

        # drop control files at the scenario's scripted tick boundaries.
        # Value-carrying controls (FR-37) write the value into the file BODY.
        if pause_after == tick_no:
            (run_dir / "PAUSE").touch()
        if resume_after == tick_no:
            (run_dir / "RESUME").touch()
        if pool_after == tick_no:
            (run_dir / "POOL").write_text(str(pool_value), encoding="utf-8")
        if cadence_after == tick_no:
            (run_dir / "CADENCE").write_text(str(cadence_value), encoding="utf-8")
        if pollnow_after == tick_no:
            (run_dir / "POLL-NOW").touch()
        if cancel_after == tick_no:
            # Snapshot every results record NOW (after the prior tick reaped the
            # genuine terminals, before CANCEL runs) so the checker can prove
            # CANCEL leaves other runs' records byte-identical (shared-state).
            results_snapshot = {p.name: p.read_bytes().hex()
                                for p in sorted((run_dir / "results").iterdir())
                                if p.is_file()}
            (run_dir / "CANCEL").write_text(str(cancel_value), encoding="utf-8")
        if stop_after == tick_no:
            pre_stop = copy.deepcopy(status)     # snapshot BEFORE the stop tick
            (run_dir / "STOP").touch()
            stopped = True

        # FR-55 stub-host scripting (a HOST-authored blocker; the engine only
        # READS blockers, so this is the operator/host writing one to disk).
        if block_after == tick_no:
            bdir = run_dir / "blockers"
            bdir.mkdir(exist_ok=True)
            (bdir / "b1.json").write_text(json.dumps(
                {"id": "b1", "created_at": "t0", "reason": "operator decision",
                 "cleared_at": None}), encoding="utf-8")
        if clear_block_after == tick_no:
            bf = run_dir / "blockers" / "b1.json"
            if bf.exists():
                obj = json.loads(bf.read_text(encoding="utf-8"))
                obj["cleared_at"] = "t9"
                bf.write_text(json.dumps(obj), encoding="utf-8")
        # FR-55: the host goes away after tick N. Record it; optionally write a
        # yield (a host-authored journal record citing the verdict it claims to
        # have observed); compute the eval-now for the abandonment judgement.
        if stop_host_after == tick_no:
            host_stopped_after = tick_no
            due = trace[-1].get("next_tick_due")
            if due is not None:
                eval_now = (due + cadence_secs + 60) if past_due else (due - 60)
            if yield_cited is not None:
                with (run_dir / "journal.ndjson").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(
                        {"ts": "host", "tick": status.get("cycle", tick_no),
                         "type": "yield", "cited_verdict": yield_cited,
                         "note": scn.get("description", "")}) + "\n")
            if not resume_after_stop:
                break                            # host vanished; stop driving
            resumed = True                       # host resumed (FR-13)

        # Deterministic settle (FR-51): before the NEXT scripted tick reaps
        # them, wait on disk truth for every dispatched non-held worker to
        # write its terminal heartbeat -- so the regression net observes
        # completion rather than racing a fixed tick budget against
        # process-startup speed. Held-open workers are excluded (they never
        # terminate). No-op when nothing is in-flight.
        _settle(run_dir, plan.get("entries", []))

        if status.get("done"):
            # FR-55 `honor`: the host yields legitimately at the done tick.
            if yield_cited is not None and stop_host_after is None:
                with (run_dir / "journal.ndjson").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(
                        {"ts": "host", "tick": status.get("cycle", tick_no),
                         "type": "yield", "cited_verdict": yield_cited,
                         "note": scn.get("description", "")}) + "\n")
            break
        # a stop tick is read-only; one tick after STOP is written is enough
        if stopped and stop_after is not None and tick_no >= stop_after + 1:
            break

    final_status = _read_status(run_dir) or {}
    meta = {"tick_trace": trace, "pre_stop_status": pre_stop,
            "stopped": stopped, "results_snapshot": results_snapshot,
            # FR-55 continuation-contract metadata for the detector
            "host_stopped_after_tick": host_stopped_after,
            "resumed": resumed, "eval_now": eval_now,
            "final_done": bool(final_status.get("done"))}
    (run_dir / "_check_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    _kill_workers(run_dir)
    return run_dir


# convenience for ad-hoc runs
if __name__ == "__main__":
    import tempfile
    d = tempfile.mkdtemp()
    rd = run_scenario(sys.argv[1], d)
    print("run-dir:", rd)
    time.sleep(0.1)
