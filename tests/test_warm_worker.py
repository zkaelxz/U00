"""jobs/warm_worker.py: one spawned worker kept between matching process jobs.
Real spawned children running the fake targets below; no model is loaded."""
import multiprocessing
import os
import queue
import signal
import threading
import time

import pytest

import background_jobs as bg
import db
import storage
from jobs import crash_retry, job_launch, job_runner, job_table, process_worker, warm_worker

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


def test_an_idle_worker_exits_by_itself(monkeypatch):
    monkeypatch.setattr(warm_worker, "IDLE_LIMIT_S", 1.0)
    pid = _run("warm_idle", tagged_worker, ("idle", False))["result"]["pid"]
    assert _wait_gone(pid, timeout=15)
    after = _run("warm_after_idle", tagged_worker, ("next", False))
    assert after["status"] == "done" and after["result"]["pid"] != pid


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

    def went_idle(self):
        self.finished = True


def test_a_jobs_reader_skips_other_jobs_items_and_stops_at_its_idle_report():
    raw, job = queue.Queue(), _FakeJob()
    reader = warm_worker._JobQueue(raw, job)
    raw.put((1, ("ok", "the first job's")))
    raw.put((2, ("ok", "mine")))
    assert reader.get(timeout=1) == ("ok", "mine")
    raw.put((2, warm_worker._IDLE))
    raw.put((3, ("progress", 0.1, "the next job's")))
    with pytest.raises(queue.Empty):
        reader.get(timeout=1)
    assert job.finished
    with pytest.raises(queue.Empty):
        reader.get(timeout=1)
    assert raw.get_nowait() == (3, ("progress", 0.1, "the next job's"))   # left for its own reader
