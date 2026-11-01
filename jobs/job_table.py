"""jobs/job_table.py -- the in-memory job table and its best-effort mirror.

`_jobs` (job id -> status dict) is the authority for every job this process
owns, guarded by `_lock`; `_workers` and `_job_threads` track the threads
that report each run's outcome. These objects live here once: background_jobs
and the other jobs modules import them, never copy them. Every status change
is mirrored to job_records (jobs/job_store.py), kept fresh by a heartbeat
thread while this process has live jobs, and pushed to change listeners.
"""

import threading
import time


_jobs = {}


# Push hook (SSE, services/event_stream_service.py): listeners are told a
# job id changed (None: every job, e.g. clear_all_jobs) and fetch what they
# need themselves. A listener must not block; one that raises is ignored,
# so a listener can never break the job it is told about.
_change_listeners = []


def add_change_listener(fn) -> None:
    if fn not in _change_listeners:
        _change_listeners.append(fn)


def remove_change_listener(fn) -> None:
    try:
        _change_listeners.remove(fn)
    except ValueError:
        pass


def _emit_change(job_id) -> None:
    for fn in list(_change_listeners):
        try:
            fn(job_id)
        except Exception:
            pass


def _mirror_locked(job_id):
    """Caller holds _lock. Writes this job's row (jobs/job_store.py)."""
    from jobs import job_store
    job_store.write_transition(job_id, _jobs.get(job_id))
    _ensure_heartbeat()


# While this process owns queued/running jobs, a daemon thread bumps
# their job_records.updated_at every HEARTBEAT_INTERVAL seconds, so another
# process's stale-record sweep (jobs_service.cancel_job) can tell a live job
# that is simply not changing status from one whose owner process died.
HEARTBEAT_INTERVAL = 60.0
# A queued/running job_records row not heartbeated for this long belongs to
# a dead owner process (records have no resume). The one staleness cutoff:
# the jobs list/startup sweep, cancel, and every "is a job running" check
# (drama delete, novel files, library admin, voice clone) use it.
STALE_JOB_SECONDS = 15 * 60
_heartbeat_thread = None


def _heartbeat_once():
    from jobs import job_store
    job_store.heartbeat_tick()


def _heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        _heartbeat_once()


def _ensure_heartbeat():
    global _heartbeat_thread
    if _heartbeat_thread is None or not _heartbeat_thread.is_alive():
        _heartbeat_thread = threading.Thread(target=_heartbeat_loop, name="job-heartbeat",
                                             daemon=True)
        _heartbeat_thread.start()


# RLock, not Lock: _promote_next_queued_gpu_job() is called from inside a
# just-finished job's own runner thread, and needs to re-take the lock it
# might already be inside of via a nested call path -- a plain Lock would
# deadlock the thread against itself.
_lock = threading.RLock()


# per-process on purpose: each process only ever manages the GPU jobs it
# itself started, so there's nothing cross-process to reconcile here.


def _warn(what, exc):
    """Logs a swallowed failure at warning, secret-redacted. Never raises."""
    try:
        import applog
        from engine_backends.shared import redact_secrets
        applog.get_logger().warning(
            f"{what}: " + redact_secrets(f"{type(exc).__name__}: {exc}")[:300])
    except Exception:
        pass


# Job runner and watcher threads that have not exited yet. A job's status
# turns final before its thread is done: the thread still writes the
# notification, the timing row and the GPU-lock release to the database
# afterwards. So "no job running" does not mean "no job thread using the
# database", and a library reset that deletes the database file then raced
# that tail (sqlite3 "database is locked" converting the new file to WAL).
_job_threads = set()

# job id -> (that run's _jobs dict, the thread that reports its outcome: the
# runner for a thread job, the watcher for a process job). Keyed to the
# run's own dict so a restarted job id is never judged by an old thread.
_workers = {}
