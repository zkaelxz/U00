"""
services/shutdown_service.py -- the API server's clean stop ("Stop Baihe",
closing the server's window, the uninstaller, Ctrl+C).

A clean stop, in order: refuse new job starts on every listener in the
process (background_jobs.refuse_new_jobs), stop starting new work (the
chapter-check scheduler, the API's own GPU-queue/backup poller registered by
`python -m api`, and any new Playwright browser), cancel this process's
running and queued jobs through the normal cancel path
(jobs_service.cancel_job), give them a few seconds to stop -- a process
job's watcher terminates its process, a thread job's ffmpeg is killed by
run_cancellable, and a Playwright sign-in window closes itself on its own
thread -- then stop the browser-extension endpoint (page_server). Whatever
is still running after that ends with the server process: on Windows every
child process is in the server's kill-on-close Job Object
(process_guard.py), so it goes too.

Who runs it:
- POST /api/system/shutdown (request_shutdown): the installed launcher's
  "Stop" and the uninstaller. Only active when the launcher started the
  server with a one-time token in BAIHE_SHUTDOWN_TOKEN (it keeps a copy in
  the data folder's launcher\\ folder for `launcher.py --stop`); without it
  the route answers 404. The launcher force-ends the server after its
  grace period.
- The console-close handler (process_guard.install_console_close_handler)
  and `python -m api` after uvicorn returns (Ctrl+C) call clean_shutdown.
"""

import hmac
import os
import threading
import time

import background_jobs

TOKEN_ENV = "BAIHE_SHUTDOWN_TOKEN"
MIN_TOKEN_LENGTH = 32
# The route's wait for jobs; with uvicorn's 3 s graceful stop this stays
# under the launcher's STOP_GRACE_SECONDS (10 s), after which it force-ends.
JOB_GRACE_SECONDS = 6.0
# A closed console window: Windows ends the process about 5 s after the
# event, so the jobs get less.
CONSOLE_GRACE_SECONDS = 3.5

_stopper = None
_background_stopper = None
_lock = threading.Lock()
_started = False
_began = False
_token = ""


def take_token_from_environment() -> None:
    """`python -m api` calls this once at startup: keeps the token in this
    module and removes it from the environment, so the processes the
    server starts (yt-dlp, Chromium, pip builds...) never inherit it."""
    global _token
    _token = os.environ.pop(TOKEN_ENV, "") or _token


def register_stopper(fn) -> None:
    """`python -m api` registers how to make the HTTP server exit."""
    global _stopper
    _stopper = fn


def register_background_stopper(fn) -> None:
    """`python -m api` registers how to stop the API's own periodic work
    (api/background.py's GPU-queue and backup poller)."""
    global _background_stopper
    _background_stopper = fn


def _expected() -> str:
    return _token or os.environ.get(TOKEN_ENV, "")


def enabled() -> bool:
    return len(_expected()) >= MIN_TOKEN_LENGTH


def token_matches(given) -> bool:
    expected = _expected()
    if len(expected) < MIN_TOKEN_LENGTH or not isinstance(given, str) or not given:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def cancel_all_jobs() -> list:
    """Asks every running/queued job in this process to stop, the same way
    the Cancel button does. Returns the ids asked."""
    from services import jobs_service
    from services.service_errors import ServiceError
    ids = background_jobs.active_job_ids()
    # Queued jobs come off the queue first, so finishing the running one
    # can't start a queued one mid-shutdown. A no-op for running jobs.
    for job_id in ids:
        background_jobs.cancel_queued(job_id)
    for job_id in ids:
        try:
            jobs_service.cancel_job(job_id)
        except ServiceError:
            # Not mirrored to job_records, or finished meanwhile: the
            # in-memory flag (and a queued job's own cancel) still apply.
            background_jobs.request_cancel(job_id)
        except Exception:
            background_jobs.request_cancel(job_id)
    return ids


def wait_for_jobs(ids, timeout: float = JOB_GRACE_SECONDS, sleep=time.sleep, now=time.monotonic) -> bool:
    """True once none of `ids` is running or queued any more, False if the
    timeout passed first. Then also waits, within what is left of the
    timeout, for those jobs' threads: a job's status turns final before its
    thread is done, and a process job's watcher still releases the GPU lock
    and runs its finish hook (removing the run's temp folder) after."""
    deadline = now() + timeout
    while True:
        remaining = [j for j in ids if (background_jobs.get_status(j) or {}).get("status")
                     in ("running", "queued")]
        if not remaining:
            background_jobs.wait_for_job_threads(max(0.0, deadline - now()), job_ids=set(ids))
            return True
        if now() >= deadline:
            return False
        sleep(0.25)


def _quietly(fn) -> None:
    try:
        fn()
    except Exception:
        pass


def stop_new_work() -> None:
    """No new scheduled check, poller tick or browser from here on."""
    from sources import chapter_check
    import page_fetch
    _quietly(chapter_check.stop_scheduler)
    if _background_stopper is not None:
        _quietly(_background_stopper)
    # Also tells an open sign-in window to close itself.
    _quietly(page_fetch.request_shutdown)


def stop_services() -> None:
    """After the jobs: the browser-extension endpoint."""
    import page_server
    _quietly(page_server.stop_server)


def _begin():
    """Stops new work and cancels every job, once per process. Returns the
    ids cancelled, or None if a stop had already begun."""
    global _began
    with _lock:
        if _began:
            return None
        _began = True
    # Before the cancel's snapshot of active jobs: a job start on either
    # listener (admin or household) during the grace wait is refused, not
    # started and then killed mid-write when the process ends.
    background_jobs.refuse_new_jobs()
    stop_new_work()
    return cancel_all_jobs()


def clean_shutdown(timeout: float = JOB_GRACE_SECONDS) -> None:
    """The whole clean stop (see the module docstring), within about
    `timeout` seconds, without stopping the HTTP server itself. Safe to call
    more than once and from any thread: only the stop that began first
    waits for the jobs (so `python -m api` doesn't wait again after the
    route's stop already did)."""
    ids = _begin()
    if ids is not None:
        wait_for_jobs(ids, timeout)
    stop_services()


def _run_shutdown(ids, timeout):
    wait_for_jobs(ids, timeout)
    stop_services()
    if _stopper is not None:
        _stopper()


def request_shutdown(timeout: float = JOB_GRACE_SECONDS) -> dict:
    """Stops new work and cancels every job now, then (in the background,
    so the caller gets its answer first) waits up to `timeout` seconds for
    them, stops the services and makes the server exit. A second request
    while one is under way changes nothing."""
    global _started
    with _lock:
        already = _started
        _started = True
    if already:
        return {"status": "stopping", "cancelled_jobs": 0}
    ids = _begin() or []
    threading.Thread(target=_run_shutdown, args=(ids, timeout), daemon=True,
                     name="api-shutdown").start()
    return {"status": "stopping", "cancelled_jobs": len(ids)}
