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

import contextlib
import threading
import time
import traceback

_jobs = {}
# RLock, not Lock: _promote_next_queued_gpu_job() is called from inside a
# just-finished job's own runner thread, and needs to re-take the lock it
# might already be inside of via a nested call path -- a plain Lock would
# deadlock the thread against itself.
_lock = threading.RLock()

# Step 5c: a soft, global "one GPU job at a time" guard, prompted by a
# shared review flagging that nothing today stops two independent
# GPU-touching jobs (e.g. a transcription on one drama and diarization on
# another, started from two different tabs/sessions) from running
# concurrently and competing for VRAM. This is NOT a model manager (R3 in
# the roadmap) -- it doesn't track what's loaded, it just refuses to let a
# second GPU-touching job *start* while one is already running, queuing it
# instead. Defaults on (matches the 8-12GB consumer-GPU assumption this
# project is built around); a Settings toggle can turn it off for anyone
# on higher-VRAM hardware.
_gpu_limit_enabled = True
_gpu_queue = []  # [{"job_id", "target", "args", "kwargs", "description"}, ...], FIFO


def set_gpu_limit_enabled(enabled: bool):
    global _gpu_limit_enabled
    with _lock:
        _gpu_limit_enabled = bool(enabled)


def gpu_limit_enabled() -> bool:
    with _lock:
        return _gpu_limit_enabled


def _other_gpu_job_running_locked(exclude_job_id):
    """Caller must already hold _lock. The id of some other running,
    GPU-touching job, or None if the GPU is free."""
    for jid, job in _jobs.items():
        if jid != exclude_job_id and job.get("gpu_touching") and job["status"] == "running":
            return jid
    return None


def gpu_busy_description():
    """The description of whichever GPU-touching job is currently running,
    for a "Waiting -- GPU busy with <this>" message. None if the GPU is free."""
    with _lock:
        jid = _other_gpu_job_running_locked(None)
        if jid is None:
            return None
        return _jobs[jid].get("description") or jid


def _spawn(job_id, target, args, kwargs):
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
        finally:
            _promote_next_queued_gpu_job()

    threading.Thread(target=runner, daemon=True, name=f"job:{job_id}").start()


def _promote_next_queued_gpu_job():
    """Called whenever a GPU-touching job/slot finishes -- starts the next
    queued GPU-touching job, if the GPU is actually free and anything is
    still waiting. Skips (and drops) queue entries that were cleared out
    from under the queue in the meantime."""
    while True:
        with _lock:
            if not _gpu_queue or _other_gpu_job_running_locked(None):
                return
            entry = _gpu_queue.pop(0)
            job_id = entry["job_id"]
            if job_id not in _jobs or _jobs[job_id]["status"] != "queued":
                continue
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["message"] = "Starting..."
            _jobs[job_id]["started_at"] = time.time()
            target, args, kwargs = entry["target"], entry["args"], entry["kwargs"]
            break
    _spawn(job_id, target, args, kwargs)


def start_job(job_id: str, target, *args, gpu_touching: bool = False,
              description: str = None, **kwargs) -> bool:
    """
    Starts target(*args, **kwargs) in a background thread under job_id.
    Returns False without starting anything if a job with that ID is
    already running or queued -- so a second click on "Translate" doesn't
    launch a duplicate.

    gpu_touching: True for anything that loads a local model onto the GPU
    (transcription, diarization, OCR, TTS/dub, local-model translation).
    When the "limit to one GPU job at a time" setting is on and another
    gpu_touching job is already running (any job_id, any drama), this one
    is queued instead of started -- see _promote_next_queued_gpu_job().
    description is a short human label for the "GPU busy with <this>"
    message; defaults to job_id if not given.
    """
    with _lock:
        existing = _jobs.get(job_id)
        if existing and existing["status"] in ("running", "queued"):
            return False
        if gpu_touching and _gpu_limit_enabled and _other_gpu_job_running_locked(job_id):
            _jobs[job_id] = {
                "status": "queued", "progress": 0.0,
                "message": "Waiting -- GPU busy with " + (gpu_busy_description() or "another job"),
                "error": None, "started_at": time.time(), "finished_at": None,
                "cancel_requested": False, "result": None,
                "gpu_touching": True, "description": description,
            }
            _gpu_queue.append({"job_id": job_id, "target": target, "args": args,
                                "kwargs": kwargs, "description": description})
            return True
        _jobs[job_id] = {
            "status": "running", "progress": 0.0, "message": "Starting...",
            "error": None, "started_at": time.time(), "finished_at": None,
            "cancel_requested": False, "result": None,
            "gpu_touching": gpu_touching, "description": description,
        }

    _spawn(job_id, target, args, kwargs)
    return True


@contextlib.contextmanager
def gpu_slot(description: str, poll_interval: float = 0.5):
    """For GPU-touching work that runs synchronously in the calling thread
    (diarization, dub/narration generation) rather than as its own
    start_job() background job -- diarization and dub both block the
    Streamlit script directly today rather than running as tracked jobs,
    so they need their own way to participate in the same "one GPU job at
    a time" accounting start_job()'s gpu_touching jobs use, rather than
    being invisible to it. Unlike start_job(), there's no later rerun to
    hand a "queued" job off to here -- the calling script IS what's
    waiting -- so this blocks (polling every poll_interval seconds)
    until a slot is free instead of queuing.
    """
    slot_id = f"_gpu_slot_{threading.get_ident()}_{id(object())}"
    while True:
        with _lock:
            if not _gpu_limit_enabled or not _other_gpu_job_running_locked(slot_id):
                _jobs[slot_id] = {
                    "status": "running", "progress": 0.0, "message": description,
                    "error": None, "started_at": time.time(), "finished_at": None,
                    "cancel_requested": False, "result": None,
                    "gpu_touching": True, "description": description,
                }
                break
        time.sleep(poll_interval)
    try:
        yield
    finally:
        with _lock:
            _jobs.pop(slot_id, None)
        _promote_next_queued_gpu_job()


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
    means progress updates go nowhere until it finishes on its own. Also
    drops it from the GPU queue if it was still queued, so a cleared job
    can't be promoted and started later out of nowhere."""
    with _lock:
        _jobs.pop(job_id, None)
        _gpu_queue[:] = [e for e in _gpu_queue if e["job_id"] != job_id]


def clear_all_jobs():
    """Wipes every job record, running or not. Used by a full library
    reset -- a job still referencing a now-deleted drama has nothing
    useful left to report."""
    with _lock:
        _jobs.clear()
        _gpu_queue.clear()


def list_running_jobs():
    with _lock:
        return {jid: dict(j) for jid, j in _jobs.items() if j["status"] == "running"}
