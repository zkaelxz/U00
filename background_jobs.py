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
import multiprocessing
import queue
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
    from under the queue in the meantime. Dispatches to the thread-based
    or process-based starter depending on how that entry was queued."""
    while True:
        with _lock:
            if not _gpu_queue or _other_gpu_job_running_locked(None):
                return
            entry = _gpu_queue.pop(0)
            job_id = entry["job_id"]
            if job_id not in _jobs or _jobs[job_id]["status"] != "queued":
                continue
            if entry.get("kind") == "process":
                proc, result_queue = _register_process_job(
                    job_id, entry["target"], entry["args"],
                    _jobs[job_id]["gpu_touching"], entry["description"])
                break
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["message"] = "Starting..."
            _jobs[job_id]["started_at"] = time.time()
            target, args, kwargs = entry["target"], entry["args"], entry["kwargs"]
            break
    if entry.get("kind") == "process":
        proc.start()
        threading.Thread(target=_process_watcher, args=(job_id, proc, result_queue),
                         daemon=True, name=f"job-watcher:{job_id}").start()
    else:
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
                "gpu_touching": True, "description": description, "kind": "thread",
            }
            _gpu_queue.append({"job_id": job_id, "target": target, "args": args,
                                "kwargs": kwargs, "description": description, "kind": "thread"})
            return True
        _jobs[job_id] = {
            "status": "running", "progress": 0.0, "message": "Starting...",
            "error": None, "started_at": time.time(), "finished_at": None,
            "cancel_requested": False, "result": None,
            "gpu_touching": gpu_touching, "description": description, "kind": "thread",
        }

    _spawn(job_id, target, args, kwargs)
    return True


def start_process_job(job_id: str, target, args: tuple = (), gpu_touching: bool = False,
                      description: str = None) -> bool:
    """
    Like start_job(), but runs target in a real OS subprocess
    (multiprocessing.Process) instead of a thread -- the first
    process-based job in this codebase, a genuinely new pattern rather
    than an extension of start_job()'s thread model. For work with no
    cooperative-cancellation checkpoint of its own (pyannote's
    diarization pipeline is one opaque call), so request_cancel() can
    actually terminate the underlying OS process instead of just asking
    it to stop at some future safe point.

    target must be a plain, top-level, picklable function (a closure or
    bound method can't cross the process boundary) whose LAST parameter
    accepts a multiprocessing.Queue -- this function appends that queue
    to `args` itself when starting the process. target should put
    exactly one plain-Python-only (no torch/pyannote objects) result
    tuple onto that queue before returning: ("ok", <result...>) or
    ("error", <exception type name>, <message>).

    Same job_id/queued/gpu_touching semantics as start_job(); returns
    False if job_id is already running or queued.
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
                "gpu_touching": True, "description": description, "kind": "process",
                "process": None,
            }
            _gpu_queue.append({"job_id": job_id, "target": target, "args": args,
                                "kwargs": {}, "description": description, "kind": "process"})
            return True
        proc, result_queue = _register_process_job(job_id, target, args, gpu_touching, description)
    proc.start()
    threading.Thread(target=_process_watcher, args=(job_id, proc, result_queue),
                     daemon=True, name=f"job-watcher:{job_id}").start()
    return True


def _register_process_job(job_id, target, args, gpu_touching, description):
    """Caller must already hold _lock. Builds the Process and its result
    queue and records the job dict entry, but doesn't call proc.start()
    itself -- constructing a Process is cheap, but actually starting one
    (forking/spawning a real OS process) shouldn't happen while holding
    _lock, so callers start it themselves right after releasing the
    lock. Returns (proc, result_queue) for that."""
    result_queue = multiprocessing.Queue()
    proc = multiprocessing.Process(target=target, args=(*args, result_queue), daemon=True)
    _jobs[job_id] = {
        "status": "running", "progress": 0.0, "message": "Starting...",
        "error": None, "started_at": time.time(), "finished_at": None,
        "cancel_requested": False, "result": None,
        "gpu_touching": gpu_touching, "description": description, "kind": "process",
        "process": proc,
    }
    return proc, result_queue


def _process_watcher(job_id, proc, result_queue, poll_interval=0.3):
    """Runs in this (the main) process, not the child -- a
    multiprocessing.Process can't write back into this process's _jobs
    dict itself (separate memory space), so this polls proc.is_alive()
    and this job's cancel_requested flag, and is the one place that
    actually calls proc.terminate() for a real, non-cooperative stop.
    Also terminates the process if its job record is cleared out from
    under it while still running (clear_job() on a thread-based job can
    only leave it running invisibly; here, holding the real Process
    object, cleanup can be immediate and complete instead)."""
    import applog
    logger = applog.get_logger()
    try:
        outcome = None
        while True:
            with _lock:
                job = _jobs.get(job_id)
                should_stop = job is None or job.get("cancel_requested")
            if should_stop:
                # terminate()/join() happen OUTSIDE the lock -- join can block
                # for real seconds, and nothing else here should have to wait
                # on that (another job's update_progress, a UI's get_status).
                was_cleared = job is None
                proc.terminate()
                proc.join(timeout=5)
                with _lock:
                    if job_id in _jobs:
                        _jobs[job_id]["status"] = "cancelled"
                        _jobs[job_id]["finished_at"] = time.time()
                logger.info(f"job {job_id} {'cleared' if was_cleared else 'cancelled'} "
                           f"(subprocess terminated)")
                return
            # Step 4i: drain the queue continuously while the process is
            # still alive, not only after it exits. A child that has put()
            # more onto the queue than fits in one OS pipe buffer (~64KB on
            # Linux) cannot exit until this side actually reads from it --
            # waiting on proc.is_alive() first deadlocks both sides forever
            # on any result past that size (real at this app's own scale:
            # any drama with roughly 250+ lines, in dub/re-segment/diarize/
            # auto-tune's subprocess workers, all of which return one item
            # per line/turn/segment). Reusing poll_interval as the get()
            # timeout keeps the same cancel-latency and CPU-use profile the
            # previous plain time.sleep(poll_interval) had.
            try:
                outcome = result_queue.get(timeout=poll_interval)
                break
            except queue.Empty:
                pass
            if not proc.is_alive():
                break

        if outcome is None:
            try:
                # Not get_nowait(): multiprocessing.Queue.put() hands the
                # pickled item to an internal feeder thread rather than
                # writing it synchronously, so a process that exits right
                # after put()ing its result can have already exited
                # (proc.is_alive() already False, as checked above) before
                # that item is actually readable from this end -- a real,
                # documented race, not just a test timing quirk. A short
                # blocking get gives it time to land.
                outcome = result_queue.get(timeout=1)
            except queue.Empty:
                outcome = None
        with _lock:
            if job_id not in _jobs:
                return
            if outcome and outcome[0] == "ok":
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["progress"] = 1.0
                _jobs[job_id]["result"] = outcome[1]
                _jobs[job_id]["finished_at"] = time.time()
                logger.info(f"job {job_id} finished")
            elif outcome and outcome[0] == "error":
                _, exc_type, msg = outcome
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = f"{exc_type}: {msg}"
                _jobs[job_id]["finished_at"] = time.time()
                logger.error(f"job {job_id} failed: {exc_type}: {msg}")
            else:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = (
                    f"Subprocess exited unexpectedly (exit code {proc.exitcode}) without "
                    "reporting a result.")
                _jobs[job_id]["finished_at"] = time.time()
                logger.error(f"job {job_id} subprocess died with no result "
                            f"(exit code {proc.exitcode})")
    finally:
        _promote_next_queued_gpu_job()


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


def eta_seconds(started_at: float, frac: float, now: float = None) -> float:
    """Estimated remaining seconds, extrapolated linearly from elapsed
    time and progress so far -- or None when there isn't enough signal
    to extrapolate from yet (right at the start, before the job's even
    reported any progress, or once it's already done): a small `frac`
    makes `(1 - frac) / frac` blow up into a wildly noisy estimate that's
    worse than showing nothing."""
    if not started_at or frac is None or frac <= 0.02 or frac >= 1.0:
        return None
    elapsed = (now if now is not None else time.time()) - started_at
    if elapsed <= 0:
        return None
    return elapsed * (1 - frac) / frac


def format_eta(seconds) -> str:
    """' (~N min remaining)' / ' (~N sec remaining)', or '' for None --
    already includes the leading space and parens so a caller can just
    concatenate it onto an existing progress message."""
    if seconds is None:
        return ""
    seconds = max(0, seconds)
    if seconds < 60:
        return f" (~{max(1, int(seconds))} sec remaining)"
    return f" (~{max(1, round(seconds / 60))} min remaining)"


def eta_text(job: dict, now: float = None) -> str:
    """format_eta(eta_seconds(...)) straight from a get_status() dict --
    the form every progress-bar call site actually has on hand."""
    if not job:
        return ""
    return format_eta(eta_seconds(job.get("started_at"), job.get("progress"), now))


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
    """Sets the cancellation flag. For a thread-based job (start_job()),
    this is purely cooperative -- the job itself has to check
    is_cancel_requested() between units of work, since a thread can't be
    forcibly killed. For a process-based job (start_process_job()), Step
    4d's own _process_watcher notices this flag and actually calls
    proc.terminate() -- a real, non-cooperative stop, since that's the
    whole reason those jobs run in their own OS process instead of a
    thread in the first place (no cooperative checkpoint to hook into)."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["cancel_requested"] = True


def is_cancel_requested(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job.get("cancel_requested"))


def cancel_queued(job_id: str) -> bool:
    """Cancels a job that's still queued (waiting for a GPU slot) -- for a
    queued job's own Cancel button. Re-checks the job's actual current
    status under the lock rather than trusting the caller's stale render:
    _promote_next_queued_gpu_job() (called whenever another GPU-touching
    job finishes) can promote this job to "running" and spawn its
    background thread in the narrow window between the render that showed
    Cancel and the click being processed. Clearing the record outright in
    that case would leave the now-genuinely-running job with no `_jobs`
    entry left for Stop/request_cancel to reach -- it would keep running
    for real, invisibly and uncancellably.

    Returns True if the job was still queued and its record was cleared.
    Returns False if it had already been promoted to running -- the
    caller should fall back to whatever it does for a live "Stop" (e.g.
    bumping a generation counter) before/alongside calling
    request_cancel(), since the job is now genuinely running."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job["status"] == "queued":
            clear_job(job_id)
            return True
        return False


def clear_job(job_id: str):
    """Removes a finished job's record so the UI stops showing it. Only
    safe to call once the job isn't running -- clearing a live THREAD-
    based job just means progress updates go nowhere until it finishes
    on its own (nothing here can forcibly stop a thread). A live
    PROCESS-based job (start_process_job()) is the one exception:
    _process_watcher holds the real Process object directly and notices
    its record disappearing, so clearing it actually terminates the
    subprocess rather than leaving it running invisibly. Also drops it
    from the GPU queue if it was still queued, so a cleared job can't be
    promoted and started later out of nowhere."""
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
