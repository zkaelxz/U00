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

import multiprocessing
import os
import queue
import threading
import time
import traceback

_jobs = {}


def _acting_user_id():
    from services import ownership_service
    return ownership_service.acting_user_id()


def _mirror_locked(job_id):
    """Caller must already hold _lock. Writes this job's current
    status-transition fields (Migration Slice 7) to the cross-process
    job_records table -- a best-effort mirror, never on the hot path of
    update_progress()'s own per-tick calls. A DB hiccup here must never
    break the job it's describing, so any exception is swallowed after
    logging; the in-memory _jobs dict stays the real, authoritative
    state for the process that owns the job either way."""
    job = _jobs.get(job_id)
    if job is None:
        return
    result_json = None
    try:
        from services.jobs_service import project_result_json
        result_json = project_result_json(job.get("result"))
    except Exception:
        import applog
        applog.get_logger().warning(f"job {job_id}: could not project result", exc_info=True)
    try:
        import db
        db.save_job_record(
            job_id, status=job.get("status"), progress=job.get("progress"),
            message=job.get("message"), error=job.get("error"),
            description=job.get("description"), gpu_touching=bool(job.get("gpu_touching")),
            started_at=job.get("started_at"), finished_at=job.get("finished_at"),
            result_json=result_json, owner_user_id=job.get("owner_user_id"))
    except Exception:
        import applog
        applog.get_logger().warning(f"job {job_id}: failed to mirror status to job_records",
                                    exc_info=True)
    _ensure_heartbeat()


# B-04: while this process owns queued/running jobs, a daemon thread bumps
# their job_records.updated_at every HEARTBEAT_INTERVAL seconds, so another
# process's stale-record sweep (jobs_service.cancel_job) can tell a live job
# that is simply not changing status from one whose owner process died.
HEARTBEAT_INTERVAL = 60.0
_heartbeat_thread = None


def _heartbeat_once():
    with _lock:
        live = [j for j, job in _jobs.items() if job.get("status") in ("queued", "running")]
    if live:
        try:
            import db
            db.touch_job_records(live)
        except Exception:
            pass   # best-effort; never breaks a job


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
#
# Migration Slice 9 (D1 fix 2): this used to be a bare module global,
# invisible to a separately-running `python -m api` process and reset to
# the hardcoded default on every restart -- D1's own two named examples
# of this exact problem. Now backed by db.app_settings, read fresh on
# each check rather than cached: these checks happen only at job
# start/finish, never in a hot per-tick loop, so a DB read each time
# costs nothing worth avoiding.
_gpu_queue = []  # [{"job_id", "target", "args", "kwargs", "description"}, ...], FIFO -- stays
# per-process on purpose: each process only ever manages the GPU jobs it
# itself started, so there's nothing cross-process to reconcile here.


def get_gpu_limit_enabled() -> bool:
    import db
    try:
        return bool(db.get_app_setting("gpu_limit_enabled", True))
    except Exception:
        # Never let a DB hiccup block a job from starting -- the GPU
        # guard is a soft, best-effort convenience, not a correctness
        # requirement. Fails open (limit stays on, the safer default).
        return True


def set_gpu_limit_enabled(enabled: bool):
    import db
    db.set_app_setting("gpu_limit_enabled", bool(enabled))


# Step 23c item 4: an optional local desktop notification when a
# background job finishes, so a long job (especially Step 9b's bulk
# series-translate, which can run unattended for a while) doesn't
# require watching the tab. Off by default -- a Settings toggle
# (settings_tab.py) turns it on via set_notify_on_completion() below.
# Migration Slice 9 (D1 fix 2): also now backed by db.app_settings, same
# reasoning as get/set_gpu_limit_enabled() above.


def get_notify_on_completion() -> bool:
    import db
    try:
        return bool(db.get_app_setting("notify_on_completion", False))
    except Exception:
        # _notify_job_finished's own contract is "never raises" -- a DB
        # hiccup here must not break the job it's reporting on. Fails
        # closed (no notification), the safer default.
        return False


def set_notify_on_completion(enabled: bool):
    import db
    db.set_app_setting("notify_on_completion", bool(enabled))


def _notify_job_finished(description, status):
    """Best-effort only -- never raises. A missing `plyer` install, or no
    notification daemon at all (common on a minimal Linux desktop), must
    never take down the job runner that calls this right after finishing
    the job's real work.

    Step 44: also queues a Discord/ntfy push when a channel is configured
    (services/notification_service; its own opt-in is configuring a
    channel, independent of the desktop toggle). That call only queues --
    the send happens on a timer thread -- and never raises."""
    try:
        from services import notification_service
        notification_service.notify_job_finished(description, status)
    except Exception:
        pass
    if not get_notify_on_completion():
        return
    try:
        from plyer import notification
        notification.notify(
            title="Baihe Subtitler",
            message=(f"Finished: {description}" if status == "done" and description else
                     "Finished: background job" if status == "done" else
                     f"Failed: {description}" if description else "Failed: background job"),
            timeout=10)
    except Exception:
        pass


def _other_gpu_job_running_locked(exclude_job_id):
    """Caller must already hold _lock. The id of some other running,
    GPU-touching job, or None if the GPU is free."""
    for jid, job in _jobs.items():
        if jid != exclude_job_id and job.get("gpu_touching") and job["status"] == "running":
            return jid
    return None


# A queued job's message never names the job holding the GPU (auth B2,
# review M-1): anyone who can see the waiting job reads its message, and
# the busy job may be another user's private drama. Also what job_records
# mirrors, so the persisted row doesn't carry it either.
GPU_WAIT_MESSAGE = "Waiting for the GPU (another job is running)"


def _gpu_slot_available_locked(job_id, description):
    """Caller must already hold _lock. True if job_id may actually start
    running right now -- nothing else, in this process, another one
    (Step 25w), or (Step 26d) a completely different application, currently
    holds the GPU. background_jobs' own guard (Step 5c) is plain in-process
    module state, invisible to a separate OS process; `cli.py`'s
    GPU-touching commands never went through it at all (confirmed: cli.py
    never imports this module), so a CLI run and a live UI job could
    previously both hold the GPU at once. db.gpu_lock's single-row table in
    the shared library.db is the cross-process coordination point instead.

    Step 26d: both of those locks only know about GPU-touching work Baihe
    itself started -- neither can see a different application on the same
    machine using the same physical GPU (Jellyfin's hardware-accelerated
    transcoding on the same card is the motivating case). nvidia-smi's own
    utilization/free-VRAM numbers (diagnostics.external_gpu_is_busy) are
    checked as a third, independent guard for exactly that: real driver-
    level load, whoever caused it. Same accepted-latency tradeoff as Step
    25w's cross-process check below: nothing polls this on its own, so a
    job queued purely because of external load resumes the next time some
    *other* GPU-touching job's finish triggers _promote_next_queued_gpu_job()
    again, not the instant the external load actually clears -- not a
    correctness gap (the GPU is never actually shared either way), just the
    same soft, best-effort latency this guard already accepts elsewhere.

    On True, this also claims the cross-process lock for job_id as a side
    effect, the same moment the in-process side is about to mark job_id
    "running" -- so a caller must be about to actually start the job right
    after this returns True, not just probe.

    Best-effort throughout: this module is deliberately usable with no
    library DB and no nvidia-smi at all (plain in-process job tracking, per
    its own docstring, and several tests exercise it standalone) -- if
    either check isn't reachable/available, this falls back to whatever
    checks still are, rather than blocking a job from starting."""
    if _other_gpu_job_running_locked(job_id):
        return False
    try:
        import diagnostics
        if diagnostics.external_gpu_is_busy():
            return False
    except Exception:
        pass
    try:
        import db
        return db.try_acquire_gpu_lock(f"ui:{job_id}", description)
    except Exception:
        return True


def _release_gpu_slot(job_id, gpu_touching):
    """Releases job_id's cross-process GPU lock, if it is gpu_touching and
    actually still holds one -- a safe no-op otherwise (never held one,
    already released, or went stale and was taken over by someone else).
    Called whenever a GPU-touching job's real work actually ends, so
    unlike the promotion helpers above this deliberately does NOT require
    holding _lock first (it's independent in-process bookkeeping vs. a
    separate cross-process record, and the job dict entry for job_id may
    already be gone by the time this runs). Best-effort, same reasoning as
    _gpu_slot_available_locked above -- never raises into a job's own
    runner thread."""
    if not gpu_touching:
        return
    try:
        import db
        db.release_gpu_lock(f"ui:{job_id}")
    except Exception:
        pass


class JobCancelled(Exception):
    """Raised by a thread job that noticed its cancel request and stopped;
    _spawn records the job as "cancelled" (never "done"/"error")."""


def _spawn(job_id, target, args, kwargs, gpu_touching=False):
    def runner():
        import applog
        from translate_engines import redact_secrets
        logger = applog.get_logger()
        logger.info(f"job {job_id} started")
        try:
            target(*args, **kwargs)
            _description = None
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id]["status"] = "done"
                    _jobs[job_id]["progress"] = 1.0
                    _jobs[job_id]["finished_at"] = time.time()
                    _description = _jobs[job_id].get("description")
                    _mirror_locked(job_id)
            logger.info(f"job {job_id} finished")
            _notify_job_finished(_description, "done")
        except JobCancelled:
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id]["status"] = "cancelled"
                    _jobs[job_id]["finished_at"] = time.time()
                    _mirror_locked(job_id)
            logger.info(f"job {job_id} cancelled")
        except Exception as exc:
            error_msg = redact_secrets(f"{type(exc).__name__}: {exc}")
            tb = redact_secrets(traceback.format_exc())
            _description = None
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id]["status"] = "error"
                    _jobs[job_id]["error"] = error_msg
                    _jobs[job_id]["traceback"] = tb
                    _jobs[job_id]["finished_at"] = time.time()
                    _description = _jobs[job_id].get("description")
                    _mirror_locked(job_id)
            logger.error(f"job {job_id} failed: {error_msg}\n{tb}")
            _notify_job_finished(_description, "error")
        finally:
            _release_gpu_slot(job_id, gpu_touching)
            _promote_next_queued_gpu_job()

    threading.Thread(target=runner, daemon=True, name=f"job:{job_id}").start()


def _promote_next_queued_gpu_job():
    """Called whenever a GPU-touching job/slot finishes -- starts the next
    queued GPU-touching job, if the GPU is actually free and anything is
    still waiting. Skips (and drops) queue entries that were cleared out
    from under the queue in the meantime. Dispatches to the thread-based
    or process-based starter depending on how that entry was queued.

    Step 25w: the GPU can also be free-in-this-process but still held
    cross-process (a `cli.py` run) -- checked via _gpu_slot_available_locked,
    same as start_job/start_process_job. If that's the case, this leaves
    the entry at the head of the queue and returns rather than popping it;
    there's no cross-process notification when the CLI later releases it,
    so a still-queued job resumes the next time some other job's finish
    triggers this function again, not immediately -- an accepted latency
    for this same soft, best-effort guard, not a correctness gap (the GPU
    is never actually shared, it just may sit idle a bit before the queued
    job notices)."""
    while True:
        with _lock:
            if not _gpu_queue or _other_gpu_job_running_locked(None):
                return
            entry = _gpu_queue[0]
            job_id = entry["job_id"]
            if job_id not in _jobs or _jobs[job_id]["status"] != "queued":
                _gpu_queue.pop(0)
                continue
            if not _gpu_slot_available_locked(job_id, entry["description"]):
                return
            _gpu_queue.pop(0)
            if entry.get("kind") == "process":
                proc, result_queue = _register_process_job(
                    job_id, entry["target"], entry["args"],
                    _jobs[job_id]["gpu_touching"], entry["description"],
                    _jobs[job_id].get("owner_user_id"))
                on_done = entry.get("on_done")
                break
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["message"] = "Starting..."
            _jobs[job_id]["started_at"] = time.time()
            _mirror_locked(job_id)
            target, args, kwargs = entry["target"], entry["args"], entry["kwargs"]
            break
    if entry.get("kind") == "process":
        proc.start()
        threading.Thread(target=_process_watcher,
                         args=(job_id, proc, result_queue, True), kwargs={"on_done": on_done},
                         daemon=True, name=f"job-watcher:{job_id}").start()
    else:
        _spawn(job_id, target, args, kwargs, gpu_touching=True)


# Set while a library restore swaps the library folder: no job may start
# then (start_job/start_process_job return False, the same "not started"
# answer as a duplicate). See services/library_admin_service.restore_backup.
_exclusive_label = None


def acquire_exclusive(label: str) -> bool:
    """Atomically: refuse (False) if any job is running/queued in this
    process or another exclusive hold is active; otherwise take the hold,
    so no new job can start until release_exclusive()."""
    global _exclusive_label
    with _lock:
        if _exclusive_label is not None or _maintenance_count or any(
                j.get("status") in ("running", "queued") for j in _jobs.values()):
            return False
        _exclusive_label = label
        return True


def release_exclusive():
    global _exclusive_label
    with _lock:
        _exclusive_label = None


_maintenance_count = 0


def enter_maintenance() -> bool:
    """A short non-job library operation (bulk delete, storage cleanup)
    that a restore must not swap under: False if an exclusive hold is
    active, otherwise counted until exit_maintenance(); acquire_exclusive
    refuses while the count is above zero."""
    global _maintenance_count
    with _lock:
        if _exclusive_label is not None:
            return False
        _maintenance_count += 1
        return True


def exit_maintenance():
    global _maintenance_count
    with _lock:
        _maintenance_count = max(0, _maintenance_count - 1)


def maintenance_active() -> bool:
    """True while a bulk delete / storage cleanup (enter_maintenance) runs."""
    with _lock:
        return _maintenance_count > 0


def exclusive_active() -> bool:
    with _lock:
        return _exclusive_label is not None


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
    description is a short human label for the job itself (never shown
    in another job's queued message; see GPU_WAIT_MESSAGE).

    The job records who started it (auth B2): the user id of the API
    request this runs in (ownership_service.acting_user_id), or None for
    the PC owner, auth off, Streamlit, the CLI and jobs started by jobs.
    """
    owner_user_id = _acting_user_id()
    with _lock:
        if _exclusive_label is not None:
            return False
        existing = _jobs.get(job_id)
        if existing and existing["status"] in ("running", "queued"):
            return False
        if gpu_touching and get_gpu_limit_enabled() and not _gpu_slot_available_locked(job_id, description):
            _jobs[job_id] = {
                "status": "queued", "progress": 0.0,
                "message": GPU_WAIT_MESSAGE,
                "error": None, "started_at": time.time(), "finished_at": None,
                "cancel_requested": False, "result": None,
                "gpu_touching": True, "description": description, "kind": "thread",
                "owner_user_id": owner_user_id,
            }
            _mirror_locked(job_id)
            _gpu_queue.append({"job_id": job_id, "target": target, "args": args,
                                "kwargs": kwargs, "description": description, "kind": "thread"})
            return True
        _jobs[job_id] = {
            "status": "running", "progress": 0.0, "message": "Starting...",
            "error": None, "started_at": time.time(), "finished_at": None,
            "cancel_requested": False, "result": None,
            "gpu_touching": gpu_touching, "description": description, "kind": "thread",
            "owner_user_id": owner_user_id,
        }
        _mirror_locked(job_id)

    _spawn(job_id, target, args, kwargs, gpu_touching=gpu_touching)
    return True


def start_process_job(job_id: str, target, args: tuple = (), gpu_touching: bool = False,
                      description: str = None, on_done=None) -> bool:
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
    ("error", <exception type name>, <message>). It may also send any
    number of intermediate ("progress", <fraction 0-1>, <message>) tuples
    first (see report_progress()); the watcher applies them to the job's
    progress/message and they are never mistaken for the final result.

    Same job_id/queued/gpu_touching semantics as start_job(); returns
    False if job_id is already running or queued.

    Migration Slice 49: on_done, if given, is called as
    on_done(job_id, result) in the watcher thread after the subprocess
    returns a successful result and BEFORE the job is marked "done" (so
    nobody polling sees "done" while the hook is still applying it).
    A process job's result otherwise lives only in this process's memory
    and only Streamlit's render loop persists it -- an API-started job
    passes on_done so it can apply its own result. If on_done raises, the
    job ends "error" with a redacted message. Not called on error/cancel.
    Carried through the GPU queue like target/args. Records its starter
    like start_job().
    """
    owner_user_id = _acting_user_id()
    with _lock:
        if _exclusive_label is not None:
            return False
        existing = _jobs.get(job_id)
        if existing and existing["status"] in ("running", "queued"):
            return False
        if gpu_touching and get_gpu_limit_enabled() and not _gpu_slot_available_locked(job_id, description):
            _jobs[job_id] = {
                "status": "queued", "progress": 0.0,
                "message": GPU_WAIT_MESSAGE,
                "error": None, "started_at": time.time(), "finished_at": None,
                "cancel_requested": False, "result": None,
                "gpu_touching": True, "description": description, "kind": "process",
                "process": None, "owner_user_id": owner_user_id,
            }
            _mirror_locked(job_id)
            _gpu_queue.append({"job_id": job_id, "target": target, "args": args,
                                "kwargs": {}, "description": description, "kind": "process",
                                "on_done": on_done})
            return True
        proc, result_queue = _register_process_job(job_id, target, args, gpu_touching, description,
                                                   owner_user_id)
    proc.start()
    threading.Thread(target=_process_watcher, args=(job_id, proc, result_queue, gpu_touching),
                     kwargs={"on_done": on_done}, daemon=True, name=f"job-watcher:{job_id}").start()
    return True


def _register_process_job(job_id, target, args, gpu_touching, description, owner_user_id=None):
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
        "process": proc, "owner_user_id": owner_user_id,
    }
    _mirror_locked(job_id)
    return proc, result_queue


def report_progress(result_queue, frac: float, message: str = ""):
    """For a process-job worker (start_process_job's `target`): sends an
    intermediate ("progress", fraction, message) tuple to the parent's
    watcher, which applies it to the job like update_progress(). Best
    effort -- never raises into the worker. The final ("ok"/"error", ...)
    tuple protocol is unchanged."""
    try:
        result_queue.put(("progress", frac, message))
    except Exception:
        pass


def _apply_progress_item(job_id, item) -> bool:
    """True if `item` was a ("progress", frac, message) tuple (applied to
    the job, with the message secret-redacted); False for anything else,
    which is the worker's final result tuple."""
    if not (isinstance(item, tuple) and item and item[0] == "progress"):
        return False
    try:
        _, frac, message = item
        from translate_engines import redact_secrets
        update_progress(job_id, float(frac), redact_secrets(str(message or "")))
    except Exception:
        pass   # malformed progress must never kill the watcher
    return True


def _process_watcher(job_id, proc, result_queue, gpu_touching=False, poll_interval=0.3,
                     on_done=None):
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
            if not should_stop and _db_cancel_requested(job_id):
                should_stop = True
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
                        _mirror_locked(job_id)
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
                item = result_queue.get(timeout=poll_interval)
            except queue.Empty:
                item = None
            else:
                if _apply_progress_item(job_id, item):
                    continue
                outcome = item
                break
            if not proc.is_alive():
                break

        while outcome is None:
            try:
                # Not get_nowait(): multiprocessing.Queue.put() hands the
                # pickled item to an internal feeder thread rather than
                # writing it synchronously, so a process that exits right
                # after put()ing its result can have already exited
                # (proc.is_alive() already False, as checked above) before
                # that item is actually readable from this end -- a real,
                # documented race, not just a test timing quirk. A short
                # blocking get gives it time to land.
                item = result_queue.get(timeout=1)
            except queue.Empty:
                break
            if not _apply_progress_item(job_id, item):
                outcome = item
        hook_error = None
        if outcome and outcome[0] == "ok" and on_done is not None:
            with _lock:
                if job_id not in _jobs:
                    return
            # Outside the lock: the hook does real DB/file work.
            try:
                on_done(job_id, outcome[1])
            except Exception as exc:
                from translate_engines import redact_secrets
                hook_error = redact_secrets(f"{type(exc).__name__}: {exc}")
                logger.error(f"job {job_id} on_done hook failed: {hook_error}",
                             exc_info=True)
        with _lock:
            if job_id not in _jobs:
                return
            if hook_error is not None:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = f"Completion hook failed: {hook_error}"
                _jobs[job_id]["finished_at"] = time.time()
            elif outcome and outcome[0] == "ok":
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
            _mirror_locked(job_id)
            _final_status = _jobs[job_id]["status"]
            _description = _jobs[job_id].get("description")
        _notify_job_finished(_description, _final_status)
    finally:
        _release_gpu_slot(job_id, gpu_touching)
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
    _gpu_touching = False
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["progress"] = frac
            if message:
                _jobs[job_id]["message"] = message
            _gpu_touching = bool(_jobs[job_id].get("gpu_touching"))
    if _gpu_touching:
        # Step 25w: refreshes this job's cross-process GPU lock (see
        # _gpu_slot_available_locked) so a long-running job's own regular
        # progress updates keep it from looking abandoned to another
        # process before it's actually done. Best-effort, same reasoning
        # as _gpu_slot_available_locked -- never raises into a job thread.
        try:
            import db
            db.heartbeat_gpu_lock(f"ui:{job_id}")
        except Exception:
            pass


def set_result(job_id: str, result, mirror: bool = False):
    """Stores an arbitrary result payload on a job (e.g. the list of
    per-batch errors from a translation run), for the caller to read
    once via get_status(job_id)["result"] after the job finishes.
    mirror=True also writes it to job_records at once, for a result that
    other pollers (GET /api/jobs/{id}) must see while the job still runs."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["result"] = result
            if mirror:
                _mirror_locked(job_id)


def get_status(job_id: str):
    """Returns a snapshot dict, or None if no such job has ever run."""
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def is_running(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job["status"] == "running")


# Jobs that write to, or propose for, a drama's existing lines (job ids
# "translate_<id>", "flag_<id>", "fixflag_<id>", and "retranscribe_<id>",
# which only proposes text for one line; its apply is a separate request).
# Since Step 2 each writes only its own fields by permanent line id, so they
# can run alongside each other and the user's own edits. Replacing ALL of a
# drama's lines (a new transcription) is the one thing that makes their work
# pointless.
LINE_WRITING_JOB_PREFIXES = ("translate_", "flag_", "fixflag_", "retranscribe_")


def cancel_line_jobs(drama_id):
    """Asks every running line-writing job for this drama to stop -- for
    when its lines are about to be replaced wholesale."""
    for prefix in LINE_WRITING_JOB_PREFIXES:
        if is_running(f"{prefix}{drama_id}"):
            request_cancel(f"{prefix}{drama_id}")


# Step 25d item 8: every job id this app starts that's scoped to one
# drama -- for "is anything still working on this drama?" checks before a
# destructive, whole-drama action (deleting it) rather than the narrower
# LINE_WRITING_JOB_PREFIXES above, which only covers jobs safe to run
# alongside each other.
DRAMA_JOB_PREFIXES = LINE_WRITING_JOB_PREFIXES + (
    "transcribe_", "consistency_", "emotion_", "notes_", "resegment_",
    "dub_", "autotune_", "sensevoice_", "diarize_", "narration_", "ocrchapter_",
    "audiobook_", "burned_video_", "softsub_video_", "dubbed_video_", "bulk_translate_", "novel_glossary_", "extract_audio_",
    "sourceimport_", "urlmedia_", "voiceref_",
)


def any_job_running_for_drama(drama_id) -> bool:
    """True if any job scoped to this drama is currently running or
    queued -- for warning before a destructive, whole-drama action (e.g.
    deleting it) rather than letting that job error out against a drama
    that no longer exists."""
    with _lock:
        for prefix in DRAMA_JOB_PREFIXES:
            job = _jobs.get(f"{prefix}{drama_id}")
            if job and job["status"] in ("running", "queued"):
                return True
        return False


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


_DB_CANCEL_CHECK_INTERVAL = 2.0
_last_db_cancel_check = {}


def _db_cancel_requested(job_id: str) -> bool:
    """Migration Slice 22: a cancel requested from another process (the API
    host) lives in job_records. Checked at most once per
    _DB_CANCEL_CHECK_INTERVAL seconds per job so a tight job loop doesn't
    hit SQLite every iteration; on a hit the in-memory flag is set so
    later checks are free. Only for jobs this process owns and that are
    still queued/running. A DB error means "not requested"."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.get("status") not in ("queued", "running"):
            return False
        if job.get("cancel_requested"):
            return True
        now = time.monotonic()
        if now - _last_db_cancel_check.get(job_id, -1e9) < _DB_CANCEL_CHECK_INTERVAL:
            return False
        _last_db_cancel_check[job_id] = now
    try:
        import db
        requested = db.is_job_record_cancel_requested(job_id)
    except Exception:
        return False
    if requested:
        request_cancel(job_id)
    return requested


def is_cancel_requested(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        if job and job.get("cancel_requested"):
            return True
    return _db_cancel_requested(job_id)


def _kill_tree(proc):
    """Kills proc and everything it started (it runs in its own process
    group/session -- see run_cancellable), so a wrapper script's ffmpeg
    grandchild can't keep the pipes open."""
    import subprocess
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def run_cancellable(job_id: str, cmd: list, cwd: str = None, poll_interval: float = 0.2,
                    kill_timeout: float = 10.0, timeout: float = None):
    """Runs an external command (ffmpeg) for a thread job and kills it when
    the job's cancel is requested, raising JobCancelled. The command gets
    its own process group (POSIX session / Windows process group) and the
    whole tree is killed; the post-kill pipe drain is bounded by
    kill_timeout. A non-zero exit raises subprocess.CalledProcessError,
    like subprocess.run(check=True). With timeout (seconds), the tree is
    killed and subprocess.TimeoutExpired raised once that much time passes."""
    import subprocess
    deadline = None if timeout is None else time.monotonic() + timeout
    if os.name == "nt":
        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        group = {"start_new_session": True}
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            **group)
    while True:
        try:
            out, err = proc.communicate(timeout=poll_interval)
            break
        except subprocess.TimeoutExpired:
            cancelled = is_cancel_requested(job_id)
            if cancelled or (deadline is not None and time.monotonic() >= deadline):
                _kill_tree(proc)
                try:
                    proc.communicate(timeout=kill_timeout)
                except subprocess.TimeoutExpired:
                    pass   # a grandchild still holds a pipe; don't hang the job
                if cancelled:
                    raise JobCancelled(job_id) from None
                raise subprocess.TimeoutExpired(cmd, timeout) from None
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=out, stderr=err)


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
    try:
        import db
        db.delete_job_record(job_id)
    except Exception:
        import applog
        applog.get_logger().warning(f"job {job_id}: failed to delete its job_records row",
                                    exc_info=True)


def clear_all_jobs():
    """Wipes every job record, running or not. Used by a full library
    reset -- a job still referencing a now-deleted drama has nothing
    useful left to report."""
    with _lock:
        _jobs.clear()
        _gpu_queue.clear()
    try:
        import db
        db.clear_all_job_records()
    except Exception:
        import applog
        applog.get_logger().warning("failed to clear job_records", exc_info=True)


def list_running_jobs():
    with _lock:
        return {jid: dict(j) for jid, j in _jobs.items() if j["status"] == "running"}


def list_all_jobs():
    """Every job this process still has a record of -- running or
    finished, since a finished job's entry isn't cleared automatically
    (see clear_all_jobs/_jobs.pop). Backs Step 58's job-level "why was
    this slow" view: it can only explain a job still resident in this
    process's own memory, never one from a prior run of the app."""
    with _lock:
        return {jid: dict(j) for jid, j in _jobs.items()}


def recheck_gpu_queue():
    """Public entry point for _promote_next_queued_gpu_job(), for callers
    outside this module. Nothing here polls the GPU queue on its own (see
    _gpu_slot_available_locked's own docstring) -- a job queued because
    nvidia-smi showed the GPU busy with something Baihe didn't start
    otherwise only gets re-checked the next time some *other* GPU-touching
    job finishes. The Running Jobs panel's auto-refresh calls this on every
    tick specifically to close that gap while it's open."""
    _promote_next_queued_gpu_job()
