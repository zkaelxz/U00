"""jobs/job_launch.py -- starting a job, and the holds that refuse a start.

start_job (a thread) and start_process_job (a child process) register the
job, apply the per-user caps and the GPU guard, and either start it or queue
it. A start is refused while a library restore holds the exclusive lock,
while its title is being deleted, cleaned up (hold_title) or restructured,
or once the server has begun its clean stop.
"""

import contextlib

import db
import gpu_probe
import job_force_stop
from jobs import crash_retry, user_caps
from jobs.gpu_queue import (_gpu_queue, _gpu_queue_waiting_locked,
                            _gpu_slot_available_locked, _note_gpu_wait_reason_locked, _release_gpu_slot)
from jobs.job_runner import (_fail_start, _new_entry, _process_watcher, _register_process_job, _spawn,
                             _start_job_thread)
from jobs.job_settings import get_gpu_limit_enabled
from jobs.job_table import _jobs, _lock, _mirror_locked
from lib import model_offline
from lib.errors import ConflictError


def _acting_user_id():
    from services import ownership_service
    return ownership_service.acting_user_id()


def _with_member_key(target):
    from services import member_key_service
    return member_key_service.for_job(target)


# Set while a library restore swaps the library folder: no job may start
# then (start_job/start_process_job return False, the same "not started"
# answer as a duplicate). See services/library_admin_service.restore_backup.
_exclusive_label = None


def acquire_exclusive(label: str, ignore_job_id: str = None) -> bool:
    """Atomically: refuse (False) if any job is running/queued in this
    process or another exclusive hold is active; otherwise take the hold,
    so no new job can start until release_exclusive(). `ignore_job_id` is
    the caller's own job, for a job that holds the library while it runs."""
    global _exclusive_label
    import live_whisper
    with _lock:
        if _exclusive_label is not None or _maintenance_count or any(
                j.get("status") in ("running", "queued")
                for jid, j in _jobs.items() if jid != ignore_job_id
        ) or live_whisper.outstanding_label() or job_force_stop.any_abandoned_alive_locked():
            return False
        _exclusive_label = label
        return True


def exclusive_held() -> bool:
    with _lock:
        return _exclusive_label is not None


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


def is_stopping() -> bool:
    return _stopping


def _refuse_if_stopping_locked(job_id):
    """Caller holds _lock."""
    job_force_stop.refuse_if_abandoned_locked(job_id)
    if _stopping:
        from lib.errors import ConflictError
        raise ConflictError(STOPPING_MESSAGE)


# Held on both titles while chapters move between them
# (services/title_restructure_service.py).
RESTRUCTURE_JOB_PREFIX = "restructure_"
_RESTRUCTURING_MESSAGE = ("Chapters are being moved to or from this title. Start this again "
                          "once that has finished.")


_HELD_MESSAGE = ("This title is being deleted or its files cleaned up. Start this again "
                 "once that has finished.")
_held_titles = {}   # drama id -> how many holds (hold_title)


class TitleBusy(ConflictError):
    """hold_title found a job of this process running or queued on the title."""


@contextlib.contextmanager
def hold_title(drama_id: int, busy_message: str):
    """Refuses every job start on drama_id while held. Raises TitleBusy
    (busy_message), taking no hold, when a job here already runs on the
    title: a hold then would refuse that job's own follow-up starts
    (transcribe starts diarize). Checked and taken under _lock, so no start
    slips in between; a job in another process is still the caller's check."""
    import background_jobs   # it imports this module
    drama_id = int(drama_id)
    with _lock:
        if background_jobs.any_job_running_for_drama(drama_id):
            raise TitleBusy(busy_message)
        _held_titles[drama_id] = _held_titles.get(drama_id, 0) + 1
    try:
        yield
    finally:
        with _lock:
            if _held_titles[drama_id] <= 1:
                del _held_titles[drama_id]
            else:
                _held_titles[drama_id] -= 1


def _refuse_if_restructuring_locked(job_id):
    """Caller holds _lock. A job on a title whose pages or chapter text are
    moving, or that is being deleted or cleaned up, could write onto rows or
    files that now belong to another title or are gone."""
    import background_jobs   # it imports this module
    job_id = str(job_id)
    for prefix in background_jobs.DRAMA_JOB_PREFIXES:
        rest = job_id[len(prefix):]
        if job_id.startswith(prefix) and rest.isdigit():
            if int(rest) in _held_titles:
                raise ConflictError(_HELD_MESSAGE)
            hold = _jobs.get(f"{RESTRUCTURE_JOB_PREFIX}{rest}")
            if (prefix != RESTRUCTURE_JOB_PREFIX and hold
                    and hold["status"] in ("running", "queued")):
                raise ConflictError(_RESTRUCTURING_MESSAGE)


def _admit_locked(job_id, gpu_touching, description, owner_user_id):
    """Caller holds _lock. The admission start_job and start_process_job
    share: None when the start is refused (a restore holds the exclusive
    lock, or job_id is already running or queued), else the status the run
    starts in. "running" for a GPU job means its slot is already claimed
    (_gpu_slot_available_locked), so the caller must start it. Raises like
    the start functions (clean stop, per-user caps)."""
    _refuse_if_stopping_locked(job_id)
    _refuse_if_restructuring_locked(job_id)
    if _exclusive_label is not None:
        return None
    existing = _jobs.get(job_id)
    if existing and existing["status"] in ("running", "queued"):
        return None
    queued = gpu_touching and get_gpu_limit_enabled() and (
        user_caps.wait_for_cap(_jobs, owner_user_id) or _gpu_queue_waiting_locked()
        or not _gpu_slot_available_locked(job_id, description))
    user_caps.refuse_over_cap(_jobs, owner_user_id, queueing=bool(queued))
    return "queued" if queued else "running"


def start_job(job_id: str, target, *args, gpu_touching: bool = False,
              description: str = None, run_settings: dict = None, line_fields=None,
              **kwargs) -> bool:
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
    run_settings: this run's settings, kept on the job's own entry so no
    other run under the same id can lend its own. Not passed to target.
    line_fields: the line columns the job writes -- a tuple (empty for a
    job that writes no lines) or db.FULL_SYNC for one that replaces a
    title's lines. Its saves are refused any other column
    (db.job_line_fields); every call site declares it
    (tests/test_static_analysis.py). Not passed to target.

    The job records who started it (for per-user caps and ownership): the user id of the API
    request this runs in (ownership_service.acting_user_id), or None for
    the PC owner, auth off, and jobs started by jobs.

    Raises ConflictError once the server's clean stop has begun
    (refuse_new_jobs), and RateLimitedError when the starter is over a
    per-user cap (jobs/user_caps.py). A GPU job whose starter is at the
    running cap queues instead, and starts once one of their jobs ends.
    """
    line_fields = db.check_line_fields(line_fields)
    owner_user_id = _acting_user_id()
    target = _with_member_key(target)
    if model_offline.active():
        target = model_offline.for_thread(target)
    if gpu_touching and get_gpu_limit_enabled():
        gpu_probe.prefetch()
    with _lock:
        status = _admit_locked(job_id, gpu_touching, description, owner_user_id)
        if status is None:
            return False
        _jobs[job_id] = _new_entry(status, "thread", gpu_touching, description, owner_user_id,
                                   run_settings, line_fields=line_fields)
        _mirror_locked(job_id)
        if status == "queued":
            _gpu_queue.append({"job_id": job_id, "target": target, "args": args,
                                "kwargs": kwargs, "description": description, "kind": "thread",
                                "cap_exempt": user_caps.refusing()})
            _note_gpu_wait_reason_locked(job_id)
            return True
        run = _jobs[job_id]

    try:
        _spawn(job_id, target, args, kwargs, gpu_touching=gpu_touching, run=run)
    except Exception as exc:
        _fail_start(job_id, gpu_touching, exc, run=run)
        raise
    return True


def _wrap_process_hooks(target, on_done, cpu_retry_args, start_method):
    """(target, on_done, the CPU retry plan); the CPU run starts the same
    wrapped target."""
    target = _with_member_key(target)
    on_done = on_done and _with_member_key(on_done)
    if model_offline.active():
        target = model_offline.for_process(target)
        # on_done may start the next stage's job (transcription -> speakers).
        on_done = on_done and model_offline.for_thread(on_done)
    return target, on_done, crash_retry.plan(target, cpu_retry_args, start_method)


def start_process_job(job_id: str, target, args: tuple = (), gpu_touching: bool = False,
                      description: str = None, on_done=None, on_finish=None,
                      kill_whole_tree: bool = False, start_method: str = None, run_settings: dict = None,
                      initial_result=None, line_fields=None, cpu_retry_args=None) -> bool:
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
    ("error", <exception type name>, <message>[, <traceback>]) -- see
    process_worker.error_item. It may also send any
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

    initial_result: the result shown while queued or running; on_done's
    return or the subprocess's result replaces it. line_fields: as
    start_job's; it limits what on_done writes.

    on_done/on_finish/kill_whole_tree/start_method/cpu_retry_args (one CPU run after a native
    crash: jobs/crash_retry.py) are carried through the GPU queue like target/args. Records its
    starter, applies the per-user caps and refuses during a clean stop like start_job().
    """
    line_fields = db.check_line_fields(line_fields)
    owner_user_id = _acting_user_id()
    target, on_done, cpu_retry = _wrap_process_hooks(target, on_done, cpu_retry_args, start_method)
    if gpu_touching and get_gpu_limit_enabled():
        gpu_probe.prefetch()
    with _lock:
        status = _admit_locked(job_id, gpu_touching, description, owner_user_id)
        if status is None:
            return False
        if status == "queued":
            _jobs[job_id] = _new_entry(status, "process", gpu_touching, description, owner_user_id,
                                       run_settings, result=initial_result, process=None,
                                       line_fields=line_fields)
            _mirror_locked(job_id)
            _gpu_queue.append({"job_id": job_id, "target": target, "args": args,
                                "kwargs": {}, "description": description, "kind": "process",
                                "on_done": on_done, "on_finish": on_finish,
                                "kill_whole_tree": kill_whole_tree, "start_method": start_method,
                                "cpu_retry": cpu_retry, "cap_exempt": user_caps.refusing()})
            _note_gpu_wait_reason_locked(job_id)
            return True
        try:
            proc, result_queue = _register_process_job(job_id, target, args, gpu_touching,
                                                       description, owner_user_id,
                                                       start_method=start_method,
                                                       run_settings=run_settings,
                                                       initial_result=initial_result,
                                                       line_fields=line_fields)
        except Exception:
            # mp.Queue()/Process() failed after the slot was taken; no run exists to free it.
            _release_gpu_slot(job_id, gpu_touching)
            raise
        run = _jobs[job_id]
    try:
        proc.start()
        _start_job_thread(_process_watcher, f"job-watcher:{job_id}",
                          job_id, proc, result_queue, gpu_touching, on_done=on_done,
                          on_finish=on_finish, kill_whole_tree=kill_whole_tree, run=run,
                          cpu_retry=cpu_retry, worker_for=job_id, worker_run=run)
    except Exception as exc:
        _fail_start(job_id, gpu_touching, exc, proc, result_queue, run=run)
        raise
    return True
