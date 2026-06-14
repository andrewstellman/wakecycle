#!/usr/bin/env python3
"""arunner jobs  -  the `jobs:` shorthand expander (FR-43).

A higher-level, ergonomic plan form the skill/TOOLKIT expands into the full
placeholder-laden ``plan.json`` the engine consumes. The low-level plan schema
stays CANONICAL; this is a pure convenience layer on top -- the engine only
ever sees the expanded plan. The expander injects the required placeholders
into each ``worker_prompt`` so the expanded plan passes ``tick.py --check``
(FR-42); adapter jobs route through the FR-41 ``adapter`` selector (the engine
synthesizes their worker_cmd, so they need no placeholder plumbing).

Shorthand (``jobs:`` list); each job is ONE of:
  * an agent job   {repo, prompt, [agent:"subagent"], [id]}
                   -> a subagent entry whose worker_prompt carries the prompt
                      under the injected placeholder header.
  * an adapter job {repo, adapter:"wrap", command:[...], [id]}
                   {repo, adapter:"tail", log_path, [success_regex/failure_regex/
                      sentinel_file/pid/command], [id]}
                   -> a shell `adapter` entry (FR-41 selector wires the rest).
Top-level knobs (pool_size, tick_interval_minutes, ...) pass through unchanged.

An already-canonical doc (has ``entries``, no ``jobs``) passes through
unchanged, so a tool can expand-then-check uniformly.

Stdlib only. Usage: ``jobs.py expand <shorthand.json>`` prints the plan JSON.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# The placeholder header injected into every agent (subagent) prompt. Carries
# the FULL engine placeholder block so the expanded prompt passes --check; the
# engine substitutes these with absolute paths at dispatch (FR-21a, no
# model-transcribed paths). Keep in lockstep with tick._PLACEHOLDERS.
_PLACEHOLDER_KEYS = ("HEARTBEAT_PATH", "TASK_ID", "RUN_DIR", "TARGET_REPO",
                     "HARNESS_BIN")
_PLACEHOLDER_HEADER = "".join("%s={%s}\n" % (k, k) for k in _PLACEHOLDER_KEYS) + "\n"

_TOPLEVEL_PASSTHROUGH = ("schema_version", "tick_interval_minutes", "pool_size",
                         "stall_threshold_minutes", "launch_grace_minutes",
                         "idle_tick_multiplier")
_ADAPTER_FIELDS = ("command", "log_path", "success_regex", "failure_regex",
                   "sentinel_file", "pid")


def _expand_job(i: int, job: dict) -> dict:
    if not isinstance(job, dict):
        return {"task_id": "job-%02d" % i, "target_repo": "",
                "dispatch_mode": "subagent", "worker_prompt": _PLACEHOLDER_HEADER}
    entry = {"task_id": str(job.get("id") or "job-%02d" % i),
             "target_repo": str(job.get("repo", ""))}
    adapter = job.get("adapter")
    if adapter:
        entry["dispatch_mode"] = "shell"
        entry["adapter"] = adapter
        for k in _ADAPTER_FIELDS:
            if k in job:
                entry[k] = job[k]
    else:
        entry["dispatch_mode"] = "subagent"
        entry["worker_prompt"] = _PLACEHOLDER_HEADER + str(job.get("prompt", ""))
    return entry


def expand_jobs(doc: dict) -> dict:
    """Expand a ``jobs:`` shorthand doc into a canonical plan. An already-
    canonical doc (no ``jobs``) is returned unchanged."""
    if not isinstance(doc, dict) or "jobs" not in doc:
        return doc
    plan = {k: doc[k] for k in _TOPLEVEL_PASSTHROUGH if k in doc}
    jobs = doc.get("jobs") or []
    plan["entries"] = [_expand_job(i, job) for i, job in enumerate(jobs, start=1)]
    return plan


def session_bundle(doc: dict) -> dict:
    """FR-52.4 ``my_run.json``: one file carrying the shorthand SOURCE (``jobs``
    + top-level knobs) AND the expanded canonical ``plan``, so a saved session
    reruns faithfully (run the ``plan``) yet stays editable (the ``jobs``)."""
    bundle = {k: v for k, v in doc.items() if k != "plan"}
    bundle["plan"] = expand_jobs(doc)
    return bundle


def bundle_drifted(bundle: dict) -> bool:
    """True if re-expanding a my_run.json bundle's shorthand no longer matches
    its saved ``plan`` (a hand-edit-drift signal -- warn, don't block)."""
    src = {k: v for k, v in bundle.items() if k != "plan"}
    return expand_jobs(src) != bundle.get("plan")


def _write_json(obj, out_path) -> None:
    Path(out_path).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out = save = None
    if "--out" in args:
        i = args.index("--out"); out = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    if "--save" in args:
        i = args.index("--save"); save = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    if len(args) == 2 and args[0] == "expand":
        try:
            doc = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("jobs: cannot read shorthand %s (%s)" % (args[1], exc),
                  file=sys.stderr)
            return 2
        if save:                              # FR-52.4: persist jobs + plan
            _write_json(session_bundle(doc), save)
            print(save)
        elif out:                             # FR-52.4: write the expanded plan
            _write_json(expand_jobs(doc), out)
            print(out)
        else:
            print(json.dumps(expand_jobs(doc), indent=2))
        return 0
    print("usage: jobs.py expand <shorthand.json> [--out <plan>] [--save <my_run.json>]",
          file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
