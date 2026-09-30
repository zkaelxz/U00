"""
services/shutdown_service.py -- a clean stop for the installed app's server
(Step 80b: "Stop Baihe" and the uninstaller stop everything).

Only active when the installed launcher started the server: it passes a
one-time token in BAIHE_SHUTDOWN_TOKEN (and keeps a copy in the data
folder's launcher\\ folder for `launcher.py --stop`). Without that token the
shutdown route answers 404, so a source checkout's server (start.bat,
`python -m api`) is unchanged.

A shutdown cancels this process's running and queued jobs through the
normal cancel path (jobs_service.cancel_job), gives them a few seconds to
stop -- a process job's watcher terminates its process and a thread job's
ffmpeg is killed by run_cancellable -- then tells uvicorn to exit. Anything
still left is the launcher's job: after a grace period it force-ends the
server, and the server's Job Object (process_guard.py) takes every child
process down with it.
"""

import hmac
import os
import threading
import time

import background_jobs

TOKEN_ENV = "BAIHE_SHUTDOWN_TOKEN"
MIN_TOKEN_LENGTH = 32
JOB_GRACE_SECONDS = 8.0

_stopper = None
_lock = threading.Lock()
_started = False


def register_stopper(fn) -> None:
    """`python -m api` registers how to make the HTTP server exit."""
    global _stopper
    _stopper = fn


def enabled() -> bool:
    return len(os.environ.get(TOKEN_ENV, "")) >= MIN_TOKEN_LENGTH


def token_matches(given) -> bool:
    expected = os.environ.get(TOKEN_ENV, "")
    if len(expected) < MIN_TOKEN_LENGTH or not isinstance(given, str) or not given:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def cancel_all_jobs() -> list:
    """Asks every running/queued job in this process to stop, the same way
    the Cancel button does. Returns the ids asked."""
    from services import jobs_service
    from services.service_errors import ServiceError
    ids = background_jobs.active_job_ids()
    for job_id in ids:
        try:
            jobs_service.cancel_job(job_id)
        except ServiceError:
            # Not mirrored to job_records, or finished meanwhile: the
            # in-memory flag (and a queued job's own cancel) still apply.
            background_jobs.request_cancel(job_id)
            background_jobs.cancel_queued(job_id)
        except Exception:
            background_jobs.request_cancel(job_id)
    return ids


def wait_for_jobs(ids, timeout: float = JOB_GRACE_SECONDS, sleep=time.sleep, now=time.monotonic) -> bool:
    """True once none of `ids` is running or queued any more, False if the
    timeout passed first."""
    deadline = now() + timeout
    while True:
        remaining = [j for j in ids if (background_jobs.get_status(j) or {}).get("status")
                     in ("running", "queued")]
        if not remaining:
            return True
        if now() >= deadline:
            return False
        sleep(0.25)


def _run_shutdown(ids, timeout):
    wait_for_jobs(ids, timeout)
    if _stopper is not None:
        _stopper()


def request_shutdown(timeout: float = JOB_GRACE_SECONDS) -> dict:
    """Cancels every job now, then (in the background, so the caller gets
    its answer first) waits up to `timeout` seconds for them and stops the
    server. A second request while one is under way changes nothing."""
    global _started
    with _lock:
        already = _started
        _started = True
    if already:
        return {"status": "stopping", "cancelled_jobs": 0}
    ids = cancel_all_jobs()
    threading.Thread(target=_run_shutdown, args=(ids, timeout), daemon=True,
                     name="api-shutdown").start()
    return {"status": "stopping", "cancelled_jobs": len(ids)}
