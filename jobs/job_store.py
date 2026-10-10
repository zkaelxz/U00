"""jobs/job_store.py -- writes and closes the job_records row of every job.

background_jobs' `_jobs` dict decides what a job this process runs does next;
its row in library.db is what every process (and a restarted server) sees.
This module is the only code that writes or closes those rows. See
docs/background-jobs.md for the row's columns and states.
"""

import atexit
import contextlib
import itertools
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
# job id -> a token unique to its latest write_transition (same lock). A
# retry written outside the lock applies its outcome only if no transition
# wrote in the meantime, or it could pass off an older state as landed.
_last_write = {}
_tokens = itertools.count()
_exit_hook_registered = False
_flushed = False


def _storage_text(text):
    """job_records lands in backups: secrets and URL query strings out."""
    if not text:
        return text
    from translate_engines import redact_for_storage
    return redact_for_storage(text)


def _is_lock_error(exc) -> bool:
    text = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in text or "busy" in text)


def _connect(timeout=None):
    """db.get_conn, with sqlite's busy wait cut to `timeout` seconds (its
    5 s default when None)."""
    conn = db.get_conn()
    if timeout is not None:
        conn.execute(f"PRAGMA busy_timeout = {max(0, int(timeout * 1000))}")
    return conn


def _retrying(fn, deadline=None):
    """Never called under background_jobs._lock: another process (cli.py, a
    second server) can hold the write lock past sqlite's busy timeout, and a
    short backoff saves the write instead of leaving the row behind the job.
    `fn` gets the seconds left before `deadline` (time.monotonic(); None for
    no limit) and no try or wait runs past it."""
    for attempt in range(_WRITE_TRIES):
        left = None if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            return fn(left)
        except sqlite3.OperationalError as exc:
            delay = _RETRY_DELAY * (2 ** attempt)
            if (attempt == _WRITE_TRIES - 1 or not _is_lock_error(exc)
                    or (left is not None and left <= delay)):
                raise
            time.sleep(delay)


def _write_row(job_id, job, result_json, timeout=None):
    """One transaction, so a sweep keyed on the row's previous owner_instance
    never sees the new run's status before its new owner."""
    detail = job.get("detail_state")
    with contextlib.closing(_connect(timeout)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        db.save_job_record(
            job_id, status=job.get("status"), progress=job.get("progress"),
            message=_storage_text(job.get("message")), error=_storage_text(job.get("error")),
            description=job.get("description"), gpu_touching=bool(job.get("gpu_touching")),
            started_at=job.get("started_at"), finished_at=job.get("finished_at"),
            result_json=result_json, owner_user_id=job.get("owner_user_id"),
            owner_pid=os.getpid(), conn=conn)
        # The first cancel time stands (the requester's, which the owner
        # adopts when it hears the cancel) while the job is active; a final
        # row has none, like cancel_requested.
        conn.execute(
            "UPDATE job_records SET kind = ?, owner_instance = ?, detail_state = ?, sync_error = NULL, "
            "cancel_requested_at = CASE WHEN status IN ('queued', 'running') "
            "THEN COALESCE(cancel_requested_at, ?) END WHERE job_id = ?",
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
    fields to its row in one try (a retry's backoff would stall every
    get_status); True when the write landed. A failed write never breaks the
    job, and is never silent: the job carries `sync_error` and detail_state
    "unknown" (the API shows them), the failure is logged once, and
    heartbeat_tick retries it outside the lock until it lands."""
    import background_jobs as bj
    _last_write[job_id] = next(_tokens)
    if job is None:
        _pending.discard(job_id)
        return False
    try:
        _write_row(job_id, job, _result_json(job_id, job))
    except Exception as exc:
        _note_failure(job_id, job, exc)
        ok = False
    else:
        _landed(job_id, job)
        ok = True
    bj._emit_change(job_id)
    return ok


def _landed(job_id, job):
    _pending.discard(job_id)
    job.pop("sync_error", None)
    if job.get("detail_state") == "unknown":
        job.pop("detail_state")


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
    retry_pending()


def _acquire(lock, deadline) -> bool:
    if deadline is None:
        return lock.acquire()
    return lock.acquire(timeout=max(0.0, deadline - time.monotonic()))


def retry_pending(deadline=None) -> None:
    """Retries every failed write. Each write runs outside
    background_jobs._lock, so a locked database never stalls get_status,
    update_progress or a Jobs poll; the lock is held only to copy the job
    and to apply the outcome. Stops at the first "database is locked" (the
    rest would wait on the same lock) and at `deadline` (time.monotonic())."""
    import background_jobs as bj
    for _ in range(_WRITE_TRIES):
        if not _acquire(bj._lock, deadline):
            return
        try:
            batch = []
            for job_id in list(_pending):
                job = bj._jobs.get(job_id)
                if job is None:
                    _pending.discard(job_id)
                    continue
                batch.append((job_id, job, _last_write.get(job_id), dict(job),
                              _result_json(job_id, job)))
        finally:
            bj._lock.release()
        again = False
        for job_id, job, token, copy, result_json in batch:
            if deadline is not None and time.monotonic() >= deadline:
                return
            try:
                _retrying(lambda left, j=job_id, c=copy, r=result_json: _write_row(j, c, r, left),
                          deadline)
                error = None
            except Exception as exc:
                error = exc
            if not _acquire(bj._lock, deadline):
                return
            try:
                current = bj._jobs.get(job_id)
                if current is None:
                    _pending.discard(job_id)
                    _last_write.pop(job_id, None)
                elif current is not job or _last_write.get(job_id) != token:
                    # A transition wrote while this copy was in flight, and
                    # this older copy may have landed over it: write again.
                    _pending.add(job_id)
                    again = True
                elif error is None:
                    _landed(job_id, job)
                else:
                    _note_failure(job_id, job, error)
            finally:
                bj._lock.release()
            if current is None:
                _drop_cleared_row(job_id, copy)
            bj._emit_change(job_id)
            if error is not None and _is_lock_error(error):
                return
        if not again:
            return


def _drop_cleared_row(job_id, copy):
    """clear_job deleted the row while a retry was writing it; the retry may
    have put it back. Only that write's row goes, never a new run's."""
    with contextlib.suppress(Exception), contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM job_records WHERE job_id = ? AND owner_instance = ? "
                     "AND started_at IS ? AND status = ?",
                     (job_id, INSTANCE_ID, copy.get("started_at"), copy.get("status")))
        conn.commit()


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
    def run(_left):
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


def owner_alive_elsewhere(pid) -> bool:
    """True when `pid` (a row's owner_pid) is another process that still
    runs: its row may be quiet but is not abandoned."""
    import background_jobs as bj
    return pid is not None and pid != os.getpid() and bj.owner_process_alive(pid)


def close_stale(job_id: str, cutoff: float) -> bool:
    """Closes the row as interrupted only if its owner has not written or
    heartbeated since `cutoff` and is not a live process. A paused owner
    (laptop lid, debugger, a long GC) stops heartbeating but rewrites the
    row when it wakes, which would flip a closed row back to running; only
    a row with no owner_pid is judged by heartbeat age alone."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT owner_pid FROM job_records WHERE job_id = ?",
                           (job_id,)).fetchone()
    if row is None:
        return False
    pid = row[0]
    if owner_alive_elsewhere(pid):
        return False
    # The pid read above must still be the row's owner when it is closed.
    return _close_one(job_id, "COALESCE(updated_at, 0) < ? AND owner_pid IS ?", (cutoff, pid))


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
    def run(_left):
        with contextlib.closing(db.get_conn()) as conn:
            cur = conn.execute(
                "UPDATE job_records SET cancel_requested = 1, "
                "cancel_requested_at = COALESCE(cancel_requested_at, ?) "
                "WHERE job_id = ? AND status IN ('queued', 'running')", (time.time(), job_id))
            conn.commit()
            return cur.rowcount > 0
    return _retrying(run)


def row_cancel_time(job_id: str):
    """The time a cancel was requested on the row (now, for a flag set
    without one), or None when none was."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT cancel_requested, cancel_requested_at FROM job_records "
                           "WHERE job_id = ?", (job_id,)).fetchone()
    if not row or not row[0]:
        return None
    return row[1] or time.time()


def flush_at_exit() -> None:
    """Leaves no row of this server saying queued or running after it is
    gone: retries failed writes, closes this instance's still-active rows as
    interrupted and frees their GPU slots, all within EXIT_FLUSH_SECONDS.
    Runs from shutdown_service after the jobs' grace wait and, in the
    server, at exit (register_exit_flush).
    A hard kill skips it; the next start's sweep (sweep_dead_owners) closes
    those rows instead."""
    global _flushed
    import background_jobs as bj
    import job_force_stop
    _flushed = True
    deadline = time.monotonic() + EXIT_FLUSH_SECONDS
    retry_pending(deadline)
    # A daemon worker can hold the lock while the interpreter exits.
    if not _acquire(bj._lock, deadline):
        return
    try:
        active = [j for j, job in bj._jobs.items() if job.get("status") in ACTIVE]
        # ui:<id> names no instance: only a running job here holds its slot.
        gpu_holders = {f"ui:{j}" for j in active if bj._jobs[j]["status"] == "running"
                       and bj._jobs[j].get("gpu_touching")}
        gpu_holders |= {f"ui:{j}" for j in list(job_force_stop._abandoned)
                        if job_force_stop.abandoned_alive_locked(j)}
    finally:
        bj._lock.release()
    if not active and not gpu_holders:
        return
    try:
        with contextlib.closing(_connect(max(0.0, deadline - time.monotonic()))) as conn:
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


def _flush_unless_flushed() -> None:
    # shutdown_service has usually flushed already; a second pass at exit
    # would only spend EXIT_FLUSH_SECONDS redoing it.
    if not _flushed:
        flush_at_exit()


def register_exit_flush() -> None:
    """Only the server that runs jobs closes their rows at interpreter exit;
    registering at import would hand the hook to every importer (tests,
    cli.py, spawned GPU workers). Safe to call more than once."""
    global _exit_hook_registered
    if not _exit_hook_registered:
        _exit_hook_registered = True
        atexit.register(_flush_unless_flushed)
