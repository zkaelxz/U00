"""jobs/warm_worker.py -- transcription worker processes kept alive between matching queued jobs.

Each transcription otherwise starts a fresh spawned worker, which pays process
start and imports, and reloads the vocal separator and Whisper. Here a worker
runs matching jobs one after another, at most one worker per GPU slot
(gpu_max_parallel), so busy and idle workers together never outnumber the GPU
jobs that may run at once. The job runner still sees a
Process per job (WarmJob): the job ends when the worker reports it idle, a
cancel, clear or stop kills the whole worker, and a native crash goes to the
usual CPU retry in a fresh normal process. Every item on the worker's
long-lived queue carries its job's number, so no item of one job is read as
another's. An idle worker holds VRAM that the GPU checks would take for
another program's, so it is retired before any GPU work that starts and
cannot use it; one may stay idle beside GPU jobs already running.
"""

import contextvars
import multiprocessing
import os
import queue
import signal
import tempfile
import threading
import time
import types

import gpu_probe
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

# nvidia-smi's utilization covers its last sample period, up to a second, so a
# reading taken as one job ends still shows that job's own work.
UTIL_SETTLE_S = 2.0

# Queue items the worker sends besides a job's own: it has the job and is
# about to run it; it has finished the job and is ready for the next.
_BEGAN = "warm-began"
_IDLE = "warm-idle"
_MEMORY = "warm-memory"

# A worker whose own GPU memory, idle, has grown this much past its first idle
# reading is retired: a fresh one starts from its first reading again.
GROWTH_LIMIT_MB = 1024.0


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
        # Declared, or ctypes passes and returns plain ints: a 64-bit handle would be cut short.
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for walk in (kernel32.Process32FirstW, kernel32.Process32NextW):
            walk.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
            walk.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
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


def _memory_report() -> dict:
    """What the worker holds once a job is done, after dropping what torch
    caches: torch's own counts, and the live thread count (each library keeps
    per-thread GPU handles). Empty parts where torch is not loaded."""
    import gc
    import sys
    gc.collect()
    report = {"threads": threading.active_count()}
    torch = sys.modules.get("torch")
    try:
        if torch is not None and torch.cuda.is_initialized():
            torch.cuda.empty_cache()
            report["torch_allocated_mb"] = torch.cuda.memory_allocated() / 2**20
            report["torch_reserved_mb"] = torch.cuda.memory_reserved() / 2**20
    except Exception:
        pass
    return report


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
            raw_queue.put((seq, (_MEMORY, _memory_report())))
            raw_queue.put((seq, _IDLE))
    finally:
        _kill_leftover_children()


# --- in the parent --------------------------------------------------------------

def _log(message):
    import applog
    applog.get_logger().info(message)


def _card_used_mb():
    """The card's used memory, whoever uses it; None without a reading."""
    try:
        import diagnostics_torch
        return (diagnostics_torch.external_gpu_load() or {}).get("memory_used_mb")
    except Exception:
        return None


def _process_used_mb(pid):
    """GPU memory pid holds, per nvidia-smi; None where the driver does not
    report it per process (Windows display mode reports N/A)."""
    import shutil
    if pid is None or not shutil.which("nvidia-smi"):
        return None
    try:
        from lib.proc import run_captured
        result = run_captured(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                               "--format=csv,noheader,nounits"], timeout=5)
        if result.timed_out or result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            fields = [x.strip() for x in line.split(",")]
            if len(fields) == 2 and fields[0] == str(pid):
                return float(fields[1])
    except Exception:
        return None
    return None


def _vram_note(used: "float | None | types.EllipsisType" = ...) -> str:
    """For the measurement: the card's used memory, whoever uses it."""
    used = _card_used_mb() if used is ... else used
    return f", VRAM used {used:.0f} MB" if used is not None else ""


def _slots() -> int:
    from jobs import job_settings
    return job_settings.get_gpu_max_parallel()


def _running_others(job_id):
    """How many GPU jobs other than job_id run in this process; None when that
    cannot be read. Takes the job lock, so never call it holding _lock."""
    from jobs import gpu_queue, job_table
    try:
        with job_table._lock:
            return gpu_queue._running_gpu_job_count_locked(job_id)
    except Exception:
        return None


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
        self.retired = False
        # The card's used memory before this worker started, and what it holds
        # beyond that while idle: a job that reuses it takes that memory over.
        self.baseline_mb = None
        self.held_mb = 0.0
        self.first_idle_mb = None   # its own memory at its first idle, for GROWTH_LIMIT_MB
        # Another GPU job ran during this worker's current job: the card's used
        # memory then says nothing about what the worker alone holds.
        self.shared_card = False

    def fits(self, key) -> bool:
        """Caller holds _lock. Same settings, still running, and inside the
        idle limit (its timer may not have fired yet)."""
        return (self.key == key and self.started and self.proc.is_alive()
                and time.monotonic() - self.idle_since < IDLE_LIMIT_S)

    def launch(self, job):
        if not self.started:
            self.baseline_mb = _card_used_mb()
            self.proc.start()
            self.started = True
            self._receive.close()   # the child has its own copy
        self.conn.send((job.seq, job.channel.path, job.target, job.args))

    def now_idle(self) -> int:
        """Caller holds _lock. Marks the worker idle from now: the round that
        idle-limit timer must still see."""
        self.busy = False
        self.idle_since = time.monotonic()
        self.idle_round += 1
        return self.idle_round

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
        """Ends this worker and everything it started; it runs no job. Once
        only: its process group id may belong to another process by a second
        call."""
        import job_process_kill
        with _lock:
            if self.retired:
                return
            self.retired = True
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
_workers: list[_Worker] = []   # the warm workers, idle or running a job


def _forget(worker):
    """worker is gone or going: never hand it another job."""
    with _lock:
        if worker in _workers:
            _workers.remove(worker)


def claim(job_id, target, args, key):
    """A WarmJob that runs target(*args, result channel) in an idle warm worker
    it fits, or in a new one while the workers do not yet fill the GPU slots;
    None when they do and all are busy (this job then gets a fresh worker of its
    own, as before). Starts nothing: the caller may hold the job lock, and
    WarmJob.start does the slow part."""
    slots = _slots()
    stale = None
    with _lock:
        worker = next((w for w in _workers if not w.busy and w.fits(key)), None)
        if worker is None:
            stale = next((w for w in _workers if not w.busy), None)
            if stale is not None:
                _workers.remove(stale)
            if len(_workers) >= slots:
                if stale is not None:
                    _workers.append(stale)   # not replaced: it stays for the idle checks to end
                return None
            worker = _Worker(key)
            _workers.append(worker)
        worker.busy = True
        worker.jobs += 1
        return WarmJob(worker, worker.jobs, job_id, target, args, stale)


def has_idle() -> bool:
    with _lock:
        return any(not w.busy for w in _workers)


def reusable_held_mb(key) -> float:
    """The memory an idle worker key fits holds, which a job with that key takes
    over rather than adds to; 0 when there is none."""
    if key is None:
        return 0.0
    with _lock:
        worker = next((w for w in _workers if not w.busy and w.fits(key)), None)
        return worker.held_mb if worker is not None else 0.0


def retire_idle(reason, keep_key=None):
    """Ends the idle warm workers before GPU work that cannot use them starts or
    is admitted: they must not hold VRAM that work needs, nor count in the
    free-VRAM checks as another program's load. A busy one is its job's.
    keep_key: the starting job's own warm key, which keeps one worker it fits."""
    going = []
    with _lock:
        kept = False
        for worker in list(_workers):
            if worker.busy:
                continue
            if keep_key is not None and not kept and worker.fits(keep_key):
                kept = True
                continue
            _workers.remove(worker)
            going.append(worker)
    for worker in going:
        worker.retire(reason)


def retire_if_the_card_reads_busy():
    """Right after gpu_probe.prefetch, before a slot check a job that fits the
    idle worker is about to pass through: the busy reading may be the worker's
    own doing, and the job would queue behind it until its idle limit. The card
    is read again once the last job's utilization has settled, with the memory
    the worker holds counted free, since the job takes it over. Only a card
    still busy then retires the worker, and is read once more."""
    with _lock:
        idle = [w for w in _workers if not w.busy]
        if not idle:
            return
        idle_since = max(w.idle_since for w in idle)
        held_mb = sum(w.held_mb for w in idle)
    if _running_others(None) and _slots() > 1:
        _retire_if_no_room_to_join(held_mb)
        return
    if not gpu_probe.prefetched_busy():
        return
    wait = UTIL_SETTLE_S - (time.monotonic() - idle_since)
    if wait > 0:
        time.sleep(wait)
    gpu_probe.prefetch(held_mb=held_mb)
    if not gpu_probe.prefetched_busy():
        return
    load = gpu_probe.prefetched_load() or {}
    retire_idle(f"the GPU looked busy to the job that would reuse it: utilization "
                f"{load.get('utilization_percent')}%, {load.get('memory_free_mb')} MB free "
                f"counting the idle workers' {held_mb:.0f} MB")
    gpu_probe.prefetch()


def _retire_if_no_room_to_join(held_mb):
    """Beside running GPU jobs the card is judged as a job joining them is
    (gpu_slots.acquire): their own load keeps utilization high, so only free
    memory counts, with the reused worker's memory already read as free."""
    from jobs import gpu_slots
    load = gpu_probe.prefetched_load()
    free = (load or {}).get("memory_free_mb")
    if free is None or free >= gpu_slots.GPU_PARALLEL_RESERVE_MB:
        return
    retire_idle(f"too little GPU memory for the job that would reuse it: {free:.0f} MB free "
                f"counting the idle workers' {held_mb:.0f} MB")
    gpu_probe.prefetch()


def _arm_idle_timer(worker, idle_round):
    timer = threading.Timer(IDLE_LIMIT_S, _retire_if_idle_since, (worker, idle_round))
    timer.daemon = True
    timer.start()


def _retire_if_idle_since(worker, idle_round):
    """The idle limit's timer: retires worker if it has been idle since then."""
    with _lock:
        if worker.busy or worker.idle_round != idle_round:
            return
        if worker in _workers:
            _workers.remove(worker)
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
            if isinstance(item, tuple) and item and item[0] == _MEMORY:
                self._job.memory = item[1]
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
        self.memory = {}
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
        worker.shared_card = bool(_running_others(self.job_id))
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
        fresh = _Worker(old.key)
        fresh.busy = True
        fresh.jobs = 1
        fresh.shared_card = old.shared_card
        with _lock:
            # In old's place in one step, so no claim sees a free slot between.
            if old in _workers:
                _workers[_workers.index(old)] = fresh
            else:
                _workers.append(fresh)
        old.retire("its pipe broke")
        self._worker, self.seq = fresh, 1

    def went_idle(self):
        """The worker stays only while the other running GPU jobs and the idle
        workers, this one included, fit the GPU slots: keeping it otherwise
        would put more on the card than may run at once."""
        self._state = "done"
        worker = self._worker
        running = _running_others(self.job_id)
        grew = self._note_memory(worker, running)
        slots = _slots()
        with _lock:
            # Marked idle, counted and removed in one step, so two workers going
            # idle together do not both count the other and both leave.
            idle_round = worker.now_idle()
            idle = sum(1 for w in _workers if not w.busy)
            if running is None:
                reason = "the running GPU jobs could not be checked"
            elif running + idle > slots:
                reason = "every GPU slot is taken"
            else:
                reason = grew
            if reason and worker in _workers:
                _workers.remove(worker)
        if reason:
            worker.retire(reason)
            return
        _arm_idle_timer(worker, idle_round)

    def _note_memory(self, worker, running) -> str:
        """Logs and records what the idle worker holds; returns why it must go
        if its own memory grew past GROWTH_LIMIT_MB, or "". Its own memory is
        nvidia-smi's per-process reading where the driver gives one, else
        torch's reserved memory from the worker's report. The card's used
        memory less its reading before the worker started stands in only when
        no other GPU job shared the card meanwhile."""
        used = _card_used_mb()
        own = _process_used_mb(worker.proc.pid)
        reserved = self.memory.get("torch_reserved_mb")
        shared = worker.shared_card or bool(running)
        if own is not None:
            worker.held_mb = own
        elif not shared and used is not None and worker.baseline_mb is not None:
            worker.held_mb = max(0.0, used - worker.baseline_mb)
        else:
            worker.held_mb = float(reserved or 0.0)
        mine = own if own is not None else reserved
        if worker.first_idle_mb is None:
            worker.first_idle_mb = mine
        torch_note = "".join(f", {name} {self.memory[name]:.0f} MB" for name in
                             ("torch_allocated_mb", "torch_reserved_mb") if name in self.memory)
        _log(f"job {self.job_id}: warm worker pid {worker.proc.pid} idle{_vram_note(used)}"
             + (f", own {own:.0f} MB" if own is not None else "")
             + torch_note + (f", threads {self.memory['threads']}" if "threads" in self.memory else ""))
        if mine is not None and worker.first_idle_mb is not None and mine - worker.first_idle_mb > GROWTH_LIMIT_MB:
            return f"its memory grew {mine - worker.first_idle_mb:.0f} MB"
        return ""

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
            idle_round = worker.now_idle()
        if fresh:
            _forget(worker)
            worker.release()
        else:
            # Its earlier timer may have fired while this job held it.
            _arm_idle_timer(worker, idle_round)
        if self._stale is not None:
            # Off this thread: the caller may hold the job lock.
            threading.Thread(target=self._stale.retire, args=("a start that failed",),
                             daemon=True).start()
            self._stale = None
