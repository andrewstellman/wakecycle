#!/usr/bin/env python3
"""wakecycle in-context worker mode (FR-46..49) -- the DETERMINISTIC core.

In-context is the THIRD dispatch mode (FR-46), orthogonal to cadence: alongside
subagent (FR-14) and shell (FR-15), the orchestrator MAY do a task itself, in
its own context, between ticks. Enabled by an ``instruction_folder`` bootstrap
setting. Harness mode (dispatch + monitor) is the base; in-context is a
superset that keeps every harness feature.

This module is the deterministic, unit-testable part: the streaming-instruction
-queue selection (FR-47) and the monitoring-pause note rendering (FR-49). The
agent-behavior parts (actually doing the work, the ERR rehydrate-on-resume
discipline of FR-48) live in the SKILL -- they are not pure functions of disk
state and get dogfood/integration coverage, not unit assertions.

Cross-axis coupling (state it, never imply otherwise): the in-context *tasks
themselves* need a live agent, so they ride on rung-1 cadence; background
workers the same run watches run at ANY rung.

LIMITATION (C-7 / FR-50 / section 8) -- load-bearing honesty:
  In-context mode DOES NOT fix Class-C loop drops. It has no auto-recovery of
  the in-context queue: if the agent turn silently dies mid-burst, the
  in-context tasks are NOT auto-relaunched -- they require operator
  re-bootstrap. The deterministic floor (ticker/cron) rescues only the
  background/harness portion of a run, never the in-context tasks. In-context
  mode is a convenience/unification superset, NOT the unattended-reliability
  path -- that is the deterministic ticker/cron rungs.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_NNN_RE = re.compile(r"^(\d+)")


def _nnn(name: str):
    """The normalized integer of a leading zero-padded numeric prefix, or None
    (so '010-foo.md' and '10-bar' share stem 10)."""
    m = _NNN_RE.match(name)
    return int(m.group(1)) if m else None


def select_next_instruction(instructions_dir, outputs_dir,
                            stop_file=None) -> Optional[Path]:
    """FR-47 streaming-queue selection (pure filesystem scan -- no agent).

    Return the lowest-numbered ``NNN-`` instruction file in ``instructions_dir``
    that has NO matching output stem in ``outputs_dir`` (an output whose own
    ``NNN`` prefix equals the instruction's), or None when every instruction is
    processed / the queue is empty.

    STOP (FR-10/FR-47): if ``stop_file`` is given and exists, return None --
    the queue halts, EVEN MID-QUEUE (an unprocessed instruction is NOT picked
    while STOP is present). This is the read-only halt invariant.
    """
    if stop_file is not None and Path(stop_file).exists():
        return None

    instr = {}
    idir = Path(instructions_dir)
    if idir.is_dir():
        for p in sorted(idir.iterdir()):
            if not p.is_file():
                continue
            n = _nnn(p.name)
            if n is not None:
                instr.setdefault(n, p)        # first (sorted) wins per number

    done = set()
    odir = Path(outputs_dir)
    if odir.is_dir():
        for p in odir.iterdir():
            if p.is_file():
                n = _nnn(p.name)
                if n is not None:
                    done.add(n)

    for num in sorted(instr):
        if num not in done:
            return instr[num]
    return None


def _hm(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M")


def monitoring_pause_note(paused_from_epoch: float, paused_to_epoch: float,
                          background_changes: int) -> str:
    """FR-49 monitoring-pause visibility: a status-table line noting the gap
    while the agent did in-context work, so the sparse cadence reads as
    intentional, not as a dropped loop. A PURE function of disk-derived state
    (the two tick wall-clock stamps + a background-change count)."""
    return ("monitoring paused %s-%s for in-context work; %d background "
            "change(s) since last poll"
            % (_hm(paused_from_epoch), _hm(paused_to_epoch),
               int(background_changes)))


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # `incontext.py next <instructions_dir> <outputs_dir> [--stop-file F]`
    if len(args) >= 3 and args[0] == "next":
        stop = None
        if "--stop-file" in args:
            i = args.index("--stop-file")
            stop = args[i + 1] if i + 1 < len(args) else None
        nxt = select_next_instruction(args[1], args[2], stop_file=stop)
        if nxt is not None:
            print(str(nxt))
        return 0
    print("usage: incontext.py next <instructions_dir> <outputs_dir> "
          "[--stop-file F]", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
