"""
background_jobs.py -- runs long operations (translate, transcribe, dub,
exports...) in background threads, so an HTTP request can start a job
and return at once. Work with no safe point to stop at (one opaque
diarization call) runs in a child process instead, so cancel can kill it.

State lives in the module-level `_jobs` dict. That dict is the authority
for jobs this process owns; the API polls it through get_status().

Every status change is also mirrored, best-effort, to the `job_records`
table so other processes (and a restarted server) can see a job's last
known state. While this process has queued or running jobs, a daemon
heartbeat thread bumps their `updated_at` so a stale-record sweep
elsewhere can tell a quiet live job from one whose owner process died.
A failed mirror write is logged and never breaks the job.

Rules for anything run this way:
  - Do the work through functions that only touch plain Python objects
    and the database (e.g. translate_lines_with_engine).
  - Write only the fields the job owns (`db.save_lines(..., fields=...)`),
    so a job can't overwrite the user's edits or another job's work.
  - A process job can't write into this process's dict; it reports
    progress and its result through a multiprocessing.Queue that a
    watcher thread here applies.

One machine, one process owning each job: not a distributed job queue.
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


def _storage_text(text):
    """job_records lands in backups: secrets and URL query strings out."""
    if not text:
        return text
    from translate_engines import redact_for_storage
    return redact_for_storage(text)


def _mirror_locked(job_id):
    """Caller must already hold _lock. Writes this job's current
    status-transition fields to the cross-process
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
            message=_storage_text(job.get("message")), error=_storage_text(job.get("error")),
            description=job.get("description"), gpu_touching=bool(job.get("gpu_touching")),
            started_at=job.get("started_at"), finished_at=job.get("finished_at"),
            result_json=result_json, owner_user_id=job.get("owner_user_id"),
            owner_pid=os.getpid())
    except Exception:
        import applog
        applog.get_logger().warning(f"job {job_id}: failed to mirror status to job_records",
                                    exc_info=True)
    _emit_change(job_id)
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
    reconcile_dead_workers()
    with _lock:
        live = [j for j, job in _jobs.items() if job.get("status") in ("queued", "running")]
    if live:
        try:
            import db
            db.touch_job_records(live)
        except Exception as e:
            # Best-effort; never breaks a job. Logged (redacted) because a
            # live job whose heartbeat can't be written for
            # STALE_JOB_SECONDS looks dead to another process's checks.
            # This process's own checks and sweep still see it as live
            # (jobs_service.sweep_stale_job_records skips in-process jobs).
            try:
                import applog
                import translate_engines
                applog.get_logger().warning(
                    "job heartbeat write failed: "
                    + translate_engines.redact_secrets(str(e))[:300])
            except Exception:
                pass


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

# A soft, global "one GPU job at a time" guard, prompted by a
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
# Backed by db.app_settings, not a module global, so a separately-running
# process sees the same setting and it survives a restart. Read fresh on
# each check rather than cached: these checks happen only at job
# start/finish, never in a hot per-tick loop, so a DB read each time
# costs nothing worth avoiding.
_gpu_queue = []  # [{"job_id", "target", "args", "kwargs", "description"}, ...], FIFO -- stays
# per-process on purpose: each process only ever manages the GPU jobs it
# itself started, so there's nothing cross-process to reconcile here.


def _warn(what, exc):
    """Logs a swallowed failure at warning, secret-redacted. Never raises."""
    try:
        import applog
        from translate_engines import redact_secrets
        applog.get_logger().warning(
            f"{what}: " + redact_secrets(f"{type(exc).__name__}: {exc}")[:300])
    except Exception:
        pass


def get_gpu_limit_enabled() -> bool:
    import db
    try:
        return bool(db.get_app_setting("gpu_limit_enabled", True))
    except Exception as exc:
        # Never let a DB hiccup block a job from starting -- the GPU
        # guard is a soft, best-effort convenience, not a correctness
        # requirement. On a read error the limit stays on (the default).
        _warn("could not read the GPU-limit setting; keeping the limit on", exc)
        return True


def set_gpu_limit_enabled(enabled: bool):
    import db
    db.set_app_setting("gpu_limit_enabled", bool(enabled))


# How many GPU-touching jobs may run at once while the GPU limit is on,
# across this process and any cli.py run. 1 (the default) is one at a time.
# The upper bound matches db.GPU_LOCK_MAX_SLOTS (the gpu_lock table's CHECK).
GPU_MAX_PARALLEL_LIMIT = 4
# A job joins one that is already running only with at least this much
# free VRAM per nvidia-smi, and only once the newest running job has had
# this long to load its model (so the free-VRAM reading includes it).
GPU_PARALLEL_RESERVE_MB = 2048
GPU_PARALLEL_SETTLE_SECONDS = 30


def _clamp_gpu_max_parallel(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(n, GPU_MAX_PARALLEL_LIMIT))


def get_gpu_max_parallel() -> int:
    import db
    try:
        return _clamp_gpu_max_parallel(db.get_app_setting("gpu_max_parallel", 1))
    except Exception as exc:
        # Fails safe: one GPU job at a time.
        _warn("could not read the GPU jobs-at-once setting; using 1", exc)
        return 1


def set_gpu_max_parallel(value: int):
    import db
    db.set_app_setting("gpu_max_parallel", _clamp_gpu_max_parallel(value))


def _vram_room_for_another_gpu_job() -> bool:
    """True only when nvidia-smi reports at least GPU_PARALLEL_RESERVE_MB
    free. No reading (no nvidia-smi, a failed query) is False: without it
    nothing can tell whether a second model fits, so jobs run one at a time.
    No per-job size estimate is used: none is known before a job loads."""
    try:
        import diagnostics
        load = diagnostics.external_gpu_load()
    except Exception as exc:
        _warn("GPU memory check failed; running GPU jobs one at a time", exc)
        return False
    free_mb = (load or {}).get("memory_free_mb")
    return free_mb is not None and free_mb >= GPU_PARALLEL_RESERVE_MB


def try_take_gpu_slot(holder: str, description: str = None, check_external_load: bool = False) -> bool:
    """Claims a cross-process GPU slot (db.gpu_lock) for `holder`; True if
    taken. Shared by UI jobs and cli.py so both follow the same cap.

    The first holder needs only a free lock (and, with check_external_load,
    no other application loading the GPU). Joining running holders needs
    gpu_max_parallel > 1, fewer holders than that, enough free VRAM and the
    settle time (see GPU_PARALLEL_SETTLE_SECONDS). The utilization check is
    skipped when joining: Baihe's own running job is what loads the GPU
    then, and free VRAM already counts every application's use.

    May raise from the database; callers decide whether that queues/waits."""
    import db
    cap = get_gpu_max_parallel()
    if cap > 1:
        others = db.gpu_lock_holder_count(exclude_holder=holder)
        if others >= cap:
            return False
        if others:
            if not _vram_room_for_another_gpu_job():
                return False
            return db.try_acquire_gpu_lock(holder, description, max_holders=cap,
                                           settle_seconds=GPU_PARALLEL_SETTLE_SECONDS)
    if check_external_load:
        try:
            import diagnostics
            if diagnostics.external_gpu_is_busy():
                return False
        except Exception as exc:
            # Fails open on purpose: this check is optional (no nvidia-smi is
            # normal and returns False without raising), the locks remain.
            _warn("external GPU load check failed; ignoring it", exc)
    return db.try_acquire_gpu_lock(holder, description)


# An optional local desktop notification when a
# background job finishes, so a long job (especially a bulk
# series-translate, which can run unattended for a while) doesn't
# require watching the page. Off by default -- a Settings toggle turns it
# on via set_notify_on_completion() below. Backed by db.app_settings, same
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


def _notify_job_finished(description, status, job_id=None, owner_user_id=None,
                         with_errors=False):
    """Best-effort only -- never raises. A missing `plyer` install, or no
    notification daemon at all (common on a minimal Linux desktop), must
    never take down the job runner that calls this right after finishing
    the job's real work.

    Also queues a Discord/ntfy push when a channel is configured
    (services/notification_service; its own opt-in is configuring a
    channel, independent of the desktop toggle). That call only queues --
    the send happens on a timer thread -- and never raises. It also keeps
    the event for the in-app list, shown by `job_id`/`owner_user_id`
    visibility."""
    try:
        from services import notification_service
        notification_service.notify_job_finished(description, status, job_id=job_id,
                                                 owner_user_id=owner_user_id,
                                                 with_errors=with_errors)
    except Exception:
        pass
    if not get_notify_on_completion():
        return
    try:
        from plyer import notification
        notification.notify(
            title="Baihe Subtitler",
            message=(f"Finished with errors: {description}" if status == "done" and with_errors
                     and description else
                     f"Finished: {description}" if status == "done" and description else
                     "Finished: background job" if status == "done" else
                     f"Failed: {description}" if description else "Failed: background job"),
            timeout=10)
    except Exception:
        pass


def _running_gpu_job_count_locked(exclude_job_id):
    """Caller must already hold _lock. How many other GPU-touching jobs in
    this process are running."""
    return sum(1 for jid, job in _jobs.items()
               if jid != exclude_job_id and job.get("gpu_touching") and job["status"] == "running")


def _gpu_queue_waiting_locked():
    """Caller must already hold _lock. True if a job is still waiting in
    _gpu_queue: a new GPU job then queues behind it (first come, first
    served) rather than taking a slot that just opened."""
    return any(_jobs.get(e["job_id"], {}).get("status") == "queued" for e in _gpu_queue)


def _take_queued_locked(job_id=None) -> list:
    """Caller holds _lock. Removes job_id's entries (every entry for None)
    from _gpu_queue and returns their (job_id, on_finish) hooks, for the
    caller to run with _run_finish_hooks once it has released _lock. An
    entry leaves the queue once, so its hook is handed out once."""
    hooks, kept = [], []
    for entry in _gpu_queue:
        if job_id is None or entry["job_id"] == job_id:
            if entry.get("on_finish") is not None:
                hooks.append((entry["job_id"], entry["on_finish"]))
        else:
            kept.append(entry)
    _gpu_queue[:] = kept
    return hooks


def _run_finish_hooks(hooks):
    """Runs (job_id, on_finish) pairs; a failing hook is logged, never raised."""
    for job_id, hook in hooks:
        try:
            hook(job_id)
        except Exception as exc:
            _warn(f"job {job_id}: its finish hook failed", exc)


# A queued job's message never names the job holding the GPU (auth B2,
# review M-1): anyone who can see the waiting job reads its message, and
# the busy job may be another user's private drama. Also what job_records
# mirrors, so the persisted row doesn't carry it either.
GPU_WAIT_MESSAGE = "Waiting for the GPU (another job is running)"


def _queue_message(position: int) -> str:
    """The waiting message with the job's place in line
    (first in line keeps the plain message). Never names another job."""
    return GPU_WAIT_MESSAGE if position <= 1 else f"{GPU_WAIT_MESSAGE} - number {position} in line"


def _external_gpu_wait_message(load: dict):
    """The waiting message when a program Baihe did not start is using the
    GPU, or None if the reading shows no such load. Numbers only: nvidia-smi
    is queried for totals, never for process names, paths or command lines."""
    import diagnostics
    util = load.get("utilization_percent") or 0
    free_mb = load.get("memory_free_mb")
    total_mb = load.get("memory_total_mb") or 0
    busy_util = util >= diagnostics.EXTERNAL_GPU_BUSY_UTIL_PERCENT
    busy_mem = free_mb is not None and free_mb < diagnostics.EXTERNAL_GPU_BUSY_MIN_FREE_MB
    if not (busy_util or busy_mem):
        return None
    used_gb = (total_mb - (free_mb or 0)) / 1024
    usage = f"about {used_gb:.1f} GB of {total_mb / 1024:.1f} GB in use"
    if busy_util and not busy_mem:
        usage += f", GPU about {util:.0f}% busy"
    return ("Waiting for the GPU: another program is using it (" + usage + "). "
            "Close GPU-heavy apps such as games, video editors or other AI tools, "
            "or turn off GPU use in Settings.")


def _note_gpu_wait_reason_locked(job_id):
    """Caller holds _lock. Records on the waiting job whether the GPU is held
    by another program rather than by a Baihe job, so the queued message can
    say why. Only looks at the driver when no Baihe job here is running."""
    job = _jobs.get(job_id)
    if job is None or job.get("status") != "queued":
        return
    reason = None
    if _running_gpu_job_count_locked(job_id) == 0:
        try:
            import diagnostics
            load = diagnostics.external_gpu_load()
            reason = _external_gpu_wait_message(load) if load else None
        except Exception as exc:
            _warn("GPU wait-reason check failed", exc)
    job["gpu_wait_external"] = reason
    _refresh_queue_messages_locked()


def _refresh_queue_messages_locked():
    """Caller holds _lock: rewrites each queued job's message to its
    current place in _gpu_queue after the queue changed."""
    position = 0
    for entry in _gpu_queue:
        job = _jobs.get(entry["job_id"])
        if not job or job.get("status") != "queued":
            continue
        position += 1
        message = (job.get("gpu_wait_external") if position == 1 else None) or _queue_message(position)
        if job.get("message") != message:
            job["message"] = message
            _mirror_locked(entry["job_id"])


def _gpu_slot_available_locked(job_id, description):
    """Caller must already hold _lock. True if job_id may actually start
    running right now -- nothing else, in this process, another one
    (cross-process), or a completely different application, currently
    holds the GPU. background_jobs' own guard is plain in-process
    module state, invisible to a separate OS process; `cli.py` imports this module but
    its own process's state is not the API server's, so a CLI run and a
    live UI job could both hold the GPU at once. db.gpu_lock's single-row table in
    the shared library.db is the cross-process coordination point instead.

    Both of those locks only know about GPU-touching work Baihe
    itself started -- neither can see a different application on the same
    machine using the same physical GPU (Jellyfin's hardware-accelerated
    transcoding on the same card is the motivating case). nvidia-smi's own
    utilization/free-VRAM numbers (diagnostics.external_gpu_is_busy) are
    checked as a third, independent guard for exactly that: real driver-
    level load, whoever caused it. Same accepted-latency tradeoff as the
    cross-process check below: nothing polls this on its own, so a
    job queued purely because of external load resumes the next time some
    *other* GPU-touching job's finish triggers _promote_next_queued_gpu_job()
    again, not the instant the external load actually clears -- not a
    correctness gap (the GPU is never actually shared either way), just the
    same soft, best-effort latency this guard already accepts elsewhere.

    On True, this also claims the cross-process lock for job_id as a side
    effect, the same moment the in-process side is about to mark job_id
    "running" -- so a caller must be about to actually start the job right
    after this returns True, not just probe.

    With gpu_max_parallel > 1, a job may also join running ones, up to
    that many in total; see try_take_gpu_slot for the free-VRAM guardrail.

    No nvidia-smi (or a failing external check) is ignored for the first
    job, and means one at a time beyond it. A failing cross-process lock
    is not ignored: the job queues (False) until the lock can be taken,
    since starting anyway could share the GPU with a CLI run."""
    if _running_gpu_job_count_locked(job_id) >= get_gpu_max_parallel():
        return False
    try:
        if try_take_gpu_slot(f"ui:{job_id}", description, check_external_load=True):
            return True
        _note_gpu_wait_reason_locked(job_id)
        return False
    except Exception as exc:
        # Fails closed: without the cross-process lock a CLI GPU run could
        # share the card. The job queues and the API's GPU-queue poller
        # (api/background.py) retries it.
        _warn(f"job {job_id}: could not take the GPU lock; queuing", exc)
        return False


def _release_gpu_slot(job_id, gpu_touching, run=None):
    """Releases job_id's cross-process GPU lock, if it is gpu_touching and
    actually still holds one -- a safe no-op otherwise (never held one,
    already released, or went stale and was taken over by someone else).
    Called whenever a GPU-touching job's real work actually ends; the job
    dict entry for job_id may already be gone by the time this runs.

    `run` is the ending run's own job dict. The lock row is named after the
    job id, and a new run of the same id can start (and take the row over)
    as soon as this run's status is final, before this release: when the
    id's current dict is another, running run, the row is that run's and is
    left alone. Checked and released under _lock, the lock a promotion takes
    the slot under. Best-effort, same reasoning as
    _gpu_slot_available_locked above -- never raises into a job's own
    runner thread."""
    if not gpu_touching:
        return
    try:
        import db
        with _lock:
            current = _jobs.get(job_id)
            if (run is not None and current is not None and current is not run
                    and current.get("status") == "running"):
                return
            db.release_gpu_lock(f"ui:{job_id}")
    except Exception:
        pass


class JobCancelled(Exception):
    """Raised by a thread job that noticed its cancel request and stopped;
    _spawn records the job as "cancelled" (never "done"/"error")."""


def _timing_start(job_id, thread_job=True):
    """(services/job_timing_service): a job's per-stage
    timing run starts when it actually runs. Never raises."""
    try:
        from services import job_timing_service
        token = job_timing_service.start_run(job_id)
        if thread_job:
            job_timing_service.set_current_job(job_id)
        return token
    except Exception:
        return None


def _timing_finish(job_id, token, thread_job=True):
    """`token` keeps a new run of the same job id, started the moment this
    one was marked finished, from being closed by this one."""
    try:
        from services import job_timing_service
        job_timing_service.finish_run(job_id, token=token)
        if thread_job:
            job_timing_service.set_current_job(None)
    except Exception:
        pass


def _spawn(job_id, target, args, kwargs, gpu_touching=False, run=None):
    def runner():
        import applog
        from translate_engines import redact_secrets
        logger = applog.get_logger()
        logger.info(f"job {job_id} started")
        _timing = _timing_start(job_id)
        try:
            target(*args, **kwargs)
            _description = _owner = None
            _with_errors = False
            with _lock:
                if _still_running_locked(job_id):
                    _result = _jobs[job_id].get("result")
                    _with_errors = isinstance(_result, dict) and bool(_result.get("errors"))
                    _jobs[job_id]["status"] = "done"
                    _jobs[job_id]["progress"] = 1.0
                    _jobs[job_id]["finished_at"] = time.time()
                    _description = _jobs[job_id].get("description")
                    _owner = _jobs[job_id].get("owner_user_id")
                    _mirror_locked(job_id)
            logger.info(f"job {job_id} finished")
            _notify_job_finished(_description, "done", job_id=job_id, owner_user_id=_owner,
                                 with_errors=_with_errors)
        except JobCancelled:
            with _lock:
                if _still_running_locked(job_id):
                    _jobs[job_id]["status"] = "cancelled"
                    _jobs[job_id]["finished_at"] = time.time()
                    _mirror_locked(job_id)
            logger.info(f"job {job_id} cancelled")
        except BaseException as exc:
            # BaseException too: a SystemExit from job code would otherwise
            # leave the job "running" forever. Not re-raised: it would only
            # end this thread, which ends here anyway, and KeyboardInterrupt
            # is only ever delivered to the main thread.
            error_msg = redact_secrets(f"{type(exc).__name__}: {exc}")
            tb = redact_secrets(traceback.format_exc())
            _description = _owner = None
            with _lock:
                if _still_running_locked(job_id):
                    _jobs[job_id]["status"] = "error"
                    _jobs[job_id]["error"] = error_msg
                    _jobs[job_id]["traceback"] = tb
                    _jobs[job_id]["finished_at"] = time.time()
                    _description = _jobs[job_id].get("description")
                    _owner = _jobs[job_id].get("owner_user_id")
                    _mirror_locked(job_id)
            logger.error(f"job {job_id} failed: {error_msg}\n{tb}")
            _notify_job_finished(_description, "error", job_id=job_id, owner_user_id=_owner)
        finally:
            _timing_finish(job_id, _timing)
            _release_gpu_slot(job_id, gpu_touching, run)
            _promote_next_queued_gpu_job()

    _start_job_thread(runner, f"job:{job_id}", worker_for=job_id)


def _still_running_locked(job_id) -> bool:
    """Caller holds _lock. A worker records its outcome only over "running":
    a job already ended (cancelled while queued, or marked interrupted by
    reconcile_dead_workers) keeps that state when a late worker returns."""
    job = _jobs.get(job_id)
    return job is not None and job.get("status") == "running"


def _fail_start(job_id, gpu_touching, exc, proc=None, result_queue=None, run=None):
    """The job was marked "running" but its thread or process could not be
    started (thread limit, fork or pickling failure). Without this the
    record stays "running" forever: the id can't be restarted, a restore
    or reset is refused and the GPU lock is never released. The process
    and its result queue are closed so their pipe fds aren't leaked."""
    import applog
    from translate_engines import redact_secrets
    error_msg = redact_secrets(f"Could not start the job: {type(exc).__name__}: {exc}")
    if proc is not None:
        try:
            if proc.is_alive():
                _stop_process(proc)
        except Exception:
            pass
        try:
            proc.close()
        except Exception:
            pass
    if result_queue is not None:
        try:
            result_queue.close()
        except Exception:
            pass
    with _lock:
        job = _jobs.get(job_id)
        # "queued": a promoted process job whose Process could not even be
        # built is still its queued run.
        if job is not None and (job.get("status") == "running" or (
                job.get("status") == "queued" and run is not None and job is run)):
            job["status"] = "error"
            job["error"] = error_msg
            job["finished_at"] = time.time()
            _mirror_locked(job_id)
    applog.get_logger().error(f"job {job_id} failed to start: {error_msg}")
    _release_gpu_slot(job_id, gpu_touching, run)
    _promote_next_queued_gpu_job()


def _stop_process(proc):
    """terminate, then kill if the child ignores SIGTERM (e.g. stuck in a
    CUDA call): the GPU slot is released right after, so a surviving
    child would hold VRAM with nothing tracking it."""
    proc.terminate()
    proc.join(timeout=5)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=5)


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


def _start_job_thread(target, name, *args, worker_for=None, **kwargs):
    def run():
        try:
            target(*args, **kwargs)
        finally:
            with _lock:
                _job_threads.discard(thread)

    thread = threading.Thread(target=run, daemon=True, name=name)
    thread.baihe_job_id = worker_for
    # Started under the lock so wait_for_job_threads never sees (and tries
    # to join) a thread that is registered but not yet started.
    with _lock:
        thread.start()
        _job_threads.add(thread)
        if worker_for is not None and worker_for in _jobs:
            _workers[worker_for] = (_jobs[worker_for], thread)


WORKER_LOST_MESSAGE = "Interrupted: the job's worker stopped without reporting back."


def reconcile_dead_workers() -> list:
    """Marks every running job whose worker thread has exited as failed
    (WORKER_LOST_MESSAGE), releasing its GPU slot, so it can't read as
    running forever. A worker always records an outcome before it exits,
    so this only catches one that died without doing so. Returns the ids."""
    lost = []
    with _lock:
        for job_id, (job, thread) in list(_workers.items()):
            if _jobs.get(job_id) is not job:
                _workers.pop(job_id, None)
                continue
            if job.get("status") != "running" or thread.is_alive():
                continue
            job["status"] = "error"
            job["error"] = WORKER_LOST_MESSAGE
            job["finished_at"] = time.time()
            _mirror_locked(job_id)
            lost.append((job_id, job))
    for job_id, job in lost:
        proc = job.get("process")
        try:
            if proc is not None and proc.is_alive():
                _stop_process(proc)
        except Exception as exc:
            _warn(f"job {job_id}: could not stop its process", exc)
        _release_gpu_slot(job_id, job.get("gpu_touching"), job)
    if lost:
        _promote_next_queued_gpu_job()
    return [job_id for job_id, _ in lost]


def wait_for_job_threads(timeout: float, job_ids=None) -> bool:
    """Joins every job thread that has not exited, including one whose job
    already reads as finished or was cleared. True once none is left,
    False if some thread outlived `timeout` seconds. Call it with no job
    running or queued (under acquire_exclusive), before replacing the
    database, or it waits on real work. job_ids: only those jobs' threads."""
    deadline = time.monotonic() + timeout
    with _lock:
        threads = [t for t in _job_threads if t is not threading.current_thread()
                   and (job_ids is None or getattr(t, "baihe_job_id", None) in job_ids)]
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    return not any(t.is_alive() for t in threads)


def _promote_next_queued_gpu_job():
    """Called whenever a GPU-touching job/slot finishes -- starts the next
    queued GPU-touching job, if the GPU is actually free and anything is
    still waiting. Skips (and drops) queue entries that were cleared out
    from under the queue in the meantime. Dispatches to the thread-based
    or process-based starter depending on how that entry was queued.

    The GPU can also be free-in-this-process but still held
    cross-process (a `cli.py` run) -- checked via _gpu_slot_available_locked,
    same as start_job/start_process_job. If that's the case, this leaves
    the entry at the head of the queue and returns rather than popping it;
    there's no cross-process notification when the CLI later releases it,
    so a still-queued job resumes the next time some other job's finish
    triggers this function again, not immediately -- an accepted latency
    for this same soft, best-effort guard, not a correctness gap (the GPU
    is never actually shared, it just may sit idle a bit before the queued
    job notices)."""
    dropped = []
    try:
        _promote_one_queued_gpu_job(dropped)
    finally:
        _run_finish_hooks(dropped)


def _promote_one_queued_gpu_job(dropped):
    """_promote_next_queued_gpu_job's body. Appends the finish hooks of the
    entries it drops, and of a promoted process job that fails to start, to
    `dropped`, which the caller runs once _lock is released."""
    while True:
        with _lock:
            if not _gpu_queue or _running_gpu_job_count_locked(None) >= get_gpu_max_parallel():
                return
            entry = _gpu_queue[0]
            job_id = entry["job_id"]
            if job_id not in _jobs or _jobs[job_id]["status"] != "queued":
                _gpu_queue.pop(0)
                if entry.get("on_finish") is not None:
                    dropped.append((job_id, entry["on_finish"]))
                continue
            if not _gpu_slot_available_locked(job_id, entry["description"]):
                return
            _gpu_queue.pop(0)
            _refresh_queue_messages_locked()
            if entry.get("kind") == "process":
                on_done = entry.get("on_done")
                on_finish = entry.get("on_finish")
                kill_whole_tree = entry.get("kill_whole_tree", False)
                run = _jobs[job_id]
                proc = result_queue = None
                build_error = None
                try:
                    proc, result_queue = _register_process_job(
                        job_id, entry["target"], entry["args"],
                        run["gpu_touching"], entry["description"],
                        run.get("owner_user_id"), start_method=entry.get("start_method"))
                    run = _jobs[job_id]
                except Exception as exc:
                    # mp.Queue()/Process() can fail (no fds, no /dev/shm)
                    # after the GPU row was claimed and the entry popped:
                    # handled below like a failed start, so the job ends,
                    # its row is released and its on_finish runs once.
                    build_error = exc
                break
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["message"] = "Starting..."
            _jobs[job_id]["started_at"] = time.time()
            _mirror_locked(job_id)
            target, args, kwargs = entry["target"], entry["args"], entry["kwargs"]
            run = _jobs[job_id]
            break
    try:
        if entry.get("kind") == "process":
            if build_error is not None:
                raise build_error
            proc.start()
            _start_job_thread(_process_watcher, f"job-watcher:{job_id}",
                              job_id, proc, result_queue, True, on_done=on_done,
                              on_finish=on_finish, kill_whole_tree=kill_whole_tree, run=run,
                              worker_for=job_id)
        else:
            _spawn(job_id, target, args, kwargs, gpu_touching=True, run=run)
    except Exception as exc:
        # Runs in another job's finishing thread: record it, don't raise.
        if entry.get("kind") == "process":
            _fail_start(job_id, True, exc, proc, result_queue, run=run)
            if on_finish is not None:
                dropped.append((job_id, on_finish))
        else:
            _fail_start(job_id, True, exc, run=run)


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


_stopping = False
STOPPING_MESSAGE = "Baihe is stopping, so no new job can start. Start Baihe again to run it."


def refuse_new_jobs() -> None:
    """The server's clean stop has begun (services/shutdown_service.py):
    from here on start_job/start_process_job raise instead of starting, so
    nothing started during the grace wait is killed mid-write when the
    process ends. Set under _lock, so a start either finished before this
    (and the stop's active_job_ids() sees it) or is refused. Never undone
    for the life of the process."""
    global _stopping
    with _lock:
        _stopping = True


def _refuse_if_stopping_locked():
    """Caller holds _lock."""
    if _stopping:
        from services.service_errors import ConflictError
        raise ConflictError(STOPPING_MESSAGE)


def start_job(job_id: str, target, *args, gpu_touching: bool = False,
              description: str = None, **kwargs) -> bool:
    """
    Starts target(*args, **kwargs) in a background thread under job_id.
    Returns False without starting anything if a job with that ID is
    already running or queued -- so a second click on "Translate" doesn't
    launch a duplicate.

    gpu_touching: True for anything that loads a local model onto the GPU
    (transcription, diarization, OCR, TTS/dub, local-model translation).
    When the GPU limit setting is on and no GPU slot is free (another
    gpu_touching job is running, any job_id, any drama, and the "GPU jobs
    at once" cap or its free-VRAM check doesn't allow one more), or an
    earlier GPU job is still waiting, this one is queued instead of
    started -- see _promote_next_queued_gpu_job().
    description is a short human label for the job itself (never shown
    in another job's queued message; see GPU_WAIT_MESSAGE).

    The job records who started it (auth B2): the user id of the API
    request this runs in (ownership_service.acting_user_id), or None for
    the PC owner, auth off, the CLI and jobs started by jobs.

    Raises ConflictError once the server's clean stop has begun
    (refuse_new_jobs).
    """
    owner_user_id = _acting_user_id()
    with _lock:
        _refuse_if_stopping_locked()
        if _exclusive_label is not None:
            return False
        existing = _jobs.get(job_id)
        if existing and existing["status"] in ("running", "queued"):
            return False
        if gpu_touching and get_gpu_limit_enabled() and (
                _gpu_queue_waiting_locked() or not _gpu_slot_available_locked(job_id, description)):
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
            _note_gpu_wait_reason_locked(job_id)
            return True
        _jobs[job_id] = {
            "status": "running", "progress": 0.0, "message": "Starting...",
            "error": None, "started_at": time.time(), "finished_at": None,
            "cancel_requested": False, "result": None,
            "gpu_touching": gpu_touching, "description": description, "kind": "thread",
            "owner_user_id": owner_user_id,
        }
        _mirror_locked(job_id)
        run = _jobs[job_id]

    try:
        _spawn(job_id, target, args, kwargs, gpu_touching=gpu_touching, run=run)
    except Exception as exc:
        _fail_start(job_id, gpu_touching, exc, run=run)
        raise
    return True


def start_process_job(job_id: str, target, args: tuple = (), gpu_touching: bool = False,
                      description: str = None, on_done=None, on_finish=None,
                      kill_whole_tree: bool = False, start_method: str = None) -> bool:
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
    first (see report_progress()), and ("stage", <fraction>, <message>)
    tuples for a stage that cannot report progress (see report_stage());
    the watcher applies them to the job's progress/message and they are
    never mistaken for the final result.

    Same job_id/queued/gpu_touching semantics as start_job(); returns
    False if job_id is already running or queued.

    on_done, if given, is called as
    on_done(job_id, result) in the watcher thread after the subprocess
    returns a successful result and BEFORE the job is marked "done" (so
    nobody polling sees "done" while the hook is still applying it).
    A process job's result otherwise lives only in this process's memory
    and nothing applies it (e.g. saves it to the drama) -- a caller that
    needs it applied passes on_done. If on_done raises, the
    job ends "error" with a redacted message. Not called on error/cancel,
    and not called when a cancel arrived after the subprocess finished
    (the job ends "cancelled" and nothing is applied). A non-None return
    value becomes the job's result instead of the subprocess's.

    on_finish, if given, is called as on_finish(job_id) exactly once for a
    call that returned True, whatever the outcome: for removing the run's
    temp files, which a killed subprocess cannot do itself. For a job that
    ran, the watcher thread calls it last, after the subprocess ended and
    the GPU slot was released (done, error, cancelled, cleared). For a job
    still waiting in the GPU queue, whichever call ends it calls it before
    returning: request_cancel/cancel_queued, clear_job/clear_all_jobs, or
    the promotion whose start of it failed. Never called when this
    function returns False or raises: the caller still owns its cleanup
    then. Never under the job lock; its errors are logged, not raised. A
    clean stop (shutdown_service.wait_for_jobs) waits for the watcher's
    call within its grace time; a process killed hard skips it (the
    startup sweep, storage.sweep_stale_temp, removes a library temp folder
    left behind once it is a day old).

    kill_whole_tree=True: a cancel kills the subprocess and everything it
    started (kill_tree) at once, instead of terminate-then-kill on the
    subprocess alone. On POSIX the target must call
    start_own_process_group() first so its children share its group.

    start_method: a multiprocessing start method ("spawn") for this job's
    process and queue instead of the platform default. A job that may use
    CUDA passes "spawn" on Linux too, where the default forks: a forked
    child of a process that has already initialised CUDA (the Diagnostics
    GPU check, an in-process GPU job) cannot use it. Under spawn, target and
    args must pickle and target's module is imported fresh in the child.

    on_done/on_finish/kill_whole_tree/start_method are carried through the
    GPU queue like target/args. Records its starter and refuses during a clean stop like
    start_job().
    """
    owner_user_id = _acting_user_id()
    with _lock:
        _refuse_if_stopping_locked()
        if _exclusive_label is not None:
            return False
        existing = _jobs.get(job_id)
        if existing and existing["status"] in ("running", "queued"):
            return False
        if gpu_touching and get_gpu_limit_enabled() and (
                _gpu_queue_waiting_locked() or not _gpu_slot_available_locked(job_id, description)):
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
                                "on_done": on_done, "on_finish": on_finish,
                                "kill_whole_tree": kill_whole_tree, "start_method": start_method})
            _note_gpu_wait_reason_locked(job_id)
            return True
        proc, result_queue = _register_process_job(job_id, target, args, gpu_touching, description,
                                                   owner_user_id, start_method=start_method)
        run = _jobs[job_id]
    try:
        proc.start()
        _start_job_thread(_process_watcher, f"job-watcher:{job_id}",
                          job_id, proc, result_queue, gpu_touching, on_done=on_done,
                          on_finish=on_finish, kill_whole_tree=kill_whole_tree, run=run,
                          worker_for=job_id)
    except Exception as exc:
        _fail_start(job_id, gpu_touching, exc, proc, result_queue, run=run)
        raise
    return True


def _register_process_job(job_id, target, args, gpu_touching, description, owner_user_id=None,
                          start_method=None):
    """Caller must already hold _lock. Builds the Process and its result
    queue and records the job dict entry, but doesn't call proc.start()
    itself -- constructing a Process is cheap, but actually starting one
    (forking/spawning a real OS process) shouldn't happen while holding
    _lock, so callers start it themselves right after releasing the
    lock. Returns (proc, result_queue) for that. start_method None keeps
    the platform's default context."""
    mp = multiprocessing.get_context(start_method) if start_method else multiprocessing
    result_queue = mp.Queue()
    proc = mp.Process(target=target, args=(*args, result_queue), daemon=True)
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
    tuple protocol is unchanged. Ends a worker whose parent is gone (see
    exit_if_parent_gone)."""
    exit_if_parent_gone()
    try:
        result_queue.put(("progress", frac, message))
    except Exception:
        pass


def report_stage(result_queue, message: str, frac: float = 0.0):
    """For a process-job worker: starts a stage that cannot report progress
    (a model download/load). The parent shows it with a stage_ticker
    (elapsed time, the no-progress note, the longer stall allowance) until
    the worker's next progress or stage item; an empty message just ends
    the stage. Best effort, like report_progress."""
    exit_if_parent_gone()
    try:
        result_queue.put(("stage", frac, message))
    except Exception:
        pass


# Set in a worker by start_own_process_group: the process that started it
# (multiprocessing.parent_process(), None outside a multiprocessing child)
# and its pid, so the worker can tell that it has been orphaned.
_worker_parent = None
_worker_parent_pid = None
# How often the worker's watchdog thread looks for a dead parent: an
# orphaned worker (its server killed) would otherwise keep running, and
# keep its GPU memory, with nobody left to read its result.
PARENT_CHECK_INTERVAL = 2.0


def start_own_process_group():
    """For a kill_whole_tree=True process-job worker, called first: on POSIX the
    worker leads a new process group, so a cancel's kill_tree also reaches
    the processes it starts (ffmpeg, ffprobe). A no-op on Windows, where
    kill_tree walks the process tree instead. Never raises.

    Leaving the parent's process group also takes the worker out of reach
    of a terminal's hang-up or Ctrl+C, so when the parent dies without
    cancelling it (killed, out of memory, terminal closed) nothing else
    would stop it. The worker ends itself once its parent is gone
    (exit_if_parent_gone): at every progress or stage report and from a
    watchdog thread, which also covers a long call that reports nothing.
    The parent comes from multiprocessing.parent_process(), which the
    child gets at start-up, so a parent killed during the worker's spawn
    start-up and imports is still noticed; os.getppid() read here would
    already be the reaper then. Not PR_SET_PDEATHSIG: Linux sends that when
    the parent *thread* that started the worker exits, and a queued job is
    started from another job's watcher thread, which ends right after."""
    global _worker_parent, _worker_parent_pid
    try:
        parent = multiprocessing.parent_process()
        _worker_parent = parent
        _worker_parent_pid = parent.pid if parent is not None else os.getppid()
        threading.Thread(target=_parent_watchdog, daemon=True, name="parent-watchdog").start()
    except Exception:
        pass
    if os.name == "nt":
        return
    try:
        os.setsid()
    except Exception:
        pass


def exit_if_parent_gone():
    """In a worker that called start_own_process_group: when its parent has
    died, kills the worker's own process group (itself and what it started)
    and exits. A no-op anywhere else. Never raises: report_progress and
    report_stage call it outside their own try."""
    if not _parent_gone():
        return
    if os.name != "nt":
        try:
            if os.getpgid(0) == os.getpid():
                import signal
                os.killpg(0, signal.SIGKILL)
        except Exception:
            pass
    os._exit(1)


def _parent_gone() -> bool:
    """The parent's sentinel (parent_process().is_alive()) when there is
    one, and on POSIX also a changed os.getppid() (re-parented to a reaper).
    Not getppid on Windows, where it never changes. Errors read as alive."""
    if _worker_parent_pid is None:
        return False
    try:
        if _worker_parent is not None and not _worker_parent.is_alive():
            return True
    except Exception:
        pass
    if os.name == "nt":
        return False
    try:
        return os.getppid() != _worker_parent_pid
    except Exception:
        return False


def _parent_watchdog():
    while _worker_parent_pid is not None:
        time.sleep(PARENT_CHECK_INTERVAL)
        exit_if_parent_gone()


def _apply_progress_item(job_id, item, stage=None) -> bool:
    """True if `item` was a ("progress", frac, message) or ("stage", frac,
    message) tuple (applied to the job, with the message secret-redacted);
    False for anything else, which is the worker's final result tuple.
    `stage` holds the watcher's running stage_ticker under "ticker": any
    progress or stage item stops it, a stage item with a message starts a
    new one."""
    if not (isinstance(item, tuple) and item and item[0] in ("progress", "stage")):
        return False
    try:
        kind, frac, message = item
        from translate_engines import redact_secrets
        message = redact_secrets(str(message or ""))
        if stage is not None and stage.get("ticker") is not None:
            stage.pop("ticker").stop()
        if kind == "progress":
            update_progress(job_id, float(frac), message)
        elif message and stage is not None:
            stage["ticker"] = stage_ticker(job_id, message, frac=float(frac)).start()
    except Exception:
        pass   # malformed progress must never kill the watcher
    return True


CANCELLED_MESSAGE = "Cancelled."


def _mark_cancelled_locked(job_id):
    """Caller holds _lock. A process job's subprocess was stopped (or its
    result dropped) because of a cancel: the record stops saying
    "Cancelling..."."""
    if _still_running_locked(job_id):
        job = _jobs[job_id]
        job["status"] = "cancelled"
        job["finished_at"] = time.time()
        if job.get("cancel_requested"):
            job["message"] = CANCELLED_MESSAGE
        _mirror_locked(job_id)


def _process_watcher(job_id, proc, result_queue, gpu_touching=False, poll_interval=0.3,
                     on_done=None, on_finish=None, kill_whole_tree=False, run=None):
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
    _timing = _timing_start(job_id, thread_job=False)
    stage = {}
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
                if kill_whole_tree:
                    kill_tree(proc)
                else:
                    _stop_process(proc)
                with _lock:
                    _mark_cancelled_locked(job_id)
                logger.info(f"job {job_id} {'cleared' if was_cleared else 'cancelled'} "
                           f"(subprocess terminated)")
                return
            # Drain the queue continuously while the process is
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
                if _apply_progress_item(job_id, item, stage):
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
            if not _apply_progress_item(job_id, item, stage):
                outcome = item
        if stage.get("ticker") is not None:
            stage.pop("ticker").stop()
        hook_error = None
        result = outcome[1] if outcome and outcome[0] == "ok" else None
        if outcome and outcome[0] == "ok" and on_done is not None:
            with _lock:
                if job_id not in _jobs:
                    return
            # A cancel that arrived while the subprocess was finishing: the
            # job applies nothing.
            if is_cancel_requested(job_id):
                with _lock:
                    _mark_cancelled_locked(job_id)
                logger.info(f"job {job_id} cancelled (after its subprocess finished)")
                return
            # Outside the lock: the hook does real DB/file work.
            try:
                returned = on_done(job_id, outcome[1])
                if returned is not None:
                    result = returned
            except Exception as exc:
                from translate_engines import redact_secrets
                hook_error = redact_secrets(f"{type(exc).__name__}: {exc}")
                logger.error(f"job {job_id} on_done hook failed: {hook_error}",
                             exc_info=True)
        with _lock:
            if not _still_running_locked(job_id):
                return
            if hook_error is not None:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = f"Completion hook failed: {hook_error}"
                _jobs[job_id]["finished_at"] = time.time()
            elif outcome and outcome[0] == "ok":
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["progress"] = 1.0
                _jobs[job_id]["result"] = result
                _jobs[job_id]["finished_at"] = time.time()
                logger.info(f"job {job_id} finished")
            elif outcome and outcome[0] == "error":
                _, exc_type, msg = outcome
                # The worker's message can carry a key (a provider's error
                # echoing it back): redact before storing or logging.
                from translate_engines import redact_secrets
                error_msg = redact_secrets(f"{exc_type}: {msg}")
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = error_msg
                _jobs[job_id]["finished_at"] = time.time()
                logger.error(f"job {job_id} failed: {error_msg}")
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
            _owner = _jobs[job_id].get("owner_user_id")
        _notify_job_finished(_description, _final_status, job_id=job_id, owner_user_id=_owner)
    except Exception as exc:
        # A complete message that fails to unpickle, a broken queue or any
        # other watcher failure: without this the job stays "running"
        # forever. Known limit, not handled: a child killed partway through
        # writing a large result can leave Queue.get blocked on the rest of
        # that message, so the watcher never gets here.
        from translate_engines import redact_secrets
        error_msg = redact_secrets(f"{type(exc).__name__}: {exc}")
        logger.error(f"job {job_id} watcher failed: {error_msg}")
        try:
            _stop_process(proc)
        except Exception:
            pass
        with _lock:
            job = _jobs.get(job_id)
            if job is not None and job.get("status") == "running":
                job["status"] = "error"
                job["error"] = f"Lost contact with the job's process: {error_msg}"
                job["finished_at"] = time.time()
                _mirror_locked(job_id)
    finally:
        if stage.get("ticker") is not None:
            stage.pop("ticker").stop()
        # Reap the child (no zombie) and close the queue's pipe fds.
        try:
            proc.join(timeout=5)
            # A worker that sent its result but has not exited (stuck in
            # interpreter or CUDA teardown, or waiting on a child it started)
            # still holds VRAM and its temp files: end it before the GPU
            # slot and the finish hook are released. On POSIX its group is
            # killed even once the worker itself is gone (stopped after a
            # watcher failure, or crashed): an ffmpeg it started may still
            # be writing into the folder on_finish removes.
            if kill_whole_tree and os.name != "nt":
                _kill_worker_group(proc)
            if kill_whole_tree and proc.is_alive():
                kill_tree(proc)
                proc.join(timeout=5)
        except Exception:
            pass
        try:
            result_queue.close()
        except Exception:
            pass
        _timing_finish(job_id, _timing, thread_job=False)
        _release_gpu_slot(job_id, gpu_touching, run)
        try:
            _promote_next_queued_gpu_job()
        finally:
            # Last: removing a large temp folder must not hold the GPU slot.
            if on_finish is not None:
                _run_finish_hooks([(job_id, on_finish)])


# A running job whose stage reports progress, but has not reported any for
# this long, is flagged "may be stalled" (state unchanged). Stages that
# cannot report progress (model download/load) get the longer allowance.
JOB_STALL_SECONDS = 5 * 60
JOB_STALL_NO_PROGRESS_SECONDS = 15 * 60


def update_progress(job_id: str, frac: float, message: str = ""):
    """Called FROM inside the background thread to report progress.
    Silently does nothing if the job was cleared (e.g. by a reset) out
    from under it, rather than raising into a background thread.

    Marks the stage as one that reports progress (stage_ticker undoes that
    for a stage that cannot)."""
    _gpu_touching = False
    with _lock:
        if job_id in _jobs:
            now = time.time()
            job = _jobs[job_id]
            job["progress"] = frac
            job["progress_at"] = now
            job["can_report_progress"] = True
            # Once cancel is asked, the job keeps saying so until it stops.
            if message and not job.get("cancel_requested"):
                job["message"] = message
            _gpu_touching = bool(_jobs[job_id].get("gpu_touching"))
            _emit_change(job_id)
    if _gpu_touching:
        # Refreshes this job's cross-process GPU lock (see
        # _gpu_slot_available_locked) so a long-running job's own regular
        # progress updates keep it from looking abandoned to another
        # process before it's actually done. Best-effort, same reasoning
        # as _gpu_slot_available_locked -- never raises into a job thread.
        try:
            import db
            db.heartbeat_gpu_lock(f"ui:{job_id}")
        except Exception:
            pass


def job_may_be_stalled(job: dict, now: float = None) -> bool:
    """True for a running job (a get_status snapshot) that has reported no
    progress for JOB_STALL_SECONDS (JOB_STALL_NO_PROGRESS_SECONDS in a stage
    that cannot report any). Advisory only: it never changes the job's state."""
    if not job or job.get("status") != "running":
        return False
    last = job.get("progress_at") or job.get("started_at")
    if not last:
        return False
    limit = JOB_STALL_SECONDS if job.get("can_report_progress", True) else JOB_STALL_NO_PROGRESS_SECONDS
    return (time.time() if now is None else now) - last > limit


class stage_ticker:
    """Context manager for a stage that cannot report progress (model
    download/load): sets `message` plus a plain "no progress available" note
    and keeps the elapsed time in it fresh every `interval` seconds, so the
    status shows the job is alive. The ticks do not count as progress, so the
    job still gets flagged by job_may_be_stalled if the stage hangs."""

    NOTE = "This stage can take several minutes; no progress is available."

    def __init__(self, job_id: str, message: str, frac: float = 0.0, interval: float = 5.0):
        self.job_id, self.message, self.frac, self.interval = job_id, message, frac, interval
        self._stop = threading.Event()
        self._thread = None
        self._t0 = time.time()

    def _text(self) -> str:
        secs = int(time.time() - self._t0)
        elapsed = f"{secs // 60}m {secs % 60:02d}s" if secs >= 60 else f"{secs}s"
        return f"{self.message.rstrip('. ')} (elapsed {elapsed}). {self.NOTE}"

    def _tick(self):
        while not self._stop.wait(self.interval):
            with _lock:
                job = _jobs.get(self.job_id)
                if (job is not None and job.get("status") == "running"
                        and not job.get("cancel_requested")):
                    job["message"] = self._text()
                    _emit_change(self.job_id)

    def start(self):
        if self._thread is None:
            update_progress(self.job_id, self.frac, self._text())
            with _lock:
                if self.job_id in _jobs:
                    _jobs[self.job_id]["can_report_progress"] = False
            self._thread = threading.Thread(target=self._tick, daemon=True,
                                            name=f"stage-ticker:{self.job_id}")
            self._thread.start()
        return self

    def stop(self):
        """Idempotent."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


def set_result(job_id: str, result, mirror: bool = False):
    """Stores an arbitrary result payload on a job (e.g. the list of
    per-batch errors from a translation run), for the caller to read
    once via get_status(job_id)["result"] after the job finishes.
    mirror=True also writes it to job_records at once, for a result that
    other pollers (GET /api/jobs/{id}) must see while the job still runs."""
    # mirror defaults off: the status-transition mirrors (finish included)
    # already carry the result, so an extra SQLite write from the job thread
    # is only worth it for a result other processes need mid-run.
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["result"] = result
            if mirror:
                _mirror_locked(job_id)
            else:
                _emit_change(job_id)


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
# Each writes only its own fields by permanent line id, so they
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


# Every job id this app starts that's scoped to one
# drama -- for "is anything still working on this drama?" checks before a
# destructive, whole-drama action (deleting it) rather than the narrower
# LINE_WRITING_JOB_PREFIXES above, which only covers jobs safe to run
# alongside each other.
DRAMA_JOB_PREFIXES = LINE_WRITING_JOB_PREFIXES + (
    "transcribe_", "consistency_", "emotion_", "notes_", "resegment_",
    "dub_", "resplit_", "autotune_", "sensevoice_", "diarize_", "narration_", "ocrchapter_",
    "audiobook_", "burned_video_", "softsub_video_", "dubbed_video_", "bulk_translate_", "novel_glossary_", "extract_audio_",
    "sourceimport_", "urlmedia_", "voiceref_", "lines_glossary_", "burnpreview_",
    "bulk_consistency_", "bulk_emotion_", "bulk_notes_", "bulk_flag_", "resegpreview_", "scanlate_",
    "lncrawl_",
    "notion_export_",
)


def active_job_ids() -> list:
    """Ids of this process's jobs that are running or queued, for a
    clean shutdown that cancels them all (services/shutdown_service.py)."""
    with _lock:
        return sorted(jid for jid, job in _jobs.items()
                      if job["status"] in ("running", "queued"))


def count_active_jobs(prefix: str) -> int:
    """How many jobs whose id starts with `prefix` are running or queued,
    for a server-wide cap on one kind of job."""
    with _lock:
        return sum(1 for jid, job in _jobs.items()
                   if jid.startswith(prefix) and job["status"] in ("running", "queued"))


def any_job_running_for_drama(drama_id, exclude_job_id=None) -> bool:
    """True if any job scoped to this drama is currently running or
    queued -- for warning before a destructive, whole-drama action (e.g.
    deleting it) rather than letting that job error out against a drama
    that no longer exists. exclude_job_id: a job checking for others."""
    with _lock:
        for prefix in DRAMA_JOB_PREFIXES:
            if f"{prefix}{drama_id}" == exclude_job_id:
                continue
            job = _jobs.get(f"{prefix}{drama_id}")
            if job and job["status"] in ("running", "queued"):
                return True
        return False


CANCELLING_MESSAGE = "Cancelling..."
# A queued/running job_records row whose owner process is gone (closed by
# services/jobs_service.py's sweep, at startup and on every job list).
INTERRUPTED_MESSAGE = "Interrupted: Baihe restarted while this was running."


def owner_process_alive(pid) -> bool:
    """True if a process with this pid exists (a job_records row's owner).
    Errs towards alive: a pid it can't check is treated as running, so a
    live owner's job is never closed under it."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if os.name == "nt":
        # os.kill(pid, 0) would end the process on Windows.
        try:
            import ctypes
            from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.OpenProcess.restype = wintypes.HANDLE
            k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            k.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() == 5      # ERROR_ACCESS_DENIED: it exists
            try:
                code = wintypes.DWORD()
                if not k.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
                return code.value == 259                 # STILL_ACTIVE
            finally:
                k.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except Exception:
        return True   # PermissionError: it exists, owned by someone else
    return True


def request_cancel(job_id: str):
    """Sets the cancellation flag. For a thread-based job (start_job()),
    this is purely cooperative -- the job itself has to check
    is_cancel_requested() between units of work, since a thread can't be
    forcibly killed. For a process-based job (start_process_job()), Step
    4d's own _process_watcher notices this flag and actually calls
    proc.terminate() -- a real, non-cooperative stop, since that's the
    whole reason those jobs run in their own OS process instead of a
    thread in the first place (no cooperative checkpoint to hook into).

    A job still waiting in the GPU queue has nothing to stop: it ends
    "cancelled" at once and never starts (a process job's on_finish runs
    before this returns). A running job's message becomes
    CANCELLING_MESSAGE (mirrored) until it stops, so a worker blocked in a
    call with no cancel point (a model load) shows the cancel was heard."""
    hooks = []
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["cancel_requested"] = True
        if job.get("status") == "queued":
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
            hooks = _take_queued_locked(job_id)
            _mirror_locked(job_id)
            _refresh_queue_messages_locked()
        elif job.get("status") == "running" and job.get("message") != CANCELLING_MESSAGE:
            job["message"] = CANCELLING_MESSAGE
            _mirror_locked(job_id)
    _run_finish_hooks(hooks)


# A cancel from another process can take up to this long to be seen; the
# trade for not reading job_records on every call from a per-line loop.
_DB_CANCEL_CHECK_INTERVAL = 2.0
_last_db_cancel_check = {}
_db_cancel_check_failed = set()   # job ids whose check failure was already logged


def _db_cancel_requested(job_id: str) -> bool:
    """A cancel requested from another process (the API
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
    except Exception as exc:
        # Retried on the next call rather than after the interval, and
        # logged once per job: an unseen failure hides a cross-process Cancel.
        with _lock:
            if _last_db_cancel_check.get(job_id) == now:
                _last_db_cancel_check.pop(job_id, None)
            first = job_id not in _db_cancel_check_failed
            _db_cancel_check_failed.add(job_id)
        if first:
            _warn(f"job {job_id}: could not read a cancel request from job_records", exc)
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


def kill_tree(proc):
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
    except ProcessLookupError:
        pass   # the group already exited
    except Exception as exc:
        _warn(f"could not kill process tree {proc.pid}", exc)
    try:
        proc.kill()
    except Exception as exc:
        _warn(f"could not kill process {proc.pid}", exc)


def _kill_worker_group(proc):
    """POSIX: SIGKILLs the process group a kill_whole_tree worker leads
    (start_own_process_group), whether or not the worker is still alive.
    A worker that never made its group leaves no group with its pid, and
    the server's own group is never targeted."""
    import signal
    pid = getattr(proc, "pid", None)
    try:
        if pid is not None and pid != os.getpgrp():
            os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    except Exception as exc:
        _warn(f"could not kill process group {pid}", exc)


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
                kill_tree(proc)
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
        if not (job is None or job["status"] == "queued"):
            return False
        hooks = _clear_job_locked(job_id)
        _delete_job_record(job_id)
    _emit_change(job_id)
    _run_finish_hooks(hooks)
    return True


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
    promoted and started later out of nowhere (a queued process job's
    on_finish runs before this returns)."""
    with _lock:
        hooks = _clear_job_locked(job_id)
    _delete_job_record(job_id)
    _emit_change(job_id)
    _run_finish_hooks(hooks)


def _clear_job_locked(job_id) -> list:
    """Caller holds _lock: clear_job's in-memory part. Returns the finish
    hooks of the queue entries it dropped (see _take_queued_locked)."""
    _jobs.pop(job_id, None)
    _workers.pop(job_id, None)
    hooks = _take_queued_locked(job_id)
    _refresh_queue_messages_locked()
    return hooks


def _delete_job_record(job_id):
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
        _workers.clear()
        hooks = _take_queued_locked()
    try:
        import db
        db.clear_all_job_records()
    except Exception:
        import applog
        applog.get_logger().warning("failed to clear job_records", exc_info=True)
    _emit_change(None)
    _run_finish_hooks(hooks)


def list_running_jobs():
    with _lock:
        return {jid: dict(j) for jid, j in _jobs.items() if j["status"] == "running"}


def list_all_jobs():
    """Every job this process still has a record of -- running or
    finished, since a finished job's entry isn't cleared automatically
    (see clear_all_jobs/_jobs.pop). Backs the job-level "why was
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
