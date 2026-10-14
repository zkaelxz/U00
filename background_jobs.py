"""
background_jobs.py -- what a running job and the API call while jobs run:
progress, results, status, cancel and clearing. Starting a job is
jobs/job_launch.py's; running it and handling how it ends is
jobs/job_runner.py's. Long operations (translate, transcribe, dub,
exports...) run in background threads, so an HTTP request can start a job
and return at once. Work with no safe point to stop at (one opaque
diarization call) runs in a child process instead, so cancel can kill it.

State lives in the `_jobs` dict in jobs/job_table.py. That dict is the
authority for jobs this process owns; the API polls it through get_status().

Every status change is also mirrored, best-effort, to the `job_records`
table so other processes (and a restarted server) can see a job's last
known state. While this process has queued or running jobs, a daemon
heartbeat thread bumps their `updated_at` so a stale-record sweep
elsewhere can tell a quiet live job from one whose owner process died.
A failed write never breaks the job; jobs/job_store.py retries it.

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

import math
import os
import sqlite3
import threading
import time

import job_force_stop
from lib import pid as pid_lib
from job_process_kill import kill_tree
from jobs.gpu_queue import _refresh_queue_messages_locked, _run_finish_hooks, _take_queued_locked
from jobs.job_table import HEARTBEAT_INTERVAL, _emit_change, _jobs, _lock, _mirror_locked, _warn, _workers


class JobCancelled(Exception):
    """Raised by a thread job that noticed its cancel request and stopped;
    _spawn records the job as "cancelled" (never "done"/"error")."""


# A running job whose stage reports progress, but has not reported any for
# this long, is flagged "may be stalled" (state unchanged). Stages that
# cannot report progress (model download/load) get the longer allowance.
JOB_STALL_SECONDS = 5 * 60
JOB_STALL_NO_PROGRESS_SECONDS = 15 * 60


def _superseded_locked(job_id: str) -> bool:
    """Caller holds _lock. True in a run's worker thread once that run is no
    longer job_id's current one (it was cleared, and maybe a new run of the
    same id started): its progress and result must not land on that run."""
    me = threading.current_thread()
    if getattr(me, "baihe_job_id", None) != job_id:
        return False
    worker = _workers.get(job_id)
    return worker is None or worker[1] is not me or worker[0] is not _jobs.get(job_id)


def update_progress(job_id: str, frac: float, message: str = ""):
    """Called FROM inside the background thread to report progress.
    Silently does nothing if the job was cleared (e.g. by a reset) out
    from under it, rather than raising into a background thread.

    Marks the stage as one that reports progress (stage_ticker undoes that
    for a stage that cannot)."""
    _gpu_touching = False
    with _lock:
        if (job_id in _jobs and not job_force_stop.current_thread_abandoned()
                and not _superseded_locked(job_id)):
            now = time.time()
            job = _jobs[job_id]
            job["progress"] = frac
            job["progress_at"] = now
            job["can_report_progress"] = True
            # Once cancel is asked, the job keeps saying so until it stops.
            if message and not job.get("cancel_requested"):
                job["message"] = message
            # At most once per heartbeat interval: a per-line loop would
            # otherwise write the GPU row on every line.
            _gpu_touching = (bool(job.get("gpu_touching"))
                             and now - job.get("gpu_beat_at", 0) >= HEARTBEAT_INTERVAL)
            if _gpu_touching:
                job["gpu_beat_at"] = now
            _emit_change(job_id)
    if _gpu_touching:
        # Refreshes this job's cross-process GPU lock (see
        # _gpu_slot_available_locked) so a long-running job's own regular
        # progress updates keep it from looking abandoned to another
        # process before it's actually done, should the heartbeat thread
        # stall. Best-effort, same reasoning
        # as _gpu_slot_available_locked -- never raises into a job thread.
        try:
            from jobs import gpu_slots
            gpu_slots.heartbeat([f"ui:{job_id}"])
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
        self._run = None
        self._t0 = time.time()

    def _text(self) -> str:
        secs = int(time.time() - self._t0)
        elapsed = f"{secs // 60}m {secs % 60:02d}s" if secs >= 60 else f"{secs}s"
        return f"{self.message.rstrip('. ')} (elapsed {elapsed}). {self.NOTE}"

    def _tick(self):
        while not self._stop.wait(self.interval):
            with _lock:
                job = _jobs.get(self.job_id)
                if (job is not None and job is self._run and job.get("status") == "running"
                        and not job.get("cancel_requested")):
                    job["message"] = self._text()
                    _emit_change(self.job_id)

    def start(self):
        if self._thread is None:
            update_progress(self.job_id, self.frac, self._text())
            with _lock:
                if self.job_id in _jobs and not _superseded_locked(self.job_id):
                    self._run = _jobs[self.job_id]
                    self._run["can_report_progress"] = False
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
        if (job_id in _jobs and not job_force_stop.current_thread_abandoned()
                and not _superseded_locked(job_id)):
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
LINE_WRITING_JOB_PREFIXES = ("translate_", "flag_", "fixflag_", "retranscribe_", "timingchk_")


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
    "sourceimport_", "urlmedia_", "voiceref_", "lines_glossary_", "spelling_suggest_", "burnpreview_",
    "bulk_consistency_", "bulk_emotion_", "bulk_notes_", "bulk_flag_", "resegpreview_", "scanlate_",
    "lncrawl_",
    "comparetx_", "retime_", "speechcov_", "restructure_",
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
            if (job and job["status"] in ("running", "queued")
                    ) or job_force_stop.abandoned_alive_locked(f"{prefix}{drama_id}"):
                return True
        return False


# A model load or one inference call can't be interrupted, so the job stops at
# its next cancel check rather than at once.
CANCELLING_MESSAGE = "Cancelling... (finishes the current step first)"
# A queued/running job_records row whose owner process is gone (closed by
# services/jobs_service.py's sweep, at startup and on every job list).
INTERRUPTED_MESSAGE = "Interrupted: Baihe restarted while this was running."
# A job the clean stop itself cancelled (Stop Baihe, closing its window).
STOPPED_MESSAGE = "Interrupted: Baihe was stopped while this was running."


def owner_process_alive(pid) -> bool:
    """True if a process with this pid exists (a job_records row's owner).
    Errs towards alive: a pid it can't check is treated as running, so a
    live owner's job is never closed under it."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if not 0 < pid <= pid_lib.MAX_PID:
        return True     # not a pid at all; a record we cannot judge is never closed under its owner
    if os.name != "nt" and pid > 0x7FFFFFFF:
        return True     # past POSIX pid_t: os.kill cannot probe it, so it cannot be judged either
    try:
        return pid_lib.pid_alive(pid)
    except Exception:
        return True


def _note_cancel_locked(job, requested_at=None) -> None:
    """Caller holds _lock: sets `job`'s cancel flag. The first cancel decides
    whether the run was stopped by someone or by Baihe's own clean stop."""
    from jobs import job_launch
    if not job.get("cancel_requested") and job_launch.is_stopping():
        job["cancelled_by_stop"] = True
    job["cancel_requested"] = True
    job["cancel_count"] = job.get("cancel_count", 0) + 1
    job.setdefault("cancel_requested_at", requested_at or time.time())


def _interrupted_by_stop_locked(job) -> None:
    """Caller holds _lock and has just ended `job` "cancelled". A run the
    clean stop cancelled reads as interrupted (outcome "interrupted"), the
    same as a row the next start closes for a server that died: nobody
    pressed Cancel. Set only now: job_store rewrites detail_state while a
    job is active."""
    if job.get("cancelled_by_stop"):
        job["detail_state"] = "interrupted"
        job["error"] = STOPPED_MESSAGE
        job["message"] = STOPPED_MESSAGE


def _cancel_queued_locked(job_id, job, requested_at=None) -> list:
    """Caller holds _lock and has seen `job` still queued: it ends
    "cancelled" without ever starting, and its record stays so the Jobs
    page lists the cancelled run. Returns the finish hooks to run once
    _lock is released."""
    _note_cancel_locked(job, requested_at)
    job["status"] = "cancelled"
    job["finished_at"] = time.time()
    _interrupted_by_stop_locked(job)
    hooks = _take_queued_locked(job_id)
    _mirror_locked(job_id)
    _refresh_queue_messages_locked()
    return hooks


_ANY_OWNER = object()


def request_cancel(job_id: str, requested_at=None, owner_user_id=_ANY_OWNER) -> bool:
    """Sets the cancellation flag. For a thread-based job (start_job()),
    this is purely cooperative -- the job itself has to check
    is_cancel_requested() between units of work, since a thread can't be
    forcibly killed. For a process-based job (start_process_job()), the
    _process_watcher notices this flag and actually calls
    proc.terminate() -- a real, non-cooperative stop, since that's the
    whole reason those jobs run in their own OS process instead of a
    thread in the first place (no cooperative checkpoint to hook into).

    A job still waiting in the GPU queue has nothing to stop: it ends
    "cancelled" at once and never starts (a process job's on_finish runs
    before this returns). A running job's message becomes
    CANCELLING_MESSAGE (mirrored) until it stops, so a worker blocked in a
    call with no cancel point (a model load) shows the cancel was heard.
    With `owner_user_id`, False (nothing done) for a queued or running run
    someone else started (see jobs_service.cancel_job)."""
    hooks = []
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return True
        if owner_user_id is not _ANY_OWNER and job.get("status") in ("queued", "running") \
                and job.get("owner_user_id") != owner_user_id:
            return False
        if job.get("status") == "queued":
            hooks = _cancel_queued_locked(job_id, job, requested_at)
        else:
            _note_cancel_locked(job, requested_at)
        if job.get("status") == "running" and job.get("message") != CANCELLING_MESSAGE:
            job["message"] = CANCELLING_MESSAGE
            _mirror_locked(job_id)
    _run_finish_hooks(hooks)
    return True


# A cancel from another process can take up to this long to be seen; the
# trade for not reading job_records on every call from a per-line loop.
_DB_CANCEL_CHECK_INTERVAL = 2.0
# Both keyed by job id and tagged with the run's own `_jobs` dict: an ending
# run forgets its state after its status is final, by which time a new run
# of the same id may have started and checked.
_last_db_cancel_check = {}       # job id -> (run, monotonic time of its last read)
_db_cancel_check_failed = {}     # job id -> the run whose check failure was already logged


def _forget_cancel_check(job_id: str, run=None) -> None:
    """A finished run's throttle time and logged-once mark must not carry into
    the next run of the same id, or its failures would never be logged.
    `run`: the ending run's dict; a newer run's state is left alone."""
    with _lock:
        if run is None:
            run = _jobs.get(job_id)
        if (_last_db_cancel_check.get(job_id) or (None,))[0] is run:
            _last_db_cancel_check.pop(job_id, None)
        if _db_cancel_check_failed.get(job_id) is run:
            _db_cancel_check_failed.pop(job_id, None)


def _db_cancel_requested(job_id: str) -> bool:
    """A cancel requested from another process (the API host) lives in
    job_records. Checked at most once per _DB_CANCEL_CHECK_INTERVAL seconds
    per run so a tight job loop doesn't hit SQLite every iteration; on a hit
    the in-memory flag and the requester's time are set. Only for queued/
    running jobs this process owns. A DB error means "not requested"."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.get("status") not in ("queued", "running"):
            return False
        if job.get("cancel_requested"):
            return True
        now = time.monotonic()
        last_run, last = _last_db_cancel_check.get(job_id, (None, -1e9))
        if last_run is job and now - last < _DB_CANCEL_CHECK_INTERVAL:
            return False
        mark = (job, now)
        _last_db_cancel_check[job_id] = mark
    try:
        from jobs import job_store
        requested = job_store.row_cancel_time(job_id)
    except Exception as exc:
        # Retried on the next call rather than after the interval, and
        # logged once per run: an unseen failure hides a cross-process Cancel.
        with _lock:
            if _last_db_cancel_check.get(job_id) is mark:
                _last_db_cancel_check.pop(job_id, None)
            first = _db_cancel_check_failed.get(job_id) is not job
            _db_cancel_check_failed[job_id] = job
        if first:
            _warn(f"job {job_id}: could not read a cancel request from job_records", exc)
        return False
    if requested:
        request_cancel(job_id, requested)
    return bool(requested)


def is_cancel_requested(job_id: str) -> bool:
    if job_force_stop.current_thread_abandoned():
        return True   # lets the abandoned worker wind down at its next check
    with _lock:
        job = _jobs.get(job_id)
        if job and job.get("cancel_requested"):
            return True
    return _db_cancel_requested(job_id)


def _cancel_count(job_id: str) -> int:
    with _lock:
        return (_jobs.get(job_id) or {}).get("cancel_count", 0)


def later_cancel_probe(job_id: str):
    """A callable that is true once the job is asked to cancel after this
    call. The cancel flag is sticky, so work a Cancel itself started (putting
    packages back after a cancelled install) would otherwise stop at once and
    could not hear a second Cancel; before any cancel it is
    is_cancel_requested (which also hears another process's)."""
    _db_cancel_requested(job_id)    # a cancel from another process sets the flag first
    # Flag and count in one read: a Cancel between two reads would be counted
    # as the one the probe waits past.
    with _lock:
        job = _jobs.get(job_id) or {}
        cancelled, asked = bool(job.get("cancel_requested")), job.get("cancel_count", 0)
    if not cancelled:
        return lambda: is_cancel_requested(job_id)
    return lambda: _cancel_count(job_id) > asked


def note_child(job_id: str, pid: int):
    """Notes job `job_id`'s external process `pid` in the job's held work
    folders (storage.record_job_child), so the next start can stop it if this
    server dies without stopping it. Never raises: a missing note only costs
    that cleanup."""
    try:
        import storage
        storage.record_job_child(job_id, pid)
    except Exception as exc:
        _warn(f"job {job_id}: could not note process {pid} for a later cleanup", exc)


def run_cancellable(job_id: str, cmd: list, cwd: str = None, poll_interval: float = 0.2,
                    kill_timeout: float = 10.0, timeout: float = None):
    """Runs an external command (ffmpeg) for a thread job and kills it when
    the job's cancel is requested, raising JobCancelled. The command runs
    through lib.proc.run_captured: its own process group, the whole tree
    killed, the post-kill pipe drain bounded by kill_timeout. A non-zero
    exit raises subprocess.CalledProcessError, like subprocess.run(check=True).
    With timeout (seconds), the tree is killed and subprocess.TimeoutExpired
    raised once that much time passes. The command is noted in the job's
    work folder (job_workdir) so the next start can stop it if this server
    dies without stopping it (storage.stop_orphaned_job_children)."""
    import subprocess
    from lib import proc

    # ffmpeg reads no Python I/O settings, so the child keeps the caller's environment.
    run = proc.run_captured(cmd, math.inf if timeout is None else timeout,
                            drain_seconds=kill_timeout, cwd=cwd,
                            cancel=lambda: is_cancel_requested(job_id), warn=_warn,
                            utf8_env=False,
                            on_start=lambda pid: note_child(job_id, pid),
                            poll_seconds=poll_interval)
    if run.cancelled:
        raise JobCancelled(job_id)
    if run.timed_out:
        raise subprocess.TimeoutExpired(cmd, timeout)
    if run.returncode != 0:
        raise subprocess.CalledProcessError(run.returncode, cmd, output=run.stdout,
                                            stderr=run.stderr)


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

    Returns True if the job was still queued (it is now "cancelled" and
    stays listed) or is unknown here (any leftover record is cleared).
    Returns False if it had already been promoted to running -- the
    caller should fall back to whatever it does for a live "Stop" (e.g.
    bumping a generation counter) before/alongside calling
    request_cancel(), since the job is now genuinely running."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            if job["status"] != "queued":
                return False
            hooks = _cancel_queued_locked(job_id, job)
        else:
            hooks = _clear_job_locked(job_id)
    if job is None:
        # Outside the lock, as in clear_job: a busy database can make the
        # delete wait seconds, and every job update waits on the lock.
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


_ANY_RUN = object()


def clear_finished_job(job_id: str, started_at=_ANY_RUN) -> bool:
    """clear_job for a run the caller saw finished, which a new run of the
    same id may have replaced since that check. A queued or running run
    here is left alone, and so is, with `started_at`, a finished run other
    than the one the caller saw (its result may not have been read yet).
    The job_records row goes only while it is still that finished run, so
    another process's new run keeps its row. False if a run was kept.
    A run here with no started_at, or `started_at` None (a row written
    without one), cannot be told apart, so it counts as the one seen."""
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            if job.get("status") in ("queued", "running"):
                return False
            if (started_at is not _ANY_RUN and started_at is not None
                    and job.get("started_at") not in (None, started_at)):
                return False
        hooks = _clear_job_locked(job_id)
    try:
        import db
        removed = (db.delete_finished_job_record(job_id) if started_at is _ANY_RUN
                   else db.delete_finished_job_record(job_id, started_at))
    except sqlite3.Error:
        import applog
        applog.get_logger().warning(f"job {job_id}: failed to delete its job_records row",
                                    exc_info=True)
        removed = True
    _emit_change(job_id)
    _run_finish_hooks(hooks)
    return removed


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
