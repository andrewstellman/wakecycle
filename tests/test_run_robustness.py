"""FR-74 (continue-past-stall) + FR-73 (OUT-AGE output-activity) — the gen-007
run-robustness layer.

THE FORCING INCIDENT (gen-007 widenet, 2026-06-22): a 15-job pool-2 subagent
batch HALTed (`journal.ndjson` tick 18 `HALT:stalled`) with 43 jobs unstarted,
because two stalled workers pinned both pool slots — `stalled` is non-terminal
but counts as inflight, so `pool - inflight == 0` starved the queue. One worker
(`defu`) was genuinely hung (output-silent ~1h53m at HALT); the other (`goshs`,
and earlier `source-controller`) was heartbeat-quiet but STILL WRITING files
(alive). So a time-only reclaim would have abandoned a live worker: the
output-freshness guard (FR-73 OUT-AGE) is load-bearing, which is why FR-74 and
FR-73 ship together.

Coverage (the two load-bearing pins first):
  * test_pool2_two_stalled_with_queue_drains_not_halt — the gen-007 drain pin:
    pool-2, both slots stalled past reclaim with OUTPUT stale, 40 queued -> the
    queue DRAINS (reclaim -> dispatch); `_halt_reason` never returns "stalled".
  * test_stalled_but_output_fresh_is_NOT_reclaimed — the quiet-but-working guard
    (false-alarm pin): heartbeat stale past reclaim BUT OUT-AGE fresh -> held,
    NOT abandoned.
  * reclaim-frees-slot, idempotent-comeback, HALT-still-reachable-when-disabled,
    shell-mode parity, multistep reclaim, and the FR-73 data-layer + display-only
    invariant + bounded-scan pins.

Time-dependent transitions are driven by the ARUNNER_NOW clock seam aligned to
explicit file mtimes (os.utime) — no sleeps, fully deterministic.

MUTATION-VERIFY EVIDENCE (in-tree, instr 006) — both load-bearing pins BITE:
  Pin 1: test_pool2_two_stalled_with_queue_drains_not_halt
    Mutation: in tick.py `_advance`, neuter the reclaim:
        if (now - mtime) > reclaim_secs and out_stale:  ->  if False:
    Observed: both slots stay `stalled`, queue starves, the tick's continuation
      verdict is HALT:stalled and no queued job dispatches -> test FAILs.
    Restored -> OK.
  Pin 2: test_stalled_but_output_fresh_is_NOT_reclaimed
    Mutation: drop the output-fresh guard:
        out_stale = out_age is not None and out_age > stall_secs  ->  out_stale = True
    Observed: the still-writing (output-fresh) worker is reclaimed `abandoned`
      -> test FAILs (a live worker was abandoned).
    Restored -> OK. (Verified 2026-06-24, Python 3.14.x.)
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_tick():
    spec = importlib.util.spec_from_file_location(
        "tick_run_robustness", str(_REPO_ROOT / "arunner" / "engine" / "tick.py"))
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["tick_run_robustness"] = mod
    spec.loader.exec_module(mod)
    return mod


T = _load_tick()

# A fixed epoch base well clear of 0 so derived mtimes/ages stay positive.
M = 2_000_000_000
MIN = 60


def _status(run_dir: Path) -> dict:
    return json.loads((run_dir / "harness_status.json").read_text())


def _set_hb(run_dir: Path, run: str, mtime, status="IN_PROGRESS", tid="t"):
    """Overwrite a run's watched heartbeat with one controlled IN_PROGRESS line
    and pin its mtime — so `now - mtime` (the stall clock) is exact."""
    p = run_dir / run / "heartbeat.ndjson"
    line = {"ts": "t", "task_id": tid, "schema_version": "2",
            "label": "working", "status": status}
    p.write_text(json.dumps(line) + "\n", encoding="utf-8")
    os.utime(p, (mtime, mtime))


def _append_hb(run_dir: Path, run: str, status, tid="t"):
    line = {"ts": "t", "task_id": tid, "schema_version": "2",
            "status": status, "result_file": "out.json", "summary": "done"}
    with (run_dir / run / "heartbeat.ndjson").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _set_output(repo: Path, mtime, rel="quality/out.txt"):
    """Write one file under a job's output area and pin its mtime — the OUT-AGE
    newest-mtime signal."""
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x")
    os.utime(f, (mtime, mtime))


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repos = self.tmp / "repos"
        self.repos.mkdir()
        os.environ["ARUNNER_RUNS_DIR"] = str(self.tmp / "harness_runs")
        os.environ.pop("ARUNNER_NOW", None)

    def tearDown(self):
        os.environ.pop("ARUNNER_NOW", None)
        os.environ.pop("ARUNNER_RUNS_DIR", None)
        self._tmp.cleanup()

    def _repo(self, name, out_mtime=M):
        repo = self.repos / name
        repo.mkdir(parents=True, exist_ok=True)
        _set_output(repo, out_mtime)
        return repo

    def _agent_job(self, tid, repo):
        return {"id": tid, "repo": str(repo), "mode": "agent",
                "prompt": "do the work on this repository"}

    def _plan(self, jobs, **top):
        p = {"tick_interval_minutes": 5, "pool_size": top.pop("pool_size", 2),
             "stall_threshold_minutes": 45, "stall_reclaim_minutes": 90,
             "jobs": jobs}
        p.update(top)
        return p

    def _init(self, plan):
        pf = self.tmp / "plan.json"
        pf.write_text(json.dumps(plan))
        return Path(T.init_run(pf))


# --------------------------------------------------------------------------
# THE TWO LOAD-BEARING PINS
# --------------------------------------------------------------------------

class DrainPin(_Base):
    """gen-007 reproduction: a pool-saturating stall must DRAIN, not HALT."""

    def test_pool2_two_stalled_with_queue_drains_not_halt(self):
        # pool 2 + 40 queued = the gen-007 shape (15 in the incident; the
        # mechanism is identical and the larger queue makes the drain visible).
        jobs = [self._agent_job("t-%02d" % i, self._repo("r-%02d" % i))
                for i in range(42)]
        rd = self._init(self._plan(jobs, pool_size=2))

        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)                                   # dispatch run-01, run-02
        s = _status(rd)
        self.assertEqual(s["runs"]["run-01"]["state"], "claimed")
        self.assertEqual(s["counts"]["queued"], 40)

        # both in-flight workers heartbeated then went silent AND stopped writing
        # output (both heartbeat mtime AND newest output mtime = M).
        for run, name in (("run-01", "r-00"), ("run-02", "r-01")):
            _set_hb(rd, run, M)
            _set_output(self.repos / name, M)        # output also stale

        # 91 min later: heartbeat age 91m > reclaim 90m AND output age 91m > 45m.
        os.environ["ARUNNER_NOW"] = str(M + 91 * MIN)
        out = T.tick(rd)
        s = _status(rd)

        # both stalled slots were reclaimed terminal `abandoned` ...
        self.assertEqual(s["runs"]["run-01"]["state"], "abandoned")
        self.assertEqual(s["runs"]["run-02"]["state"], "abandoned")
        self.assertEqual(s["counts"]["abandoned"], 2)
        # ... freeing both slots, so the queue DISPATCHED two waiting jobs ...
        self.assertEqual(s["runs"]["run-03"]["state"], "claimed")
        self.assertEqual(s["runs"]["run-04"]["state"], "claimed")
        self.assertEqual(s["counts"]["queued"], 38)
        # ... and the run CONTINUEs — it did NOT HALT:stalled.
        self.assertEqual(out["continuation"]["verdict"], "CONTINUE")
        self.assertNotEqual(out["continuation"].get("reason"), "stalled")
        self.assertFalse(out["done"])


class OutputFreshGuardPin(_Base):
    """The quiet-but-working guard: a stalled-but-still-writing worker (OUT-AGE
    fresh) is NEVER reclaimed — the gen-007 goshs/source-controller false-stall."""

    def test_stalled_but_output_fresh_is_NOT_reclaimed(self):
        jobs = [self._agent_job("t-1", self._repo("r-1")),
                self._agent_job("t-2", self._repo("r-2"))]
        rd = self._init(self._plan(jobs, pool_size=1))   # 1 slot -> t-2 queues

        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)                                   # dispatch run-01
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "claimed")

        # heartbeat went silent (mtime M) BUT the worker is still WRITING files:
        # newest output mtime is recent (M + 89m), so at 'now' OUT-AGE is fresh.
        _set_hb(rd, "run-01", M)
        _set_output(self.repos / "r-1", M + 89 * MIN)

        # 91 min later: heartbeat age 91m > reclaim 90m, BUT output age is only
        # ~2m (< 45m) -> the guard HOLDS: stalled, NOT abandoned.
        os.environ["ARUNNER_NOW"] = str(M + 91 * MIN)
        out = T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "stalled")
        self.assertNotEqual(r["state"], "abandoned")
        # the slot is still held (the live worker keeps it), the second job waits
        self.assertEqual(_status(rd)["runs"]["run-02"]["state"], "queued")
        self.assertFalse((rd / "results" / "result-00001.json").exists())
        self.assertFalse(out["done"])


# --------------------------------------------------------------------------
# FR-74 supporting behavior
# --------------------------------------------------------------------------

class ReclaimMechanics(_Base):
    def test_reclaimed_stall_is_abandoned_and_frees_slot(self):
        jobs = [self._agent_job("t-1", self._repo("r-1")),
                self._agent_job("t-2", self._repo("r-2"))]
        rd = self._init(self._plan(jobs, pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        _set_hb(rd, "run-01", M)
        _set_output(self.repos / "r-1", M)
        os.environ["ARUNNER_NOW"] = str(M + 91 * MIN)
        T.tick(rd)
        s = _status(rd)
        # run-01 abandoned (terminal, synthesized result), slot freed, t-2 ran
        self.assertEqual(s["runs"]["run-01"]["state"], "abandoned")
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertEqual(rec["terminal_status"], "ABANDONED")
        self.assertTrue(rec.get("synthesized"))
        self.assertEqual(s["runs"]["run-02"]["state"], "claimed")

    def test_reclaimed_worker_late_terminal_does_not_resurrect_or_double_dispatch(self):
        # The subagent reclaim is an ACCOUNTING free, not a kill: the un-killed
        # worker may later emit its own terminal. It must NOT resurrect,
        # double-count, or double-dispatch (idempotency is sacred, FR-6).
        jobs = [self._agent_job("t-1", self._repo("r-1")),
                self._agent_job("t-2", self._repo("r-2"))]
        rd = self._init(self._plan(jobs, pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        _set_hb(rd, "run-01", M)
        _set_output(self.repos / "r-1", M)
        os.environ["ARUNNER_NOW"] = str(M + 91 * MIN)
        T.tick(rd)                                   # run-01 abandoned, run-02 claimed
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "abandoned")
        self.assertEqual(_status(rd)["runs"]["run-02"]["state"], "claimed")
        # keep run-02 demonstrably alive (a fresh heartbeat) so it is not itself
        # reclaimed on the next tick — we are isolating run-01's comeback.
        _set_hb(rd, "run-02", M + 91 * MIN)
        _set_output(self.repos / "r-2", M + 91 * MIN)

        # the un-killed run-01 worker COMES BACK and writes a terminal COMPLETED
        _append_hb(rd, "run-01", "COMPLETED", tid="t-1")
        os.environ["ARUNNER_NOW"] = str(M + 92 * MIN)
        T.tick(rd)
        s = _status(rd)
        # still abandoned — NOT resurrected to completed
        self.assertEqual(s["runs"]["run-01"]["state"], "abandoned")
        self.assertEqual(s["counts"]["abandoned"], 1)
        self.assertEqual(s["counts"]["completed"], 0)
        # run-02 was dispatched exactly once (in-flight, alive) — not re-dispatched
        # or abandoned by run-01's comeback; run-01 itself was not re-run.
        self.assertIn(s["runs"]["run-02"]["state"], ("claimed", "running"))
        # the synthesized abandoned result is the single terminal record for r-1
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertEqual(rec["terminal_status"], "ABANDONED")

    def test_halt_stalled_still_reachable_when_reclaim_disabled(self):
        # Reclaim effectively disabled (window set far beyond any tick): a
        # pool-saturating stall HALTs on `stalled` — reserved for the genuinely
        # unrecoverable wedge (operator out: CANCEL).
        jobs = [self._agent_job("t-1", self._repo("r-1")),
                self._agent_job("t-2", self._repo("r-2"))]
        rd = self._init(self._plan(jobs, pool_size=1,
                                   stall_reclaim_minutes=100000))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        _set_hb(rd, "run-01", M)
        _set_output(self.repos / "r-1", M)           # output stale, but reclaim disabled
        os.environ["ARUNNER_NOW"] = str(M + 91 * MIN)
        out = T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "stalled")      # NOT reclaimed
        self.assertEqual(out["continuation"]["verdict"], "HALT")
        self.assertEqual(out["continuation"]["reason"], "stalled")

    def test_shell_mode_stall_also_reclaims(self):
        # FR-74 applies in shell mode too (the same stall branch). A shell worker
        # stalled past reclaim with stale output is reclaimed `abandoned`.
        repo = self._repo("rs")
        job = {"id": "s-1", "repo": str(repo), "mode": "shell",
               "command": ["python3", "w.py", "--hb", "{HEARTBEAT_PATH}"]}
        rd = self._init(self._plan([job], pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)                                   # claimed (no STARTING in shell)
        _set_hb(rd, "run-01", M)                     # the worker beat, then went quiet
        _set_output(repo, M)
        os.environ["ARUNNER_NOW"] = str(M + 91 * MIN)
        T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "abandoned")

    def test_below_reclaim_window_stays_stalled_and_recovers(self):
        # Reversibility BELOW the reclaim threshold is intact: stalled then a
        # fresh heartbeat returns it to running (never reclaimed in between).
        rd = self._init(self._plan([self._agent_job("t-1", self._repo("r-1"))],
                                   pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        _set_hb(rd, "run-01", M)
        _set_output(self.repos / "r-1", M)
        # 50 min: past stall (45) but below reclaim (90) -> stalled, not abandoned
        os.environ["ARUNNER_NOW"] = str(M + 50 * MIN)
        T.tick(rd)
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "stalled")
        # a fresh heartbeat arrives -> back to running
        _set_hb(rd, "run-01", M + 50 * MIN)
        os.environ["ARUNNER_NOW"] = str(M + 50 * MIN + 1)
        T.tick(rd)
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "running")


class MultistepReclaim(_Base):
    def test_multistep_stall_reclaimed(self):
        # A stalled `pipeline` step past reclaim with stale output reclaims the
        # whole run `abandoned` (slot frees), mirroring the single-prompt path.
        repo = self._repo("rp")
        job = {"id": "p-1", "repo": str(repo), "mode": "pipeline",
               "steps": [{"mode": "agent", "label": "s0", "prompt": "step zero"},
                         {"mode": "agent", "label": "s1", "prompt": "step one"}]}
        rd = self._init(self._plan([job], pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)                                   # dispatch step 0 (steps/step-01/)
        # the engine watches the CURRENT step's heartbeat (1-based step dir);
        # use the engine's own path helper so the test never hardcodes layout.
        hb = T._step_hb(rd, "run-01", 0)
        line = {"ts": "t", "task_id": "p-1", "schema_version": "2",
                "label": "s0", "status": "IN_PROGRESS"}
        hb.write_text(json.dumps(line) + "\n", encoding="utf-8")
        os.utime(hb, (M, M))
        _set_output(repo, M)
        os.environ["ARUNNER_NOW"] = str(M + 91 * MIN)
        T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "abandoned")
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertEqual(rec["terminal_status"], "ABANDONED")


# --------------------------------------------------------------------------
# FR-73 OUT-AGE data layer + display-only invariant
# --------------------------------------------------------------------------

class OutAgeDataLayer(_Base):
    def test_out_age_newest_mtime_correct(self):
        repo = self.repos / "d"
        repo.mkdir()
        _set_output(repo, M - 100, rel="a.txt")
        _set_output(repo, M - 10, rel="sub/b.txt")     # the newest
        _set_output(repo, M - 50, rel="sub/c.txt")
        newest = T._newest_output_mtime(repo)
        self.assertEqual(int(newest), M - 10)
        r = {"target_repo": str(repo)}
        age = T._output_age_secs(repo, "run-01", r, None, {}, M)
        self.assertEqual(int(age), 10)

    def test_out_age_globs_scope_the_scan(self):
        """FR-73: a trailing ``**`` must match files on the Python 3.10+ floor;
        pre-3.13 ``Path.glob('x/**')`` matched directories only — verified bug
        2026-06-24, instr 007. The impl uses ``glob.glob(..., recursive=True)``
        (version-stable) so this scopes to quality/ and finds the file on 3.10+."""
        repo = self.repos / "g"
        repo.mkdir()
        _set_output(repo, M, rel="quality/fresh.txt")   # in scope
        _set_output(repo, M + 999, rel="node_modules/newer.txt")  # out of scope
        r = {"target_repo": str(repo)}
        entry = {"output_globs": ["quality/**"]}
        age = T._output_age_secs(repo, "run-01", r, entry, {}, M + 10)
        self.assertEqual(int(age), 10)                  # scoped to quality/, not node_modules

    def test_out_age_globs_non_doublestar_pattern(self):
        """FR-73: the fix is not ``**``-specific — a plain ``*.txt`` glob (no
        recursion) resolves to the correct newest mtime on the 3.10+ floor."""
        repo = self.repos / "gn"
        repo.mkdir()
        _set_output(repo, M, rel="top.txt")             # in scope (top level)
        _set_output(repo, M + 999, rel="nested/deep.txt")  # out of scope (not top level)
        r = {"target_repo": str(repo)}
        entry = {"output_globs": ["*.txt"]}
        age = T._output_age_secs(repo, "run-01", r, entry, {}, M + 10)
        self.assertEqual(int(age), 10)                  # only top.txt matched

    def test_out_age_globs_multi_glob_list(self):
        """FR-73: a multi-pattern ``output_globs`` list takes the newest mtime
        across all patterns (union), portably on the 3.10+ floor."""
        repo = self.repos / "gm"
        repo.mkdir()
        _set_output(repo, M - 100, rel="quality/old.txt")    # via quality/**
        _set_output(repo, M, rel="reports/new.json")         # via reports/** (the newest in scope)
        _set_output(repo, M + 999, rel="node_modules/x.txt")  # out of scope
        r = {"target_repo": str(repo)}
        entry = {"output_globs": ["quality/**", "reports/**"]}
        age = T._output_age_secs(repo, "run-01", r, entry, {}, M + 10)
        self.assertEqual(int(age), 10)                  # newest across both globs, not node_modules

    def test_outage_scan_is_bounded(self):
        # The scan is bounded: a zero file budget stats nothing and returns None,
        # proving the cap is enforced (never an unbounded recursive walk).
        repo = self.repos / "b"
        repo.mkdir()
        for i in range(20):
            _set_output(repo, M, rel="f%02d.txt" % i)
        self.assertIsNone(T._newest_output_mtime(repo, file_cap=0))
        self.assertIsNotNone(T._newest_output_mtime(repo, file_cap=4000))

    def test_vcs_dirs_pruned(self):
        repo = self.repos / "v"
        repo.mkdir()
        _set_output(repo, M, rel="quality/out.txt")
        _set_output(repo, M + 999, rel=".git/index")    # VCS churn — must be pruned
        newest = T._newest_output_mtime(repo)
        self.assertEqual(int(newest), M)                # .git ignored, not the newest

    def test_unmeasurable_output_area_is_none(self):
        r = {"target_repo": str(self.repos / "does-not-exist")}
        self.assertIsNone(T._output_age_secs(None, "run-01", r, None, {}, M))


class OutAgeDisplay(_Base):
    def test_out_age_column_renders_in_table(self):
        rd = self._init(self._plan([self._agent_job("t-1", self._repo("r-1"))],
                                   pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        out = T.tick(rd)
        self.assertIn("OUT-AGE", out["status_table"])

    def test_out_age_is_display_only_not_lifecycle(self):
        # The display-only invariant: doneness is the DECLARED terminal status,
        # never OUT-AGE. A run with a real COMPLETED terminal is reaped
        # `completed` even though its OUTPUT is STALE — output staleness never
        # drives doneness. (The FR-74 reclaim reads the DATA signal directly, not
        # this rendered column — proven biting by the DrainPin above.)
        rd = self._init(self._plan([self._agent_job("t-1", self._repo("r-1"))],
                                   pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        _set_hb(rd, "run-01", M)
        _set_output(self.repos / "r-1", M)               # output deliberately stale
        _append_hb(rd, "run-01", "COMPLETED", tid="t-1")
        # only ~1 min later (well within stall) so the ONLY terminal signal is the
        # declared COMPLETED, not any time-based reclaim.
        os.environ["ARUNNER_NOW"] = str(M + 60)
        out = T.tick(rd)
        s = _status(rd)
        self.assertEqual(s["runs"]["run-01"]["state"], "completed")
        self.assertIn("OUT-AGE", out["status_table"])    # column present (display)
        self.assertTrue(out["done"])


# --------------------------------------------------------------------------
# --check validation for the new knobs
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# FR-76 — target-state done-check + idempotent resume (run-dir-independent)
# --------------------------------------------------------------------------

class DoneCheckResume(_Base):
    """The gen-007 stop/restart acceptance: re-running the same plan = resume
    derived from TARGET STATE (done_check), independent of run-dir survival —
    skip the done, dispatch the remainder, redo a partial target."""

    def _done_job(self, tid, repo, artifact=None, command=None):
        j = {"id": tid, "repo": str(repo), "mode": "agent",
             "prompt": "do the work on this repository"}
        dc = {}
        if artifact is not None:
            dc["artifact"] = artifact
        if command is not None:
            dc["command"] = command
        j["done_check"] = dc
        return j

    def test_stop_restart_skips_done_dispatches_remainder(self):
        # THE load-bearing pin — model 33/58 -> resume the 25: N targets, K
        # already satisfy done_check (their artifact exists from the prior run).
        # A (re-)run dispatches ONLY the N-K remainder; the K done are NOT
        # re-dispatched; the remainder is not lost.
        # MUTATION (instr 009): delete the pre-dispatch done_check eval block in
        # _dispatch -> all N dispatch (nothing skipped) -> this test FAILs. Bite.
        N, K = 8, 3
        jobs = []
        for i in range(N):
            repo = self._repo("t-%02d" % i)
            if i < K:
                (repo / "DONE.flag").write_text("done")        # already complete
            jobs.append(self._done_job("job-%02d" % i, repo, artifact="DONE.flag"))
        rd = self._init(self._plan(jobs, pool_size=N))         # room for all remainder
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        s = _status(rd)
        skipped = [n for n, r in s["runs"].items()
                   if r["state"] == "completed" and r.get("done_skipped")]
        dispatched = [n for n, r in s["runs"].items()
                      if r["state"] in ("claimed", "running")]
        self.assertEqual(len(skipped), K)                      # the K done -> skipped
        self.assertEqual(len(dispatched), N - K)               # only remainder dispatched
        # the K done targets carry a synthesized COMPLETED sentinel and were never
        # dispatched (no subagent dispatch_mode recorded).
        for run in ("run-01", "run-02", "run-03"):             # job-00..02 (i<K)
            self.assertTrue(s["runs"][run].get("done_skipped"))
            self.assertIsNone(s["runs"][run].get("dispatch_mode"))
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertEqual(rec["terminal_status"], "COMPLETED")
        self.assertTrue(rec.get("synthesized"))
        self.assertIn("done_check", rec.get("summary", ""))
        # disk-truth: a skipped job leaves queue/ AND claimed/ empty (only the
        # result sentinel), exactly like a reaped completed job.
        self.assertFalse((rd / "queue" / "job-00001.json").exists())
        self.assertFalse((rd / "claimed" / "job-00001.json").exists())

    def test_partial_target_not_satisfied_is_redone_not_skipped(self):
        # A target whose done_check is NOT satisfied is dispatched (redone), never
        # skipped. MUTATION (instr 009): invert the guard to skip-on-unsatisfied
        # -> this target is wrongly skipped -> FAIL. Bite.
        repo = self._repo("p-1")                               # no DONE.flag
        job = self._done_job("job-1", repo, artifact="DONE.flag")
        rd = self._init(self._plan([job], pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertEqual(r["state"], "claimed")                # dispatched, redone
        self.assertFalse(r.get("done_skipped"))

    def test_done_check_command_exit0_skips_nonzero_dispatches(self):
        # The check-command shape: exit 0 => done (skip); non-zero => dispatch.
        done = self._repo("c-1")
        rd = self._init(self._plan(
            [self._done_job("job-1", done, command=["true"])], pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        self.assertTrue(_status(rd)["runs"]["run-01"].get("done_skipped"))

        todo = self._repo("c-2")
        os.environ["ARUNNER_RUNS_DIR"] = str(self.tmp / "runs2")   # distinct run-dir
        rd2 = self._init(self._plan(
            [self._done_job("job-1", todo, command=["false"])], pool_size=1))
        T.tick(rd2)
        self.assertEqual(_status(rd2)["runs"]["run-01"]["state"], "claimed")

    def test_artifact_glob_shape_matches_file(self):
        # The artifact predicate is a path/glob (a trailing ** matches files on
        # the 3.10+ floor — same portable glob as FR-73).
        repo = self._repo("g-1")
        (repo / "out").mkdir()
        (repo / "out" / "report.json").write_text("{}")
        rd = self._init(self._plan(
            [self._done_job("job-1", repo, artifact="out/**")], pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        self.assertTrue(_status(rd)["runs"]["run-01"].get("done_skipped"))

    def test_resume_from_fresh_run_dir_skips_done(self):
        # Run-dir independence: the SAME plan re-init'd into a FRESH run-dir
        # consults done_check on entry and skips the satisfied target — WITHOUT
        # the original run-dir (a lost/rotated run-dir resumes identically).
        done = self._repo("d-1"); (done / "DONE.flag").write_text("x")
        todo = self._repo("d-2")
        jobs = [self._done_job("job-1", done, artifact="DONE.flag"),
                self._done_job("job-2", todo, artifact="DONE.flag")]
        rd1 = self._init(self._plan(jobs, pool_size=2))        # first run-dir
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd1)
        # rd1 is now "lost": re-init the same plan into a brand-new run-dir
        # (distinct ARUNNER_RUNS_DIR so the wall-clock-stamped dir never collides).
        os.environ["ARUNNER_RUNS_DIR"] = str(self.tmp / "runs2")
        rd2 = self._init(self._plan(jobs, pool_size=2))
        self.assertNotEqual(rd1, rd2)
        T.tick(rd2)
        s = _status(rd2)
        self.assertTrue(s["runs"]["run-01"].get("done_skipped"))   # resume-skipped
        self.assertEqual(s["runs"]["run-02"]["state"], "claimed")  # remainder dispatched

    def test_inflight_job_with_done_check_not_double_dispatched(self):
        # FR-6 compose: only QUEUED runs are done-checked, so a claimed (in-flight)
        # job is never re-evaluated — even if its artifact appears mid-flight, it
        # is NOT flipped to done_skipped or re-dispatched; doneness stays the
        # worker's own terminal.
        repo = self._repo("f-1")                               # no DONE.flag yet
        job = self._done_job("job-1", repo, artifact="DONE.flag")
        rd = self._init(self._plan([job], pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        self.assertEqual(_status(rd)["runs"]["run-01"]["state"], "claimed")
        # the worker produces the artifact AND stays alive (fresh heartbeat)
        (repo / "DONE.flag").write_text("x")
        _set_hb(rd, "run-01", M + 1 * MIN)
        os.environ["ARUNNER_NOW"] = str(M + 2 * MIN)
        T.tick(rd)
        r = _status(rd)["runs"]["run-01"]
        self.assertIn(r["state"], ("claimed", "running"))
        self.assertFalse(r.get("done_skipped"))


# --------------------------------------------------------------------------
# FR-75 — per-job retry policy (max_attempts + backoff), landing the
# stall_retries seam. The gen-007 ~20% transient-abort rate ("child runner
# exited 1") needed manual wrapper re-runs; FR-75 auto-heals it in-engine.
# Time/clock via the ARUNNER_NOW seam — no real sleeps.
# --------------------------------------------------------------------------

class RetryPolicy(_Base):
    """A retryable terminal (`failed`, or a FR-74 stall-reclaim `abandoned`) is
    REQUEUED up to `max_attempts`, then goes terminal. Resume-not-restart;
    composes with FR-6 (no double-dispatch) and FR-76 (a now-done retry is
    skipped). max_attempts default 1 = the pre-FR-75 behavior."""

    def _retry_job(self, tid, repo, max_attempts=2, backoff=None, **extra):
        j = {"id": tid, "repo": str(repo), "mode": "agent",
             "prompt": "do the work on this repository",
             "max_attempts": max_attempts}
        if backoff is not None:
            j["retry_backoff_seconds"] = backoff
        j.update(extra)
        return j

    def test_retry_then_succeed(self):
        # THE PIN — a stub that FAILS attempt 1 with max_attempts:2 is requeued +
        # dispatched again, and succeeds on attempt 2 -> completed.
        # MUTATION: make _maybe_retry always return False (remove the requeue) ->
        # the run stays `failed` after attempt 1 -> this test FAILs. Bite.
        repo = self._repo("r-1")
        rd = self._init(self._plan([self._retry_job("t-1", repo)], pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)                                   # attempt 1 dispatched
        self.assertEqual(_status(rd)["runs"]["run-01"]["attempts"], 1)
        _append_hb(rd, "run-01", "FAILED")           # attempt 1 fails
        T.tick(rd)                                   # reap FAILED -> requeue + redispatch
        s = _status(rd)["runs"]["run-01"]
        self.assertIn(s["state"], ("claimed", "running"))   # attempt 2 in flight
        self.assertEqual(s["attempts"], 2)
        _append_hb(rd, "run-01", "COMPLETED")        # attempt 2 succeeds
        T.tick(rd)                                   # reap COMPLETED
        f = _status(rd)["runs"]["run-01"]
        self.assertEqual(f["state"], "completed")
        self.assertEqual(f["attempts"], 2)           # exactly two attempts, no more

    def test_cap_honored_terminal_failed_after_max_attempts(self):
        # max_attempts:2 with an always-failing job -> exactly 2 attempts then
        # terminal `failed` (never infinite).
        # MUTATION: off-by-one (`attempts > max_attempts`) or no-cap (always
        # requeue) -> attempts climbs past 2 / never terminal -> FAIL. Bite.
        repo = self._repo("r-1")
        rd = self._init(self._plan([self._retry_job("t-1", repo, max_attempts=2)],
                                   pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)                                   # attempt 1
        for _ in range(5):                           # bounded; a no-cap bug never breaks
            r = _status(rd)["runs"]["run-01"]
            if r["state"] in T._TERMINAL_STATES:
                break
            _append_hb(rd, "run-01", "FAILED")
            T.tick(rd)
        f = _status(rd)["runs"]["run-01"]
        self.assertEqual(f["state"], "failed")       # terminal after the cap
        self.assertEqual(f["attempts"], 2)           # exactly max_attempts, not more

    def test_no_double_dispatch_on_requeue(self):
        # FR-6 compose: a requeued job is dispatched ONCE per attempt, never
        # concurrently — exactly one dispatch entry + one claim lock per attempt.
        repo = self._repo("r-1")
        rd = self._init(self._plan([self._retry_job("t-1", repo)], pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        d1 = T.tick(rd)["dispatch_list"]
        self.assertEqual([d["run"] for d in d1], ["run-01"])   # one dispatch
        _append_hb(rd, "run-01", "FAILED")
        d2 = T.tick(rd)["dispatch_list"]             # requeue + redispatch this tick
        self.assertEqual([d["run"] for d in d2], ["run-01"])   # exactly one, not two
        locks = list((rd / "claimed").glob("job-00001.lock"))
        self.assertEqual(len(locks), 1)             # a single live claim, no double-claim
        self.assertEqual(_status(rd)["runs"]["run-01"]["attempts"], 2)

    def test_retry_skipped_when_done_check_now_satisfied(self):
        # Resume-not-restart + FR-76 compose: a retry whose done_check is now
        # satisfied is SKIPPED — no wasted attempt.
        # MUTATION: don't clear done_checked on requeue -> the retry re-dispatches
        # (attempts==2) instead of skipping -> FAIL. Bite.
        repo = self._repo("r-1")                     # no DONE.flag yet
        job = self._retry_job("t-1", repo, done_check={"artifact": "DONE.flag"})
        rd = self._init(self._plan([job], pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)                                   # attempt 1 dispatched (not done)
        self.assertEqual(_status(rd)["runs"]["run-01"]["attempts"], 1)
        (repo / "DONE.flag").write_text("done")      # target completes despite the fail
        _append_hb(rd, "run-01", "FAILED")
        T.tick(rd)                                   # reap FAILED -> requeue -> done-skip
        f = _status(rd)["runs"]["run-01"]
        self.assertEqual(f["state"], "completed")
        self.assertTrue(f.get("done_skipped"))       # skipped via done_check
        self.assertEqual(f["attempts"], 1)           # NO second attempt burned

    def test_max_attempts_1_is_no_retry_backcompat(self):
        # max_attempts:1 (and absent) = a single failure is terminal (unchanged).
        for ma in (1, None):
            repo = self._repo("r-%s" % ma)
            job = {"id": "t", "repo": str(repo), "mode": "agent",
                   "prompt": "do the work"}
            if ma is not None:
                job["max_attempts"] = ma
            os.environ["ARUNNER_RUNS_DIR"] = str(self.tmp / ("runs-%s" % ma))
            rd = self._init(self._plan([job], pool_size=1))
            os.environ["ARUNNER_NOW"] = str(M)
            T.tick(rd)
            _append_hb(rd, "run-01", "FAILED")
            T.tick(rd)
            f = _status(rd)["runs"]["run-01"]
            self.assertEqual(f["state"], "failed")   # terminal — no requeue
            self.assertEqual(f["attempts"], 1)

    def test_stall_reclaimed_job_is_requeued_when_under_cap(self):
        # The stall_retries seam now LIVE: an FR-74-reclaimed (stalled, output-
        # stale) job with max_attempts>1 is REQUEUED, not abandoned.
        # MUTATION: don't wire _maybe_retry into the reclaim caller -> it abandons
        # even with max_attempts -> assertNotEqual(state,'abandoned') FAILs. Bite.
        repo = self._repo("r-1")
        rd = self._init(self._plan([self._retry_job("t-1", repo, max_attempts=2)],
                                   pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)                                   # attempt 1
        _set_hb(rd, "run-01", M)                     # heartbeat goes silent at M
        _set_output(repo, M)                         # output also stale at M
        os.environ["ARUNNER_NOW"] = str(M + 91 * MIN)  # past reclaim AND stale
        T.tick(rd)                                   # reclaim -> requeue + redispatch
        s = _status(rd)["runs"]["run-01"]
        self.assertNotEqual(s["state"], "abandoned")  # requeued, NOT abandoned
        self.assertEqual(s["attempts"], 2)            # second attempt consumed
        # the prior attempt's synthesized ABANDONED sentinel was cleared on requeue
        self.assertFalse((rd / "results" / "result-00001.json").exists())

    def test_stall_reclaimed_abandoned_when_cap_exhausted(self):
        # End-state decision: a stall-reclaimed job that EXHAUSTS its budget ends
        # `abandoned` (FR-74's honest "gave up waiting"), not `failed`.
        repo = self._repo("r-1")
        rd = self._init(self._plan([self._retry_job("t-1", repo, max_attempts=1)],
                                   pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)
        _set_hb(rd, "run-01", M)
        _set_output(repo, M)
        os.environ["ARUNNER_NOW"] = str(M + 91 * MIN)
        T.tick(rd)
        s = _status(rd)["runs"]["run-01"]
        self.assertEqual(s["state"], "abandoned")    # exhausted -> abandoned (not failed)
        rec = json.loads((rd / "results" / "result-00001.json").read_text())
        self.assertEqual(rec["terminal_status"], "ABANDONED")

    def test_auth_or_launch_failed_is_not_retried(self):
        # Transient-vs-fatal default (Council C-F1): a FATAL terminal
        # (auth_or_launch_failed) is NEVER retried, even with max_attempts>1 — it
        # won't succeed on a blind re-run, so it never burns attempts. A shell job
        # that emits no heartbeat past launch_grace is the launch-failure case.
        # MUTATION: wire _maybe_retry into the auth/launch-fail path -> the job is
        # requeued (state leaves auth_or_launch_failed) -> this test FAILs. Bite.
        repo = self._repo("r-1")
        job = {"id": "t-1", "repo": str(repo), "mode": "shell",
               "command": ["true"], "max_attempts": 3}
        rd = self._init(self._plan([job], pool_size=1, launch_grace_minutes=10))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)                                   # claimed (shell), no heartbeat
        self.assertEqual(_status(rd)["runs"]["run-01"]["attempts"], 1)
        os.environ["ARUNNER_NOW"] = str(M + 11 * MIN)  # past the 10-min launch grace
        T.tick(rd)
        s = _status(rd)["runs"]["run-01"]
        self.assertEqual(s["state"], "auth_or_launch_failed")  # fatal — terminal
        self.assertEqual(s["attempts"], 1)           # NOT retried (no attempt burned)

    def test_backoff_delays_redispatch(self):
        # A requeued attempt is NOT dispatch-eligible until retry_backoff_seconds
        # elapse (via ARUNNER_NOW).
        # MUTATION: ignore retry_not_before -> the retry redispatches immediately
        # (state claimed at tick2) -> assertEqual(state,'queued') FAILs. Bite.
        repo = self._repo("r-1")
        rd = self._init(self._plan(
            [self._retry_job("t-1", repo, max_attempts=2, backoff=600)], pool_size=1))
        os.environ["ARUNNER_NOW"] = str(M)
        T.tick(rd)                                   # attempt 1
        _append_hb(rd, "run-01", "FAILED")
        os.environ["ARUNNER_NOW"] = str(M + 1 * MIN)
        T.tick(rd)                                   # reap -> requeue; backoff not elapsed
        s = _status(rd)["runs"]["run-01"]
        self.assertEqual(s["state"], "queued")       # held in backoff, NOT redispatched
        self.assertEqual(s["attempts"], 1)
        os.environ["ARUNNER_NOW"] = str(M + 12 * MIN)  # past the 600s backoff
        T.tick(rd)                                   # now eligible -> redispatch
        f = _status(rd)["runs"]["run-01"]
        self.assertIn(f["state"], ("claimed", "running"))
        self.assertEqual(f["attempts"], 2)


class CheckValidation(_Base):
    def _check(self, plan):
        pf = self.tmp / "p.json"
        pf.write_text(json.dumps(plan))
        return T.check_plan(str(pf))

    def test_reclaim_must_exceed_stall_threshold(self):
        probs = self._check(self._plan(
            [self._agent_job("t-1", "/tmp")], stall_threshold_minutes=45,
            stall_reclaim_minutes=30))
        self.assertTrue(any("stall_reclaim_minutes" in p and "stall_threshold" in p
                            for p in probs))

    def test_reclaim_must_be_below_hard_cap(self):
        probs = self._check(self._plan(
            [self._agent_job("t-1", "/tmp")], stall_reclaim_minutes=900,
            subagent_hard_cap_minutes=720))
        self.assertTrue(any("stall_reclaim_minutes" in p and "subagent_hard_cap" in p
                            for p in probs))

    def test_stall_retries_must_be_non_negative(self):
        probs = self._check(self._plan([self._agent_job("t-1", "/tmp")],
                                       stall_retries=-1))
        self.assertTrue(any("stall_retries" in p for p in probs))
        # 0 is the valid default
        self.assertEqual(self._check(self._plan([self._agent_job("t-1", "/tmp")],
                                                stall_retries=0)), [])

    def test_output_globs_must_be_string_list(self):
        self.assertTrue(any("output_globs" in p for p in self._check(self._plan(
            [self._agent_job("t-1", "/tmp")], output_globs="quality/**"))))
        self.assertEqual(self._check(self._plan(
            [self._agent_job("t-1", "/tmp")], output_globs=["quality/**"])), [])

    def test_clean_plan_with_all_new_knobs(self):
        job = self._agent_job("t-1", "/tmp")
        job["output_globs"] = ["out/**"]
        job["done_check"] = {"artifact": "DONE.flag"}
        self.assertEqual(self._check(self._plan(
            [job], stall_reclaim_minutes=120, stall_retries=1,
            output_globs=["quality/**"])), [])

    def test_done_check_requires_exactly_one_shape(self):
        job = self._agent_job("t-1", "/tmp")
        # neither artifact nor command
        job["done_check"] = {}
        self.assertTrue(any("done_check" in p for p in
                            self._check(self._plan([dict(job)]))))
        # both at once
        job["done_check"] = {"artifact": "x", "command": ["true"]}
        self.assertTrue(any("done_check" in p for p in
                            self._check(self._plan([dict(job)]))))
        # each single shape is clean
        job["done_check"] = {"artifact": "DONE.flag"}
        self.assertEqual(self._check(self._plan([dict(job)])), [])
        job["done_check"] = {"command": ["test", "-f", "DONE.flag"]}
        self.assertEqual(self._check(self._plan([dict(job)])), [])

    def test_max_attempts_must_be_pos_int(self):
        # FR-75: max_attempts is an integer >= 1 (default 1).
        for bad in (0, -1, 1.5, True, "2"):
            job = self._agent_job("t-1", "/tmp")
            job["max_attempts"] = bad
            self.assertTrue(any("max_attempts" in p for p in
                                self._check(self._plan([job]))),
                            "expected a max_attempts problem for %r" % (bad,))
        job = self._agent_job("t-1", "/tmp")
        job["max_attempts"] = 3                       # valid
        self.assertEqual(self._check(self._plan([job])), [])

    def test_retry_backoff_must_be_non_negative_number(self):
        # FR-75: retry_backoff_seconds is a number >= 0 (default 0).
        for bad in (-1, -0.5, True, "5"):
            job = self._agent_job("t-1", "/tmp")
            job["retry_backoff_seconds"] = bad
            self.assertTrue(any("retry_backoff_seconds" in p for p in
                                self._check(self._plan([job]))),
                            "expected a retry_backoff_seconds problem for %r" % (bad,))
        for ok in (0, 30, 12.5):                      # int and float both fine
            job = self._agent_job("t-1", "/tmp")
            job["retry_backoff_seconds"] = ok
            job["max_attempts"] = 2
            self.assertEqual(self._check(self._plan([job])), [])

    def test_clean_plan_with_retry_knobs(self):
        job = self._agent_job("t-1", "/tmp")
        job["max_attempts"] = 3
        job["retry_backoff_seconds"] = 60
        job["done_check"] = {"artifact": "DONE.flag"}
        self.assertEqual(self._check(self._plan([job], stall_retries=1)), [])

    def test_done_check_bad_member_shapes(self):
        job = self._agent_job("t-1", "/tmp")
        job["done_check"] = {"artifact": ""}                   # empty string
        self.assertTrue(any("artifact" in p for p in
                            self._check(self._plan([dict(job)]))))
        job["done_check"] = {"command": []}                    # empty argv
        self.assertTrue(any("command" in p for p in
                            self._check(self._plan([dict(job)]))))
        job["done_check"] = {"command": [1, 2]}                # non-string argv
        self.assertTrue(any("command" in p for p in
                            self._check(self._plan([dict(job)]))))
        job["done_check"] = {"nope": 1}                        # unknown key
        self.assertTrue(any("done_check" in p for p in
                            self._check(self._plan([dict(job)]))))


if __name__ == "__main__":
    unittest.main()
