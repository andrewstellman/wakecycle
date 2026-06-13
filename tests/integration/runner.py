"""Ticker-driven scenario runner for the integration suite (FR-51).

Drives a scenario with the DETERMINISTIC ticker (`bin/ticker.py --once` in a
loop) -- never the agent loop -- so a run is reproducible and the flaky
Class-C path never enters the regression net. Records a ``_check_meta.json``
in the run-dir (per-tick trace + the pre-STOP snapshot + the stopped flag) for
the INDEPENDENT checker to read. The runner may use repo code freely; only the
checker is constrained to stdlib (that boundary is the whole point).

Scenario format (documented in tests/integration/README.md): a folder with a
single ``scenario.json`` = {description, plan, control?, expected}. The plan is
a normal wakecycle plan; the runner substitutes ``{STUB}`` (this dir's
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
_TICKER = _ROOT / "bin" / "ticker.py"
_STUB = _HERE / "stub_worker.py"
_MAX_TICKS = 60


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


def run_scenario(scenario_dir, work_dir):
    scenario_dir = Path(scenario_dir)
    work_dir = Path(work_dir)
    scn = json.loads((scenario_dir / "scenario.json").read_text(encoding="utf-8"))
    plan = _substitute(scn["plan"], {"{STUB}": str(_STUB),
                                     "{SCENARIO_DIR}": str(scenario_dir)})
    plan_path = work_dir / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    runs_root = work_dir / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, WAKECYCLE_RUNS_DIR=str(runs_root))

    control = scn.get("control") or {}
    stop_after = control.get("write_stop_after_tick")

    run_dir = None
    trace = []
    pre_stop = None
    stopped = False
    for tick_no in range(1, _MAX_TICKS + 1):
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
        trace.append(dict(status.get("counts", {})))

        if stop_after == tick_no:
            pre_stop = copy.deepcopy(status)     # snapshot BEFORE the stop tick
            (run_dir / "STOP").touch()
            stopped = True

        if status.get("done"):
            break
        # a stop tick is read-only; one tick after STOP is written is enough
        if stopped and stop_after is not None and tick_no >= stop_after + 1:
            break

    meta = {"tick_trace": trace, "pre_stop_status": pre_stop,
            "stopped": stopped}
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
