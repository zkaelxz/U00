"""jobs/store.py -- writes and closes the job_records row of every job.

background_jobs' `_jobs` dict decides what a job this process runs does next;
its row in library.db is what every process (and a restarted server) sees.
This module is the only code that writes or closes those rows. See
docs/background-jobs.md for the row's columns and states.
"""

import atexit
import contextlib
import os
import secrets
import sqlite3
import time

import db

# Tells this server apart from an earlier one that had the same pid, so a
# restarted server never counts as the live owner of a row it did not write.
INSTANCE_ID = secrets.token_hex(8)

ACTIVE = ("queued", "running")
_WRITE_TRIES = 3
_RETRY_DELAY = 0.05
EXIT_FLUSH_SECONDS = 2.0
SYNC_ERROR_SUFFIX = "Job state could not be saved ({}); showing the in-memory state"

# Job ids whose last write failed, retried by heartbeat_tick. Guarded by
# background_jobs._lock, like the jobs themselves.
_pending = set()


def _storage_text(text):
    """job_records lands in backups: secrets and URL query strings out."""
    if not text:
        return text
    from translate_engines import redact_for_storage
    return redact_for_storage(text)


def _is_lock_error(exc) -> bool:
    text = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in text or "busy" in text)


def _retrying(fn):
    """Another process (cli.py, a second server) can hold the write lock past
    sqlite's busy timeout; a short backoff saves the write instead of
    leaving the row behind the job."""
    for attempt in range(_WRITE_TRIES):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if attempt == _WRITE_TRIES - 1 or not _is_lock_error(exc):
                raise
            time.sleep(_RETRY_DELAY * (2 ** attempt))


def _write_row(job_id, job, result_json):
    db.save_job_record(
        job_id, status=job.get("status"), progress=job.get("progress"),
        message=_storage_text(job.get("message")), error=_storage_text(job.get("error")),
        description=job.get("description"), gpu_touching=bool(job.get("gpu_touching")),
        started_at=job.get("started_at"), finished_at=job.get("finished_at"),
        result_json=result_json, owner_user_id=job.get("owner_user_id"),
        owner_pid=os.getpid())
    detail = job.get("detail_state")
    with contextlib.closing(db.get_conn()) as conn:
        # A cancel another process asked for keeps its time while the job is
        # active (the owner hears it later); a final row has none, like
        # cancel_requested.
        conn.execute(
            "UPDATE job_records SET kind = ?, owner_instance = ?, detail_state = ?, sync_error = NULL, "
            "cancel_requested_at = CASE WHEN status IN ('queued', 'running') "
            "THEN COALESCE(?, cancel_requested_at) END WHERE job_id = ?",
            (job.get("kind"), INSTANCE_ID, None if detail == "unknown" else detail,
             job.get("cancel_requested_at"), job_id))
        conn.commit()


def _result_json(job_id, job):
    try:
        from services.jobs_service import project_result_json
        return project_result_json(job.get("result"), job.get("run_settings"))
    except Exception:
        import applog
        applog.get_logger().warning(f"job {job_id}: could not project result", exc_info=True)
        return None


def write_transition(job_id, job) -> bool:
    """Caller holds background_jobs._lock. Writes `job`'s status-transition
    fields to its row; True when the write landed. A failed write never
    breaks the job, and is never silent: the job carries `sync_error` and
    detail_state "unknown" (the API shows them), the failure is logged once,
    and heartbeat_tick retries it until it lands."""
    import background_jobs as bj
    if job is None:
        _pending.discard(job_id)
        return False
    result_json = _result_json(job_id, job)
    try:
        _retrying(lambda: _write_row(job_id, job, result_json))
    except Exception as exc:
        _note_failure(job_id, job, exc)
        ok = False
    else:
        ok = True
        _pending.discard(job_id)
        job.pop("sync_error", None)
        if job.get("detail_state") == "unknown":
            job.pop("detail_state")
    bj._emit_change(job_id)
    return ok


def _note_failure(job_id, job, exc):
    import applog
    import translate_engines
    text = translate_engines.redact_secrets(f"{type(exc).__name__}: {exc}")[:200]
    if job_id not in _pending:
        applog.get_logger().warning(f"job {job_id}: could not save its state to job_records: {text}")
    _pending.add(job_id)
    job["sync_error"] = text
    if job.get("status") in ACTIVE or not job.get("detail_state"):
        job["detail_state"] = "unknown"
    if _is_lock_error(exc):
        return   # the same lock would block this write too, under background_jobs._lock
    # Lands when only the full write failed (a value it could not store);
    # with the database down this fails too and the job still carries it.
    with contextlib.suppress(Exception), contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE job_records SET sync_error = ? WHERE job_id = ?",
                     (_storage_text(text), job_id))
        conn.commit()


def sync_error_suffix(sync_error) -> str:
    return SYNC_ERROR_SUFFIX.format(sync_error)


def heartbeat_tick():
    """One beat of background_jobs' heartbeat thread: reconciles dead
    workers, keeps this process's live rows and GPU slots fresh so another
    process can tell a quiet job from a dead one, and retries failed writes."""
    import background_jobs as bj
    import job_force_stop
    bj.reconcile_dead_workers()
    job_force_stop.refresh_abandoned_gpu_rows()
    with bj._lock:
        live = [j for j, job in bj._jobs.items() if job.get("status") in ACTIVE]
        running_gpu = [j for j in live if bj._jobs[j]["status"] == "running"
                       and bj._jobs[j].get("gpu_touching")]
    # A job that reports no progress (dub) never refreshes its own row.
    for j in running_gpu:
        with contextlib.suppress(Exception):
            db.heartbeat_gpu_lock(f"ui:{j}")
    if live:
        try:
            db.touch_job_records(live)
        except Exception as e:
            # A live job whose heartbeat can't be written for
            # STALE_JOB_SECONDS looks dead to another process's checks.
            # This process's own sweep still sees it as live.
            with contextlib.suppress(Exception):
                import applog
                import translate_engines
                applog.get_logger().warning(
                    "job heartbeat write failed: " + translate_engines.redact_secrets(str(e))[:300])
    with bj._lock:
        for job_id in list(_pending):
            write_transition(job_id, bj._jobs.get(job_id))


def owner_gone(record: dict) -> bool:
    """True when the row names an owner process that no longer runs it: an
    exited pid, or this process's own pid with no such job live here (an
    earlier server had the same pid). Rows written before owner_pid existed
    fall back to the heartbeat cutoff. Callers have already checked the job
    isn't live in this process."""
    import background_jobs as bj
    pid = record.get("owner_pid")
    if pid is None:
        return False
    if pid == os.getpid():
        return True
    return not bj.owner_process_alive(pid)


def _close(conn, job_id, condition, params) -> bool:
    """The one way a row is closed by anyone but its worker: a single
    conditional UPDATE, so a live owner's write, heartbeat or new run
    always wins."""
    import background_jobs as bj
    cur = conn.execute(
        "UPDATE job_records SET status = 'cancelled', finished_at = ?, cancel_requested = 0, "
        "cancel_requested_at = NULL, detail_state = 'interrupted', error = ? "
        f"WHERE job_id = ? AND status IN ('queued', 'running') AND {condition}",
        (time.time(), bj.INTERRUPTED_MESSAGE, job_id, *params))
    return cur.rowcount > 0


def _close_one(job_id, condition, params) -> bool:
    def run():
        with contextlib.closing(db.get_conn()) as conn:
            closed = _close(conn, job_id, condition, params)
            conn.commit()
            return closed
    return _retrying(run)


def close_if_owner_gone(record: dict) -> bool:
    """Closes `record`'s row as interrupted if its owner is gone and it still
    names that owner: the instance, or the pid for a row from before
    owner_instance existed."""
    if not owner_gone(record):
        return False
    if record.get("owner_instance"):
        return _close_one(record["job_id"], "owner_instance = ?", (record["owner_instance"],))
    return _close_one(record["job_id"], "owner_instance IS NULL AND owner_pid = ?",
                      (record["owner_pid"],))


def close_stale(job_id: str, cutoff: float) -> bool:
    """Closes the row as interrupted only if its owner has not written or
    heartbeated since `cutoff`."""
    return _close_one(job_id, "COALESCE(updated_at, 0) < ?", (cutoff,))


def sweep_dead_owners(stale_seconds: float) -> int:
    """Closes every queued/running row that is not live in this process and
    whose owner is gone (close_if_owner_gone) or has not heartbeated for
    `stale_seconds`. Also marks this process's own running jobs whose
    worker thread is gone (background_jobs.reconcile_dead_workers). Returns
    how many rows it closed."""
    import background_jobs as bj
    bj.reconcile_dead_workers()
    cutoff = time.time() - stale_seconds
    closed = 0
    for rec in db.list_job_records():
        job_id = rec.get("job_id")
        if rec.get("status") not in ACTIVE or bj.get_status(job_id):
            continue
        if close_if_owner_gone(rec) or (
                (rec.get("updated_at") or 0) < cutoff and close_stale(job_id, cutoff)):
            closed += 1
    return closed


def request_cancel(job_id: str) -> bool:
    """Flags a still queued/running row as cancel-requested, with the time,
    so the owner (maybe another process) stops it and every process judges
    Force stop from the same moment. Returns whether it flagged a row."""
    def run():
        with contextlib.closing(db.get_conn()) as conn:
            cur = conn.execute(
                "UPDATE job_records SET cancel_requested = 1, "
                "cancel_requested_at = COALESCE(cancel_requested_at, ?) "
                "WHERE job_id = ? AND status IN ('queued', 'running')", (time.time(), job_id))
            conn.commit()
            return cur.rowcount > 0
    return _retrying(run)


def flush_at_exit() -> None:
    """Leaves no row of this server saying queued or running after it is
    gone: retries failed writes, closes this instance's still-active rows as
    interrupted and frees their GPU slots. Runs from atexit and from
    shutdown_service after the jobs' grace wait. A hard kill skips it; the
    next start's sweep (sweep_dead_owners) closes those rows instead."""
    import background_jobs as bj
    import job_force_stop
    # A daemon worker can hold the lock while the interpreter exits.
    if not bj._lock.acquire(timeout=EXIT_FLUSH_SECONDS):
        return
    try:
        deadline = time.monotonic() + EXIT_FLUSH_SECONDS
        for job_id in list(_pending):
            if time.monotonic() >= deadline:
                break
            write_transition(job_id, bj._jobs.get(job_id))
        active = [j for j, job in bj._jobs.items() if job.get("status") in ACTIVE]
        gpu_holders = {f"ui:{j}" for j in active if bj._jobs[j].get("gpu_touching")}
        gpu_holders |= {f"ui:{j}" for j in list(job_force_stop._abandoned)
                        if job_force_stop.abandoned_alive_locked(j)}
    finally:
        bj._lock.release()
    if not active and not gpu_holders:
        return
    try:
        with contextlib.closing(db.get_conn()) as conn:
            for job_id in active:
                _close(conn, job_id, "owner_instance = ?", (INSTANCE_ID,))
            for holder in gpu_holders:
                conn.execute("DELETE FROM gpu_lock WHERE holder = ?", (holder,))
            conn.commit()
    except Exception as exc:
        with contextlib.suppress(Exception):
            import applog
            import translate_engines
            applog.get_logger().warning(
                "could not close this server's job rows at exit: "
                + translate_engines.redact_secrets(str(exc))[:300])


atexit.register(flush_at_exit)
