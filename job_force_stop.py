"""job_force_stop.py -- the escape hatch for a thread job that ignores Cancel.

A Python thread can't be killed, so "force stop" ends the job's *record*, not
its thread: the record is closed with a fixed message and the thread is left
to finish on its own (abandoned). Two rules keep that safe:

  - One abandoned thread per job id. A new run of the same id is refused
    while it lives, so the old thread can never write into the new run's
    record (job ids repeat per drama).
  - The GPU claim stays with the abandoned thread. It may still be using the
    card, so the slot is released only when no worker is alive; otherwise the
    runner's own `finally` releases it when the thread finally ends. Until
    then the live worker still counts against gpu_max_parallel and its
    cross-process `ui:<id>` row is kept fresh by the heartbeat.
  - Exclusive admin holds and whole-drama checks treat a live abandoned
    worker as a running job: it may still be writing models or drama files.

Exclusive holds (background_jobs.acquire_exclusive) belong to the service that
took them, not to a job, so there is nothing per-job to free here; a restore
still joins the abandoned thread through wait_for_job_threads.
"""

import threading
import time

# Cancelling for this long without the worker stopping is what "hung" means.
FORCE_STOP_AFTER_CANCEL_SECONDS = 60.0

FORCE_STOPPED_MESSAGE = ("Force stopped: the job did not react to Cancel. Its worker may still "
                         "be finishing in the background.")
ABANDONED_REFUSAL = ("The earlier run of this job was force stopped and its worker is still "
                     "finishing. Wait until it ends, then start the job again.")

_abandoned = {}   # job id -> the worker thread the job stopped waiting for


def _live_abandoned(job_id):
    """Caller holds background_jobs._lock."""
    thread = _abandoned.get(job_id)
    if thread is not None and not thread.is_alive():
        del _abandoned[job_id]
        return None
    return thread


def abandoned_alive_locked(job_id) -> bool:
    """Caller holds background_jobs._lock."""
    return _live_abandoned(job_id) is not None


def any_abandoned_alive_locked() -> bool:
    """Caller holds background_jobs._lock."""
    return any(_live_abandoned(jid) is not None for jid in list(_abandoned))


def abandoned_gpu_count_locked(exclude_job_id=None) -> int:
    """Caller holds background_jobs._lock. Live abandoned workers that still
    hold the GPU, which the record-based running count no longer sees. The
    calling thread is skipped: an abandoned worker is still alive while its
    own `finally` releases the slot and promotes the queue, and counting
    itself there would leave the next GPU job to the 20 s poller."""
    me = threading.current_thread()
    return sum(1 for jid in list(_abandoned)
               if jid != exclude_job_id and _live_abandoned(jid) is not None
               and _abandoned[jid] is not me
               and getattr(_abandoned[jid], "baihe_abandoned_gpu", False))


def refresh_abandoned_gpu_rows() -> None:
    """The abandoned worker no longer reports progress, so without this its
    `ui:<id>` row would age past db.GPU_LOCK_STALE_SECONDS and another
    process could take the card beside it. Best-effort."""
    import background_jobs as bj
    with bj._lock:
        ids = [jid for jid in list(_abandoned)
               if _live_abandoned(jid) is not None
               and getattr(_abandoned[jid], "baihe_abandoned_gpu", False)]
    if not ids:
        return
    try:
        import db
        for jid in ids:
            db.heartbeat_gpu_lock(f"ui:{jid}")
    except Exception:
        pass


def refuse_if_abandoned_locked(job_id) -> None:
    """Caller holds background_jobs._lock (start_job's own check)."""
    if _live_abandoned(job_id) is not None:
        from services.service_errors import ConflictError
        raise ConflictError(ABANDONED_REFUSAL)


def current_thread_abandoned() -> bool:
    """True inside a worker whose job was force stopped. Its progress and
    result writes must go nowhere: the record is already closed."""
    return getattr(threading.current_thread(), "baihe_abandoned", False)


def cancelling_too_long(job: dict, now: float = None) -> bool:
    at = job.get("cancel_requested_at")
    return bool(at) and (time.time() if now is None else now) - at > FORCE_STOP_AFTER_CANCEL_SECONDS


def can_force_stop(job: dict, now: float = None) -> bool:
    """A running thread job whose Cancel was heard over a minute ago."""
    return bool(job) and job.get("status") == "running" and job.get("kind") == "thread" \
        and cancelling_too_long(job, now)


def force_stop(job_id: str) -> dict:
    """Closes the record of a thread job that is Cancelling too long. Raises
    ConflictError when the job is not eligible, which also makes a second
    call a no-op instead of a second release. Returns
    {"status", "worker_still_running"}."""
    import background_jobs as bj
    from services.service_errors import ConflictError
    with bj._lock:
        job = bj._jobs.get(job_id)
        if job is None or job.get("status") != "running":
            raise ConflictError("This job is not running, so there is nothing to force stop.")
        if job.get("kind") != "thread":
            raise ConflictError("Only a job running in a thread needs a force stop; Cancel ends the others.")
        if not cancelling_too_long(job):
            raise ConflictError("Cancel the job first and give it a minute to stop on its own.")
        _, worker = bj._workers.get(job_id, (None, None))
        alive = worker is not None and worker.is_alive()
        if alive:
            worker.baihe_abandoned = True
            worker.baihe_abandoned_gpu = bool(job.get("gpu_touching"))
            _abandoned[job_id] = worker
        job["status"] = "cancelled"
        job["message"] = FORCE_STOPPED_MESSAGE
        job["finished_at"] = time.time()
        bj._mirror_locked(job_id)
    if not alive:
        bj._release_gpu_slot(job_id, job.get("gpu_touching"), job)
        bj._promote_next_queued_gpu_job()
    return {"status": job["status"], "worker_still_running": alive}
