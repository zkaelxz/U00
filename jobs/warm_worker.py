"""jobs/warm_worker.py -- a transcription worker process kept alive between matching queued jobs.

Each transcription otherwise starts a fresh spawned worker, which pays process
start and imports, and reloads the vocal separator and Whisper. Here one
worker runs the matching jobs one after another. The job runner still sees a
Process per job (WarmJob): the job ends when the worker reports it idle, a
cancel, clear or stop kills the whole worker, and a native crash goes to the
usual CPU retry in a fresh normal process. Every item on the worker's
long-lived queue carries its job's number, so no item of one job is read as
another's. An idle worker holds VRAM that the GPU checks would take for
another program's, so it is retired before any GPU work it cannot run.
"""

import contextvars
import multiprocessing
import os
import queue
import signal
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

# The parent retires a worker idle this long, so it never sits on VRAM with no
# job to come.
IDLE_LIMIT_S = 90.0
# The worker's own exit when idle, only a safety net for a parent that stopped
# retiring it: far later than IDLE_LIMIT_S, so the parent never sends a job to
# a worker that is leaving.
CHILD_IDLE_EXIT_S = 600.0

# Queue items the worker sends besides a job's own: it has the job and is
# about to run it; it has finished the job and is ready for the next.
_BEGAN = "warm-began"
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


def _reusable_outcome(item) -> bool:
    """Only a plain success on the GPU leaves the worker fit for another job.
    After an error or a failed outcome its state is not known to be clean (a
    model left loaded by a run that stopped early); after a fall back to the
    CPU, a fresh worker would try the GPU again."""
    outcome = item[1] if item[0] == "ok" and len(item) > 1 else None
    if not isinstance(outcome, dict) or outcome.get("failed_reason"):
        return False
    return not outcome.get("gpu_fallback_msgs") and "cpu" not in str(outcome.get("device_msg") or "").lower()


class _JobChannel(job_process_result.ResultChannel):
    """A job's result channel inside the worker; notes whether its final item
    leaves the worker fit for another job (_reusable_outcome)."""

    reusable = False

    def put(self, item):
        if item and item[0] not in ("progress", "stage", "unit"):
            self.reusable = _reusable_outcome(item)
        super().put(item)


def _keep_models(current_arm):
    if current_arm in ("b", "c"):
        import audio_preprocess
        audio_preprocess.keep_separator_loaded = True
    if current_arm == "c":
        import whisper_models
        whisper_models.keep_whisper_between_runs = True


def _child_pids() -> list:
    """The worker's own live child processes (on Linux also the members of the
    group it leads, which outlive their parent). Empty where they can't be
    listed."""
    me = os.getpid()
    if os.name == "nt":
        return _windows_child_pids(me)
    if not os.path.isdir("/proc"):
        return []
    leads_group = os.getpgid(0) == me
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                stat = f.read()
        except OSError:
            continue
        # The command name in parentheses may itself contain spaces or ")".
        state, ppid, pgrp = stat[stat.rindex(b")") + 2:].split()[:3]
        if state != b"Z" and (int(ppid) == me or (leads_group and int(pgrp) == me)):
            found.append(int(name))
    return found


def _windows_child_pids(me) -> list:
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    try:
        kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return []
    except Exception:
        return []
    found = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32ParentProcessID == me and entry.th32ProcessID != me:
                found.append(int(entry.th32ProcessID))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


class _Pid:
    """What lib.proc_kill.kill_tree needs of a process it did not start."""

    def __init__(self, pid):
        self.pid = pid

    def kill(self):
        try:
            os.kill(self.pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
        except OSError:
            pass


def _kill_leftover_children():
    """Kills what a finished job left running (an ffmpeg, a helper process):
    it must not write into the scratch folder the parent is removing, or hold
    the GPU into the next job. Never raises."""
    try:
        for pid in _child_pids():
            proc_kill.kill_tree(_Pid(pid))
    except Exception:
        pass


def _serve(jobs_conn, raw_queue, current_arm, idle_exit_s):
    """The worker process: runs the jobs it is sent, strictly one at a time,
    until it has been idle idle_exit_s, the parent closes the pipe, or a job
    leaves it unfit."""
    process_worker.start_own_process_group()
    _keep_models(current_arm)
    base_tempdir = tempfile.tempdir
    try:
        while jobs_conn.poll(idle_exit_s):
            try:
                seq, path, target, args = jobs_conn.recv()
            except (EOFError, OSError):
                return
            raw_queue.put((seq, _BEGAN))
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
            _kill_leftover_children()
            raw_queue.put((seq, _IDLE))
    finally:
        _kill_leftover_children()


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


def _others_need_the_gpu(job_id) -> str:
    """Why the worker must not stay loaded once job_id ends, or "": another
    GPU job runs beside it, or GPU jobs may run in parallel, where the
    free-VRAM check that lets one join cannot tell an idle worker's memory
    from a busy job's."""
    from jobs import gpu_queue, job_settings, job_table
    try:
        if job_settings.get_gpu_max_parallel() > 1:
            return "GPU jobs may run in parallel"
        with job_table._lock:
            if gpu_queue._running_gpu_job_count_locked(job_id):
                return "another GPU job is running"
    except Exception:
        return "the running GPU jobs could not be checked"
    return ""


class _Worker:
    """The parent's side of one warm worker process."""

    def __init__(self, key):
        context = multiprocessing.get_context("spawn")
        self.key = key
        self.queue = context.Queue()
        self._receive, self.conn = context.Pipe(duplex=False)
        self.proc = context.Process(target=_serve, args=(self._receive, self.queue, key[0], CHILD_IDLE_EXIT_S),
                                    daemon=True, name="baihe-warm-worker")
        self.started = False
        self.busy = False
        self.idle_since = 0.0
        self.idle_round = 0
        self.jobs = 0

    def fits(self, key) -> bool:
        """Caller holds _lock. Same settings, still running, and inside the
        idle limit (its timer may not have fired yet)."""
        return (self.key == key and self.started and self.proc.is_alive()
                and time.monotonic() - self.idle_since < IDLE_LIMIT_S)

    def launch(self, job):
        if not self.started:
            self.proc.start()
            self.started = True
            self._receive.close()   # the child has its own copy
        self.conn.send((job.seq, job.channel.path, job.target, job.args))

    def kill(self):
        proc_kill.kill_tree(self.proc)

    def release(self):
        """Closes this side's pipe and queue once the worker is gone or going."""
        for end in (self.conn, self._receive):
            try:
                end.close()
            except OSError:
                pass
        try:
            self.queue.close()
        except Exception:
            pass

    def retire(self, reason):
        """Ends this worker and everything it started; it runs no job."""
        import job_process_kill
        try:
            self.conn.close()
        except OSError:
            pass
        if self.started and self.proc.is_alive():
            self.kill()
            self.proc.join(timeout=5)
        if self.started and os.name != "nt":
            # Also once the worker itself is gone: what it started may live on in its group.
            job_process_kill._kill_worker_group(self.proc)
        self.release()
        if self.started:
            _log(f"warm worker pid {self.proc.pid} retired ({reason}){_vram_note()}")


_lock = threading.Lock()
_current: _Worker | None = None   # the one warm worker, idle or running a job


def _forget(worker):
    """worker is gone or going: never hand it another job."""
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


def idle_key():
    """The idle warm worker's key, or None when there is no idle one."""
    with _lock:
        worker = _current
        return worker.key if worker is not None and not worker.busy else None


def retire_idle(reason, keep_key=None):
    """Ends the warm worker if it is idle, before GPU work that cannot use it
    starts or is admitted: it must not hold VRAM that work needs, nor count in
    the free-VRAM checks as another program's load. A busy one is its job's.
    keep_key: the starting job's own warm key, which keeps a worker it fits."""
    global _current
    with _lock:
        worker = _current
        if worker is None or worker.busy or (keep_key is not None and worker.fits(keep_key)):
            return
        _current = None
    worker.retire(reason)


def _retire_if_idle_since(worker, idle_round):
    """The idle limit's timer: retires worker if it has been idle since then."""
    global _current
    with _lock:
        if _current is not worker or worker.busy or worker.idle_round != idle_round:
            return
        _current = None
    worker.retire("idle limit")


class _JobQueue:
    """The parent's reading end of the worker's queue for one job: only items
    tagged with this job's number, and nothing once the worker has reported
    the job finished, when the queue may already carry the next job's."""

    def __init__(self, job):
        self._job = job

    def get(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._job.finished:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            seq, item = self._job.raw_queue.get(timeout=remaining)
            if seq != self._job.seq:
                continue   # an earlier job's leftover, never this job's
            if item == _BEGAN:
                self._job.began = True
                continue
            if item == _IDLE:
                self._job.went_idle()
                break
            return item
        raise queue.Empty

    def close(self):
        """The worker owns the queue; it is closed with this job only when the
        worker is gone."""
        if self._job.worker_gone:
            self._job.release_worker()

    def join_thread(self):
        """The parent never puts on the queue."""

    def cancel_join_thread(self):
        """As join_thread."""


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
        self.began = False
        self.channel = job_process_result.wrap_queue(_JobQueue(self), job_id)

    @property
    def raw_queue(self):
        return self._worker.queue

    @property
    def finished(self) -> bool:
        return self._state == "done"

    @property
    def worker_gone(self) -> bool:
        return self._state == "lost"

    @property
    def pid(self):
        return None if self.finished else self._worker.proc.pid

    @property
    def exitcode(self):
        return 0 if self.finished else self._worker.proc.exitcode

    def ended_before_its_job(self) -> bool:
        """The worker is gone without this job's result for a reason that is not
        the job's: before it took the job, or by a clean exit. The job can run
        again in a fresh process."""
        return self.worker_gone and (not self.began or self._worker.proc.exitcode == 0)

    def start(self):
        if self._stale is not None:
            stale, self._stale = self._stale, None
            stale.retire("the next transcription has other settings")
        worker = self._worker
        reused = worker.started
        try:
            worker.launch(self)
        except Exception:
            if not reused:
                self._give_up(worker)
                raise
            # A reused worker whose pipe broke (it was leaving): the job goes to a fresh one.
            self._replace_worker(worker)
            try:
                self._worker.launch(self)
            except BaseException:
                self._give_up(self._worker)
                raise
        except BaseException:
            self._give_up(worker)
            raise
        self._state = "sent"
        _log(f"job {self.job_id}: warm worker (arm {self._worker.key[0]}) pid {self._worker.proc.pid}, "
             f"job {self.seq} in this worker{_vram_note()}")

    def _give_up(self, worker):
        self._state = "lost"
        _forget(worker)
        worker.retire("it could not be given a job")

    def _replace_worker(self, old):
        global _current
        _forget(old)
        old.retire("its pipe broke")
        fresh = _Worker(old.key)
        with _lock:
            fresh.busy = True
            fresh.jobs = 1
            if _current is None:
                _current = fresh
        self._worker, self.seq = fresh, 1

    def went_idle(self):
        self._state = "done"
        worker = self._worker
        reason = _others_need_the_gpu(self.job_id)
        with _lock:
            worker.busy = False
            worker.idle_since = time.monotonic()
            worker.idle_round += 1
            idle_round = worker.idle_round
        if reason:
            _forget(worker)
            worker.retire(reason)
            return
        _log(f"job {self.job_id}: warm worker pid {worker.proc.pid} idle{_vram_note()}")
        timer = threading.Timer(IDLE_LIMIT_S, _retire_if_idle_since, (worker, idle_round))
        timer.daemon = True
        timer.start()

    def _lost(self):
        self._state = "lost"
        _forget(self._worker)

    def release_worker(self):
        self._worker.release()

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
        jobs_queue = _JobQueue(self)
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
        worker = self._worker
        with _lock:
            fresh = not worker.started
            worker.busy = False
        if fresh:
            _forget(worker)
            worker.release()
        if self._stale is not None:
            # Off this thread: the caller may hold the job lock.
            threading.Thread(target=self._stale.retire, args=("a start that failed",),
                             daemon=True).start()
            self._stale = None
