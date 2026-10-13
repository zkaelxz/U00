"""jobs/gpu_queue.py -- this process's wait line for GPU-touching jobs.

Decides whether a GPU job may start now (the setting, the parallel cap, free
VRAM and the cross-process slot in jobs/gpu_slots.py), keeps the FIFO of jobs
waiting, their "place in line" messages, and frees a finished job's slot.
Starting the next job in line is jobs/job_runner.py's.
"""

import gpu_probe
import job_force_stop
from jobs import user_caps
from jobs.job_settings import get_gpu_max_parallel
from jobs.job_table import _jobs, _lock, _mirror_locked, _warn


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


def _running_gpu_job_count_locked(exclude_job_id):
    """Caller must already hold _lock. How many other GPU-touching jobs in
    this process are running."""
    return job_force_stop.abandoned_gpu_count_locked(exclude_job_id) + sum(
        1 for jid, job in _jobs.items()
        if jid != exclude_job_id and job.get("gpu_touching") and job["status"] == "running")


def _gpu_queue_waiting_locked():
    """Caller must already hold _lock. True if a job that could start is
    still waiting in _gpu_queue: a new GPU job then queues behind it (first
    come, first served) rather than taking a slot that just opened. One held
    back by its starter's running cap doesn't count, or it would hold up
    everyone else's."""
    return any(_jobs.get(e["job_id"], {}).get("status") == "queued"
               and not user_caps.held(_jobs, e) for e in _gpu_queue)


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


def _note_gpu_wait_reason_locked(job_id):
    """Caller holds _lock. Records on the waiting job whether the GPU is held
    by another program rather than by a Baihe job, so the queued message can
    say why. Only looks at the driver when no Baihe job here is running."""
    job = _jobs.get(job_id)
    if job is None or job.get("status") != "queued":
        return
    import live_whisper
    reason = live_whisper.WAIT_MESSAGE if live_whisper.gpu_claim_held() else None
    if reason is None and _running_gpu_job_count_locked(job_id) == 0:
        try:
            from gpu_wait_message import external_gpu_wait_message
            load = gpu_probe.external_gpu_load()
            reason = external_gpu_wait_message(load) if load else None
            if reason is None:
                from jobs import gpu_slots
                reason = gpu_slots.previous_run_wait_message()
        except Exception as exc:
            _warn("GPU wait-reason check failed", exc)
    job["gpu_wait_external"] = reason
    _refresh_queue_messages_locked()


def _queued_in_order_locked() -> list:
    """Caller holds _lock: the job ids still waiting in _gpu_queue, next first."""
    return [e["job_id"] for e in _gpu_queue
            if _jobs.get(e["job_id"], {}).get("status") == "queued"]


def _refresh_queue_messages_locked():
    """Caller holds _lock: rewrites each queued job's message to its
    current place in _gpu_queue after the queue changed."""
    for position, job_id in enumerate(_queued_in_order_locked(), start=1):
        job = _jobs[job_id]
        message = (job.get("gpu_wait_external") if position == 1 else None) or _queue_message(position)
        if job.get("message") != message:
            job["message"] = message
            _mirror_locked(job_id)


def gpu_queue_order() -> list:
    """[(job_id, owner_user_id)] waiting for the GPU here, next first."""
    with _lock:
        return [(jid, _jobs[jid].get("owner_user_id")) for jid in _queued_in_order_locked()]


def _gpu_slot_available_locked(job_id, description):
    """Caller must already hold _lock. True if job_id may actually start
    running right now -- nothing else, in this process, another one
    (cross-process), or a completely different application, currently
    holds the GPU. This module's own guard is in-process state, invisible
    to a `cli.py` run, so jobs/gpu_slots.py is the cross-process coordination point.

    Both of those locks only know about GPU-touching work Baihe
    itself started -- neither can see a different application on the same
    machine using the same physical GPU (Jellyfin's hardware-accelerated
    transcoding on the same card is the motivating case). nvidia-smi's own
    utilization/free-VRAM numbers (diagnostics_torch.external_gpu_is_busy) are
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
    that many in total; see gpu_slots.acquire for the free-VRAM guardrail.

    No nvidia-smi (or a failing external check) is ignored for the first
    job, and means one at a time beyond it. A failing cross-process lock
    is not ignored: the job queues (False) until the lock can be taken,
    since starting anyway could share the GPU with a CLI run."""
    if _running_gpu_job_count_locked(job_id) >= get_gpu_max_parallel():
        return False
    try:
        from jobs import gpu_slots
        if gpu_slots.acquire(f"ui:{job_id}", description, job_id=job_id, check_external_load=True):
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
        from jobs import gpu_slots
        with _lock:
            current = _jobs.get(job_id)
            if (run is not None and current is not None and current is not run
                    and current.get("status") == "running"):
                return
            gpu_slots.release(f"ui:{job_id}")
    except Exception:
        pass
