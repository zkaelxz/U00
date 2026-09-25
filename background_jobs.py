"""
background_jobs.py -- runs long operations in a real background thread
so they survive Streamlit's script lifecycle.

The problem this solves: Streamlit reruns its whole script on every
interaction, and by default CANCELS whatever script run is currently in
flight when a new one starts. A translation job that runs as a blocking
loop directly inside the button-click handler dies the moment the person
clicks anything else -- including something in another tab, since every
tab's content renders in the same script execution regardless of which
one is visually active (Streamlit tabs are a client-side CSS toggle, not
a separate script per tab).

The fix: run the job in an actual `threading.Thread`, with its progress
kept in a plain module-level dict. Module state survives reruns because
imported modules are only loaded once per process and cached in
sys.modules -- so a thread started during one script run keeps running,
and its state dict is still there to read on the next one, no matter
what the person clicked in between.

Two hard rules for anything run this way:
  - Never touch st.* from inside the thread. Streamlit's session state
    and widgets are not thread-safe to write from a background thread.
    Progress goes into this module's dict instead; the main script
    reads it and renders normally.
  - Do the actual work through functions that already only touch plain
    Python objects and the database (e.g. translate_lines_with_engine),
    not anything that assumes it's running inside a Streamlit script.

Single-process, in-memory only -- fine for a local personal app with one
user. Would need a real job queue (Celery, RQ) for anything multi-user
or multi-process.
"""

import threading
import time
import traceback

_jobs = {}
_lock = threading.Lock()


def start_job(job_id: str, target, *args, **kwargs) -> bool:
    """
    Starts target(*args, **kwargs) in a background thread under job_id.
    Returns False without starting anything if a job with that ID is
    already running -- so a second click on "Translate" doesn't launch
    a duplicate.
    """
    with _lock:
        existing = _jobs.get(job_id)
        if existing and existing["status"] == "running":
            return False
        _jobs[job_id] = {
            "status": "running", "progress": 0.0, "message": "Starting...",
            "error": None, "started_at": time.time(), "finished_at": None,
            "cancel_requested": False, "result": None,
        }

    def runner():
        import applog
        from translate_engines import redact_secrets
        logger = applog.get_logger()
        logger.info(f"job {job_id} started")
        try:
            target(*args, **kwargs)
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id]["status"] = "done"
                    _jobs[job_id]["progress"] = 1.0
                    _jobs[job_id]["finished_at"] = time.time()
            logger.info(f"job {job_id} finished")
        except Exception as exc:
            error_msg = redact_secrets(f"{type(exc).__name__}: {exc}")
            tb = redact_secrets(traceback.format_exc())
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id]["status"] = "error"
                    _jobs[job_id]["error"] = error_msg
                    _jobs[job_id]["traceback"] = tb
                    _jobs[job_id]["finished_at"] = time.time()
            logger.error(f"job {job_id} failed: {error_msg}\n{tb}")

    threading.Thread(target=runner, daemon=True, name=f"job:{job_id}").start()
    return True


def update_progress(job_id: str, frac: float, message: str = ""):
    """Called FROM inside the background thread to report progress.
    Silently does nothing if the job was cleared (e.g. by a reset) out
    from under it, rather than raising into a background thread."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["progress"] = frac
            if message:
                _jobs[job_id]["message"] = message


def set_result(job_id: str, result):
    """Stores an arbitrary result payload on a job (e.g. the list of
    per-batch errors from a translation run), for the caller to read
    once via get_status(job_id)["result"] after the job finishes."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["result"] = result


def get_status(job_id: str):
    """Returns a snapshot dict, or None if no such job has ever run."""
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def is_running(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job["status"] == "running")


# Jobs that write to a drama's existing lines (job ids "translate_<id>",
# "flag_<id>", "fixflag_<id>"). Since Step 2 each writes only its own
# fields by permanent line id, so they can run alongside each other and
# the user's own edits. Replacing ALL of a drama's lines (a new
# transcription) is the one thing that makes their work pointless.
LINE_WRITING_JOB_PREFIXES = ("translate_", "flag_", "fixflag_")


def cancel_line_jobs(drama_id):
    """Asks every running line-writing job for this drama to stop -- for
    when its lines are about to be replaced wholesale."""
    for prefix in LINE_WRITING_JOB_PREFIXES:
        if is_running(f"{prefix}{drama_id}"):
            request_cancel(f"{prefix}{drama_id}")


def request_cancel(job_id: str):
    """Sets a cooperative cancellation flag. The job itself has to check
    is_cancel_requested() between units of work -- this can't forcibly
    kill a thread, only ask it to stop at the next safe point."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["cancel_requested"] = True


def is_cancel_requested(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job.get("cancel_requested"))


def clear_job(job_id: str):
    """Removes a finished job's record so the UI stops showing it. Only
    safe to call once the job isn't running -- clearing a live job just
    means progress updates go nowhere until it finishes on its own."""
    with _lock:
        _jobs.pop(job_id, None)


def clear_all_jobs():
    """Wipes every job record, running or not. Used by a full library
    reset -- a job still referencing a now-deleted drama has nothing
    useful left to report."""
    with _lock:
        _jobs.clear()


def list_running_jobs():
    with _lock:
        return {jid: dict(j) for jid, j in _jobs.items() if j["status"] == "running"}
