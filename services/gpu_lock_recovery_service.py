"""Frees GPU slots left behind by a Baihe server that was killed or restarted.

The gpu_lock table is shared with cli.py runs and, via library.db, possibly
with another server process, so nothing here deletes a row on its name alone:
a server job's row ("ui:<job id>") is released only when the job_records row
of that job id names an owner process that is gone. A "cli:<pid>" row is never
touched, even a dead one: it expires on its own after db.GPU_LOCK_STALE_SECONDS.
"""

import contextlib
import math
import os
import time

import background_jobs
import db

SERVER_HOLDER_PREFIX = "ui:"


def _owner_is_other_live_process(job_id: str) -> bool:
    """True when job_records says another process that still runs owns this
    job id. No record, or no owner pid (a row from before owner_pid existed),
    reads as False: the caller then treats the holder as left behind."""
    record = db.get_job_record(job_id)
    pid = (record or {}).get("owner_pid")
    return pid is not None and pid != os.getpid() and background_jobs.owner_process_alive(pid)


def _owner_is_gone(job_id: str) -> bool:
    """Stricter than _owner_is_other_live_process: a missing record or pid is
    not proof, so such a row is left to expire. This process's own pid counts
    as gone because the job is not live here (the same-pid rule of
    jobs_service.sweep_stale_job_records: an earlier run had the pid)."""
    pid = (db.get_job_record(job_id) or {}).get("owner_pid")
    if pid is None:
        return False
    return pid == os.getpid() or not background_jobs.owner_process_alive(pid)


def release_orphaned_server_holders() -> int:
    """Deletes the gpu_lock rows of server jobs whose owner process is gone;
    returns how many. Meant to run once at startup, after the previous run's
    job records were swept, so the next GPU job does not queue for up to
    db.GPU_LOCK_STALE_SECONDS behind a job that no longer exists."""
    released = 0
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, holder, acquired_at, heartbeat_at FROM gpu_lock WHERE holder LIKE ?",
            (SERVER_HOLDER_PREFIX + "%",)).fetchall()
        for row in rows:
            job_id = row["holder"][len(SERVER_HOLDER_PREFIX):]
            if background_jobs.get_status(job_id) is not None or not _owner_is_gone(job_id):
                continue
            # Matching the timestamps read above makes this a compare-and-delete:
            # a job that took the slot since (even under the same holder name)
            # rewrites them, so its row survives.
            cur = conn.execute(
                "DELETE FROM gpu_lock WHERE id = ? AND holder = ? AND acquired_at = ? AND heartbeat_at = ?",
                (row["id"], row["holder"], row["acquired_at"], row["heartbeat_at"]))
            conn.commit()
            released += cur.rowcount
    return released


def previous_run_wait_message(now: float = None):
    """The queued-job message when the only thing holding the GPU is a live
    gpu_lock row left by a server job nobody here runs, else None. Never names
    the holder, its description or a path. A live "cli:" row, or a server row
    whose owner process still runs, is a real job, so it keeps the generic
    wording."""
    now = time.time() if now is None else now
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT holder, heartbeat_at FROM gpu_lock").fetchall()
    live = [r for r in rows if now - r["heartbeat_at"] < db.GPU_LOCK_STALE_SECONDS]
    if not live or any(
            not r["holder"].startswith(SERVER_HOLDER_PREFIX)
            or _owner_is_other_live_process(r["holder"][len(SERVER_HOLDER_PREFIX):])
            for r in live):
        return None
    remaining = db.GPU_LOCK_STALE_SECONDS - (now - max(r["heartbeat_at"] for r in live))
    return f"Waiting for a previous run to be released (up to {max(1, math.ceil(remaining / 60))} min)"
