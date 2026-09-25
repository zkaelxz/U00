"""
tests/test_background_jobs.py -- the background job runner that lets long
operations (translation, dubbing) survive Streamlit's script lifecycle.

The property that matters isn't "does the function run" -- it's "does it
keep running with nobody polling it," since that's exactly the scenario
of switching to another tab and coming back later.
"""
import sys
import os
import threading
import time

import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs as bg


def _wait(job_id, timeout=2.0):
    start = time.time()
    while bg.is_running(job_id) and time.time() - start < timeout:
        time.sleep(0.01)


class TestBasicLifecycle:
    def test_job_starts_and_reports_running(self):
        bg.start_job("t1", lambda: time.sleep(0.05))
        assert bg.is_running("t1")
        _wait("t1")
        bg.clear_job("t1")

    def test_completed_job_reports_done_with_full_progress(self):
        bg.start_job("t2", lambda: None)
        _wait("t2")
        status = bg.get_status("t2")
        assert status["status"] == "done"
        assert status["progress"] == 1.0
        bg.clear_job("t2")

    def test_unknown_job_returns_none_not_an_error(self):
        assert bg.get_status("never_started") is None
        assert bg.is_running("never_started") is False

    def test_duplicate_start_is_refused_while_running(self):
        calls = []

        def slow():
            time.sleep(0.05)
            calls.append(1)

        assert bg.start_job("t3", slow) is True
        assert bg.start_job("t3", slow) is False  # must not launch a second one
        _wait("t3")
        assert len(calls) == 1
        bg.clear_job("t3")

    def test_can_restart_after_previous_run_finished(self):
        bg.start_job("t4", lambda: None)
        _wait("t4")
        assert bg.start_job("t4", lambda: None) is True  # fine once t4 isn't running
        _wait("t4")
        bg.clear_job("t4")


class TestSurvivesWithNoPolling:
    """The actual bug being fixed: work has to complete even when the
    Streamlit script isn't executing at all -- exactly what happens while
    someone is looking at a different tab."""

    def test_progress_advances_during_a_silent_period(self):
        sink = []

        def work():
            for i in range(5):
                time.sleep(0.03)
                sink.append(i)
                bg.update_progress("t5", (i + 1) / 5)

        bg.start_job("t5", work)
        time.sleep(0.1)  # nobody polls during this window
        status = bg.get_status("t5")
        assert status["progress"] > 0
        assert len(sink) > 0
        _wait("t5")
        assert len(sink) == 5
        bg.clear_job("t5")

    def test_job_finishes_even_if_never_polled_until_the_end(self):
        def work():
            time.sleep(0.05)

        bg.start_job("t6", work)
        time.sleep(0.15)  # long enough to finish, zero polling in between
        assert bg.get_status("t6")["status"] == "done"
        bg.clear_job("t6")


class TestErrorHandling:
    def test_exception_is_captured_not_raised_to_the_caller(self):
        bg.start_job("t7", lambda: (_ for _ in ()).throw(ValueError("boom")))
        _wait("t7")
        status = bg.get_status("t7")
        assert status["status"] == "error"
        assert "ValueError" in status["error"]
        assert "boom" in status["error"]
        bg.clear_job("t7")

    def test_traceback_is_available_for_debugging(self):
        bg.start_job("t8", lambda: 1 / 0)
        _wait("t8")
        status = bg.get_status("t8")
        assert "ZeroDivisionError" in status["traceback"]
        bg.clear_job("t8")


class TestResultPassing:
    def test_set_result_is_readable_after_completion(self):
        def work():
            bg.set_result("t9", {"errors": [], "count": 42})

        bg.start_job("t9", work)
        _wait("t9")
        assert bg.get_status("t9")["result"] == {"errors": [], "count": 42}
        bg.clear_job("t9")

    def test_result_defaults_to_none(self):
        bg.start_job("t10", lambda: None)
        _wait("t10")
        assert bg.get_status("t10")["result"] is None
        bg.clear_job("t10")


class TestCancellationFlag:
    def test_flag_is_off_by_default(self):
        bg.start_job("t11", lambda: time.sleep(0.02))
        assert bg.is_cancel_requested("t11") is False
        _wait("t11")
        bg.clear_job("t11")

    def test_request_cancel_sets_the_flag(self):
        bg.start_job("t12", lambda: time.sleep(0.05))
        bg.request_cancel("t12")
        assert bg.is_cancel_requested("t12") is True
        _wait("t12")
        bg.clear_job("t12")


class TestCleanup:
    def test_clear_job_removes_the_record(self):
        bg.start_job("t13", lambda: None)
        _wait("t13")
        bg.clear_job("t13")
        assert bg.get_status("t13") is None

    def test_clear_all_jobs_wipes_everything(self):
        bg.start_job("t14", lambda: time.sleep(0.02))
        bg.start_job("t15", lambda: None)
        time.sleep(0.01)
        bg.clear_all_jobs()
        assert bg.get_status("t14") is None
        assert bg.get_status("t15") is None

    def test_list_running_jobs_only_includes_active_ones(self):
        bg.start_job("t16", lambda: time.sleep(0.05))
        bg.start_job("t17", lambda: None)
        _wait("t17")
        running = bg.list_running_jobs()
        assert "t16" in running
        assert "t17" not in running
        _wait("t16")
        bg.clear_all_jobs()


class TestCancellationActuallyStopsWork:
    """The cancellation flag existed since background_jobs.py was built,
    but nothing ever checked it -- a background translation job would run
    to completion (or failure) no matter what. This is the real fix:
    translate_lines_with_engine now checks cancel_check_cb between batches
    and stops early, saving whatever finished so far."""

    def test_job_stops_between_batches_once_cancelled(self):
        import translate_engines as te
        from core import Line

        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(20)]

        class TrackingEngine:
            supports_reference = False
            def __init__(self):
                self.calls = 0
            def translate_batch(self, zh_lines, context):
                self.calls += 1
                return [f"EN:{z}" for z in zh_lines]

        engine = TrackingEngine()
        cancel_after_first_batch = {"cancelled": False}

        def cancel_check():
            if engine.calls >= 1:
                cancel_after_first_batch["cancelled"] = True
            return cancel_after_first_batch["cancelled"]

        te.translate_lines_with_engine(
            lines, engine, {}, batch_size=2, cancel_check_cb=cancel_check)

        translated = sum(1 for l in lines if l.en)
        assert translated < len(lines), "must have stopped early, not run to completion"
        assert translated == 2, f"exactly the first batch should have completed, got {translated}"

    def test_no_cancel_callback_runs_to_completion_as_before(self):
        import translate_engines as te
        from core import Line
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]

        class Engine:
            supports_reference = False
            def translate_batch(self, zh_lines, context):
                return [f"EN:{z}" for z in zh_lines]

        te.translate_lines_with_engine(lines, Engine(), {}, batch_size=2)
        assert all(l.en for l in lines)  # unaffected when nobody asks to cancel

    def test_full_reset_race_does_not_corrupt_a_reused_id(self, isolated_db):
        """Regression test for a real, serious bug: an in-flight background
        job's stale writes landing on a brand-new drama that reused the id
        of the one the job's original drama was deleted from. Reproduces
        the exact scenario, with the fix in place: cancel + wait before
        the destructive reset proceeds."""
        import time
        import translate_engines as te
        from core import Line

        did_old = isolated_db.create_drama(title_en="Original")
        lines_old = [Line(idx=i, start=0, end=1, zh=f"OLD_{i}") for i in range(10)]
        isolated_db.save_lines(did_old, lines_old)

        class SlowEngine:
            supports_reference = False
            def translate_batch(self, zh_lines, context):
                time.sleep(0.02)
                return [f"STALE_{z}" for z in zh_lines]

        def run_job(job_id, drama_id, lines, engine):
            te.translate_lines_with_engine(
                lines, engine, {}, batch_size=2,
                save_cb=lambda ls: isolated_db.save_lines(drama_id, ls),
                cancel_check_cb=lambda: bg.is_cancel_requested(job_id))

        import background_jobs as bg
        job_id = f"translate_{did_old}"
        bg.start_job(job_id, run_job, job_id, did_old, lines_old, SlowEngine())
        time.sleep(0.03)  # mid-flight

        # The fixed sequence: cancel + wait, THEN reset.
        running = bg.list_running_jobs()
        for jid in running:
            bg.request_cancel(jid)
        deadline = time.time() + 5
        while time.time() < deadline and any(bg.is_running(j) for j in running):
            time.sleep(0.02)
        assert not any(bg.is_running(j) for j in running)

        isolated_db.reset_library()
        bg.clear_all_jobs()

        did_new = isolated_db.create_drama(title_en="Fresh")
        isolated_db.save_lines(did_new, [Line(idx=0, start=0, end=1, zh="clean", en="")])
        time.sleep(0.1)
        final = isolated_db.load_lines(did_new)
        assert not any("STALE_" in (r.get("en") or "") for r in final)
        assert not any(r["zh"].startswith("OLD_") for r in final)


class TestCancelLineJobs:
    """Step 2: translate/flag/fix-flagged jobs now write only their own
    fields by permanent line id, so the old "one at a time" guard is gone.
    The one thing that still has to stop them is the drama's lines being
    replaced wholesale (a new transcription) -- cancel_line_jobs asks each
    running one to stop."""

    def test_cancels_every_running_line_job_for_that_drama_only(self):
        release = threading.Event()
        for jid in ("translate_601", "flag_601", "fixflag_601", "translate_602"):
            bg.start_job(jid, release.wait)
        bg.cancel_line_jobs(601)
        assert bg.is_cancel_requested("translate_601")
        assert bg.is_cancel_requested("flag_601")
        assert bg.is_cancel_requested("fixflag_601")
        assert not bg.is_cancel_requested("translate_602")
        release.set()
        for jid in ("translate_601", "flag_601", "fixflag_601", "translate_602"):
            _wait(jid)
            bg.clear_job(jid)

    def test_nothing_running_is_a_no_op(self):
        bg.cancel_line_jobs(603)  # must not raise


class TestFailedJobIsLogged:
    """Before this, the app had no logging at all -- a background job's
    failure left only whatever happened to be on screen at the time.
    Every job failure now writes its traceback to library/logs/app.log,
    with any API key redacted first."""

    def test_traceback_is_written_to_the_log_file(self, isolated_db):
        import applog

        bg.start_job("t_log_1", lambda: 1 / 0)
        _wait("t_log_1")
        bg.clear_job("t_log_1")

        lines = applog.tail(50)
        joined = "\n".join(lines)
        assert "t_log_1" in joined
        assert "ZeroDivisionError" in joined

    def test_logged_error_has_no_api_key_in_it(self, isolated_db):
        import applog

        def boom():
            raise RuntimeError("400 Client Error: Bad Request for url: "
                                "https://generativelanguage.googleapis.com/v1beta/"
                                "models/x:generateContent?key=AIzaSyFAKESECRETVALUE12345")

        bg.start_job("t_log_2", boom)
        _wait("t_log_2")
        status = bg.get_status("t_log_2")
        bg.clear_job("t_log_2")

        assert "AIzaSyFAKESECRETVALUE12345" not in status["error"]
        assert "AIzaSyFAKESECRETVALUE12345" not in status["traceback"]
        joined = "\n".join(applog.tail(50))
        assert "AIzaSyFAKESECRETVALUE12345" not in joined


class TestGpuJobGuard:
    """Step 5c: a soft, global "one GPU job at a time" guard, prompted by a
    shared review flagging that nothing today stops two independent
    GPU-touching jobs (different job_ids, different dramas) from running
    concurrently and competing for the same VRAM. gpu_touching=True jobs
    queue behind each other instead of starting immediately; non-GPU jobs
    are unaffected either way."""

    def setup_method(self):
        bg.set_gpu_limit_enabled(True)

    def teardown_method(self):
        bg.set_gpu_limit_enabled(True)  # never leak into other tests

    def test_a_second_gpu_job_queues_instead_of_starting(self):
        release = threading.Event()
        started = threading.Event()

        def slow():
            started.set()
            release.wait(timeout=2.0)

        assert bg.start_job("gpu_a", slow, gpu_touching=True) is True
        started.wait(timeout=2.0)
        assert bg.get_status("gpu_a")["status"] == "running"

        calls = []
        assert bg.start_job("gpu_b", lambda: calls.append(1), gpu_touching=True) is True
        assert bg.get_status("gpu_b")["status"] == "queued"
        assert calls == []  # queued, not started

        release.set()
        _wait("gpu_a")
        bg.clear_job("gpu_a")
        bg.clear_job("gpu_b")

    def test_a_non_gpu_job_is_unaffected_by_a_running_gpu_job(self):
        release = threading.Event()
        bg.start_job("gpu_c", lambda: release.wait(timeout=2.0), gpu_touching=True)

        calls = []
        assert bg.start_job("cpu_a", lambda: calls.append(1), gpu_touching=False) is True
        _wait("cpu_a")
        assert calls == [1]  # ran immediately, no queueing

        release.set()
        _wait("gpu_c")
        bg.clear_job("gpu_c")
        bg.clear_job("cpu_a")

    def test_queued_gpu_job_starts_automatically_once_the_running_one_finishes(self):
        release = threading.Event()
        started = threading.Event()
        bg.start_job("gpu_d", lambda: (started.set(), release.wait(timeout=2.0)),
                     gpu_touching=True)
        started.wait(timeout=2.0)

        second_ran = threading.Event()
        bg.start_job("gpu_e", lambda: second_ran.set(), gpu_touching=True)
        assert bg.get_status("gpu_e")["status"] == "queued"

        release.set()
        assert second_ran.wait(timeout=2.0), "queued job never started after the first finished"
        _wait("gpu_e")
        assert bg.get_status("gpu_e")["status"] == "done"
        bg.clear_job("gpu_d")
        bg.clear_job("gpu_e")

    def test_toggle_off_allows_two_gpu_jobs_to_run_concurrently(self):
        bg.set_gpu_limit_enabled(False)
        release = threading.Event()
        started_f = threading.Event()
        bg.start_job("gpu_f", lambda: (started_f.set(), release.wait(timeout=2.0)),
                     gpu_touching=True)
        started_f.wait(timeout=2.0)

        started_g = threading.Event()
        bg.start_job("gpu_g", lambda: started_g.set(), gpu_touching=True)
        assert started_g.wait(timeout=2.0), "second GPU job should start immediately when the limit is off"
        assert bg.get_status("gpu_g")["status"] != "queued"

        release.set()
        _wait("gpu_f")
        _wait("gpu_g")
        bg.clear_job("gpu_f")
        bg.clear_job("gpu_g")

    def test_gpu_busy_description_reflects_the_running_job(self):
        release = threading.Event()
        started = threading.Event()
        bg.start_job("gpu_h", lambda: (started.set(), release.wait(timeout=2.0)),
                     gpu_touching=True, description="Transcription for Test Drama")
        started.wait(timeout=2.0)

        assert bg.gpu_busy_description() == "Transcription for Test Drama"
        assert "Transcription for Test Drama" in bg.get_status("gpu_h")["description"]

        release.set()
        _wait("gpu_h")
        bg.clear_job("gpu_h")

    def test_clearing_a_queued_job_drops_it_from_the_queue(self):
        release = threading.Event()
        bg.start_job("gpu_i", lambda: release.wait(timeout=2.0), gpu_touching=True)

        calls = []
        bg.start_job("gpu_j", lambda: calls.append(1), gpu_touching=True)
        assert bg.get_status("gpu_j")["status"] == "queued"

        bg.clear_job("gpu_j")  # the user navigated away / reset before it ever ran
        release.set()
        _wait("gpu_i")
        time.sleep(0.1)  # give a wrongly-surviving queue entry a chance to fire
        assert bg.get_status("gpu_j") is None
        assert calls == []
        bg.clear_job("gpu_i")


class TestGpuSlot:
    """gpu_slot() is for GPU-touching work that runs synchronously in the
    calling thread (diarization, dub generation) instead of as its own
    start_job() job -- it still has to participate in the same "one GPU
    job at a time" accounting, or it would be invisible to the guard
    above and defeat the point of Step 5c."""

    def setup_method(self):
        bg.set_gpu_limit_enabled(True)

    def teardown_method(self):
        bg.set_gpu_limit_enabled(True)

    def test_blocks_until_a_running_gpu_job_finishes(self):
        release = threading.Event()
        started = threading.Event()
        bg.start_job("gpu_k", lambda: (started.set(), release.wait(timeout=2.0)),
                     gpu_touching=True)
        started.wait(timeout=2.0)

        entered = threading.Event()

        def use_slot():
            with bg.gpu_slot("Diarization", poll_interval=0.02):
                entered.set()

        t = threading.Thread(target=use_slot)
        t.start()
        time.sleep(0.1)
        assert not entered.is_set(), "gpu_slot must not enter while a GPU job is running"

        release.set()
        t.join(timeout=2.0)
        assert entered.is_set()
        _wait("gpu_k")
        bg.clear_job("gpu_k")

    def test_a_gpu_touching_start_job_queues_behind_an_active_gpu_slot(self):
        entered = threading.Event()
        release = threading.Event()

        def hold_slot():
            with bg.gpu_slot("Dub generation", poll_interval=0.02):
                entered.set()
                release.wait(timeout=2.0)

        t = threading.Thread(target=hold_slot)
        t.start()
        entered.wait(timeout=2.0)

        bg.start_job("gpu_l", lambda: None, gpu_touching=True)
        assert bg.get_status("gpu_l")["status"] == "queued"

        release.set()
        t.join(timeout=2.0)
        _wait("gpu_l")
        assert bg.get_status("gpu_l")["status"] == "done"
        bg.clear_job("gpu_l")

    def test_off_toggle_lets_gpu_slot_run_immediately_alongside_a_gpu_job(self):
        bg.set_gpu_limit_enabled(False)
        release = threading.Event()
        bg.start_job("gpu_m", lambda: release.wait(timeout=2.0), gpu_touching=True)

        entered = threading.Event()
        with bg.gpu_slot("Diarization", poll_interval=0.02):
            entered.set()
        assert entered.is_set()

        release.set()
        _wait("gpu_m")
        bg.clear_job("gpu_m")


class TestEtaHelpers:
    """Step 9b.1: a simple ETA next to the existing progress bar, from
    started_at + progress -- no new job-tracking fields."""

    def test_no_estimate_right_at_the_start(self):
        assert bg.eta_seconds(started_at=1000.0, frac=0.0, now=1005.0) is None
        assert bg.eta_seconds(started_at=1000.0, frac=0.01, now=1005.0) is None

    def test_no_estimate_once_done(self):
        assert bg.eta_seconds(started_at=1000.0, frac=1.0, now=1100.0) is None

    def test_no_estimate_with_no_start_time(self):
        assert bg.eta_seconds(started_at=None, frac=0.5, now=1000.0) is None
        assert bg.eta_seconds(started_at=0, frac=0.5, now=1000.0) is None

    def test_linear_extrapolation(self):
        # 50s elapsed at 25% -> 150s remaining.
        assert bg.eta_seconds(started_at=1000.0, frac=0.25, now=1050.0) == pytest.approx(150.0)

    def test_format_seconds_vs_minutes(self):
        assert bg.format_eta(45) == " (~45 sec remaining)"
        assert bg.format_eta(90) == " (~2 min remaining)"
        assert bg.format_eta(600) == " (~10 min remaining)"

    def test_format_none_is_empty(self):
        assert bg.format_eta(None) == ""

    def test_format_rounds_up_to_at_least_one(self):
        assert bg.format_eta(0.4) == " (~1 sec remaining)"
        assert bg.format_eta(59) == " (~59 sec remaining)"
        assert bg.format_eta(65) == " (~1 min remaining)"

    def test_eta_text_reads_a_job_status_dict(self):
        job = {"started_at": 1000.0, "progress": 0.5}
        assert bg.eta_text(job, now=1010.0) == " (~10 sec remaining)"

    def test_eta_text_handles_a_missing_or_empty_job(self):
        assert bg.eta_text(None) == ""
        assert bg.eta_text({}) == ""

    def test_eta_text_is_wired_into_a_real_running_job(self):
        job_id = "test_eta_real_job"
        bg.clear_job(job_id)
        started = bg.start_job(job_id, lambda: time.sleep(0.3))
        assert started
        time.sleep(0.05)
        bg.update_progress(job_id, 0.5, "Working...")
        status = bg.get_status(job_id)
        assert bg.eta_text(status) != ""
        time.sleep(0.4)
        bg.clear_job(job_id)


class _FakeProcess:
    """Stands in for multiprocessing.Process -- no real OS process is
    ever spawned in these tests, matching this repo's no-GPU/no-real-
    subprocess testing convention. Behavior is driven by the flags
    below rather than an actual target function running in isolation."""

    def __init__(self, target, args, daemon=True, run_target_on_start=False,
                alive_forever=False, exitcode_if_no_result=1):
        self._target = target
        self._args = args
        self._alive = True
        self.terminated = False
        self.exitcode = None
        self._run_target_on_start = run_target_on_start
        self._alive_forever = alive_forever
        self._exitcode_if_no_result = exitcode_if_no_result

    def start(self):
        if self._run_target_on_start:
            self._target(*self._args)
            self._alive = False
            self.exitcode = 0
        elif not self._alive_forever:
            self._alive = False
            self.exitcode = self._exitcode_if_no_result

    def is_alive(self):
        return self._alive

    def terminate(self):
        self.terminated = True
        self._alive = False
        self.exitcode = -15

    def join(self, timeout=None):
        pass


def _install_fake_process(monkeypatch, **kwargs):
    """Patches bg.multiprocessing.Process with a factory building
    _FakeProcess(**kwargs) instances, and returns the list of instances
    it creates (in order) so a test can inspect e.g. .terminated after
    the fact."""
    instances = []

    def factory(target, args, daemon=True):
        proc = _FakeProcess(target, args, daemon=daemon, **kwargs)
        instances.append(proc)
        return proc

    monkeypatch.setattr(bg.multiprocessing, "Process", factory)
    return instances


def _wait_for_status(job_id, not_status, timeout=2.0):
    """Polls until get_status(job_id)["status"] is no longer not_status
    (or the timeout elapses) -- for the process-watcher tests below,
    where a real background thread (only multiprocessing.Process itself
    is faked) needs a moment to notice and react."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = bg.get_status(job_id)
        if status is None or status["status"] != not_status:
            return status
        time.sleep(0.02)
    return bg.get_status(job_id)


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
        assert bg.start_process_job(job_id, fake_worker, args=(2, 3)) is True
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
        bg.start_process_job(job_id, fake_worker, args=())
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert "RuntimeError" in status["error"] and "boom" in status["error"]
        bg.clear_job(job_id)

    def test_subprocess_exiting_with_no_result_is_reported_as_an_error(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=False, exitcode_if_no_result=1)

        job_id = "test_process_crash"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda result_queue: None, args=())
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
        assert bg.start_process_job(job_id, lambda result_queue: None, args=()) is True
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
        bg.start_process_job(job_id, lambda result_queue: None, args=())
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
        bg.start_job("test_process_gpu_thread", lambda: (started.set(), release.wait(timeout=2.0)),
                     gpu_touching=True)
        started.wait(timeout=2.0)

        job_id = "test_process_gpu_process"
        bg.clear_job(job_id)
        assert bg.start_process_job(
            job_id, lambda result_queue: result_queue.put(("ok", {})),
            args=(), gpu_touching=True) is True
        assert bg.get_status(job_id)["status"] == "queued"

        release.set()
        _wait("test_process_gpu_thread")
        status = _wait_for_status(job_id, "queued")
        assert status["status"] == "done"
        bg.clear_job("test_process_gpu_thread")
        bg.clear_job(job_id)
