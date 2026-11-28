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

KEY = ("a", ("transcribe", "large-v3", True, "", "audio_separator", "whisper", "whisper", False))
OTHER_KEY = ("a", ("transcribe", "large-v3-turbo", True, "", "audio_separator", "whisper", "whisper", False))

_runs_here = 0


def tagged_worker(tag, big, result_queue):
    """Counts its runs in this process, so a reused worker says 2. Sets up its
    process group first, as the transcription worker does."""
    global _runs_here
    process_worker.start_own_process_group()
    _runs_here += 1
    result_queue.put(("progress", 0.5, f"working on {tag}"))
    import audio_preprocess
    import whisper_models
    watchdogs = sum(t.name == "parent-watchdog" for t in threading.enumerate())
    result = {"tag": tag, "pid": os.getpid(), "runs_here": _runs_here, "watchdogs": watchdogs,
              "keeps": (audio_preprocess.keep_separator_loaded, whisper_models.keep_whisper_between_runs)}
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
    yield
    with warm_worker._lock:
        left, warm_worker._current = warm_worker._current, None
    if left is not None:
        left.retire("the test ended")


def test_two_matching_jobs_run_in_one_worker_and_each_reads_only_its_own_results():
    first = _run("warm_one", tagged_worker, ("one", True))
    second = _run("warm_two", tagged_worker, ("two", True))
    assert first["status"] == second["status"] == "done", (first, second)
    one, two = first["result"], second["result"]
    assert (one["tag"], one["runs_here"], one["filler"]) == ("one", 1, "one" * 5000)
    assert (two["tag"], two["runs_here"], two["filler"]) == ("two", 2, "two" * 5000)
    assert one["pid"] == two["pid"] and not _gone(two["pid"])
    assert one["watchdogs"] == two["watchdogs"] == 1
    assert one["keeps"] == (False, False)   # arm "a" keeps the process only
    assert _result_files() == []


@pytest.mark.parametrize("arm, keeps", [("b", (True, False)), ("c", (True, True))])
def test_the_arm_decides_what_the_worker_keeps_loaded(arm, keeps):
    status = _run(f"warm_arm_{arm}", tagged_worker, (arm, False), key=(arm, KEY[1]))
    assert status["result"]["keeps"] == keeps


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
    assert warm_worker._current is None
    bg.clear_job("cold_one")


@pytest.mark.parametrize("value, key", [("", None), ("x", None), ("C", "c")])
def test_the_switch_names_an_arm_or_leaves_transcription_as_it_was(monkeypatch, value, key):
    monkeypatch.setenv(warm_worker.ENV_VAR, value)
    found = warm_worker.transcription_key("large-v3", True, None, "audio_separator", "whisper", "whisper")
    assert (found and found[0]) == key
    assert warm_worker.transcription_key("large-v3", False, None, "audio_separator", "whisper",
                                         "whisper") is None


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
    assert warm_worker._current is None   # retired by the parent, not left to exit
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
            after.append(warm_worker._current is worker and worker.proc.is_alive())
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
    assert _wait_gone(pid, timeout=15) and warm_worker._current is None


def test_a_replacement_worker_that_found_the_slot_taken_ends_at_the_idle_limit(monkeypatch):
    monkeypatch.setattr(warm_worker, "IDLE_LIMIT_S", 1.0)
    pid = _run("warm_replaced_a", tagged_worker, ("a", False))["result"]["pid"]
    warm_worker._current.conn.close()
    other = warm_worker._Worker(OTHER_KEY)
    real_forget = warm_worker._forget

    def forget_then_another_job_claims(worker):
        real_forget(worker)
        with warm_worker._lock:
            if warm_worker._current is None:
                warm_worker._current = other
    monkeypatch.setattr(warm_worker, "_forget", forget_then_another_job_claims)
    status = _run("warm_replaced_b", tagged_worker, ("b", False))
    assert status["status"] == "done", status
    fresh = status["result"]["pid"]
    assert fresh != pid and warm_worker._current is other
    assert _wait_gone(fresh, timeout=15)


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


def test_a_worker_the_parent_stopped_retiring_exits_by_itself(monkeypatch):
    monkeypatch.setattr(warm_worker, "IDLE_LIMIT_S", 120.0)
    monkeypatch.setattr(warm_worker, "CHILD_IDLE_EXIT_S", 1.0)
    pid = _run("warm_safety", tagged_worker, ("idle", False))["result"]["pid"]
    assert _wait_gone(pid, timeout=15)


def test_a_worker_idle_past_the_limit_is_not_reused_even_before_its_timer():
    pid = _run("warm_limit_a", tagged_worker, ("a", False))["result"]["pid"]
    warm_worker._current.idle_since -= warm_worker.IDLE_LIMIT_S + 1
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
    raw.put((2, warm_worker._IDLE))
    raw.put((3, ("progress", 0.1, "the next job's")))
    with pytest.raises(queue.Empty):
        reader.get(timeout=1)
    assert job.finished and job.began
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
    worker = warm_worker._current
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
        assert _gone(status["result"]["pid"]) and warm_worker._current is None
    finally:
        go.set()
        _wait_end("busy_gpu_job")
        bg.clear_job("busy_gpu_job")


def test_a_worker_is_not_kept_when_gpu_jobs_may_run_in_parallel(monkeypatch):
    monkeypatch.setattr(job_settings, "get_gpu_max_parallel", lambda: 2)
    status = _run("warm_parallel", tagged_worker, ("w", False))
    assert _gone(status["result"]["pid"]) and warm_worker._current is None


def test_the_gpu_guard_retires_an_idle_worker_when_memory_is_short():
    pid = _run("warm_guard", tagged_worker, ("w", False))["result"]["pid"]
    gpu_guard._free_idle_models({"unloaded_at": time.time()}, time.time())
    assert _gone(pid) and warm_worker._current is None


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
    assert _gone(pid) and warm_worker._current is None


def test_a_reused_worker_whose_pipe_broke_hands_the_job_to_a_fresh_worker():
    pid = _run("warm_pipe_a", tagged_worker, ("a", False))["result"]["pid"]
    warm_worker._current.conn.close()
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
    assert warm_worker._current is None
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
    assert _wait_gone(pid) and warm_worker._current is None
    status = _wait_end("warm_swept")
    assert status["status"] == "error" and status["error"] == job_runner.WORKER_LOST_MESSAGE
    bg.clear_job("warm_swept")
