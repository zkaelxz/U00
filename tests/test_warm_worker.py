"""jobs/warm_worker.py: one spawned worker kept between matching process jobs.
Real spawned children running the fake targets below; no model is loaded."""
import contextvars
import multiprocessing
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time

import pytest

import background_jobs as bg
import db
import storage
import job_process_kill
from jobs import (crash_retry, gpu_guard, job_launch, job_runner, job_settings, job_table, process_worker,
                  warm_worker)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="reads process states from /proc")

KEY = ("transcribe", "large-v3", True, "", "audio_separator", "whisper", "whisper", False)
OTHER_KEY = ("transcribe", "large-v3-turbo", True, "", "audio_separator", "whisper", "whisper", False)
V100_MB, CARD_12_GB_MB = 32768.0, 12288.0

_runs_here = 0


def tagged_worker(tag, big, result_queue):
    """Counts its runs in this process, so a reused worker says 2. Sets up its
    process group first, as the transcription worker does."""
    global _runs_here
    process_worker.start_own_process_group()
    _runs_here += 1
    result_queue.put(("progress", 0.5, f"working on {tag}"))
    import audio_preprocess
    watchdogs = sum(t.name == "parent-watchdog" for t in threading.enumerate())
    result = {"tag": tag, "pid": os.getpid(), "runs_here": _runs_here, "watchdogs": watchdogs,
              "keeps_separator": audio_preprocess.keep_separator_loaded}
    if big:
        result["filler"] = tag * 5000   # over the inline limit: goes through the result file
    result_queue.put(("ok", result))


def waits_for_file(path, tag, result_queue):
    global _runs_here
    _runs_here += 1
    with open(path + f".{tag}.pid", "w") as f:
        f.write(str(os.getpid()))
    deadline = time.monotonic() + 30
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.05)
    result_queue.put(("ok", {"tag": tag, "pid": os.getpid(), "runs_here": _runs_here}))


def crashes_on_gpu(use_gpu, result_queue):
    if use_gpu:
        os.kill(os.getpid(), signal.SIGSEGV)
    result_queue.put(("ok", {"pid": os.getpid(), "on_gpu": use_gpu}))


def reports_a_failure(result_queue):
    result_queue.put(("ok", {"failed_reason": "vocal_separation", "pid": os.getpid()}))


def reports(outcome, result_queue):
    result_queue.put(("ok", {**outcome, "pid": os.getpid()}))


_job_var = contextvars.ContextVar("warm_test_job", default="unset")


def changes_process_state(tag, scratch, result_queue):
    """Reports the temp folder and a context variable as it found them, then
    changes both, as a transcription target does."""
    seen = {"tempdir": tempfile.tempdir, "var": _job_var.get(), "pid": os.getpid()}
    tempfile.tempdir = scratch
    _job_var.set(tag)
    result_queue.put(("ok", seen))


def leaves_children(result_queue):
    """Leaves two processes running: one in its own session (as run_cancellable
    starts ffmpeg) and one in the worker's group."""
    own = subprocess.Popen(["sleep", "60"], start_new_session=True)
    shared = subprocess.Popen(["sleep", "60"])
    result_queue.put(("ok", {"pid": os.getpid(), "children": [own.pid, shared.pid]}))


def _rebuild_outside_the_warm_worker():
    if multiprocessing.current_process().name == "baihe-warm-worker":
        raise RuntimeError("the warm worker is leaving")
    return tagged_worker


class _TargetTheWarmWorkerCannotTake:
    """Unpickles to tagged_worker, except inside a warm worker, which then dies
    before it has taken the job: a worker leaving just as a job reaches it."""

    def __reduce__(self):
        return (_rebuild_outside_the_warm_worker, ())


def _gone(pid) -> bool:
    """Not running: no such process, or a zombie (it holds nothing any more)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            stat = f.read()
        return stat[stat.rindex(b")") + 2:].split()[0] == b"Z"
    except FileNotFoundError:
        return True


def _wait_gone(pid, timeout=15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _gone(pid):
            return True
        time.sleep(0.05)
    return _gone(pid)


def _wait_end(job_id, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = bg.get_status(job_id)
        if status and status["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    # The watcher removes the run's files and frees its slot just after the status.
    job_runner.wait_for_job_threads(10, job_ids={job_id})
    return bg.get_status(job_id)


def _start(job_id, target, args, key=KEY, **kw):
    bg.clear_job(job_id)
    assert job_launch.start_process_job(job_id, target, args=args, kill_whole_tree=True,
                                        start_method="spawn", line_fields=(), warm_key=key, **kw)


def _run(job_id, target, args, key=KEY, **kw):
    _start(job_id, target, args, key, **kw)
    status = _wait_end(job_id)
    bg.clear_job(job_id)
    return status


def _result_files():
    folder = os.path.join(db.LIBRARY_DIR, storage.TEMP_DIRNAME)
    return [n for n in os.listdir(folder) if "~result-" in n] if os.path.isdir(folder) else []


@pytest.fixture(autouse=True)
def _no_worker_left(isolated_db):
    # Not by monkeypatch: a test's monkeypatch.undo() would put the stub back. The
    # conftest fixture that set the stub restores the real function after the test.
    warm_worker.claim = warm_worker.claim.real
    # A V100's 32 GB: room for a warm worker in every slot these tests use.
    saved_total, warm_worker._card_total_mb = warm_worker._card_total_mb, V100_MB
    yield
    warm_worker._card_total_mb = saved_total
    with warm_worker._lock:
        left, warm_worker._workers[:] = list(warm_worker._workers), []
    for worker in left:
        worker.retire("the test ended")


def _one():
    """The only warm worker, or None."""
    with warm_worker._lock:
        assert len(warm_worker._workers) <= 1, warm_worker._workers
        return warm_worker._workers[0] if warm_worker._workers else None


def test_two_matching_jobs_run_in_one_worker_and_each_reads_only_its_own_results():
    first = _run("warm_one", tagged_worker, ("one", True))
    second = _run("warm_two", tagged_worker, ("two", True))
    assert first["status"] == second["status"] == "done", (first, second)
    one, two = first["result"], second["result"]
    assert (one["tag"], one["runs_here"], one["filler"]) == ("one", 1, "one" * 5000)
    assert (two["tag"], two["runs_here"], two["filler"]) == ("two", 2, "two" * 5000)
    assert one["pid"] == two["pid"] and not _gone(two["pid"])
    assert one["watchdogs"] == two["watchdogs"] == 1
    assert one["keeps_separator"] is True
    assert _result_files() == []


def test_a_queued_matching_gpu_job_runs_in_the_worker_the_finished_one_left(tmp_path):
    release = str(tmp_path / "release")
    _start("warm_gpu_a", waits_for_file, (release, "a"), gpu_touching=True)
    _start("warm_gpu_b", waits_for_file, (release, "b"), gpu_touching=True)
    assert bg.get_status("warm_gpu_b")["status"] == "queued"
    open(release, "w").close()
    first, second = _wait_end("warm_gpu_a"), _wait_end("warm_gpu_b")
    assert first["status"] == second["status"] == "done", (first, second)
    assert first["result"]["pid"] == second["result"]["pid"]
    assert (first["result"]["runs_here"], second["result"]["runs_here"]) == (1, 2)


def test_without_a_key_the_job_gets_its_own_process_as_before():
    bg.clear_job("cold_one")
    assert job_launch.start_process_job("cold_one", tagged_worker, args=("cold", False),
                                        start_method="spawn", line_fields=())
    assert isinstance(job_table._jobs["cold_one"]["process"], multiprocessing.process.BaseProcess)
    status = _wait_end("cold_one")
    assert status["status"] == "done" and status["result"]["runs_here"] == 1
    assert _one() is None
    bg.clear_job("cold_one")


def test_a_cpu_run_gets_no_warm_worker():
    assert warm_worker.transcription_key("large-v3", False, "", "audio_separator", "whisper", "whisper") is None
    assert warm_worker.transcription_key("large-v3", True, "", "audio_separator", "whisper", "whisper") == KEY


def test_a_job_with_other_settings_gets_a_new_worker_and_the_old_one_ends():
    first = _run("warm_mismatch_a", tagged_worker, ("a", False))
    second = _run("warm_mismatch_b", tagged_worker, ("b", False), key=OTHER_KEY)
    assert second["status"] == "done" and second["result"]["runs_here"] == 1
    assert second["result"]["pid"] != first["result"]["pid"]
    assert _gone(first["result"]["pid"])


def test_a_cancel_ends_the_worker_and_the_next_job_still_runs(tmp_path):
    release = str(tmp_path / "never")
    _start("warm_cancel", waits_for_file, (release, "c"))
    pid_file = release + ".c.pid"
    deadline = time.monotonic() + 30
    while not os.path.exists(pid_file) and time.monotonic() < deadline:
        time.sleep(0.05)
    with open(pid_file) as f:
        pid = int(f.read())
    bg.request_cancel("warm_cancel")
    assert _wait_end("warm_cancel")["status"] == "cancelled"
    assert _gone(pid)
    bg.clear_job("warm_cancel")
    after = _run("warm_after_cancel", tagged_worker, ("next", False))
    assert after["status"] == "done" and after["result"]["runs_here"] == 1
    assert after["result"]["pid"] != pid


def test_a_native_crash_still_runs_once_more_on_the_cpu_in_a_fresh_process():
    warm_pid = _run("warm_before_crash", tagged_worker, ("warm", False))["result"]["pid"]
    status = _run("warm_crash", crashes_on_gpu, (True,), cpu_retry_args=(False,))
    assert status["status"] == "done", status
    assert status["result"]["on_gpu"] is False
    assert status["result"][crash_retry.REASON_KEY] == crash_retry.crash_reason(-signal.SIGSEGV)
    assert status["result"]["pid"] != warm_pid and _gone(warm_pid)
    after = _run("warm_after_crash", tagged_worker, ("next", False))
    assert after["status"] == "done" and after["result"]["runs_here"] == 1


def test_a_failed_outcome_ends_the_worker_instead_of_keeping_it():
    failed = _run("warm_failed", reports_a_failure, ())
    assert failed["result"]["failed_reason"] == "vocal_separation"
    assert _wait_gone(failed["result"]["pid"])
    after = _run("warm_after_failure", tagged_worker, ("next", False))
    assert after["status"] == "done" and after["result"]["runs_here"] == 1


def test_the_parent_retires_an_idle_worker_at_the_idle_limit(monkeypatch):
    monkeypatch.setattr(warm_worker, "IDLE_LIMIT_S", 1.0)
    pid = _run("warm_idle", tagged_worker, ("idle", False))["result"]["pid"]
    assert _wait_gone(pid, timeout=15)
    assert _one() is None   # retired by the parent, not left to exit
    after = _run("warm_after_idle", tagged_worker, ("next", False))
    assert after["status"] == "done" and after["result"]["pid"] != pid


def _watch_first_timer(monkeypatch):
    """Spies on the idle-limit timers: an event set once the first job's timer
    has run, and whether the worker was still the warm one right after it."""
    fired, after = threading.Event(), []
    real = warm_worker._retire_if_idle_since

    def spy(worker, idle_round):
        real(worker, idle_round)
        if idle_round == 1:
            after.append(worker in warm_worker._workers and worker.proc.is_alive())
            fired.set()
    monkeypatch.setattr(warm_worker, "_retire_if_idle_since", spy)
    return fired, after


@pytest.mark.parametrize("second_job", ["still running", "already idle again"])
def test_the_first_jobs_timer_leaves_a_worker_a_second_job_reused(monkeypatch, tmp_path, second_job):
    monkeypatch.setattr(warm_worker, "IDLE_LIMIT_S", 1.0)
    fired, after = _watch_first_timer(monkeypatch)
    pid = _run("warm_timer_a", tagged_worker, ("a", False))["result"]["pid"]
    if second_job == "still running":
        release = str(tmp_path / "release")
        _start("warm_timer_b", waits_for_file, (release, "b"))
        assert fired.wait(15)
        open(release, "w").close()
        second = _wait_end("warm_timer_b")
        bg.clear_job("warm_timer_b")
    else:
        second = _run("warm_timer_b", tagged_worker, ("b", False))
        assert fired.wait(15)
    assert second["status"] == "done", second
    assert second["result"]["pid"] == pid and second["result"]["runs_here"] == 2
    assert after == [True]


def test_a_reused_worker_whose_start_failed_still_ends_at_the_idle_limit(monkeypatch):
    monkeypatch.setattr(warm_worker, "IDLE_LIMIT_S", 1.0)
    fired, _ = _watch_first_timer(monkeypatch)
    pid = _run("warm_given_back_a", tagged_worker, ("a", False))["result"]["pid"]
    job = warm_worker.claim("warm_given_back_b", tagged_worker, ("b", False), KEY)
    assert job is not None
    assert fired.wait(15)   # the first timer found the worker taken
    job.close()
    assert _wait_gone(pid, timeout=15) and _one() is None


def test_a_job_whose_start_failed_no_longer_reaches_the_worker_it_gave_back():
    pid = _run("warm_handed_a", tagged_worker, ("a", False))["result"]["pid"]
    job = warm_worker.claim("warm_handed_b", tagged_worker, ("b", False), KEY)
    job.close()
    job.channel.close()
    assert not job.is_alive() and job.pid is None
    job.terminate()   # a later stop of the failed job must not end the next job's worker
    job.join(timeout=1)
    after = _run("warm_handed_c", tagged_worker, ("c", False))
    assert after["result"]["pid"] == pid and after["result"]["runs_here"] == 2


def test_a_replacement_worker_takes_the_broken_ones_slot_and_ends_at_the_idle_limit(monkeypatch):
    monkeypatch.setattr(warm_worker, "IDLE_LIMIT_S", 1.0)
    pid = _run("warm_replaced_a", tagged_worker, ("a", False))["result"]["pid"]
    _one().conn.close()
    status = _run("warm_replaced_b", tagged_worker, ("b", False))
    assert status["status"] == "done", status
    fresh = status["result"]["pid"]
    assert fresh != pid and _one() is not None and _one().proc.pid == fresh
    assert _wait_gone(fresh, timeout=15) and _one() is None


def _card_full_while_alive(monkeypatch, pid_of):
    """nvidia-smi as the fake reads it: no free memory while pid_of() runs."""
    import diagnostics_torch

    def load():
        pid = pid_of()
        return {"utilization_percent": 0, "memory_used_mb": 0,
                "memory_free_mb": 8000 if pid is None or _gone(pid) else 0}
    monkeypatch.setattr(diagnostics_torch, "external_gpu_load", load)
    monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy",
                        lambda reading=...: (load() if reading is ... else reading)["memory_free_mb"] < 1000)


def test_a_matching_job_the_workers_own_memory_would_queue_retires_it_and_starts(monkeypatch):
    pid = _run("warm_own_memory_a", tagged_worker, ("a", False), gpu_touching=True)["result"]["pid"]
    _card_full_while_alive(monkeypatch, lambda: pid)
    _start("warm_own_memory_b", tagged_worker, ("b", False), gpu_touching=True)
    assert bg.get_status("warm_own_memory_b")["status"] != "queued"
    status = _wait_end("warm_own_memory_b")
    bg.clear_job("warm_own_memory_b")
    assert status["status"] == "done", status
    assert status["result"]["pid"] != pid and _gone(pid)


def test_a_queued_matching_job_the_workers_own_memory_would_hold_back_is_promoted(monkeypatch, tmp_path):
    release = str(tmp_path / "release")
    _start("warm_promote_a", waits_for_file, (release, "a"), gpu_touching=True)
    pid = _pid_from(release + ".a.pid")
    _card_full_while_alive(monkeypatch, lambda: pid)
    _start("warm_promote_b", tagged_worker, ("b", False), gpu_touching=True)
    assert bg.get_status("warm_promote_b")["status"] == "queued"
    open(release, "w").close()
    assert _wait_end("warm_promote_a")["status"] == "done"
    status = _wait_end("warm_promote_b", timeout=15)
    bg.clear_job("warm_promote_a")
    bg.clear_job("warm_promote_b")
    assert status["status"] == "done", status
    assert status["result"]["pid"] != pid and _gone(pid)


def _fake_card(monkeypatch, reading):
    import diagnostics_torch
    monkeypatch.setattr(diagnostics_torch, "external_gpu_load", reading)
    monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy",
                        lambda load=...: (lambda r: r["utilization_percent"] >= 50 or r["memory_free_mb"] < 1000)(
                            reading() if load is ... else load))


def _idle_worker():
    worker = _one()
    return worker if worker is not None and worker.started and not worker.busy and worker.proc.is_alive() else None


def test_the_workers_own_memory_counts_as_free_for_the_job_that_reuses_it(monkeypatch):
    monkeypatch.setattr(warm_worker, "UTIL_SETTLE_S", 0.0)

    def reading():
        worker = _one()
        used = 1000.0 + (5000.0 if worker is not None and worker.started and worker.proc.is_alive() else 0.0)
        return {"utilization_percent": 0, "memory_used_mb": used, "memory_total_mb": 6000.0,
                "memory_free_mb": 6000.0 - used}
    _fake_card(monkeypatch, reading)
    pid = _run("warm_held_a", tagged_worker, ("a", False), gpu_touching=True)["result"]["pid"]
    assert _one().held_mb == 5000.0
    status = _run("warm_held_b", tagged_worker, ("b", False), gpu_touching=True)
    assert status["status"] == "done", status
    assert status["result"]["pid"] == pid


def test_the_last_jobs_own_utilization_is_waited_out_instead_of_retiring_the_worker(monkeypatch):
    monkeypatch.setattr(warm_worker, "UTIL_SETTLE_S", 3.0)
    busy_reads = []

    def reading():
        worker = _idle_worker()
        hot = worker is not None and time.monotonic() - worker.idle_since < 3.0
        if hot:
            busy_reads.append(True)
        return {"utilization_percent": 90 if hot else 0, "memory_used_mb": 1000.0, "memory_total_mb": 12000.0,
                "memory_free_mb": 11000.0}
    _fake_card(monkeypatch, reading)
    pid = _run("warm_util_a", tagged_worker, ("a", False), gpu_touching=True)["result"]["pid"]
    status = _run("warm_util_b", tagged_worker, ("b", False), gpu_touching=True)
    assert status["status"] == "done", status
    assert busy_reads and status["result"]["pid"] == pid


def test_a_worker_the_parent_stopped_retiring_exits_by_itself(monkeypatch):
    monkeypatch.setattr(warm_worker, "IDLE_LIMIT_S", 120.0)
    monkeypatch.setattr(warm_worker, "CHILD_IDLE_EXIT_S", 1.0)
    pid = _run("warm_safety", tagged_worker, ("idle", False))["result"]["pid"]
    assert _wait_gone(pid, timeout=15)


def test_a_worker_idle_past_the_limit_is_not_reused_even_before_its_timer():
    pid = _run("warm_limit_a", tagged_worker, ("a", False))["result"]["pid"]
    _one().idle_since -= warm_worker.IDLE_LIMIT_S + 1
    after = _run("warm_limit_b", tagged_worker, ("b", False))
    assert after["result"]["runs_here"] == 1 and after["result"]["pid"] != pid
    assert _gone(pid)


def test_another_gpu_job_retires_an_idle_worker_before_it_starts():
    pid = _run("warm_then_other", tagged_worker, ("warm", False))["result"]["pid"]
    assert not _gone(pid)
    seen = []
    bg.clear_job("other_gpu_job")
    assert job_launch.start_job("other_gpu_job", lambda: seen.append(_gone(pid)),
                                gpu_touching=True, line_fields=())
    _wait_end("other_gpu_job")
    assert seen == [True]
    bg.clear_job("other_gpu_job")


def test_a_cpu_job_leaves_the_idle_worker_alone():
    pid = _run("warm_then_cpu", tagged_worker, ("warm", False))["result"]["pid"]
    bg.clear_job("cpu_job")
    assert job_launch.start_job("cpu_job", lambda: None, line_fields=())
    _wait_end("cpu_job")
    assert not _gone(pid)
    bg.clear_job("cpu_job")


class _FakeJob:
    seq = 2
    finished = False
    began = False
    memory = None

    def __init__(self, raw):
        self.raw_queue = raw

    def went_idle(self):
        self.finished = True


def test_a_jobs_reader_skips_other_jobs_items_and_stops_at_its_idle_report():
    raw = queue.Queue()
    job = _FakeJob(raw)
    reader = warm_worker._JobQueue(job)
    raw.put((2, warm_worker._BEGAN))
    raw.put((1, ("ok", "the first job's")))
    raw.put((2, ("ok", "mine")))
    assert reader.get(timeout=1) == ("ok", "mine")
    raw.put((2, (warm_worker._MEMORY, {"threads": 3})))
    raw.put((2, warm_worker._IDLE))
    raw.put((3, ("progress", 0.1, "the next job's")))
    with pytest.raises(queue.Empty):
        reader.get(timeout=1)
    assert job.finished and job.began and job.memory == {"threads": 3}
    with pytest.raises(queue.Empty):
        reader.get(timeout=1)
    assert raw.get_nowait() == (3, ("progress", 0.1, "the next job's"))   # left for its own reader


def _pid_from(pid_file):
    deadline = time.monotonic() + 30
    while not os.path.exists(pid_file) and time.monotonic() < deadline:
        time.sleep(0.05)
    with open(pid_file) as f:
        return int(f.read())


def test_each_job_starts_with_the_temp_folder_and_context_a_fresh_worker_has(tmp_path):
    first = _run("warm_state_a", changes_process_state, ("one", str(tmp_path / "a")))["result"]
    second = _run("warm_state_b", changes_process_state, ("two", str(tmp_path / "b")))["result"]
    assert first["pid"] == second["pid"]
    assert second["var"] == first["var"] == "unset"
    assert second["tempdir"] == first["tempdir"] != str(tmp_path / "a")


def test_children_a_finished_job_left_running_are_killed_before_the_next_job():
    status = _run("warm_children", leaves_children, ())
    assert status["status"] == "done"
    assert all(_wait_gone(pid, timeout=5) for pid in status["result"]["children"])
    after = _run("warm_after_children", tagged_worker, ("next", False))
    assert after["result"]["pid"] == status["result"]["pid"]   # still warm


def test_retiring_kills_the_workers_group_even_once_it_has_exited(monkeypatch):
    _run("warm_group", tagged_worker, ("a", False))
    killed = []
    monkeypatch.setattr(job_process_kill, "_kill_worker_group", lambda proc: killed.append(proc.pid))
    worker = _one()
    worker.kill()
    worker.proc.join(5)
    warm_worker.retire_idle("test")
    assert killed == [worker.proc.pid]


@pytest.mark.parametrize("outcome", [{"gpu_fallback_msgs": ["CUDA error: out of memory"]},
                                     {"gpu_fallback_msgs": [], "device_msg": "GPU unavailable (no CUDA); using CPU"}])
def test_a_run_that_fell_back_to_the_cpu_ends_the_worker(outcome):
    status = _run("warm_fallback", reports, (outcome,))
    assert status["status"] == "done"
    assert _wait_gone(status["result"]["pid"])
    after = _run("warm_after_fallback", tagged_worker, ("next", False))
    assert after["result"]["runs_here"] == 1


def test_a_plain_gpu_run_keeps_the_worker():
    status = _run("warm_on_gpu", reports, ({"gpu_fallback_msgs": [], "device_msg": "Using GPU (float16)"},))
    after = _run("warm_after_gpu", tagged_worker, ("next", False))
    assert after["result"]["pid"] == status["result"]["pid"]


@pytest.mark.parametrize("kind", ["thread", "process"])
def test_an_idle_worker_is_retired_before_another_gpu_jobs_slot_check(monkeypatch, kind):
    pid = _run("warm_before_admit", tagged_worker, ("warm", False))["result"]["pid"]
    seen = []
    real = job_launch._gpu_slot_available_locked
    monkeypatch.setattr(job_launch, "_gpu_slot_available_locked",
                        lambda jid, desc: (seen.append(_gone(pid)), real(jid, desc))[1])
    bg.clear_job("admitted_gpu_job")
    if kind == "thread":
        assert job_launch.start_job("admitted_gpu_job", lambda: None, gpu_touching=True, line_fields=())
    else:
        assert job_launch.start_process_job("admitted_gpu_job", tagged_worker, args=("cold", False),
                                            gpu_touching=True, start_method="spawn", line_fields=())
    _wait_end("admitted_gpu_job")
    bg.clear_job("admitted_gpu_job")
    assert seen == [True]


def test_an_idle_worker_is_retired_before_the_queue_promotes_a_job_that_cannot_use_it(
        monkeypatch, tmp_path):
    release = str(tmp_path / "release")
    _start("warm_then_queued", waits_for_file, (release, "w"), gpu_touching=True)
    pid = _pid_from(release + ".w.pid")
    bg.clear_job("queued_gpu_job")
    ran = threading.Event()
    assert job_launch.start_job("queued_gpu_job", ran.set, gpu_touching=True, line_fields=())
    assert bg.get_status("queued_gpu_job")["status"] == "queued"
    seen = []
    real = job_runner._gpu_slot_available_locked
    monkeypatch.setattr(job_runner, "_gpu_slot_available_locked",
                        lambda jid, desc: (seen.append((jid, _gone(pid))), real(jid, desc))[1])
    open(release, "w").close()
    assert _wait_end("warm_then_queued")["status"] == "done"
    assert ran.wait(15)
    _wait_end("queued_gpu_job")
    bg.clear_job("warm_then_queued")
    bg.clear_job("queued_gpu_job")
    assert ("queued_gpu_job", True) in seen and ("queued_gpu_job", False) not in seen


def test_a_worker_is_not_kept_while_another_gpu_job_runs(monkeypatch):
    monkeypatch.setattr(job_launch, "get_gpu_limit_enabled", lambda: False)
    go = threading.Event()
    bg.clear_job("busy_gpu_job")
    assert job_launch.start_job("busy_gpu_job", lambda: go.wait(30), gpu_touching=True, line_fields=())
    try:
        status = _run("warm_beside_gpu", tagged_worker, ("w", False), gpu_touching=True)
        assert status["status"] == "done"
        assert _gone(status["result"]["pid"]) and _one() is None
    finally:
        go.set()
        _wait_end("busy_gpu_job")
        bg.clear_job("busy_gpu_job")


def _two_slots(monkeypatch):
    monkeypatch.setattr(job_settings, "get_gpu_max_parallel", lambda: 2)
    monkeypatch.setattr(job_launch, "get_gpu_limit_enabled", lambda: False)


def test_with_two_gpu_slots_a_worker_is_kept_between_jobs(monkeypatch):
    _two_slots(monkeypatch)
    first = _run("warm_parallel_a", tagged_worker, ("a", False), gpu_touching=True)
    second = _run("warm_parallel_b", tagged_worker, ("b", False), gpu_touching=True)
    assert second["result"]["pid"] == first["result"]["pid"] and second["result"]["runs_here"] == 2


def test_with_two_gpu_slots_a_worker_is_kept_beside_one_other_running_gpu_job(monkeypatch):
    _two_slots(monkeypatch)
    go = threading.Event()
    bg.clear_job("busy_gpu_job")
    assert job_launch.start_job("busy_gpu_job", lambda: go.wait(30), gpu_touching=True, line_fields=())
    try:
        status = _run("warm_beside_one", tagged_worker, ("w", False), gpu_touching=True)
        assert status["status"] == "done"
        assert not _gone(status["result"]["pid"]) and _one() is not None
    finally:
        go.set()
        _wait_end("busy_gpu_job")
        bg.clear_job("busy_gpu_job")


def test_two_slots_give_two_workers_and_a_third_job_its_own_process(monkeypatch, tmp_path):
    _two_slots(monkeypatch)
    release = str(tmp_path / "release")
    for tag in ("x", "y", "z"):
        _start(f"warm_slot_{tag}", waits_for_file, (release, tag))
        _pid_from(release + f".{tag}.pid")
    with warm_worker._lock:
        assert len(warm_worker._workers) == 2
    assert isinstance(job_table._jobs["warm_slot_z"]["process"], multiprocessing.process.BaseProcess)
    open(release, "w").close()
    done = {tag: _wait_end(f"warm_slot_{tag}") for tag in ("x", "y", "z")}
    assert all(d["status"] == "done" for d in done.values()), done
    assert len({d["result"]["pid"] for d in done.values()}) == 3
    assert _wait_gone(done["z"]["result"]["pid"])
    with warm_worker._lock:
        kept = {w.proc.pid for w in warm_worker._workers}
    assert kept == {done["x"]["result"]["pid"], done["y"]["result"]["pid"]}
    again = _run("warm_slot_again", tagged_worker, ("again", False))
    assert again["result"]["pid"] in kept and again["result"]["runs_here"] == 2


def test_idle_workers_never_outnumber_the_slots_other_gpu_jobs_leave(monkeypatch, tmp_path):
    _two_slots(monkeypatch)
    release = str(tmp_path / "release")
    for tag in ("x", "y"):
        _start(f"warm_full_{tag}", waits_for_file, (release, tag))
        _pid_from(release + f".{tag}.pid")
    with job_table._lock:   # a GPU job running beside both, as an earlier admission left it
        job_table._jobs["busy_gpu_job"] = {"status": "running", "gpu_touching": True}
    try:
        open(release, "w").close()
        assert all(_wait_end(f"warm_full_{tag}")["status"] == "done" for tag in ("x", "y"))
        with warm_worker._lock:
            assert len(warm_worker._workers) == 1   # 1 running + 1 idle fill the 2 slots
    finally:
        with job_table._lock:
            job_table._jobs.pop("busy_gpu_job", None)


def test_an_idle_worker_whose_own_memory_keeps_growing_is_retired(monkeypatch):
    readings = iter([1000.0, 1600.0, 2100.0])
    monkeypatch.setattr(warm_worker, "_process_used_mb", lambda pid: next(readings))
    pids = [_run(f"warm_grow_{n}", tagged_worker, (str(n), False))["result"]["pid"] for n in range(3)]
    assert len(set(pids)) == 1
    assert _wait_gone(pids[0]) and _one() is None   # 2100 - 1000 > GROWTH_LIMIT_MB
    after = _run("warm_grow_after", tagged_worker, ("after", False))
    assert after["result"]["runs_here"] == 1


def test_the_workers_own_memory_counts_as_held_when_the_driver_reports_it(monkeypatch):
    monkeypatch.setattr(warm_worker, "_process_used_mb", lambda pid: 3300.0)
    _run("warm_own_mb", tagged_worker, ("o", False))
    assert _one().held_mb == 3300.0
    assert warm_worker.reusable_held_mb(KEY) == 3300.0 and warm_worker.reusable_held_mb(OTHER_KEY) == 0.0


def test_the_gpu_guard_retires_an_idle_worker_when_memory_is_short():
    pid = _run("warm_guard", tagged_worker, ("w", False))["result"]["pid"]
    gpu_guard._free_idle_models({"unloaded_at": time.time()}, time.time())
    assert _gone(pid) and _one() is None


class _FakeProc:
    def __init__(self, on_start):
        self.start = on_start


@pytest.mark.parametrize("gpu_touching", [True, False])
def test_starting_a_gpu_process_retires_an_idle_worker_at_the_last_moment(gpu_touching):
    pid = _run("warm_last_moment", tagged_worker, ("w", False))["result"]["pid"]
    seen = []
    job_runner._start_worker(_FakeProc(lambda: seen.append(_gone(pid))), gpu_touching)
    assert seen == [gpu_touching]


def test_a_job_sent_to_a_worker_that_was_leaving_runs_in_a_fresh_process():
    pid = _run("warm_leaving_a", tagged_worker, ("a", False))["result"]["pid"]
    status = _run("warm_leaving_b", _TargetTheWarmWorkerCannotTake(), ("b", False))
    assert status["status"] == "done", status
    assert status["result"]["tag"] == "b" and status["result"]["pid"] != pid
    assert _gone(pid) and _one() is None


def test_a_reused_worker_whose_pipe_broke_hands_the_job_to_a_fresh_worker():
    pid = _run("warm_pipe_a", tagged_worker, ("a", False))["result"]["pid"]
    _one().conn.close()
    status = _run("warm_pipe_b", tagged_worker, ("b", False))
    assert status["status"] == "done", status
    assert status["result"]["runs_here"] == 1 and status["result"]["pid"] != pid
    assert _gone(pid)
    again = _run("warm_pipe_c", tagged_worker, ("c", False))
    assert again["result"]["pid"] == status["result"]["pid"]   # the fresh one is the warm one now


def test_a_failed_first_launch_fails_the_start_and_leaves_no_worker(monkeypatch):
    def refuse(self):
        raise OSError("no more processes")
    monkeypatch.setattr(multiprocessing.get_context("spawn").Process, "start", refuse)
    bg.clear_job("warm_no_launch")
    with pytest.raises(OSError):
        job_launch.start_process_job("warm_no_launch", tagged_worker, args=("x", False),
                                     start_method="spawn", line_fields=(), warm_key=KEY)
    assert bg.get_status("warm_no_launch")["status"] == "error"
    assert _one() is None
    bg.clear_job("warm_no_launch")


def _fail_for(job_id, real):
    def maybe_fail(jid, *a, **k):
        if jid == job_id:
            raise RuntimeError("database is locked")
        return real(jid, *a, **k)
    return maybe_fail


@pytest.mark.parametrize("where", ["registration", "run"])
def test_a_start_that_fails_after_claiming_the_worker_gives_it_back(monkeypatch, where):
    pid = _run("warm_claim_a", tagged_worker, ("a", False))["result"]["pid"]
    if where == "registration":
        monkeypatch.setattr(job_runner, "_mirror_locked", _fail_for("warm_claim_b", job_runner._mirror_locked))
    else:
        monkeypatch.setattr(job_launch.gpu_guard, "take_restart_offer_locked",
                            _fail_for("warm_claim_b", job_launch.gpu_guard.take_restart_offer_locked))
    bg.clear_job("warm_claim_b")
    with pytest.raises(RuntimeError):
        job_launch.start_process_job("warm_claim_b", tagged_worker, args=("b", False),
                                     start_method="spawn", line_fields=(), warm_key=KEY)
    monkeypatch.undo()
    with job_table._lock:
        job_table._jobs.pop("warm_claim_b", None)
    after = _run("warm_claim_c", tagged_worker, ("c", False))
    assert after["result"]["pid"] == pid and after["result"]["runs_here"] == 2


def test_the_dead_worker_sweep_ends_a_warm_job_and_its_worker(tmp_path):
    release = str(tmp_path / "never")
    _start("warm_swept", waits_for_file, (release, "s"))
    pid = _pid_from(release + ".s.pid")
    gone_thread = threading.Thread(target=lambda: None)
    gone_thread.start()
    gone_thread.join()
    with job_table._lock:
        job_table._workers["warm_swept"] = (job_table._jobs["warm_swept"], gone_thread)
    assert job_runner.reconcile_dead_workers() == ["warm_swept"]
    assert _wait_gone(pid) and _one() is None
    status = _wait_end("warm_swept")
    assert status["status"] == "error" and status["error"] == job_runner.WORKER_LOST_MESSAGE
    bg.clear_job("warm_swept")


@pytest.mark.parametrize("free_mb, kept", [(28000.0, True), (1000.0, False)])
def test_beside_a_running_gpu_job_only_a_memory_shortage_retires_the_worker_a_job_would_reuse(
        monkeypatch, free_mb, kept):
    _two_slots(monkeypatch)
    monkeypatch.setattr(warm_worker, "UTIL_SETTLE_S", 0.0)
    pid = _run("warm_join_a", tagged_worker, ("a", False))["result"]["pid"]
    _fake_card(monkeypatch, lambda: {"utilization_percent": 96, "memory_used_mb": 32000.0 - free_mb,
                                     "memory_total_mb": 32000.0, "memory_free_mb": free_mb})
    with job_table._lock:   # another GPU job busy on the card: its load is the 96%
        job_table._jobs["busy_gpu_job"] = {"status": "running", "gpu_touching": True}
    try:
        warm_worker.gpu_probe.prefetch(held_mb=warm_worker.reusable_held_mb(KEY))
        warm_worker.retire_if_the_card_reads_busy()
        assert (not _gone(pid)) == kept and (_one() is not None) == kept
    finally:
        with job_table._lock:
            job_table._jobs.pop("busy_gpu_job", None)


def test_with_another_gpu_job_on_the_card_the_cards_reading_is_not_taken_as_the_workers(monkeypatch):
    _two_slots(monkeypatch)
    go = threading.Event()

    def reading():
        worker = _one()
        mine = 4000.0 if worker is not None and worker.started and worker.proc.is_alive() else 0.0
        other = 0.0 if go.is_set() else 6000.0
        used = 1000.0 + mine + other
        return {"utilization_percent": 0, "memory_used_mb": used, "memory_total_mb": 32000.0,
                "memory_free_mb": 32000.0 - used}
    _fake_card(monkeypatch, reading)
    bg.clear_job("busy_gpu_job")
    assert job_launch.start_job("busy_gpu_job", lambda: go.wait(30), gpu_touching=True, line_fields=())
    try:
        assert _run("warm_shared", tagged_worker, ("s", False), gpu_touching=True)["status"] == "done"
        assert _one().held_mb == 0.0   # no per-process reading and no torch here: nothing claimed as its own
    finally:
        go.set()
        _wait_end("busy_gpu_job")
        bg.clear_job("busy_gpu_job")


@pytest.mark.parametrize("total, slots, key, budget", [
    (V100_MB, 1, KEY, 1), (V100_MB, 2, KEY, 2), (V100_MB, 4, KEY, 3),
    (CARD_12_GB_MB, 1, KEY, 1), (CARD_12_GB_MB, 2, KEY, 0), (CARD_12_GB_MB, 2, OTHER_KEY, 1),
    (None, 1, KEY, 0),
    (V100_MB, 1, ("transcribe", "large-v3", True, "", "audio_separator", "qwen3_asr", "whisper", False), 0),
])
def test_the_card_budget_counts_a_kept_separator_in_every_slot_it_runs(total, slots, key, budget):
    """On a 12 GB card two workers each keeping the separator starved the jobs beside
    them (3080 Ti, 2 GPU jobs at once): large-v3 there gets no warm worker at 2 at once."""
    warm_worker._card_total_mb = total
    assert warm_worker.card_budget(key, slots) == budget


def test_a_card_too_small_for_a_warm_worker_runs_each_job_fresh(monkeypatch):
    _two_slots(monkeypatch)
    warm_worker._card_total_mb = CARD_12_GB_MB
    first = _run("warm_small_one", tagged_worker, ("one", False))
    second = _run("warm_small_two", tagged_worker, ("two", False))
    assert first["status"] == second["status"] == "done", (first, second)
    assert first["result"]["pid"] != second["result"]["pid"]
    assert first["result"]["keeps_separator"] is second["result"]["keeps_separator"] is False
    assert _one() is None


def test_a_worker_the_budget_no_longer_allows_is_retired_when_it_goes_idle(monkeypatch, tmp_path):
    _two_slots(monkeypatch)
    release = str(tmp_path / "go")
    _start("warm_shrunk", waits_for_file, (release, "s"))
    pid = _pid_from(release + ".s.pid")
    # "Keep free graphics memory" raised while the job runs: one warm worker no longer fits.
    warm_worker._card_total_mb = CARD_12_GB_MB
    open(release, "w").close()
    assert _wait_end("warm_shrunk")["status"] == "done"
    assert _wait_gone(pid)
    assert _one() is None


def test_a_worker_that_has_served_its_jobs_is_retired_and_says_so(monkeypatch):
    # Windows reports no per-process memory, so the growth limit can't see what each job adds.
    monkeypatch.setattr(warm_worker, "_process_used_mb", lambda pid: None)
    logged = []
    monkeypatch.setattr(warm_worker, "_log", logged.append)
    n = warm_worker.MAX_JOBS_PER_WORKER
    pids = [_run(f"warm_served_{i}", tagged_worker, (str(i), False))["result"]["pid"] for i in range(n)]
    assert len(set(pids)) == 1
    assert _wait_gone(pids[0]) and _one() is None
    assert any(f"retired (it has served {n} jobs), {n} jobs served" in line for line in logged), logged
    after = _run("warm_served_after", tagged_worker, ("after", False))
    assert after["result"]["runs_here"] == 1


class _FakeKernel32:
    """The kernel32 calls _windows_child_pids makes, over a made-up process table:
    pid -> (parent pid, creation time)."""

    def __init__(self, me, table):
        self.me, self.table, self.rows = me, table, []

        def func(fn):
            fn.argtypes = fn.restype = None
            return fn
        self.CreateToolhelp32Snapshot = func(lambda flags, pid: 7)
        self.Process32FirstW = func(lambda snap, ref: self._next(ref, first=True))
        self.Process32NextW = func(lambda snap, ref: self._next(ref))
        self.CloseHandle = func(lambda handle: True)
        self.GetCurrentProcess = func(lambda: -1)
        self.OpenProcess = func(lambda access, inherit, pid: 1000 + pid if pid in self.table else 0)
        self.GetProcessTimes = func(lambda handle, created, *rest: self._times(handle, created))

    def _next(self, ref, first=False):
        if first:
            self.rows = sorted(self.table.items())
        if not self.rows:
            return False
        pid, (parent, _) = self.rows.pop(0)
        ref._obj.th32ProcessID, ref._obj.th32ParentProcessID = pid, parent
        return True

    def _times(self, handle, created):
        pid = self.me if handle == -1 else handle - 1000
        created._obj.dwHighDateTime, created._obj.dwLowDateTime = 0, self.table[pid][1]
        return True


def test_on_windows_only_a_process_started_after_the_worker_counts_as_its_child(monkeypatch):
    import ctypes
    # 20: the worker. 21 started after it. 22 is older: its own parent was an earlier
    # process that had the worker's id, which Windows reused. 23 has another parent.
    table = {20: (1, 500), 21: (20, 600), 22: (20, 400), 23: (5, 700)}
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: _FakeKernel32(20, table), raising=False)
    assert warm_worker._windows_child_pids(20) == [21]
