"""jobs/gpu_guard.py: Baihe holds, unloads and stops GPU work while the card is
short of memory or hot. Mocked: readings are passed to tick(), no nvidia-smi."""
import threading
import time

import pytest

import background_jobs as bg
import diagnostics_torch
import gpu_wait_message
from jobs import gpu_guard, gpu_slots, job_launch, job_runner, job_settings
from lib.errors import ConflictError
from services import settings_service

FREE = 20000.0
COOL = 60.0
# Short of the 1 GB floor but above half of it, so only the set minutes stop a job.
SHORT = 800.0
_REAL_IS_BUSY = diagnostics_torch.external_gpu_is_busy


@pytest.fixture(autouse=True)
def _library(isolated_db, monkeypatch):
    job_settings.set_gpu_limit_enabled(True)
    # No other GPU program: only the guard holds jobs in these tests.
    monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
    monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: None)
    import ollama_unload
    import whisper_models
    calls = {"ollama": 0, "app": 0}
    monkeypatch.setattr(ollama_unload, "unload_loaded_models",
                        lambda: calls.__setitem__("ollama", calls["ollama"] + 1) or 1)
    monkeypatch.setattr(whisper_models, "release_gpu_models",
                        lambda: calls.__setitem__("app", calls["app"] + 1))
    yield calls
    for jid in list(bg.list_all_jobs()):
        bg.request_cancel(jid)
    job_runner.wait_for_job_threads(5)
    bg.clear_all_jobs()


def _wait_for(predicate, timeout=3.0):
    end = time.time() + timeout
    while not predicate() and time.time() < end:
        time.sleep(0.01)
    return predicate()


def _status(job_id):
    return (bg.get_status(job_id) or {}).get("status")


def _message(job_id):
    return (bg.get_status(job_id) or {}).get("message") or ""


def _clear(t0):
    """Ticks a clear card long enough for any hold to end; returns the time after."""
    gpu_guard.tick(now=t0, reading=(FREE, COOL))
    t = t0 + gpu_guard.CLEAR_SECONDS + 1
    gpu_guard.tick(now=t, reading=(FREE, COOL))
    return t


class TestHoldState:
    def test_short_memory_holds_until_clear_for_a_minute(self):
        t = time.time()
        gpu_guard.tick(now=t, reading=(500.0, COOL))
        assert "Held: low GPU memory (0.5 GB free, 1.0 GB kept free)" in gpu_guard.hold_reason(now=t)
        # Just over the limit is not clear: the margin keeps it from flapping.
        gpu_guard.tick(now=t + 20, reading=(1500.0, COOL))
        gpu_guard.tick(now=t + 200, reading=(1500.0, COOL))
        assert gpu_guard.hold_reason(now=t + 200)
        gpu_guard.tick(now=t + 220, reading=(FREE, COOL))
        assert gpu_guard.hold_reason(now=t + 220)
        gpu_guard.tick(now=t + 300, reading=(FREE, COOL))
        assert gpu_guard.hold_reason(now=t + 300) is None

    def test_a_dip_during_the_clear_minute_restarts_it(self):
        t = time.time()
        gpu_guard.tick(now=t, reading=(500.0, COOL))
        gpu_guard.tick(now=t + 10, reading=(FREE, COOL))
        gpu_guard.tick(now=t + 40, reading=(500.0, COOL))
        gpu_guard.tick(now=t + 80, reading=(FREE, COOL))
        assert gpu_guard.hold_reason(now=t + 80)

    def test_the_clear_minute_says_what_the_card_reads_now(self):
        t = time.time()
        gpu_guard.tick(now=t, reading=(500.0, COOL))
        gpu_guard.tick(now=t + 20, reading=(FREE, COOL))
        reason = gpu_guard.hold_reason(now=t + 20)
        assert reason.startswith("Held: GPU memory is free again (19.5 GB free), checking it stays free")
        assert "0.5 GB" not in reason
        t = _clear(t + 40)
        assert gpu_guard.hold_reason(now=t) is None
        gpu_guard.tick(now=t + 200, reading=(FREE, 90.0))
        gpu_guard.tick(now=t + 220, reading=(FREE, 70.0))
        assert gpu_guard.hold_reason(now=t + 220).startswith(
            "Held: the GPU has cooled (70 °C), checking it stays cool")

    def test_a_changed_limit_is_judged_at_once(self):
        t = time.time()
        gpu_guard.tick(now=t, reading=(FREE, 86.0))
        assert gpu_guard.hold_reason(now=t)
        settings_service.set_settings({"gpu_guard_temp_c": 90})
        gpu_guard.tick(now=t + 20, reading=(FREE, 86.0))
        assert gpu_guard.hold_reason(now=t + 20) is None
        settings_service.set_settings({"keep_free_vram_gb": 5})
        gpu_guard.tick(now=t + 40, reading=(4800.0, COOL))
        assert "5.0 GB kept free" in gpu_guard.hold_reason(now=t + 40)
        settings_service.set_settings({"keep_free_vram_gb": 4})
        gpu_guard.tick(now=t + 60, reading=(4800.0, COOL))
        assert gpu_guard.hold_reason(now=t + 60) is None

    def test_a_limit_change_that_is_still_crossed_keeps_the_hold(self):
        t = time.time()
        gpu_guard.tick(now=t, reading=(FREE, 92.0))
        settings_service.set_settings({"gpu_guard_temp_c": 90})
        gpu_guard.tick(now=t + 20, reading=(FREE, 92.0))
        assert "limit 90 °C" in gpu_guard.hold_reason(now=t + 20)
        assert gpu_guard.status()["since"] == t

    def test_keep_free_setting_is_the_memory_limit(self):
        settings_service.set_settings({"keep_free_vram_gb": 5})
        t = time.time()
        gpu_guard.tick(now=t, reading=(4800.0, COOL))
        assert "4.7 GB free, 5.0 GB kept free" in gpu_guard.hold_reason(now=t)

    def test_hot_card_holds_and_cools_by_the_margin(self):
        t = time.time()
        gpu_guard.tick(now=t, reading=(FREE, 86.0))
        assert gpu_guard.hold_reason(now=t) == ("Held: the GPU is hot (86 °C, limit 85 °C). "
                                                "Starts by itself when the GPU is clear.")
        gpu_guard.tick(now=t + 100, reading=(FREE, 82.0))   # under 85, not 5 under
        assert gpu_guard.hold_reason(now=t + 100)
        gpu_guard.tick(now=t + 120, reading=(FREE, 79.0))
        gpu_guard.tick(now=t + 200, reading=(FREE, 79.0))
        assert gpu_guard.hold_reason(now=t + 200) is None

    def test_temperature_setting(self):
        settings_service.set_settings({"gpu_guard_temp_c": 75})
        t = time.time()
        gpu_guard.tick(now=t, reading=(FREE, 76.0))
        assert "limit 75 °C" in gpu_guard.hold_reason(now=t)

    def test_no_reading_or_an_old_one_never_holds(self):
        t = time.time()
        gpu_guard.tick(now=t, reading=(500.0, COOL))
        assert gpu_guard.hold_reason(now=t + gpu_guard.STALE_SECONDS + 1) is None
        gpu_guard.tick(now=t + 1, reading=None)
        assert gpu_guard.hold_reason(now=t + 1) is None

    def test_no_temperature_reported_judges_memory_only(self):
        t = time.time()
        gpu_guard.tick(now=t, reading=(FREE, None))
        assert gpu_guard.hold_reason(now=t) is None

    def test_turned_off_never_holds(self):
        settings_service.set_settings({"gpu_guard": False})
        t = time.time()
        gpu_guard.tick(now=t, reading=(100.0, 99.0))
        assert gpu_guard.hold_reason(now=t) is None

    def test_card_reading_parses_nvidia_smi(self, monkeypatch):
        from lib import proc
        import shutil

        class Run:
            timed_out, returncode, stdout = False, 0, "30000, 32768, 77\n500, 8192, [N/A]\n"
        monkeypatch.setattr(shutil, "which", lambda _n: "nvidia-smi")
        monkeypatch.setattr(proc, "run_captured", lambda *a, **k: Run())
        assert gpu_guard.read_card() == (2768.0, 77.0)
        Run.stdout = "30000, 32768, [N/A]\n"
        assert gpu_guard.read_card() == (2768.0, None)
        Run.returncode = 1
        assert gpu_guard.read_card() is None


class TestStartCheck:
    def test_keep_free_raises_the_start_check_floor(self):
        load = {"utilization_percent": 10.0, "memory_used_mb": 0.0, "memory_total_mb": 32768.0,
                "memory_free_mb": 3000.0}
        assert _REAL_IS_BUSY(load) is False
        settings_service.set_settings({"keep_free_vram_gb": 5})
        assert _REAL_IS_BUSY(load) is True
        assert "Waiting for the GPU" in gpu_wait_message.external_gpu_wait_message(load)
        settings_service.set_settings({"keep_free_vram_gb": 0.5})
        assert diagnostics_torch.external_busy_min_free_mb() == 1024.0

    def test_acquire_refuses_while_held_only_for_jobs_that_check_load(self):
        gpu_guard.tick(reading=(500.0, COOL))
        assert gpu_slots.acquire("ui:a", check_external_load=True) is False
        assert gpu_slots.acquire("ui:a") is True
        gpu_slots.release("ui:a")

    def test_queued_job_says_held_then_starts_once_clear(self):
        t = time.time()
        gpu_guard.tick(now=t, reading=(500.0, COOL))
        release = threading.Event()
        assert job_launch.start_job("held_a", release.wait, 5, gpu_touching=True)
        assert _status("held_a") == "queued"
        assert _message("held_a").startswith("Held: low GPU memory")
        job_runner.recheck_gpu_queue()
        assert _status("held_a") == "queued"
        _clear(time.time())
        job_runner.recheck_gpu_queue()
        assert _status("held_a") == "running"
        release.set()

    def test_non_gpu_jobs_are_never_held(self):
        gpu_guard.tick(reading=(500.0, 95.0))
        release = threading.Event()
        assert job_launch.start_job("cpu_a", release.wait, 5)
        assert _status("cpu_a") == "running"
        release.set()


class TestIdleModels:
    def test_short_memory_unloads_when_nothing_runs(self, _library):
        t = time.time()
        gpu_guard.tick(now=t, reading=(500.0, COOL))
        assert _library == {"ollama": 1, "app": 1}
        gpu_guard.tick(now=t + 20, reading=(500.0, COOL))
        assert _library["ollama"] == 1   # at most once a minute
        gpu_guard.tick(now=t + gpu_guard.UNLOAD_EVERY_SECONDS + 1, reading=(500.0, COOL))
        assert _library["ollama"] == 2

    def test_nothing_is_unloaded_while_a_job_runs(self, _library):
        release = threading.Event()
        assert job_launch.start_job("busy_a", release.wait, 5, gpu_touching=True)
        gpu_guard.tick(reading=(500.0, COOL))
        assert _library == {"ollama": 0, "app": 0}
        release.set()

    def test_heat_alone_unloads_nothing(self, _library):
        gpu_guard.tick(reading=(FREE, 95.0))
        assert _library == {"ollama": 0, "app": 0}


def _stoppable(job_id, started):
    """A GPU thread job that runs until it is cancelled, at its cancel check."""
    def work():
        started.append(job_id)
        while True:
            if bg.is_cancel_requested(job_id):
                raise bg.JobCancelled(job_id)
            time.sleep(0.01)
    return job_launch.start_job(job_id, work, gpu_touching=True)


class TestStopAndRestart:
    @pytest.fixture
    def job(self):
        started = []
        calls = []

        def again():
            calls.append(time.time())
            if not _stoppable("stop_a", started):
                raise ConflictError("A run is already going.")
            gpu_guard.allow_restart("stop_a", again)
        assert _stoppable("stop_a", started)
        gpu_guard.allow_restart("stop_a", again)
        assert _wait_for(lambda: started)
        return started, calls

    def _short_for(self, minutes, t0):
        gpu_guard.tick(now=t0, reading=(SHORT, COOL))
        gpu_guard.tick(now=t0 + minutes * 60, reading=(SHORT, COOL))
        return t0 + minutes * 60

    def test_waits_the_set_minutes_then_stops_and_requeues_held(self, job):
        started, calls = job
        t = time.time()
        gpu_guard.tick(now=t, reading=(SHORT, COOL))
        gpu_guard.tick(now=t + 100, reading=(SHORT, COOL))
        assert _status("stop_a") == "running"
        gpu_guard.tick(now=t + 120, reading=(SHORT, COOL))
        assert _wait_for(lambda: _status("stop_a") != "running")
        gpu_guard.tick(now=t + 140, reading=(SHORT, COOL))
        assert len(calls) == 1
        assert _status("stop_a") == "queued"
        assert _message("stop_a").startswith("Held: low GPU memory")
        _clear(t + 160)
        job_runner.recheck_gpu_queue()
        assert _status("stop_a") == "running"
        assert _wait_for(lambda: len(started) == 2)

    def test_a_guard_stop_says_why_not_just_cancelled(self, job):
        t = self._short_for(2, time.time())
        assert _message("stop_a").startswith(("Stopping: low GPU memory", "Stopped: low GPU memory"))
        assert _wait_for(lambda: _status("stop_a") == "cancelled")
        assert _message("stop_a").startswith("Stopped: low GPU memory")
        assert "Starts again by itself" in _message("stop_a")
        gpu_guard.tick(now=t + 20, reading=(SHORT, COOL))
        assert _message("stop_a").startswith("Held: low GPU memory")

    def test_the_users_own_cancel_still_says_cancelled(self, job):
        bg.request_cancel("stop_a")
        assert _wait_for(lambda: _status("stop_a") == "cancelled")
        assert _message("stop_a") == job_runner.CANCELLED_MESSAGE

    def test_a_run_that_finished_before_the_stop_took_is_not_started_again(self):
        release = threading.Event()
        calls = []

        def work():
            release.wait(5)   # no cancel check: it finishes whatever is asked
        assert job_launch.start_job("done_a", work, gpu_touching=True)
        gpu_guard.allow_restart("done_a", lambda: calls.append(1))
        t = self._short_for(2, time.time())
        release.set()
        assert _wait_for(lambda: _status("done_a") == "done")
        assert _message("done_a") == job_runner.FINISHED_MESSAGE
        gpu_guard.tick(now=t + 20, reading=(SHORT, COOL))
        assert calls == [] and gpu_guard.status()["pending_restarts"] == 0

    def test_a_process_job_promoted_from_the_queue_can_still_be_stopped(self, monkeypatch):
        """Promotion gives a queued process job a new job dict
        (job_runner._register_process_job); the registration follows it."""
        from jobs.job_table import _jobs, _lock
        cancelled = []
        monkeypatch.setattr(bg, "request_cancel", lambda jid, *a, **k: cancelled.append(jid))
        queued = {"status": "queued", "kind": "process", "gpu_touching": True,
                  "cancel_requested": False, "message": ""}
        with _lock:
            _jobs["proc_a"] = queued
        gpu_guard.allow_restart("proc_a", lambda: None)
        with _lock:
            _jobs["proc_a"] = {**queued, "status": "running"}
        self._short_for(2, time.time())
        assert cancelled == ["proc_a"]
        with _lock:
            _jobs.pop("proc_a")

    def test_a_job_cleared_while_queued_does_not_pass_its_restart_on(self, monkeypatch):
        from jobs.job_table import _jobs, _lock
        cancelled = []
        monkeypatch.setattr(bg, "request_cancel", lambda jid, *a, **k: cancelled.append(jid))
        queued = {"status": "queued", "kind": "process", "gpu_touching": True, "cancel_requested": False}
        with _lock:
            _jobs["proc_b"] = queued
        gpu_guard.allow_restart("proc_b", lambda: None)
        queued["status"] = "cancelled"
        with _lock:
            _jobs["proc_b"] = {**queued, "status": "running"}
        self._short_for(2, time.time())
        assert cancelled == []
        with _lock:
            _jobs.pop("proc_b")

    def test_very_low_memory_stops_at_once(self, job):
        t = time.time()
        gpu_guard.tick(now=t, reading=(1024 * gpu_guard.CRITICAL_SHARE - 1, COOL))
        assert _wait_for(lambda: _status("stop_a") == "cancelled")
        assert _message("stop_a").startswith("Stopped: low GPU memory")

    def test_half_the_reserve_or_more_waits_the_set_minutes(self, job):
        t = time.time()
        gpu_guard.tick(now=t, reading=(1024 * gpu_guard.CRITICAL_SHARE, COOL))
        gpu_guard.tick(now=t + 60, reading=(1024 * gpu_guard.CRITICAL_SHARE, COOL))
        assert _status("stop_a") == "running"

    def test_no_stop_while_the_card_is_clearing(self, job):
        t = time.time()
        gpu_guard.tick(now=t, reading=(SHORT, COOL))
        gpu_guard.tick(now=t + 110, reading=(FREE, COOL))
        gpu_guard.tick(now=t + 130, reading=(FREE, COOL))
        assert _status("stop_a") == "running"

    def test_heat_stops_too(self, job):
        t = time.time()
        gpu_guard.tick(now=t, reading=(FREE, 90.0))
        gpu_guard.tick(now=t + 120, reading=(FREE, 90.0))
        assert _wait_for(lambda: _status("stop_a") != "running")

    def test_zero_minutes_never_stops(self, job):
        settings_service.set_settings({"gpu_guard_stop_minutes": 0})
        self._short_for(30, time.time())
        assert _status("stop_a") == "running"

    def test_a_job_that_did_not_register_is_never_stopped(self):
        started = []
        assert _stoppable("plain_a", started)
        self._short_for(30, time.time())
        assert _status("plain_a") == "running"

    def test_restart_runs_as_its_starter(self, monkeypatch):
        from lib import acting
        seen = []
        started = []

        def again():
            seen.append(acting.acting_principal())
            _stoppable("own_a", started)

        token = acting.bind_request()
        try:
            acting.note_acting_principal({"id": 7, "permissions": []})
            assert _stoppable("own_a", started)
            gpu_guard.allow_restart("own_a", again)
        finally:
            acting.unbind_request(token)
        t = self._short_for(2, time.time())
        assert _wait_for(lambda: _status("own_a") != "running")
        gpu_guard.tick(now=t + 20, reading=(SHORT, COOL))
        assert seen == [{"id": 7, "permissions": []}]
        # The poller's own context is untouched.
        assert acting.acting_principal() is None

    def test_after_max_restarts_a_stop_is_final_and_says_so(self, job):
        started, calls = job
        t = time.time()
        for _ in range(gpu_guard.MAX_RESTARTS + 1):
            t = self._short_for(2, t)
            # Each stop has ended before the card clears; a run still winding down
            # would read as the restarted one.
            assert _wait_for(lambda: _status("stop_a") == "cancelled")
            t = _clear(t + 1) + 1
            job_runner.recheck_gpu_queue()
            if _status("stop_a") == "cancelled":
                break
            assert _wait_for(lambda: _status("stop_a") == "running")
        assert len(calls) == gpu_guard.MAX_RESTARTS
        assert _status("stop_a") == "cancelled"
        assert f"Stopped {gpu_guard.MAX_RESTARTS + 1} times, so it was not started again" in _message("stop_a")

    def test_busy_restart_is_retried_then_given_up(self, monkeypatch):
        attempts = []

        def again():
            attempts.append(1)
            raise ConflictError("The last request is still running.")
        started = []
        assert _stoppable("busy_b", started)
        gpu_guard.allow_restart("busy_b", again)
        t = self._short_for(2, time.time())
        assert _wait_for(lambda: _status("busy_b") == "cancelled")
        gpu_guard.tick(now=t + 20, reading=(SHORT, COOL))
        assert attempts == [1]
        assert _message("busy_b").startswith("Stopped: low GPU memory")
        gpu_guard.tick(now=t + 40, reading=(SHORT, COOL))
        assert attempts == [1, 1]
        gpu_guard.tick(now=t + gpu_guard.RESTART_RETRY_SECONDS + 1, reading=(SHORT, COOL))
        assert "could not start again" in _message("busy_b")
        gpu_guard.tick(now=t + gpu_guard.RESTART_RETRY_SECONDS + 30, reading=(SHORT, COOL))
        assert len(attempts) == 3

    def test_failed_restart_names_the_reason_without_secrets(self):
        def again():
            raise RuntimeError("no audio at sk-ant-api03-" + "x" * 40)
        started = []
        assert _stoppable("fail_a", started)
        gpu_guard.allow_restart("fail_a", again)
        t = self._short_for(2, time.time())
        assert _wait_for(lambda: _status("fail_a") == "cancelled")
        gpu_guard.tick(now=t + 20, reading=(SHORT, COOL))
        message = _message("fail_a")
        assert "did not start again: no audio" in message and "x" * 40 not in message

    def test_gpu_limit_off_waits_for_clear_before_restarting(self, job):
        started, calls = job
        t = self._short_for(2, time.time())
        assert _wait_for(lambda: _status("stop_a") != "running")
        job_settings.set_gpu_limit_enabled(False)
        gpu_guard.tick(now=t + 20, reading=(SHORT, COOL))
        assert calls == []
        assert _message("stop_a").startswith("Stopped: low GPU memory")
        gpu_guard.tick(now=t + 40, reading=(FREE, COOL))
        gpu_guard.tick(now=t + 40 + gpu_guard.CLEAR_SECONDS + 1, reading=(FREE, COOL))
        assert len(calls) == 1
        assert _wait_for(lambda: _status("stop_a") == "running")

    def test_user_cancel_of_the_held_restart_ends_it(self, job):
        started, calls = job
        t = self._short_for(2, time.time())
        assert _wait_for(lambda: _status("stop_a") != "running")
        gpu_guard.tick(now=t + 20, reading=(SHORT, COOL))
        assert _status("stop_a") == "queued"
        bg.request_cancel("stop_a")
        assert _status("stop_a") == "cancelled"
        _clear(t + 40)
        job_runner.recheck_gpu_queue()
        assert _status("stop_a") == "cancelled" and len(calls) == 1

    def test_turning_the_guard_off_drops_pending_restarts(self, job):
        started, calls = job
        t = self._short_for(2, time.time())
        assert _wait_for(lambda: _status("stop_a") != "running")
        settings_service.set_settings({"gpu_guard": False})
        gpu_guard.tick(now=t + 20, reading=(SHORT, COOL))
        settings_service.set_settings({"gpu_guard": True})
        gpu_guard.tick(now=t + 40, reading=(FREE, COOL))
        assert calls == []


class TestWaitForRoom:
    """A registered job whose model memory_headroom refused goes back to
    queued, waits for room and starts again, instead of ending Failed."""

    @pytest.fixture
    def notified(self, monkeypatch):
        from jobs import job_settings
        sent = []

        def note(description, status, **kw):
            sent.append((status, kw.get("job_id")))
        monkeypatch.setattr(job_runner, "_notify_job_finished", note)
        monkeypatch.setattr(job_settings, "_notify_job_finished", note)
        return sent

    @pytest.fixture
    def refused(self, notified):
        import memory_headroom
        calls = []
        registered = threading.Event()

        def refuse():
            # A refusal that lands before allow_restart has nothing to wait
            # for and ends Failed; on a busy runner the job thread can get there first.
            assert registered.wait(5)
            raise memory_headroom.HeadroomError("Not loading Whisper.", vram_need_mb=3072.0)

        def again():
            calls.append(1)
            assert job_launch.start_job("room_a", lambda: None, gpu_touching=True)
        gpu_guard.tick(now=time.time(), reading=(2048.0, COOL))
        assert job_launch.start_job("room_a", refuse, gpu_touching=True)
        gpu_guard.allow_restart("room_a", again)
        registered.set()
        assert _wait_for(lambda: _status("room_a") != "running")
        return calls

    def test_it_waits_queued_without_a_failure_notice(self, refused, notified):
        assert _status("room_a") == "queued"
        assert _message("room_a") == (
            "Waiting for GPU memory: its model needs about 3.0 GB on top of the 1.0 GB kept "
            "free, and 2.0 GB is free. Starts by itself when there is room.")
        assert notified == [] and gpu_guard.status()["pending_restarts"] == 1

    def test_starts_again_once_free_covers_the_reserve_and_the_model(self, refused, notified):
        t = time.time()
        gpu_guard.tick(now=t + 20, reading=(3000.0, COOL))
        assert refused == [] and _status("room_a") == "queued"
        gpu_guard.tick(now=t + 40, reading=(4200.0, COOL))
        assert refused == [1] and gpu_guard.status()["pending_restarts"] == 0
        # The notice goes out just after the status is recorded.
        assert _wait_for(lambda: notified == [("done", "room_a")])
        assert _status("room_a") == "done"

    def test_gives_up_after_the_wait_as_failed_with_the_notice(self, refused, notified):
        t = time.time()
        gpu_guard.tick(now=t + gpu_guard.ROOM_WAIT_SECONDS + 1, reading=(2048.0, COOL))
        assert refused == [] and _status("room_a") == "error"
        assert "Not loading Whisper." in bg.get_status("room_a")["error"]
        assert "within an hour" in _message("room_a")
        assert notified == [("error", "room_a")]

    def test_cancel_while_waiting_ends_it(self, refused, notified):
        bg.request_cancel("room_a")
        assert _status("room_a") == "cancelled"
        gpu_guard.tick(now=time.time() + 20, reading=(FREE, COOL))
        assert refused == [] and gpu_guard.status()["pending_restarts"] == 0

    def test_a_run_cleared_while_waiting_is_not_started(self, refused):
        bg.clear_job("room_a")
        gpu_guard.tick(now=time.time() + 20, reading=(FREE, COOL))
        assert refused == [] and gpu_guard.status()["pending_restarts"] == 0

    def test_turning_the_guard_off_ends_it_as_failed(self, refused, notified):
        settings_service.set_settings({"gpu_guard": False})
        gpu_guard.tick(now=time.time() + 20, reading=(FREE, COOL))
        assert _status("room_a") == "error" and notified == [("error", "room_a")]

    def test_without_a_fresh_card_reading_it_fails_as_before(self, notified):
        import memory_headroom

        def refuse():
            raise memory_headroom.HeadroomError("Not loading Whisper.", vram_need_mb=3072.0)
        assert job_launch.start_job("room_b", refuse, gpu_touching=True)
        gpu_guard.allow_restart("room_b", lambda: None)
        assert _wait_for(lambda: notified == [("error", "room_b")])
        assert _status("room_b") == "error"

    def test_a_process_job_waits_from_its_result(self, notified):
        import logging
        from jobs.job_table import _jobs, _lock
        run = {"status": "running", "kind": "process", "gpu_touching": True,
               "cancel_requested": False, "message": ""}
        with _lock:
            _jobs["proc_r"] = run
        gpu_guard.allow_restart("proc_r", lambda: None)
        gpu_guard.tick(now=time.time(), reading=(2048.0, COOL))
        result = {"failed_reason": "headroom", "detail": "Not loading Whisper.", "vram_need_mb": 3072.0}
        assert job_runner._record_final_status("proc_r", run, None, ("ok", result), result, None,
                                               logging.getLogger("t")) is None
        assert run["status"] == "queued" and "result" not in run
        with _lock:
            _jobs.pop("proc_r")

    def test_a_ram_refusal_or_other_failure_ends_as_before(self, notified):
        assert job_runner._refused_need_mb({"failed_reason": "headroom", "vram_need_mb": None}) is None
        assert job_runner._refused_need_mb({"failed_reason": "no_lines"}) is None
        gpu_guard.tick(now=time.time(), reading=(2048.0, COOL))

        def fail():
            raise RuntimeError("broken")
        assert job_launch.start_job("fail_a", fail, gpu_touching=True)
        gpu_guard.allow_restart("fail_a", lambda: None)
        assert _wait_for(lambda: _status("fail_a") == "error")
        assert gpu_guard.status()["pending_restarts"] == 0


class TestServicesRegister:
    """The job types whose stop and start again is clean register a restart;
    the call repeats the user's own call."""

    def test_translation_registers_only_a_plain_run(self, monkeypatch):
        from services import translate_run_service as trs
        seen = {}
        monkeypatch.setattr(gpu_guard, "allow_restart", lambda jid, fn: seen.update({jid: fn}))
        # Just the tail of start_translate_run: everything before the launch is stubbed.
        for name in ("_resolve_run_options", "_build_engine_chain", "_prepare_run", "_launch_translate",
                     "_refuse_if_busy"):
            monkeypatch.setattr(trs, name, _stub_for(name))
        monkeypatch.setattr(trs.ownership_service, "require_drama", lambda _id: {"id": 3})
        for name in ("check_mode_options",):
            monkeypatch.setattr(trs.translate_run_options, name, lambda *a, **k: None)
        monkeypatch.setattr(trs.translate_run_options, "select_lines", lambda *a, **k: ([], None, []))
        monkeypatch.setattr(trs.translate_run_options, "instantiate_engines",
                            lambda *a, **k: ([object()], object(), None))
        trs.start_translate_run(3, engine_name="ollama", model="m")
        assert seen["translate_3"].keywords["engine_name"] == "ollama"
        assert seen["translate_3"].args == ()
        seen.clear()
        trs.start_translate_run(3, force_retranslate=True)
        trs.start_translate_run(3, reflect=True)
        trs.start_translate_run(3, line_ids=[1])
        assert seen == {}


def _stub_for(name):
    from types import SimpleNamespace
    if name == "_resolve_run_options":
        return lambda *a, **k: SimpleNamespace(engine_name="ollama", model="m", locale="en-US")
    if name == "_build_engine_chain":
        return lambda *a, **k: ([{"engine": "ollama"}], [], [None])
    if name == "_prepare_run":
        return lambda *a, **k: ({"job_id": "translate_3"}, {}, None, [], [])
    return lambda *a, **k: None
