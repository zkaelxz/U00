"""jobs/warm_worker.py -- a transcription worker process kept alive between matching queued jobs.

Each transcription otherwise starts a fresh spawned worker, which pays process
start and imports, and reloads the vocal separator and Whisper. Here one
worker runs the matching jobs one after another. The job runner still sees a
Process per job (WarmJob): the job ends when the worker reports it idle, a
cancel, clear or stop kills the whole worker, and a native crash goes to the
usual CPU retry in a fresh normal process. Every item on the worker's
long-lived queue carries its job's number, so no item of one job is read as
another's.
"""

import contextvars
import multiprocessing
import os
import queue
import tempfile
import threading
import time

import job_process_result
from jobs import process_worker
from lib import proc_kill

# A test switch for one V100 measurement, read in the parent at each start.
# Unset or "" gives every transcription a fresh worker, as before. "a" keeps the
# process between jobs, "b" also keeps the vocal separator loaded, "c" also
# keeps the Whisper model loaded. The winning arm stays and the variable goes.
ENV_VAR = "BAIHE_WARM_WORKER"
ARMS = ("a", "b", "c")

# An idle worker exits by itself after this long, so it never sits on VRAM
# with no job to come.
IDLE_LIMIT_S = 90.0
# The parent stops handing an idle worker jobs this much before that: the
# worker's idle clock starts before the parent reads its idle report, and a
# job sent to a worker that has just exited would be lost.
REUSE_MARGIN_S = 10.0

# What the worker puts after a job's final item once it is ready for the next.
_IDLE = "warm-idle"


def arm() -> str:
    value = os.environ.get(ENV_VAR, "").strip().lower()
    return value if value in ARMS else ""


def transcription_key(whisper_size, use_gpu, local_model_path, separation_backend,
                      asr_backend_choice, transcript_mode):
    """(arm, settings) a transcription must share with the warm worker's last
    job to run in it, or None for a fresh worker as before: no arm is on, or
    the run is on the CPU. The settings are what decides which models the
    worker loads and may keep, plus the offline switch, which a target sets
    in its process's environment and never undoes."""
    current = arm()
    if not current or not use_gpu:
        return None
    from lib import model_offline
    return (current, ("transcribe", whisper_size, bool(use_gpu), local_model_path or "",
                      separation_backend or "", asr_backend_choice, transcript_mode,
                      model_offline.active()))


# --- inside the worker process ------------------------------------------------

class _Tagged:
    """The worker's end of the long-lived queue for one job: every item
    carries the job's number."""

    def __init__(self, raw, seq):
        self._raw, self._seq = raw, seq

    def put(self, item):
        self._raw.put((self._seq, item))

    def close(self):
        """The queue outlives the job: the worker's next job uses it too."""

    def join_thread(self):
        """As close."""

    def cancel_join_thread(self):
        """As close."""


class _JobChannel(job_process_result.ResultChannel):
    """A job's result channel inside the worker; notes whether its final item
    leaves the worker fit for another job. Only a plain success does: after an
    error or a failed outcome the worker's state is not known to be clean
    (a model left loaded by a run that stopped early), so it exits instead."""

    reusable = False

    def put(self, item):
        if item and item[0] not in ("progress", "stage", "unit"):
            outcome = item[1] if item[0] == "ok" and len(item) > 1 else None
            self.reusable = isinstance(outcome, dict) and not outcome.get("failed_reason")
        super().put(item)


def _keep_models(current_arm):
    if current_arm in ("b", "c"):
        import audio_preprocess
        audio_preprocess.keep_separator_loaded = True
    if current_arm == "c":
        import whisper_models
        whisper_models.keep_whisper_between_runs = True


def _serve(jobs_conn, raw_queue, current_arm, idle_exit_s):
    """The worker process: runs the jobs it is sent, strictly one at a time,
    until it has been idle idle_exit_s, the parent closes the pipe, or a job
    leaves it unfit."""
    process_worker.start_own_process_group()
    _keep_models(current_arm)
    base_tempdir = tempfile.tempdir
    while jobs_conn.poll(idle_exit_s):
        try:
            seq, path, target, args = jobs_conn.recv()
        except (EOFError, OSError):
            return
        channel = _JobChannel(_Tagged(raw_queue, seq), os.path.dirname(path), "", path=path)
        try:
            # A fresh copy per job: a context variable a target sets (the
            # offline switch, the member key) must not reach the next job.
            contextvars.copy_context().run(target, *args, channel)
        except BaseException as exc:
            channel.put(process_worker.error_item(exc))
        finally:
            # The target points temp files at its own scratch folder, which the
            # parent removes once the job ends.
            tempfile.tempdir = base_tempdir
        if not channel.reusable:
            return
        raw_queue.put((seq, _IDLE))


# --- in the parent --------------------------------------------------------------

def _log(message):
    import applog
    applog.get_logger().info(message)


def _vram_note() -> str:
    """For the measurement: the card's used memory, whoever uses it."""
    try:
        import diagnostics_torch
        load = diagnostics_torch.external_gpu_load()
    except Exception:
        return ""
    return f", VRAM used {load['memory_used_mb']:.0f} MB" if load else ""


class _Worker:
    """The parent's side of one warm worker process."""

    def __init__(self, key):
        context = multiprocessing.get_context("spawn")
        self.key = key
        self.queue = context.Queue()
        self._receive, self.conn = context.Pipe(duplex=False)
        self.proc = context.Process(target=_serve, args=(self._receive, self.queue, key[0], IDLE_LIMIT_S),
                                    daemon=True, name="baihe-warm-worker")
        self.started = False
        self.busy = False
        self.idle_since = 0.0
        self.jobs = 0

    def fits(self, key) -> bool:
        """Caller holds _lock. Same settings, still running, and idle short
        enough that it will not exit before the job reaches it."""
        return (self.key == key and self.started and self.proc.is_alive()
                and time.monotonic() - self.idle_since < IDLE_LIMIT_S - REUSE_MARGIN_S)

    def launch(self, job):
        if not self.started:
            self.proc.start()
            self.started = True
            self._receive.close()   # the child has its own copy
        self.conn.send((job.seq, job.channel.path, job.target, job.args))

    def kill(self):
        proc_kill.kill_tree(self.proc)

    def retire(self, reason):
        """Ends this worker and everything it started; it runs no job."""
        try:
            self.conn.close()
        except OSError:
            pass
        if self.started and self.proc.is_alive():
            self.kill()
            self.proc.join(timeout=5)
        try:
            self.queue.close()
        except Exception:
            pass
        if self.started:
            _log(f"warm worker pid {self.proc.pid} retired ({reason}){_vram_note()}")


_lock = threading.Lock()
_current: _Worker | None = None   # the one warm worker, idle or running a job


def _forget(worker):
    global _current
    with _lock:
        if _current is worker:
            _current = None


def claim(job_id, target, args, key):
    """A WarmJob that runs target(*args, result channel) in the warm worker,
    or None when that worker is running another job (this one then gets a
    fresh worker of its own, as before). Starts nothing: the caller may hold
    the job lock, and WarmJob.start does the slow part."""
    global _current
    stale = None
    with _lock:
        worker = _current
        if worker is not None and worker.busy:
            return None
        if worker is not None and not worker.fits(key):
            stale, worker = worker, None
        if worker is None:
            worker = _current = _Worker(key)
        worker.busy = True
        worker.jobs += 1
        return WarmJob(worker, worker.jobs, job_id, target, args, stale)


def retire_idle(reason):
    """Ends the warm worker if it is idle, before GPU work that cannot use it
    starts: it must not hold VRAM that work needs. A busy one is its job's."""
    global _current
    with _lock:
        worker = _current
        if worker is None or worker.busy:
            return
        _current = None
    worker.retire(reason)


class _JobQueue:
    """The parent's reading end of the worker's queue for one job: only items
    tagged with this job's number, and nothing once the worker has reported
    the job finished, when the queue may already carry the next job's."""

    def __init__(self, raw, job):
        self._raw, self._job = raw, job

    def get(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._job.finished:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            seq, item = self._raw.get(timeout=remaining)
            if seq != self._job.seq:
                continue   # an earlier job's leftover, never this job's
            if item == _IDLE:
                self._job.went_idle()
                break
            return item
        raise queue.Empty

    def close(self):
        """The worker owns the queue; it outlives this job."""

    def join_thread(self):
        """As close."""

    def cancel_join_thread(self):
        """As close."""


class WarmJob:
    """One job's run in the warm worker, shaped like the multiprocessing.Process
    the job runner watches. Once the worker reports the job finished the
    handle names no process: not alive, exit code 0, no pid, and terminate
    does nothing, so reaping a finished job leaves the idle worker for the
    next one. Before that, every stop ends the whole worker."""

    def __init__(self, worker, seq, job_id, target, args, stale):
        self._worker, self.seq, self.job_id = worker, seq, job_id
        self.target, self.args = target, args
        self._stale = stale
        self._state = "claimed"   # then "sent", then "done" or "lost"
        self.channel = job_process_result.wrap_queue(_JobQueue(worker.queue, self), job_id)

    @property
    def finished(self) -> bool:
        return self._state == "done"

    @property
    def pid(self):
        return None if self.finished else self._worker.proc.pid

    @property
    def exitcode(self):
        return 0 if self.finished else self._worker.proc.exitcode

    def start(self):
        if self._stale is not None:
            stale, self._stale = self._stale, None
            stale.retire("the next transcription has other settings")
        worker = self._worker
        try:
            worker.launch(self)
        except BaseException:
            self._state = "lost"
            _forget(worker)
            worker.retire("it could not be given a job")
            raise
        self._state = "sent"
        _log(f"job {self.job_id}: warm worker (arm {worker.key[0]}) pid {worker.proc.pid}, "
             f"job {self.seq} in this worker{_vram_note()}")

    def went_idle(self):
        self._state = "done"
        with _lock:
            self._worker.busy = False
            self._worker.idle_since = time.monotonic()
        _log(f"job {self.job_id}: warm worker pid {self._worker.proc.pid} idle{_vram_note()}")

    def _lost(self):
        self._state = "lost"
        _forget(self._worker)

    def is_alive(self) -> bool:
        if self._state in ("claimed", "done"):
            return False
        if self._worker.proc.is_alive():
            return True
        self._lost()
        return False

    def join(self, timeout=None):
        """Waits for the worker's report that this job finished, reading the
        queue for it (the watcher has stopped reading by then), or for the
        process to end."""
        if self._state in ("claimed", "done"):
            return
        deadline = None if timeout is None else time.monotonic() + timeout
        jobs_queue = _JobQueue(self._worker.queue, self)
        while self._state == "sent" and self._worker.proc.is_alive():
            wait = 0.2 if deadline is None else min(0.2, deadline - time.monotonic())
            if wait <= 0:
                return
            try:
                jobs_queue.get(timeout=wait)   # nothing follows the final item but the idle report
            except queue.Empty:
                pass
            except (EOFError, OSError):
                break
        if not self.finished:
            self._worker.proc.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
            if not self._worker.proc.is_alive():
                self._lost()

    def terminate(self):
        """A job cannot be stopped inside the worker, so stopping it ends the worker."""
        if self._state in ("sent", "lost"):
            self._worker.kill()
            self._lost()

    kill = terminate

    def close(self):
        """Gives the worker back when this job never started; a started job's
        worker outlives it."""
        if self._state != "claimed":
            return
        self._state = "lost"
        with _lock:
            fresh = not self._worker.started
            self._worker.busy = False
        if fresh:
            _forget(self._worker)
        if self._stale is not None:
            # Off this thread: the caller may hold the job lock.
            threading.Thread(target=self._stale.retire, args=("a start that failed",),
                             daemon=True).start()
            self._stale = None
