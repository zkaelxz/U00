"""jobs/crash_retry.py and the process watcher's one CPU run after a worker
dies of a native crash. Real child processes: the GPU run kills itself with
SIGSEGV, as a fault in CUDA or a model library does."""
import os
import signal
import threading
import time

import pytest

import background_jobs as bg
from jobs import crash_retry, job_launch, job_runner

pytestmark = pytest.mark.skipif(os.name == "nt", reason="crashes the child with SIGSEGV")


def _runs(run_dir):
    return sorted(n for n in os.listdir(run_dir) if n.startswith("run-"))


def crash_on_gpu_worker(run_dir, use_gpu, crash_on_cpu, result_queue):
    """Records each run; the GPU run (and, when asked, the CPU run) crashes.
    The CPU run reports whether the GPU run's process was already gone."""
    n = len(_runs(run_dir)) + 1
    with open(os.path.join(run_dir, f"run-{n}-{'gpu' if use_gpu else 'cpu'}"), "w") as f:
        f.write(str(os.getpid()))
    if use_gpu or crash_on_cpu:
        result_queue.put(("progress", 0.4, "Hearing"))
        os.kill(os.getpid(), signal.SIGSEGV)
    with open(os.path.join(run_dir, "run-1-gpu")) as f:
        gpu_pid = int(f.read())
    try:
        os.kill(gpu_pid, 0)
        gpu_gone = False
    except ProcessLookupError:
        gpu_gone = True
    result_queue.put(("ok", {"on_gpu": use_gpu, "gpu_worker_gone": gpu_gone}))


def exits_on_gpu_worker(run_dir, use_gpu, crash_on_cpu, result_queue):
    with open(os.path.join(run_dir, f"run-{len(_runs(run_dir)) + 1}"), "w"):
        pass
    os._exit(1)


def _wait_end(job_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = bg.get_status(job_id)
        if status and status["status"] not in ("queued", "running"):
            return status
        time.sleep(0.05)
    return bg.get_status(job_id)


@pytest.fixture
def run_dir(tmp_path, isolated_db):
    yield str(tmp_path)


def _start(job_id, run_dir, worker=crash_on_gpu_worker, crash_on_cpu=False, retry=True,
           **kw):
    bg.clear_job(job_id)
    return job_launch.start_process_job(
        job_id, worker, args=(run_dir, True, crash_on_cpu),
        cpu_retry_args=(run_dir, False, crash_on_cpu) if retry else None, **kw)


def test_a_native_crash_runs_the_job_once_more_on_the_cpu(run_dir, monkeypatch):
    seen, messages = [], []
    real_update = job_runner.update_progress
    monkeypatch.setattr(job_runner, "update_progress",
                        lambda jid, frac, msg="": (messages.append(msg), real_update(jid, frac, msg)))
    assert _start("crash_retry_ok", run_dir, on_done=lambda jid, result: seen.append(result))
    status = _wait_end("crash_retry_ok")
    assert status["status"] == "done", status
    assert _runs(run_dir) == ["run-1-gpu", "run-2-cpu"]
    reason = crash_retry.crash_reason(-signal.SIGSEGV)
    assert seen == [{"on_gpu": False, "gpu_worker_gone": True, crash_retry.REASON_KEY: reason}]
    assert job_runner.CPU_RETRY_MESSAGE in messages
    bg.clear_job("crash_retry_ok")


def test_cpu_retry_args_given_as_a_function_are_read_when_the_cpu_run_starts(run_dir):
    calls = []

    def args_now():
        # By now the GPU run has started and crashed.
        calls.append(_runs(run_dir))
        return (run_dir, False, False)

    seen = []
    bg.clear_job("crash_retry_late_args")
    assert job_launch.start_process_job(
        "crash_retry_late_args", crash_on_gpu_worker, args=(run_dir, True, False),
        cpu_retry_args=args_now, on_done=lambda jid, result: seen.append(result))
    status = _wait_end("crash_retry_late_args")
    assert status["status"] == "done", status
    assert calls == [["run-1-gpu"]]
    assert seen and seen[0]["on_gpu"] is False
    bg.clear_job("crash_retry_late_args")


def test_a_crash_of_the_cpu_run_ends_the_job_without_a_third_run(run_dir):
    assert _start("crash_retry_twice", run_dir, crash_on_cpu=True)
    status = _wait_end("crash_retry_twice")
    assert status["status"] == "error"
    assert "exited unexpectedly" in status["error"]
    assert _runs(run_dir) == ["run-1-gpu", "run-2-cpu"]
    bg.clear_job("crash_retry_twice")


def test_without_cpu_retry_args_a_crash_ends_the_job(run_dir):
    assert _start("crash_no_retry", run_dir, retry=False)
    status = _wait_end("crash_no_retry")
    assert status["status"] == "error" and "exited unexpectedly" in status["error"]
    assert _runs(run_dir) == ["run-1-gpu"]
    bg.clear_job("crash_no_retry")


def test_an_exit_that_is_not_a_native_crash_is_not_run_again(run_dir):
    assert _start("crash_plain_exit", run_dir, worker=exits_on_gpu_worker)
    status = _wait_end("crash_plain_exit")
    assert status["status"] == "error" and "exit code 1" in status["error"]
    assert _runs(run_dir) == ["run-1"]
    bg.clear_job("crash_plain_exit")


def test_a_cancel_that_lands_while_the_crashed_worker_is_reaped_starts_no_cpu_run(
        run_dir, monkeypatch):
    real_reap = job_runner.reap_worker

    def reap_then_cancel(proc, kill_whole_tree):
        real_reap(proc, kill_whole_tree)
        bg.request_cancel("crash_retry_cancel")
    monkeypatch.setattr(job_runner, "reap_worker", reap_then_cancel)
    assert _start("crash_retry_cancel", run_dir)
    status = _wait_end("crash_retry_cancel")
    assert status["status"] == "cancelled"
    assert _runs(run_dir) == ["run-1-gpu"]
    bg.clear_job("crash_retry_cancel")


def test_a_clean_stop_in_progress_starts_no_cpu_run(run_dir, monkeypatch):
    monkeypatch.setattr(job_launch, "is_stopping", lambda: True)
    assert _start("crash_retry_stopping", run_dir)
    status = _wait_end("crash_retry_stopping")
    assert status["status"] == "cancelled"
    assert _runs(run_dir) == ["run-1-gpu"]
    bg.clear_job("crash_retry_stopping")


def test_a_queued_job_keeps_its_cpu_retry(run_dir):
    release, started = threading.Event(), threading.Event()
    job_launch.start_job("crash_retry_blocker",
                         lambda: (started.set(), release.wait(timeout=10.0)), gpu_touching=True)
    started.wait(timeout=5.0)
    assert _start("crash_retry_queued", run_dir, gpu_touching=True)
    assert bg.get_status("crash_retry_queued")["status"] == "queued"
    release.set()
    status = _wait_end("crash_retry_queued")
    assert status["status"] == "done", status
    assert _runs(run_dir) == ["run-1-gpu", "run-2-cpu"]
    bg.clear_job("crash_retry_blocker")
    bg.clear_job("crash_retry_queued")


@pytest.mark.parametrize("exitcode, crashed", [
    (-signal.SIGSEGV, True), (-signal.SIGABRT, True), (-signal.SIGBUS, True),
    (3221225477, True), (0xC0000409, True),
    (-signal.SIGKILL, False), (-signal.SIGTERM, False), (0xC000013A, False), (0, False),
    (1, False), (None, False)])
def test_only_a_native_fault_counts_as_a_crash(exitcode, crashed):
    assert crash_retry.is_native_crash(exitcode) is crashed


def test_a_failed_result_is_left_as_it_is():
    assert crash_retry.mark_result(("ok", {"a": 1}), None) == ("ok", {"a": 1})
    assert crash_retry.mark_result(("error", "E", "m"), "why") == ("error", "E", "m")
    assert crash_retry.mark_result(None, "why") is None
