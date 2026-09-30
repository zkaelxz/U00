"""
services/job_timing_service.py -- Step 41 item 5: per-stage duration and
estimated spend for every real background job (not just benchmark runs).
UI-free; rows live in db.py's `job_stage_timings`.

background_jobs calls `start_run(job_id)` when a job actually starts
running (after any GPU-queue wait) and `finish_run(job_id)` when it ends,
whatever the outcome. In between, a job may call `mark_stage(job_id,
"Translate")` at each stage boundary; a job that never does gets one
"Whole job" row, so every run still has a breakdown. Spend comes from
`db.log_usage`, which calls `add_cost` from the job's own thread (the
`current job` is a thread-local that background_jobs sets for thread jobs).
Limits: spend logged from a helper thread a job starts itself, or from a
subprocess job (dub, narration track), isn't attributed; those jobs still
get their timing.

Every function here swallows its own errors: timing must never break the
job it measures.
"""

import contextlib
import threading
import time

import db

WHOLE_JOB = "Whole job"
MAX_STAGE_LEN = 60
RUNS_KEPT_PER_JOB = 10

_lock = threading.Lock()
_open = {}      # job_id -> {"run": float, "stage": str|None, "t0": float, "cost": float}
_local = threading.local()


@contextlib.contextmanager
def _conn():
    with contextlib.closing(db.get_conn()) as conn:
        with conn:
            yield conn


def set_current_job(job_id):
    _local.job_id = job_id


def current_job():
    return getattr(_local, "job_id", None)


def start_run(job_id, now=None):
    """Returns the run's token (its start time) for finish_run, or None."""
    try:
        now = time.time() if now is None else now
        with _lock:
            _open[job_id] = {"run": now, "stage": None, "t0": now, "cost": 0.0}
        return now
    except Exception:
        return None


def add_cost(usd, job_id=None):
    """Adds spend to the current job's open stage (no-op outside a job)."""
    try:
        job_id = job_id or current_job()
        if not job_id or not usd:
            return
        with _lock:
            run = _open.get(job_id)
            if run is not None:
                run["cost"] += float(usd)
    except Exception:
        pass


def _close_stage_locked(job_id, now, final=False):
    run = _open.get(job_id)
    if run is None:
        return None
    stage = run["stage"]
    if stage is None and not final:
        # Time before the first mark is only kept if it's the whole job
        # or if something was spent in it.
        if run["cost"] <= 0 and now - run["t0"] < 1.0:
            return None
        stage = "Preparing"
    row = (job_id, run["run"], stage or WHOLE_JOB, run["t0"], now,
           max(0.0, now - run["t0"]), round(run["cost"], 6))
    run["t0"], run["cost"] = now, 0.0
    return row


def _insert(rows):
    rows = [r for r in rows if r]
    if not rows:
        return
    with _conn() as conn:
        conn.executemany(
            "INSERT INTO job_stage_timings (job_id, run_started_at, stage, started_at, "
            "ended_at, duration_s, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def mark_stage(job_id, stage, now=None):
    """Ends the job's current stage (if any) and starts `stage`."""
    try:
        now = time.time() if now is None else now
        name = " ".join(str(stage or "").split())[:MAX_STAGE_LEN] or WHOLE_JOB
        with _lock:
            if job_id not in _open:
                return
            row = _close_stage_locked(job_id, now)
            _open[job_id]["stage"] = name
        _insert([row])
    except Exception:
        pass


def finish_run(job_id, now=None, token=None):
    """With `token` (start_run's return), finishes only that run: a new run
    of the same job id that started meanwhile is left alone."""
    try:
        now = time.time() if now is None else now
        with _lock:
            if token is not None and (_open.get(job_id) or {}).get("run") != token:
                return
            row = _close_stage_locked(job_id, now, final=True)
            run = _open.pop(job_id, None)
        _insert([row])
        if run is not None:
            _prune(job_id)
    except Exception:
        pass


def _prune(job_id):
    with _conn() as conn:
        conn.execute(
            "DELETE FROM job_stage_timings WHERE job_id = ? AND run_started_at NOT IN ("
            "SELECT DISTINCT run_started_at FROM job_stage_timings WHERE job_id = ? "
            "ORDER BY run_started_at DESC LIMIT ?)", (job_id, job_id, RUNS_KEPT_PER_JOB))


def list_runs(job_id, limit=RUNS_KEPT_PER_JOB) -> list:
    """Newest run first: {run_started_at, running, total_seconds, cost_usd,
    stages: [{stage, started_at, duration_seconds, cost_usd}]}. A run still
    going shows its finished stages plus the open one so far."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT run_started_at, stage, started_at, duration_s, cost_usd "
            "FROM job_stage_timings WHERE job_id = ? ORDER BY run_started_at DESC, started_at",
            (job_id,)).fetchall()
    runs = {}
    for run_started, stage, started, duration, cost in rows:
        runs.setdefault(run_started, []).append(
            {"stage": stage, "started_at": started, "duration_seconds": round(duration, 3),
             "cost_usd": round(cost or 0.0, 6)})
    with _lock:
        live = dict(_open.get(job_id) or {})
    if live:
        now = time.time()
        runs.setdefault(live["run"], []).append(
            {"stage": live["stage"] or WHOLE_JOB, "started_at": live["t0"],
             "duration_seconds": round(max(0.0, now - live["t0"]), 3),
             "cost_usd": round(live["cost"], 6)})
    out = []
    for run_started in sorted(runs, reverse=True)[:limit]:
        stages = runs[run_started]
        out.append({"run_started_at": run_started,
                    "running": bool(live) and live["run"] == run_started,
                    "total_seconds": round(sum(s["duration_seconds"] for s in stages), 3),
                    "cost_usd": round(sum(s["cost_usd"] for s in stages), 6),
                    "stages": stages})
    return out


def reset_for_tests():
    with _lock:
        _open.clear()
    _local.job_id = None
