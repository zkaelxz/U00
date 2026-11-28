"""Tests for jobs that run in a worker process: starting, the result watcher, large results, orphaned workers and process trees."""

import sys
import os
import queue
import threading
import time

import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs as bg
from jobs import gpu_queue, job_launch, job_runner, job_settings, job_table, process_worker
import diagnostics_torch
from jobs import gpu_slots
from tests.test_background_jobs import (  # noqa: F401  (_private_library is the autouse fixture)
    _FakeProcess, _install_fake_process, _private_library, _wait, _wait_for, _wait_for_status,
)


class TestProcessBasedJobs:
    """Step 4d: the first process-based job type in this codebase.
    start_process_job() runs target in a real multiprocessing.Process
    instead of a thread, so request_cancel() can actually terminate the
    underlying work -- built for diarization, whose pyannote pipeline
    call has no cooperative-cancellation checkpoint of its own, unlike
    every other job type here. multiprocessing.Process itself is faked
    throughout (_FakeProcess above); the real background_jobs lock,
    watcher thread, and GPU-queue machinery all run for real."""

    def test_successful_job_reports_the_result(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)

        def fake_worker(a, b, result_queue):
            result_queue.put(("ok", {"sum": a + b}))

        job_id = "test_process_ok"
        bg.clear_job(job_id)
        assert job_launch.start_process_job(job_id, fake_worker, args=(2, 3)) is True
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "done"
        assert status["result"] == {"sum": 5}
        bg.clear_job(job_id)

    def test_error_in_the_subprocess_is_reported_not_swallowed(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)

        def fake_worker(result_queue):
            result_queue.put(("error", "RuntimeError", "boom"))

        job_id = "test_process_error"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, fake_worker, args=())
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert "RuntimeError" in status["error"] and "boom" in status["error"]
        bg.clear_job(job_id)

    def test_a_refused_file_in_the_subprocess_is_named_plainly(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)

        def fake_worker(result_queue):
            result_queue.put(("error", "PermissionError",
                              "[Errno 13] Permission denied: 'C:\\Users\\x\\audio.wav'"))

        job_id = "test_process_denied"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, fake_worker, args=())
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert status["error"].startswith("Permission denied: Baihe is not allowed to open a file")
        bg.clear_job(job_id)

    def test_a_result_that_did_not_run_for_a_missing_package_ends_as_an_error(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        plain = "Transcription isn't installed yet. Open Diagnostics to install it."

        def fake_worker(result_queue):
            result_queue.put(("ok", {"failed_reason": "dependency_missing", "detail": plain}))

        job_id = "test_process_missing_package"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, fake_worker, args=())
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert status["error"] == plain
        assert status["result"]["failed_reason"] == "dependency_missing"
        bg.clear_job(job_id)

    def test_subprocess_exiting_with_no_result_is_reported_as_an_error(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=False, exitcode_if_no_result=1)

        job_id = "test_process_crash"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda result_queue: None, args=())
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert "exit code 1" in status["error"]
        bg.clear_job(job_id)

    def test_cancel_actually_terminates_the_process_not_just_a_flag(self, monkeypatch):
        """Exit condition: mocked Process.terminate() is actually called
        (not just cancel_requested set), and status ends up 'cancelled',
        not 'failed' or 'completed'."""
        instances = _install_fake_process(monkeypatch, alive_forever=True)

        job_id = "test_process_cancel"
        bg.clear_job(job_id)
        assert job_launch.start_process_job(job_id, lambda result_queue: None, args=()) is True
        status = None
        deadline = time.time() + 2
        while time.time() < deadline:
            status = bg.get_status(job_id)
            if status and status["status"] == "running":
                break
            time.sleep(0.01)
        assert status["status"] == "running"

        bg.request_cancel(job_id)
        status = _wait_for_status(job_id, "running")

        assert status["status"] == "cancelled"
        assert status["status"] not in ("failed", "error", "done")
        assert instances[0].terminated is True
        bg.clear_job(job_id)

    def test_clearing_a_running_process_job_terminates_it(self, monkeypatch):
        instances = _install_fake_process(monkeypatch, alive_forever=True)
        job_id = "test_process_clear"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda result_queue: None, args=())
        deadline = time.time() + 2
        while time.time() < deadline and not bg.is_running(job_id):
            time.sleep(0.01)
        assert bg.is_running(job_id)

        bg.clear_job(job_id)
        deadline = time.time() + 2
        while time.time() < deadline and not instances[0].terminated:
            time.sleep(0.02)
        assert instances[0].terminated is True

    def test_a_process_job_queues_behind_a_running_gpu_thread_job(self, monkeypatch):
        """Process-based and thread-based GPU jobs share the same GPU
        guard -- a diarization subprocess must still queue behind a
        running transcription thread job."""
        _install_fake_process(monkeypatch, run_target_on_start=True)
        release = threading.Event()
        started = threading.Event()
        job_launch.start_job("test_process_gpu_thread", lambda: (started.set(), release.wait(timeout=2.0)),
                     gpu_touching=True)
        started.wait(timeout=2.0)

        job_id = "test_process_gpu_process"
        bg.clear_job(job_id)
        assert job_launch.start_process_job(
            job_id, lambda result_queue: result_queue.put(("ok", {})),
            args=(), gpu_touching=True) is True
        assert bg.get_status(job_id)["status"] == "queued"

        release.set()
        _wait("test_process_gpu_thread")
        # Migration Slice 7's status-transition mirror to job_records does
        # real (if brief) DB I/O at the queued->running step, which is
        # enough to make the intermediate "running" state observable by a
        # poll that previously only ever saw "queued" or "done" -- wait
        # for a genuinely terminal status, not just "no longer queued".
        deadline = time.time() + 2.0
        status = bg.get_status(job_id)
        while time.time() < deadline and status and status["status"] in ("queued", "running"):
            time.sleep(0.01)
            status = bg.get_status(job_id)
        assert status["status"] == "done"
        bg.clear_job("test_process_gpu_thread")
        bg.clear_job(job_id)


class TestProcessJobOnDone:
    """Migration Slice 49: start_process_job(on_done=...) completion hook."""

    def test_hook_runs_with_result_before_done(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        seen = []

        def hook(jid, result):
            seen.append((jid, result, bg.get_status(jid)["status"]))

        job_id = "test_ondone_ok"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {"v": 1})), args=(), on_done=hook)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "done"
        assert seen == [(job_id, {"v": 1}, "running")]
        bg.clear_job(job_id)

    def test_an_exception_with_no_message_still_gives_a_reason(self):
        def fail():
            raise TimeoutError()

        job_id = "test_empty_message_error"
        bg.clear_job(job_id)
        assert job_launch.start_job(job_id, fail) is True
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert status["error"].startswith("TimeoutError: Something took too long")
        bg.clear_job(job_id)

    def test_hook_raising_marks_error_with_redacted_message(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)

        def hook(jid, result):
            raise RuntimeError("boom key=sk-abcdefghijklmnopqrstuvwxyz123456")

        job_id = "test_ondone_raises"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(), on_done=hook)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert "boom" in status["error"]
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in status["error"]
        bg.clear_job(job_id)

    def test_subprocess_error_traceback_is_logged_redacted(self, monkeypatch, isolated_db):
        import os
        import applog
        import db
        from jobs import process_worker
        _install_fake_process(monkeypatch, run_target_on_start=True)
        key = "sk-abcdefghijklmnopqrstuvwxyz654321"

        def failing_step():
            raise RuntimeError(f"model.bin unreadable {key}")

        def target(q):
            try:
                failing_step()
            except RuntimeError as exc:
                q.put(process_worker.error_item(exc))
        job_id = "test_process_err_traceback"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, target, args=())
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert status["error"].startswith("RuntimeError: model.bin unreadable")
        for h in applog.get_logger().handlers:
            h.flush()
        with open(os.path.join(db.LIBRARY_DIR, "logs", "app.log"), encoding="utf-8") as f:
            log_text = f.read()
        assert "worker traceback:" in log_text and "failing_step" in log_text
        assert key not in log_text
        bg.clear_job(job_id)

    def test_hook_not_called_on_subprocess_error(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        seen = []
        job_id = "test_ondone_err"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("error", "RuntimeError", "x")), args=(),
                             on_done=lambda j, r: seen.append(j))
        assert _wait_for_status(job_id, "running")["status"] == "error"
        assert seen == []
        bg.clear_job(job_id)

    def test_subprocess_error_is_redacted_in_record_and_log(self, monkeypatch, isolated_db):
        import os
        import applog
        import db
        _install_fake_process(monkeypatch, run_target_on_start=True)
        key = "sk-abcdefghijklmnopqrstuvwxyz123456"
        job_id = "test_process_err_redacted"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("error", "RuntimeError", f"401 bad key {key}")),
                             args=())
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert "401 bad key" in status["error"]
        assert key not in status["error"]
        rec = db.get_job_record(job_id)
        assert rec["status"] == "error" and key not in (rec["error"] or "")
        for h in applog.get_logger().handlers:
            h.flush()
        with open(os.path.join(db.LIBRARY_DIR, "logs", "app.log"), encoding="utf-8") as f:
            log_text = f.read()
        assert f"job {job_id} failed" in log_text
        assert key not in log_text
        bg.clear_job(job_id)

    def test_hook_not_called_on_cancel(self, monkeypatch):
        _install_fake_process(monkeypatch, alive_forever=True)
        seen = []
        job_id = "test_ondone_cancel"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: None, args=(), on_done=lambda j, r: seen.append(j))
        deadline = time.time() + 2
        while time.time() < deadline and not bg.is_running(job_id):
            time.sleep(0.01)
        bg.request_cancel(job_id)
        assert _wait_for_status(job_id, "running")["status"] == "cancelled"
        assert seen == []
        bg.clear_job(job_id)

    def test_queued_start_preserves_the_hook(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        release = threading.Event()
        started = threading.Event()
        job_launch.start_job("test_ondone_gpu_thread",
                     lambda: (started.set(), release.wait(timeout=2.0)), gpu_touching=True)
        started.wait(timeout=2.0)
        seen = []
        job_id = "test_ondone_queued"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {"r": 2})), args=(),
                             gpu_touching=True, on_done=lambda j, r: seen.append((j, r)))
        assert bg.get_status(job_id)["status"] == "queued"
        release.set()
        deadline = time.time() + 3.0
        status = bg.get_status(job_id)
        while time.time() < deadline and status and status["status"] in ("queued", "running"):
            time.sleep(0.01)
            status = bg.get_status(job_id)
        assert status["status"] == "done"
        assert seen == [(job_id, {"r": 2})]
        bg.clear_job("test_ondone_gpu_thread")
        bg.clear_job(job_id)

    def test_queued_start_preserves_the_partial_hook(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        release = threading.Event()
        started = threading.Event()
        job_launch.start_job("test_partial_gpu_thread",
                             lambda: (started.set(), release.wait(timeout=2.0)), gpu_touching=True)
        started.wait(timeout=2.0)
        seen = []

        def worker(q):
            process_worker.report_partial(q, {"part": 1})
            q.put(("ok", {}))
        job_id = "test_partial_queued"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, worker, args=(), gpu_touching=True,
                                     on_partial=lambda j, p: seen.append((j, p)))
        assert bg.get_status(job_id)["status"] == "queued"
        release.set()
        deadline = time.time() + 3.0
        status = bg.get_status(job_id)
        while time.time() < deadline and status and status["status"] in ("queued", "running"):
            time.sleep(0.01)
            status = bg.get_status(job_id)
        assert status["status"] == "done"
        assert seen == [(job_id, {"part": 1})]
        bg.clear_job("test_partial_gpu_thread")
        bg.clear_job(job_id)

    def test_hook_return_value_becomes_the_result(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        job_id = "test_ondone_returns"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {"raw": 1})), args=(),
                             on_done=lambda j, r: {"applied": r["raw"] + 1})
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "done" and status["result"] == {"applied": 2}
        bg.clear_job(job_id)

    def test_cancel_after_the_subprocess_finished_applies_nothing(self, monkeypatch):
        """A cancel landing between the worker's result and the hook: the
        hook never runs and the job ends cancelled, not done."""
        _install_fake_process(monkeypatch, run_target_on_start=True)
        job_id = "test_ondone_late_cancel"

        class CancelOnResultQueue(queue.Queue):
            def get(self, *a, **k):
                item = super().get(*a, **k)
                if item[0] == "ok":
                    bg.request_cancel(job_id)
                return item

            def close(self):
                pass
        monkeypatch.setattr(job_runner.multiprocessing, "Queue", CancelOnResultQueue)
        seen, finished = [], []
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {"v": 1})), args=(),
                             on_done=lambda j, r: seen.append(j), on_finish=finished.append)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "cancelled"
        assert status["message"] == job_runner.CANCELLED_MESSAGE
        assert seen == []
        assert _wait_for(lambda: finished == [job_id])
        bg.clear_job(job_id)

    def test_finish_hook_runs_on_done_and_on_cancel(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        finished = []
        bg.clear_job("test_finish_done")
        job_launch.start_process_job("test_finish_done", lambda q: q.put(("ok", {})), args=(),
                             on_finish=finished.append)
        assert _wait_for_status("test_finish_done", "running")["status"] == "done"
        assert _wait_for(lambda: finished == ["test_finish_done"])
        bg.clear_job("test_finish_done")

        _install_fake_process(monkeypatch, alive_forever=True)
        bg.clear_job("test_finish_cancel")
        job_launch.start_process_job("test_finish_cancel", lambda q: None, args=(),
                             on_finish=finished.append)
        assert _wait_for(lambda: bg.is_running("test_finish_cancel"))
        bg.request_cancel("test_finish_cancel")
        assert _wait_for_status("test_finish_cancel", "running")["status"] == "cancelled"
        assert _wait_for(lambda: finished == ["test_finish_done", "test_finish_cancel"])
        bg.clear_job("test_finish_cancel")

    def test_kill_whole_tree_job_cancel_kills_the_whole_tree(self, monkeypatch):
        instances = _install_fake_process(monkeypatch, alive_forever=True)
        killed = []

        def fake_kill_tree(proc):
            killed.append(proc)
            proc.terminate()
        monkeypatch.setattr(job_runner, "kill_tree", fake_kill_tree)
        job_id = "test_kill_whole_tree_cancel"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: None, args=(), kill_whole_tree=True)
        assert _wait_for(lambda: bg.is_running(job_id))
        bg.request_cancel(job_id)
        assert _wait_for_status(job_id, "running")["status"] == "cancelled"
        assert killed == [instances[0]]
        bg.clear_job(job_id)

    def test_a_worker_alive_after_its_result_is_killed_before_the_slot_and_hook(self, monkeypatch):
        instances = _install_fake_process(monkeypatch, alive_forever=True)
        events = []

        def fake_kill_tree(proc):
            events.append("kill")
            proc.terminate()
        monkeypatch.setattr(job_runner, "kill_tree", fake_kill_tree)
        real_release = gpu_queue._release_gpu_slot
        monkeypatch.setattr(job_runner, "_release_gpu_slot",
                            lambda *a, **k: (events.append("release"), real_release(*a, **k)))
        job_id = "test_alive_after_result"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: None, args=(), gpu_touching=True,
                             kill_whole_tree=True, on_finish=lambda j: events.append("finish"))
        assert _wait_for(lambda: bg.is_running(job_id))
        instances[0]._args[-1].put(("ok", {"v": 1}))
        assert _wait_for(lambda: "finish" in events)
        assert bg.get_status(job_id)["status"] == "done"
        assert events == ["kill", "release", "finish"]
        assert instances[0].terminated is True
        assert gpu_slots.holder_count() == 0
        bg.clear_job(job_id)

    def test_a_worker_that_exited_is_not_killed_again(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        killed = []
        monkeypatch.setattr(job_runner, "kill_tree", killed.append)
        finished = []
        job_id = "test_exited_not_killed"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(), kill_whole_tree=True,
                             on_finish=finished.append)
        assert _wait_for(lambda: finished == [job_id])
        assert killed == []
        bg.clear_job(job_id)

    def test_an_ended_runs_late_release_keeps_a_rerun_s_gpu_lock(self, monkeypatch):
        """The lock row is named after the job id: a re-run started between
        the first run's final status and its watcher's release takes the
        same row over, and the old watcher must not delete it."""
        job_id = "transcribe_9999"
        instances = []

        def factory(target, args, daemon=True):
            first = not instances
            instances.append(_FakeProcess(target, args, daemon=daemon,
                                          run_target_on_start=first, alive_forever=not first))
            return instances[-1]
        monkeypatch.setattr(job_runner.multiprocessing, "Process", factory)
        real_notify = job_settings._notify_job_finished
        reran, finished = [], []

        def notify_then_rerun(*a, **k):
            real_notify(*a, **k)
            if not reran:
                reran.append(job_launch.start_process_job(job_id, lambda q: None, args=(),
                                                  gpu_touching=True))
        monkeypatch.setattr(job_runner, "_notify_job_finished", notify_then_rerun)
        bg.clear_job(job_id)

        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(), gpu_touching=True,
                             on_finish=finished.append)
        assert _wait_for(lambda: finished == [job_id])
        assert reran == [True]
        assert bg.get_status(job_id)["status"] == "running"
        assert gpu_slots.holder_count() == 1

        bg.request_cancel(job_id)
        assert _wait_for(lambda: bg.get_status(job_id)["status"] == "cancelled")
        assert _wait_for(lambda: gpu_slots.holder_count() == 0)
        bg.clear_job(job_id)

    def test_start_method_picks_the_context_also_through_the_gpu_queue(self, monkeypatch):
        contexts = []

        class FakeContext:
            def Queue(self):
                return queue.Queue()

            def Process(self, target, args, daemon=True):
                return _FakeProcess(target, args, daemon=daemon, run_target_on_start=True)
        monkeypatch.setattr(job_runner.multiprocessing, "get_context",
                            lambda method: contexts.append(method) or FakeContext())
        _install_fake_process(monkeypatch, run_target_on_start=True)

        bg.clear_job("test_start_method_default")
        job_launch.start_process_job("test_start_method_default", lambda q: q.put(("ok", {})), args=())
        assert _wait_for_status("test_start_method_default", "running")["status"] == "done"
        assert contexts == []

        bg.clear_job("test_start_method_direct")
        job_launch.start_process_job("test_start_method_direct", lambda q: q.put(("ok", {})), args=(),
                             start_method="spawn")
        assert _wait_for_status("test_start_method_direct", "running")["status"] == "done"
        assert contexts == ["spawn"]

        release, started = threading.Event(), threading.Event()
        job_launch.start_job("test_start_method_gpu_thread",
                     lambda: (started.set(), release.wait(timeout=2.0)), gpu_touching=True)
        started.wait(timeout=2.0)
        job_id = "test_start_method_queued"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(), gpu_touching=True,
                             start_method="spawn")
        assert bg.get_status(job_id)["status"] == "queued"
        release.set()
        assert _wait_for(lambda: bg.get_status(job_id)["status"] == "done")
        assert contexts == ["spawn", "spawn"]
        for j in ("test_start_method_default", "test_start_method_direct",
                  "test_start_method_gpu_thread", job_id):
            bg.clear_job(j)

    def _queue_behind_a_gpu_thread_job(self, job_id, on_finish):
        """Starts a GPU thread job that holds the slot until the returned
        event is set, then queues process job `job_id` behind it."""
        release, started = threading.Event(), threading.Event()
        bg.clear_job("test_finish_holder")
        job_launch.start_job("test_finish_holder", lambda: (started.set(), release.wait(timeout=5.0)),
                     gpu_touching=True)
        started.wait(timeout=2.0)
        bg.clear_job(job_id)
        assert job_launch.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(),
                                    gpu_touching=True, on_finish=on_finish) is True
        assert bg.get_status(job_id)["status"] == "queued"
        return release

    def _end_holder(self, release):
        release.set()
        _wait("test_finish_holder")
        bg.clear_job("test_finish_holder")

    @pytest.mark.parametrize("end", ["request_cancel", "cancel_queued", "clear_job",
                                     "clear_all_jobs"])
    def test_finish_hook_runs_once_when_a_queued_job_ends(self, monkeypatch, end):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        finished = []
        job_id = "test_finish_queued"
        release = self._queue_behind_a_gpu_thread_job(job_id, finished.append)

        getattr(bg, end)(*(() if end == "clear_all_jobs" else (job_id,)))

        assert finished == [job_id]
        self._end_holder(release)
        time.sleep(0.1)
        assert finished == [job_id]
        assert (bg.get_status(job_id) or {}).get("status") in (None, "cancelled")
        bg.clear_job(job_id)

    def test_finish_hook_runs_once_when_a_promoted_job_fails_to_start(self, monkeypatch):
        instances = _install_fake_process(monkeypatch, run_target_on_start=True)
        finished = []
        job_id = "test_finish_promote_fails"
        release = self._queue_behind_a_gpu_thread_job(job_id, finished.append)

        def refuse_to_start():
            raise OSError("cannot start a process")
        monkeypatch.setattr(_FakeProcess, "start", lambda self: refuse_to_start())
        self._end_holder(release)

        assert _wait_for(lambda: finished == [job_id])
        status = bg.get_status(job_id)
        assert status["status"] == "error" and "cannot start a process" in status["error"]
        assert len(instances) == 1
        time.sleep(0.1)
        assert finished == [job_id]
        bg.clear_job(job_id)

    def test_a_process_that_cannot_be_built_at_promotion_ends_both_jobs_cleanly(self, monkeypatch):
        """mp.Process() raising (no fds, no /dev/shm) while another process
        job's watcher promotes the queued one: the finishing job's on_finish
        still runs once, the promoted job ends failed with its on_finish run
        once, its GPU row is released and the id can start again."""
        instances = _install_fake_process(monkeypatch, alive_forever=True)
        finished = []
        bg.clear_job("test_build_holder")
        assert job_launch.start_process_job("test_build_holder", lambda q: None, args=(),
                                    gpu_touching=True, on_finish=finished.append)
        assert _wait_for(lambda: bg.is_running("test_build_holder"))
        job_id = "test_build_promoted"
        bg.clear_job(job_id)
        assert job_launch.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(),
                                    gpu_touching=True, on_finish=finished.append) is True
        assert bg.get_status(job_id)["status"] == "queued"

        def no_process(target, args, daemon=True):
            raise OSError("Too many open files")
        monkeypatch.setattr(job_runner.multiprocessing, "Process", no_process)
        bg.request_cancel("test_build_holder")

        assert _wait_for(lambda: sorted(finished) == sorted(["test_build_holder", job_id]))
        status = bg.get_status(job_id)
        assert status["status"] == "error" and "Too many open files" in status["error"]
        assert _wait_for(lambda: gpu_slots.holder_count() == 0)
        assert len(instances) == 1
        time.sleep(0.1)
        assert sorted(finished) == sorted(["test_build_holder", job_id])

        _install_fake_process(monkeypatch, run_target_on_start=True)
        assert job_launch.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(),
                                    gpu_touching=True) is True
        assert _wait_for(lambda: bg.get_status(job_id)["status"] == "done")
        for j in ("test_build_holder", job_id):
            bg.clear_job(j)

    def test_a_raising_finish_hook_never_breaks_the_cancel(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)

        def broken(job_id):
            raise RuntimeError("cleanup failed")
        job_id = "test_finish_raises"
        release = self._queue_behind_a_gpu_thread_job(job_id, broken)
        bg.request_cancel(job_id)
        assert bg.get_status(job_id)["status"] == "cancelled"
        self._end_holder(release)
        bg.clear_job(job_id)

    def test_clearing_a_running_job_runs_its_finish_hook_once(self, monkeypatch):
        _install_fake_process(monkeypatch, alive_forever=True)
        finished = []
        job_id = "test_finish_cleared_running"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: None, args=(), on_finish=finished.append)
        assert _wait_for(lambda: bg.is_running(job_id))
        bg.clear_job(job_id)
        assert _wait_for(lambda: finished == [job_id])
        time.sleep(0.1)
        assert finished == [job_id]

    def test_a_clean_stop_waits_for_the_watchers_finish_hook(self, monkeypatch):
        from services import shutdown_service
        _install_fake_process(monkeypatch, alive_forever=True)
        finished = []

        def slow_cleanup(job_id):
            time.sleep(0.5)
            finished.append(job_id)
        job_id = "test_finish_clean_stop"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: None, args=(), on_finish=slow_cleanup)
        assert _wait_for(lambda: bg.is_running(job_id))
        bg.request_cancel(job_id)
        assert shutdown_service.wait_for_jobs([job_id], timeout=5) is True
        assert finished == [job_id]
        bg.clear_job(job_id)

    def test_a_reported_stage_shows_as_a_no_progress_stage(self, monkeypatch):
        """report_stage: the parent runs a stage_ticker (no-progress note,
        longer stall allowance) until the worker's next progress."""
        release = threading.Event()
        snapshots = []

        def worker(q):
            process_worker.report_stage(q, "Loading the model")
            release.wait(timeout=2.0)
            process_worker.report_progress(q, 0.5, "Working... 50%")
            q.put(("ok", {}))
        _install_fake_process(monkeypatch, alive_forever=True)
        job_id = "test_reported_stage"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: None, args=(),
                             on_done=lambda j, r: snapshots.append(bg.get_status(j)))
        assert _wait_for(lambda: bg.is_running(job_id))
        q = bg.get_status(job_id)["process"]._args[-1]
        threading.Thread(target=worker, args=(q,), daemon=True).start()
        assert _wait_for(lambda: "Loading the model" in (bg.get_status(job_id)["message"] or ""))
        status = bg.get_status(job_id)
        assert bg.stage_ticker.NOTE in status["message"]
        assert status["can_report_progress"] is False
        release.set()
        assert _wait_for(lambda: snapshots)
        assert snapshots[0]["message"] == "Working... 50%"
        assert snapshots[0]["can_report_progress"] is True
        bg.request_cancel(job_id)
        bg.clear_job(job_id)


def _large_result_worker(size_bytes, result_queue):
    """Real, top-level (picklable) worker for TestProcessWatcherLargeResult
    -- must be a plain module-level function, not a closure, to cross a
    real multiprocessing.Process boundary. Puts a single result well past
    a typical OS pipe buffer (~64KB on Linux) onto the queue, then returns
    immediately -- the exact shape of dub/re-segment/diarize/auto-tune's
    real subprocess workers, which each return one item per line/turn/
    segment and can add up to exactly this at real drama sizes (Step 4i)."""
    result_queue.put(("ok", {"payload": "x" * size_bytes}))


def _cancellable_large_result_worker(size_bytes, result_queue):
    """Same shape as _large_result_worker, but sleeps first so a test can
    cancel it before it ever reaches result_queue.put() -- confirms the
    Step 4i fix didn't break real mid-run cancellation for a job that
    would otherwise return a large result."""
    time.sleep(5)
    result_queue.put(("ok", {"payload": "x" * size_bytes}))


class TestProcessWatcherLargeResult:
    """Step 4i: a real, severe, confirmed bug -- _process_watcher() waited
    for proc.is_alive() to go False before ever reading the result queue,
    but a child process that has put() more onto the queue than fits in
    one OS pipe buffer cannot exit until the parent reads from it. Parent
    and child waited on each other forever. Uses a REAL
    multiprocessing.Process (no _FakeProcess/_install_fake_process here,
    deliberately) -- the fake process runs its target synchronously in
    the test's own process/thread and never exercises a real OS pipe
    boundary, which is exactly the mechanism this bug depends on."""

    def test_a_large_result_completes_instead_of_hanging(self):
        # 200KB of payload, past the ~64KB Linux pipe-buffer default this
        # bug depends on (confirmed real at this app's own scale: any
        # drama with roughly 250+ lines, per Line objects pickling to
        # about 257 bytes each).
        job_id = "test_process_large_result"
        bg.clear_job(job_id)
        assert job_launch.start_process_job(job_id, _large_result_worker, args=(200_000,)) is True

        status = _wait_for_status(job_id, "running", timeout=15.0)
        assert status is not None
        assert status["status"] == "done", (
            f"expected 'done', got {status['status']!r} -- the large-result deadlock is back"
            if status["status"] == "running" else status.get("error"))
        assert len(status["result"]["payload"]) == 200_000
        bg.clear_job(job_id)

    def test_cancelling_still_works_after_the_fix(self):
        """Step 4d/4e's existing real mid-run cancel guarantee, re-run
        against the fixed watcher -- not just the new large-result case."""
        job_id = "test_process_large_result_cancel"
        bg.clear_job(job_id)
        assert job_launch.start_process_job(
            job_id, _cancellable_large_result_worker, args=(200_000,)) is True

        deadline = time.time() + 5
        status = None
        while time.time() < deadline:
            status = bg.get_status(job_id)
            if status and status["status"] == "running":
                break
            time.sleep(0.02)
        assert status is not None and status["status"] == "running"

        bg.request_cancel(job_id)
        status = _wait_for_status(job_id, "running", timeout=10.0)
        assert status["status"] == "cancelled"
        bg.clear_job(job_id)


def _child_dies_mid_result_worker(result_queue):
    """Real worker: starts writing a large result, then exits before it is
    complete or announced -- a kill or crash mid-write."""
    with open(result_queue._path + ".part", "wb") as f:
        f.write(b"x" * 1000)
    os._exit(1)


def _large_result_worker_with_path(size_bytes, result_queue):
    result_queue.put(("ok", {"payload": "x" * size_bytes}))


class TestProcessWatcherChildDiesMidResult:
    def test_a_child_that_dies_after_a_partial_write_ends_the_job_in_error(self, isolated_db):
        import storage
        job_id = "test_process_dies_mid_result"
        bg.clear_job(job_id)
        assert job_launch.start_process_job(job_id, _child_dies_mid_result_worker) is True

        status = _wait_for_status(job_id, "running", timeout=15.0)
        assert status["status"] == "error", status
        assert "exited unexpectedly" in status["error"]
        assert [n for n in os.listdir(storage.temp_root()) if "result-" in n] == []
        bg.clear_job(job_id)

    def test_the_result_file_is_removed_once_the_job_is_done(self, isolated_db):
        import storage
        job_id = "test_process_result_file_removed"
        bg.clear_job(job_id)
        assert job_launch.start_process_job(job_id, _large_result_worker_with_path,
                                    args=(200_000,)) is True
        status = _wait_for_status(job_id, "running", timeout=15.0)
        assert status["status"] == "done"
        assert [n for n in os.listdir(storage.temp_root()) if "result-" in n] == []
        bg.clear_job(job_id)

    def test_done_is_not_published_while_the_result_file_still_exists(
            self, isolated_db, monkeypatch):
        import storage
        import job_process_result
        job_id = "test_process_result_file_before_done"
        release = threading.Event()
        real_discard = job_process_result.ResultChannel.discard

        def held_discard(channel):
            # The watcher's last step, held back so a done-then-remove
            # ordering would be observable.
            release.wait(timeout=10)
            real_discard(channel)
        monkeypatch.setattr(job_process_result.ResultChannel, "discard", held_discard)
        bg.clear_job(job_id)
        try:
            assert job_launch.start_process_job(job_id, _large_result_worker_with_path,
                                        args=(200_000,)) is True
            status = _wait_for_status(job_id, "running", timeout=15.0)
            assert status["status"] == "done"
            assert [n for n in os.listdir(storage.temp_root()) if "result-" in n] == []
        finally:
            release.set()
            bg.clear_job(job_id)


class TestProcessJobHookCancelled:
    def test_on_done_raising_job_cancelled_ends_the_job_cancelled(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        job_id = "test_ondone_job_cancelled"

        def on_done(jid, result):
            bg.request_cancel(jid)
            raise bg.JobCancelled(jid)
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {"v": 1})), args=(), on_done=on_done)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "cancelled"
        assert status["message"] == job_runner.CANCELLED_MESSAGE
        bg.clear_job(job_id)


class TestProcessJobProgressTuples:
    """Process workers may send ("progress", frac, message) tuples before
    their final ("ok"/"error", ...) tuple."""

    def test_progress_tuples_update_job_state_then_final_result(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        seen = []

        def worker(q):
            process_worker.report_progress(q, 0.25, "Loading model")
            process_worker.report_progress(q, 0.5, "Transcribing key sk-abcdefghijklmnopqrstuvwx")
            q.put(("ok", {"v": 1}))

        real_update = bg.update_progress
        monkeypatch.setattr(job_runner, "update_progress",
                            lambda j, f, m="": (seen.append((f, m)), real_update(j, f, m)))
        job_id = "test_progress_tuples"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, worker, args=(), on_done=lambda j, r: seen.append("hook"))
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "done"
        assert status["result"] == {"v": 1}
        assert seen[0] == (0.25, "Loading model")
        assert seen[1][0] == 0.5 and "sk-abcdefghijklmnopqrstuvwx" not in seen[1][1]
        assert seen.count("hook") == 1          # on_done fired exactly once
        bg.clear_job(job_id)

    def test_last_progress_message_is_kept_on_the_finished_job(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)

        def worker(q):
            process_worker.report_progress(q, 0.4, "Aligning...")
            q.put(("ok", {}))

        job_id = "test_progress_message_kept"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, worker, args=())
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "done"
        assert status["message"] == "Aligning..."
        bg.clear_job(job_id)

    def test_error_after_progress_still_reported(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)

        def worker(q):
            process_worker.report_progress(q, 0.1, "Loading")
            q.put(("error", "RuntimeError", "boom"))

        job_id = "test_progress_then_error"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, worker, args=(), on_done=lambda j, r: pytest.fail("no hook"))
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error" and "boom" in status["error"]
        bg.clear_job(job_id)

    def test_cancel_still_terminates_a_running_process_job(self, monkeypatch):
        procs = _install_fake_process(monkeypatch, alive_forever=True)
        job_id = "test_progress_cancel"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: None, args=())
        bg.request_cancel(job_id)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "cancelled"
        assert procs[0].terminated is True
        bg.clear_job(job_id)

    def test_work_so_far_reaches_on_partial_under_the_jobs_line_fields(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        import db
        seen = []

        def worker(q):
            process_worker.report_partial(q, {"n": 1})
            process_worker.report_partial(q, {"n": 2})
            q.put(("ok", {"n": 3}))

        job_id = "test_partial_items"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, worker, args=(), line_fields=("start", "end"),
                                     on_partial=lambda j, p: seen.append((j, p, db.current_job_scope())))
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "done" and status["result"] == {"n": 3}
        assert seen == [(job_id, {"n": i}, (job_id, frozenset({"start", "end"}))) for i in (1, 2)]
        bg.clear_job(job_id)

    def test_work_so_far_is_not_applied_once_a_cancel_is_pending(self):
        calls = []
        run = {"on_partial": lambda j, p: calls.append(p), "cancel_requested": True, "status": "running"}
        with job_table._lock:
            job_table._jobs["test_partial_cancel"] = run
        try:
            assert job_runner._apply_progress_item("test_partial_cancel", ("partial", 1), run=run) is True
            run["cancel_requested"] = False
            job_runner._apply_progress_item("test_partial_cancel", ("partial", 2), run=run)
        finally:
            with job_table._lock:
                job_table._jobs.pop("test_partial_cancel", None)
        assert calls == [2]

    def test_a_failing_on_partial_does_not_fail_the_job(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)

        def worker(q):
            process_worker.report_partial(q, {})
            q.put(("ok", "fine"))

        def broken(j, p):
            raise RuntimeError("no")
        job_id = "test_partial_broken"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, worker, args=(), on_partial=broken)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "done" and status["result"] == "fine"
        bg.clear_job(job_id)

    def test_malformed_progress_tuple_is_ignored(self):
        assert job_runner._apply_progress_item("nope", ("progress", "x")) is True
        assert job_runner._apply_progress_item("nope", ("ok", {})) is False

    def test_first_progress_logs_worker_start_up_once(self, monkeypatch):
        import applog
        lines = []
        monkeypatch.setattr(applog, "get_logger", lambda: type("L", (), {"info": staticmethod(lines.append)})())
        stage = {"t0": time.monotonic() - 5}
        job_runner._apply_progress_item("jid", ("progress", 0.1, "a"), stage)
        job_runner._apply_progress_item("jid", ("progress", 0.2, "b"), stage)
        assert len(lines) == 1 and "jid" in lines[0] and "first progress after 5." in lines[0]


class _WorkerExited(BaseException):
    pass


class TestOrphanedWorkerExits:
    """A worker that left its parent's process group (start_own_process_group)
    ends itself once its parent dies, instead of running on unseen."""

    def _orphan(self, monkeypatch):
        killed = []
        monkeypatch.setattr(process_worker, "_worker_parent_pid", 4242)
        monkeypatch.setattr(process_worker.os, "getppid", lambda: 1)
        monkeypatch.setattr(process_worker.os, "getpgid", lambda pid: os.getpid())
        monkeypatch.setattr(process_worker.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

        def fake_exit(code):
            raise _WorkerExited(code)
        monkeypatch.setattr(process_worker.os, "_exit", fake_exit)
        return killed

    @pytest.mark.parametrize("report", [lambda q: process_worker.report_progress(q, 0.5, "x"),
                                        lambda q: process_worker.report_stage(q, "Loading")])
    def test_a_report_after_the_parent_died_kills_the_group_and_exits(self, monkeypatch, report):
        import signal
        killed = self._orphan(monkeypatch)
        q = queue.Queue()
        with pytest.raises(_WorkerExited):
            report(q)
        assert killed == [(0, signal.SIGKILL)]
        assert q.empty()

    def test_a_live_parent_changes_nothing(self, monkeypatch):
        monkeypatch.setattr(process_worker, "_worker_parent_pid", os.getppid())
        q = queue.Queue()
        process_worker.report_progress(q, 0.5, "x")
        assert q.get_nowait() == ("progress", 0.5, "x")

    def test_no_check_outside_a_worker(self, monkeypatch):
        monkeypatch.setattr(process_worker, "_worker_parent_pid", None)
        monkeypatch.setattr(process_worker.os, "getppid", lambda: 1)
        process_worker.exit_if_parent_gone()

    class _FakeParent:
        def __init__(self, pid, alive=True):
            self.pid = pid
            self.alive = alive

        def is_alive(self):
            return self.alive

    def test_the_parent_comes_from_the_spawn_data_not_a_late_getppid(self, monkeypatch):
        """A parent killed during the worker's start-up: getppid already
        reads the reaper when start_own_process_group runs, but
        parent_process() still names the real parent, so the worker exits."""
        monkeypatch.setattr(process_worker, "_worker_parent", None)
        monkeypatch.setattr(process_worker, "_worker_parent_pid", None)
        monkeypatch.setattr(process_worker.multiprocessing, "parent_process", lambda: self._FakeParent(4242))
        monkeypatch.setattr(process_worker, "_parent_watchdog", lambda: None)
        monkeypatch.setattr(process_worker.os, "setsid", lambda: None)
        monkeypatch.setattr(process_worker.os, "getppid", lambda: 1)
        process_worker.start_own_process_group()
        assert process_worker._worker_parent_pid == 4242
        killed = self._orphan(monkeypatch)
        monkeypatch.setattr(process_worker, "_worker_parent_pid", 4242)
        with pytest.raises(_WorkerExited):
            process_worker.exit_if_parent_gone()
        assert len(killed) == 1

    def test_without_parent_process_data_it_falls_back_to_getppid(self, monkeypatch):
        monkeypatch.setattr(process_worker, "_worker_parent", None)
        monkeypatch.setattr(process_worker, "_worker_parent_pid", None)
        monkeypatch.setattr(process_worker.multiprocessing, "parent_process", lambda: None)
        monkeypatch.setattr(process_worker, "_parent_watchdog", lambda: None)
        monkeypatch.setattr(process_worker.os, "setsid", lambda: None)
        monkeypatch.setattr(process_worker.os, "getppid", lambda: 777)
        process_worker.start_own_process_group()
        assert process_worker._worker_parent is None and process_worker._worker_parent_pid == 777

    def test_a_dead_parent_sentinel_exits_even_with_the_same_ppid(self, monkeypatch):
        killed = self._orphan(monkeypatch)
        monkeypatch.setattr(process_worker.os, "getppid", lambda: 4242)
        monkeypatch.setattr(process_worker, "_worker_parent", self._FakeParent(4242, alive=False))
        with pytest.raises(_WorkerExited):
            process_worker.report_progress(queue.Queue(), 0.5, "x")
        assert len(killed) == 1

    def test_on_windows_only_the_sentinel_is_checked_and_nothing_raises(self, monkeypatch):
        def no_getppid():
            raise OSError("getppid failed")
        monkeypatch.setattr(process_worker.os, "name", "nt")
        monkeypatch.setattr(process_worker.os, "getppid", no_getppid)
        monkeypatch.setattr(process_worker, "_worker_parent_pid", 4242)
        monkeypatch.setattr(process_worker, "_worker_parent", self._FakeParent(4242))
        q = queue.Queue()
        process_worker.report_progress(q, 0.5, "x")
        process_worker.report_stage(q, "Loading")
        assert q.get_nowait() == ("progress", 0.5, "x")
        assert q.get_nowait() == ("stage", 0.0, "Loading")

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX process groups")
    def test_a_real_worker_ends_after_its_parent_is_killed(self, tmp_path):
        """A parent process starts a worker that leads its own group and
        then reports nothing; the parent is killed hard. The watchdog ends
        the worker within a few check intervals."""
        import signal
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ready = str(tmp_path / "ready")
        script = (
            "import multiprocessing, sys, time\n"
            f"sys.path.insert(0, {root!r})\n"
            "from jobs import process_worker\n"
            "def worker():\n"
            "    process_worker.start_own_process_group()\n"
            f"    open({ready!r}, 'w').close()\n"
            "    time.sleep(120)\n"
            "p = multiprocessing.get_context('fork').Process(target=worker, daemon=False)\n"
            "p.start()\n"
            "print(p.pid, flush=True)\n"
            "time.sleep(120)\n")
        parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                                  text=True)
        try:
            worker_pid = int(parent.stdout.readline())
            # The worker has recorded its parent and started its watchdog.
            assert _wait_for(lambda: os.path.exists(ready), timeout=30)
            os.kill(parent.pid, signal.SIGKILL)
            parent.wait(timeout=10)
            assert _wait_for(lambda: _pid_gone(worker_pid),
                             timeout=4 * process_worker.PARENT_CHECK_INTERVAL + 2)
        finally:
            if parent.poll() is None:
                parent.kill()
            try:
                os.kill(worker_pid, signal.SIGKILL)
            except (ProcessLookupError, UnboundLocalError):
                pass


def _pid_gone(pid) -> bool:
    """Linux: True once `pid` has exited (a zombie counts: a re-parented
    child may wait on a reaper that never collects it here). A read that
    lands while the kernel is releasing the task gets ESRCH, not ENOENT."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        return True


def _worker_leaving_a_child(pid_file, result_queue):
    process_worker.start_own_process_group()
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(pid_file, "w") as f:
        f.write(str(child.pid))
    result_queue.put(("ok", {}))


class TestWholeTreeWorkerGroup:
    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX process groups")
    def test_a_child_outliving_its_worker_is_killed_before_on_finish(self, tmp_path):
        """The worker exits after starting a child (an ffmpeg) that keeps
        running: the watcher kills the worker's group before on_finish
        removes the scratch folder under that child."""
        import signal
        pid_file = str(tmp_path / "child.pid")
        seen = []

        def on_finish(job_id):
            child_pid = int(open(pid_file).read())
            seen.append(_wait_for(lambda: _pid_gone(child_pid), timeout=3))
        job_id = "test_whole_tree_child"
        bg.clear_job(job_id)
        try:
            assert job_launch.start_process_job(job_id, _worker_leaving_a_child, args=(pid_file,),
                                        on_finish=on_finish, kill_whole_tree=True,
                                        start_method="fork")
            assert _wait_for(lambda: seen, timeout=20)
            assert seen == [True]
            assert bg.get_status(job_id)["status"] == "done"
        finally:
            try:
                os.kill(int(open(pid_file).read()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
            bg.clear_job(job_id)


class TestStartFailure:
    """A job is marked "running" before its thread/process starts; if the
    start raises, the record must not stay "running" forever (blocking a
    restart, acquire_exclusive and the GPU lock)."""

    def _fail_thread_starts(self, monkeypatch, name=None):
        real = job_runner._start_job_thread

        def flaky(target, thread_name, *args, **kwargs):
            if name is None or thread_name == name:
                raise RuntimeError("can't start new thread key=AIzaSyFAKESECRETVALUE12345")
            return real(target, thread_name, *args, **kwargs)

        monkeypatch.setattr(job_runner, "_start_job_thread", flaky)
        return real

    def test_thread_start_failure_marks_error_and_frees_everything(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
        real = self._fail_thread_starts(monkeypatch)
        with pytest.raises(RuntimeError):
            job_launch.start_job("sf_thread", lambda: None, gpu_touching=True)
        status = bg.get_status("sf_thread")
        assert status["status"] == "error"
        assert "AIzaSyFAKESECRETVALUE12345" not in status["error"]
        assert gpu_slots.take("someone_else")
        gpu_slots.release("someone_else")
        assert job_launch.acquire_exclusive("test")
        job_launch.release_exclusive()
        monkeypatch.setattr(job_runner, "_start_job_thread", real)
        assert job_launch.start_job("sf_thread", lambda: None) is True
        _wait("sf_thread")
        assert bg.get_status("sf_thread")["status"] == "done"

    def test_process_start_failure_marks_error(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)

        procs, queues = [], []

        class _Unstartable(_FakeProcess):
            closed = False

            def start(self):
                raise TypeError("cannot pickle '_thread.lock' object")

            def close(self):
                self.closed = True

        def make_proc(target, args, daemon=True):
            procs.append(_Unstartable(target, args))
            return procs[-1]

        def make_queue():
            queues.append(_SpyQueue())
            return queues[-1]

        monkeypatch.setattr(job_runner.multiprocessing, "Process", make_proc)
        monkeypatch.setattr(job_runner.multiprocessing, "Queue", make_queue)
        with pytest.raises(TypeError):
            job_launch.start_process_job("sf_proc", lambda q: None, gpu_touching=True)
        assert bg.get_status("sf_proc")["status"] == "error"
        assert procs[0].closed and queues[0].closed   # no leaked pipe fds
        assert gpu_slots.take("someone_else")
        gpu_slots.release("someone_else")
        assert job_launch.acquire_exclusive("test")
        job_launch.release_exclusive()

    def test_promoted_job_failing_to_start_errors_and_the_next_one_runs(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
        job_settings.set_gpu_limit_enabled(True)
        release = threading.Event()
        assert job_launch.start_job("sf_a", lambda: release.wait(5), gpu_touching=True)
        assert job_launch.start_job("sf_b", lambda: None, gpu_touching=True)
        assert job_launch.start_job("sf_c", lambda: None, gpu_touching=True)
        assert bg.get_status("sf_b")["status"] == "queued"
        self._fail_thread_starts(monkeypatch, name="job:sf_b")
        release.set()
        assert _wait_for(lambda: bg.get_status("sf_c")["status"] == "done", timeout=5)
        assert bg.get_status("sf_b")["status"] == "error"


class _StubbornProcess(_FakeProcess):
    """Ignores terminate() (a child stuck in a CUDA call); only kill() stops it."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.killed = False
        self.joins = 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self._alive = False
        self.exitcode = -9

    def join(self, timeout=None):
        self.joins += 1


class _SpyQueue:
    def __init__(self, items=(), error=None):
        self.items = list(items)
        self.error = error
        self.closed = False

    def get(self, timeout=None):
        if self.error is not None:
            raise self.error
        if self.items:
            return self.items.pop(0)
        time.sleep(0.01)
        raise queue.Empty

    def close(self):
        self.closed = True


def _register_fake_process_job(job_id):
    proc = _StubbornProcess(lambda q: None, (), alive_forever=True)
    with job_table._lock:
        job_table._jobs[job_id] = {
            "status": "running", "progress": 0.0, "message": "", "error": None,
            "started_at": time.time(), "finished_at": None, "cancel_requested": False,
            "result": None, "gpu_touching": False, "description": None, "kind": "process",
            "process": proc, "owner_user_id": None,
        }
    return proc


class TestProcessWatcherRobustness:
    def test_queue_error_marks_job_errored_and_stops_the_child(self):
        import pickle
        proc = _register_fake_process_job("w_torn")
        q = _SpyQueue(error=pickle.UnpicklingError(
            "pickle data was truncated key=AIzaSyFAKESECRETVALUE12345"))
        job_runner._process_watcher("w_torn", proc, q, poll_interval=0.01)
        status = bg.get_status("w_torn")
        assert status["status"] == "error"
        assert "UnpicklingError" in status["error"]
        assert "AIzaSyFAKESECRETVALUE12345" not in status["error"]
        assert proc.killed
        assert q.closed

    def test_cancel_kills_a_child_that_ignores_terminate(self):
        proc = _register_fake_process_job("w_stubborn")
        bg.request_cancel("w_stubborn")
        job_runner._process_watcher("w_stubborn", proc, _SpyQueue(), poll_interval=0.01)
        assert bg.get_status("w_stubborn")["status"] == "cancelled"
        assert proc.terminated and proc.killed
        assert not proc.is_alive()

    def test_normal_exit_reaps_the_child_and_closes_the_queue(self):
        proc = _register_fake_process_job("w_normal")
        q = _SpyQueue(items=[("ok", {"n": 1})])
        job_runner._process_watcher("w_normal", proc, q, poll_interval=0.01)
        assert bg.get_status("w_normal")["status"] == "done"
        assert proc.joins >= 1
        assert q.closed

    def test_job_is_not_done_until_the_worker_has_been_joined(self):
        proc = _register_fake_process_job("w_order")
        seen = []
        original_join = proc.join

        def recording_join(timeout=None):
            seen.append(bg.get_status("w_order")["status"])
            original_join(timeout)

        proc.join = recording_join
        job_runner._process_watcher("w_order", proc, _SpyQueue(items=[("ok", {"n": 1})]),
                            poll_interval=0.01)
        assert seen and set(seen) == {"running"}
        assert bg.get_status("w_order")["status"] == "done"

    def test_worker_alive_after_the_grace_join_is_stopped_before_done(self):
        # kill_whole_tree is off, so there is no group kill to fall back on;
        # the stub ignores join and terminate until kill().
        proc = _register_fake_process_job("w_linger")
        seen = []
        original_join = proc.join

        def recording_join(timeout=None):
            seen.append(bg.get_status("w_linger")["status"])
            original_join(timeout)

        proc.join = recording_join
        job_runner._process_watcher("w_linger", proc, _SpyQueue(items=[("ok", {"n": 1})]),
                            poll_interval=0.01)
        assert proc.terminated and proc.killed
        assert not proc.is_alive()
        assert set(seen) == {"running"}
        assert bg.get_status("w_linger")["status"] == "done"
