"""
tests/test_background_jobs.py -- the background job runner that lets long
operations (translation, dubbing) survive Streamlit's script lifecycle.

The property that matters isn't "does the function run" -- it's "does it
keep running with nobody polling it," since that's exactly the scenario
of switching to another tab and coming back later.
"""
import sys
import os
import queue
import shutil
import sqlite3
import tempfile
import threading
import time

import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs as bg
import db
import diagnostics_torch


def _isolate_library():
    """Migration Slice 9: several settings (GPU-limit, notify-on-completion)
    now persist to db.app_settings for real, so any setup_method/
    teardown_method exercising them needs a real, valid LIBRARY_DIR --
    setup_method runs outside pytest's own fixture resolution, so
    isolated_db can't be requested the normal way here. Same
    isolate/restore logic as conftest.py's isolated_db fixture, inlined."""
    previous = db.LIBRARY_DIR
    temp_dir = tempfile.mkdtemp(prefix="baihe_test_bg_")
    db.configure_library_dir(temp_dir)
    db.init_db()
    return previous, temp_dir


def _restore_library(previous, temp_dir):
    db.configure_library_dir(previous)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _private_library():
    """Every test here gets its own library, not just the classes with a
    setup_method. Job status is mirrored to job_records and GPU-touching
    jobs take db.gpu_lock, both in whatever library db points at; left at
    the default that is the repo's own library/library.db, shared by all
    pytest-xdist workers. Another worker's GPU job holding that lock made
    a GPU job here queue instead of run
    (test_a_process_job_queues_behind_a_running_gpu_thread_job saw
    'running' where it expected 'queued'). Classes that isolate again in
    setup_method nest inside this and restore back to it."""
    state = _isolate_library()
    yield
    _restore_library(*state)


def _wait(job_id, timeout=2.0):
    start = time.time()
    while bg.is_running(job_id) and time.time() - start < timeout:
        time.sleep(0.01)


def _wait_for(predicate, timeout=2.0):
    """Polls until `predicate()` is truthy, or the timeout elapses; returns
    what it ended up as.

    `_wait` above only waits for a job to stop *running*, and a job's
    completion side effects do not all land at that moment. A finishing
    job sets its status inside `background_jobs`' lock and then notifies
    *outside* it, with a log call in between, so `is_running()` goes
    False while the notification is still pending. Asserting on the
    notification right after `_wait` therefore races the worker thread:
    it usually wins, and occasionally doesn't -- which showed up as a
    single unreproducible failure in an otherwise green full-suite run.
    Wait for the side effect itself instead of a proxy for it.
    """
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        time.sleep(0.01)
    return predicate()


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
        # Waits on the worker's own signal instead of a fixed sleep, so a
        # loaded machine (pytest-xdist) can't make the check run before
        # the first step; still nothing polls background_jobs meanwhile.
        sink = []
        first_step = threading.Event()

        def work():
            for i in range(5):
                time.sleep(0.03)
                sink.append(i)
                bg.update_progress("t5", (i + 1) / 5)
                first_step.set()

        bg.start_job("t5", work)
        assert first_step.wait(10)  # nobody polls during this window
        status = bg.get_status("t5")
        assert status["progress"] > 0
        assert len(sink) > 0
        _wait("t5", timeout=10)
        assert len(sink) == 5
        bg.clear_job("t5")

    def test_job_finishes_even_if_never_polled_until_the_end(self):
        # The work signals when it has returned; the test blocks on that
        # (zero polling of background_jobs in between) rather than on a
        # fixed 0.15 s sleep that a busy xdist worker could overrun. The
        # "done" transition lands just after the target returns, so that
        # one read is polled with a deadline.
        returned = threading.Event()

        def work():
            time.sleep(0.05)
            returned.set()

        bg.start_job("t6", work)
        assert returned.wait(10)
        assert _wait_for(lambda: bg.get_status("t6")["status"] == "done", timeout=10)
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
        # t16 holds until released: a fixed sleep(0.05) raced the start of
        # t17 plus _wait's polling, and lost on a loaded machine.
        release = threading.Event()
        bg.start_job("t16", lambda: release.wait(10))
        try:
            bg.start_job("t17", lambda: None)
            _wait("t17")
            running = bg.list_running_jobs()
            assert "t16" in running
            assert "t17" not in running
        finally:
            release.set()
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

        # The fixed sequence: cancel + wait, THEN reset. Waiting means the
        # job's thread has exited, not just that it stopped reading as
        # running: its tail still writes to the database after that.
        running = bg.list_running_jobs()
        for jid in running:
            bg.request_cancel(jid)
        assert bg.wait_for_job_threads(5)
        assert not any(bg.is_running(j) for j in running)

        isolated_db.reset_library()
        bg.clear_all_jobs()

        did_new = isolated_db.create_drama(title_en="Fresh")
        isolated_db.save_lines(did_new, [Line(idx=0, start=0, end=1, zh="clean", en="")])
        time.sleep(0.1)
        final = isolated_db.load_lines(did_new)
        assert not any("STALE_" in (r.get("en") or "") for r in final)
        assert not any(r["zh"].startswith("OLD_") for r in final)


class TestWaitForJobThreads:
    """A job reads as finished before its thread is done: the thread still
    writes its notification, timing row and GPU-lock release to the
    database. wait_for_job_threads waits for the thread itself."""

    def test_waits_for_a_finished_jobs_thread_still_in_its_tail(self, monkeypatch):
        in_tail, release = threading.Event(), threading.Event()

        def blocked_notify(*args, **kwargs):
            in_tail.set()
            release.wait(10)

        monkeypatch.setattr(bg, "_notify_job_finished", blocked_notify)
        bg.start_job("t_tail", lambda: None)
        try:
            assert in_tail.wait(10)
            assert bg.get_status("t_tail")["status"] == "done"
            assert bg.is_running("t_tail") is False
            assert bg.wait_for_job_threads(0.05) is False
        finally:
            release.set()
        assert bg.wait_for_job_threads(10) is True


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


class TestAnyJobRunningForDrama:
    """Step 25d item 8: any_job_running_for_drama() -- a broader check
    than LINE_WRITING_JOB_PREFIXES/cancel_line_jobs above, for "is
    anything still working on this drama at all?" before a destructive,
    whole-drama action (deleting it) rather than the narrower "these jobs
    are safe to run alongside each other" question those answer."""

    def test_false_when_nothing_is_running(self):
        assert bg.any_job_running_for_drama(701) is False

    def test_true_while_a_line_writing_job_is_running(self):
        release = threading.Event()
        bg.start_job("translate_701", release.wait)
        try:
            assert bg.any_job_running_for_drama(701) is True
        finally:
            release.set()
            _wait("translate_701")
            bg.clear_job("translate_701")

    def test_true_while_a_non_line_writing_job_is_running(self):
        """transcribe_/consistency_/emotion_/etc. aren't in
        LINE_WRITING_JOB_PREFIXES (they don't need to run alongside a
        translate job the same way), but they still touch the drama and
        must still block a delete."""
        release = threading.Event()
        bg.start_job("transcribe_702", release.wait)
        try:
            assert bg.any_job_running_for_drama(702) is True
        finally:
            release.set()
            _wait("transcribe_702")
            bg.clear_job("transcribe_702")

    def test_true_while_queued_not_just_running(self, isolated_db):
        bg.set_gpu_limit_enabled(True)
        release = threading.Event()
        bg.start_job("gpu_busy_703", release.wait, gpu_touching=True)
        try:
            bg.start_job("transcribe_703", release.wait, gpu_touching=True)  # queues behind it
            assert bg.get_status("transcribe_703")["status"] == "queued"
            assert bg.any_job_running_for_drama(703) is True
        finally:
            release.set()
            _wait("gpu_busy_703")
            bg.clear_job("gpu_busy_703")
            bg.clear_job("transcribe_703")

    def test_false_for_a_different_drama(self):
        release = threading.Event()
        bg.start_job("translate_704", release.wait)
        try:
            assert bg.any_job_running_for_drama(705) is False
        finally:
            release.set()
            _wait("translate_704")
            bg.clear_job("translate_704")

    def test_false_once_the_job_is_done(self):
        bg.start_job("translate_706", lambda: None)
        _wait("translate_706")
        assert bg.any_job_running_for_drama(706) is False
        bg.clear_job("translate_706")


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
        self._library_state = _isolate_library()
        bg.set_gpu_limit_enabled(True)

    def teardown_method(self):
        bg.set_gpu_limit_enabled(True)  # never leak into other tests
        _restore_library(*self._library_state)

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

    def test_queued_message_never_names_the_running_job(self):
        # Auth B2 (review M-1): the busy job may be another user's private
        # drama, and whoever can see the waiting job reads its message.
        release = threading.Event()
        started = threading.Event()
        bg.start_job("gpu_h", lambda: (started.set(), release.wait(timeout=2.0)),
                     gpu_touching=True, description="Transcription for Test Drama")
        started.wait(timeout=2.0)
        bg.start_job("gpu_h2", lambda: None, gpu_touching=True, description="Mine")

        queued = bg.get_status("gpu_h2")
        assert queued["status"] == "queued"
        assert queued["message"] == bg.GPU_WAIT_MESSAGE
        assert "Test Drama" not in queued["message"] and "gpu_h" not in queued["message"]
        assert "Transcription for Test Drama" in bg.get_status("gpu_h")["description"]

        release.set()
        _wait("gpu_h")
        _wait("gpu_h2")
        bg.clear_job("gpu_h")
        bg.clear_job("gpu_h2")

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


class TestExternalGpuLoadGuard:
    """Step 26d: the GPU guard also respects real load from nvidia-smi
    (diagnostics_torch.external_gpu_is_busy), not just its own two locks --
    so a completely different application on the same GPU (Jellyfin
    transcoding on the same card, say) is respected too, not just other
    Baihe jobs."""

    def setup_method(self):
        self._library_state = _isolate_library()
        bg.set_gpu_limit_enabled(True)

    def teardown_method(self):
        bg.set_gpu_limit_enabled(True)  # never leak into other tests
        _restore_library(*self._library_state)

    def test_queues_when_gpu_is_externally_busy_even_with_no_baihe_job_running(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: True)
        calls = []
        assert bg.start_job("gpu_ext_a", lambda: calls.append(1), gpu_touching=True) is True
        assert bg.get_status("gpu_ext_a")["status"] == "queued"
        assert calls == []  # queued, not started -- nothing Baihe-side is even running
        bg.clear_job("gpu_ext_a")

    def test_recheck_gpu_queue_promotes_once_external_load_clears(self, monkeypatch):
        busy = {"value": True}
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: busy["value"])
        started = threading.Event()
        bg.start_job("gpu_ext_b", lambda: started.set(), gpu_touching=True)
        assert bg.get_status("gpu_ext_b")["status"] == "queued"
        assert not started.is_set()

        busy["value"] = False  # e.g. Jellyfin's transcode finished
        bg.recheck_gpu_queue()
        assert started.wait(timeout=2.0), "queued job never started after external load cleared"
        _wait("gpu_ext_b")
        assert bg.get_status("gpu_ext_b")["status"] == "done"
        bg.clear_job("gpu_ext_b")

    def test_a_non_gpu_job_is_unaffected_by_external_gpu_load(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: True)
        calls = []
        assert bg.start_job("cpu_ext", lambda: calls.append(1), gpu_touching=False) is True
        _wait("cpu_ext")
        assert calls == [1]  # non-GPU jobs never consult the GPU guard at all
        bg.clear_job("cpu_ext")

    def test_queued_message_for_external_load_is_generic(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: True)
        bg.start_job("gpu_ext_c", lambda: None, gpu_touching=True)
        assert bg.get_status("gpu_ext_c")["message"] == bg.GPU_WAIT_MESSAGE
        bg.clear_job("gpu_ext_c")


class TestExternalGpuWaitMessage:
    """A job held back by another program's GPU use says so, with numbers
    only (no process names, paths or command lines)."""

    def setup_method(self):
        self._library_state = _isolate_library()
        bg.set_gpu_limit_enabled(True)

    def teardown_method(self):
        bg.set_gpu_limit_enabled(True)
        _restore_library(*self._library_state)

    def test_queued_message_names_external_use_and_the_fix(self, monkeypatch):
        load = {"utilization_percent": 20.0, "memory_used_mb": 9216.0,
                "memory_total_mb": 10240.0, "memory_free_mb": 1024.0 - 1}
        monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: load)
        bg.start_job("gpu_wait_a", lambda: None, gpu_touching=True)
        status = bg.get_status("gpu_wait_a")
        assert status["status"] == "queued"
        assert status["message"].startswith("Waiting for the GPU: another program is using it")
        assert "9.0 GB of 10.0 GB in use" in status["message"]
        assert "Close GPU-heavy apps" in status["message"] and "Settings" in status["message"]
        bg.clear_job("gpu_wait_a")

    def test_message_goes_back_to_generic_when_load_is_not_external(self, monkeypatch):
        load = {"utilization_percent": 5.0, "memory_used_mb": 100.0,
                "memory_total_mb": 10240.0, "memory_free_mb": 10140.0}
        monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: load)
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: True)
        bg.start_job("gpu_wait_b", lambda: None, gpu_touching=True)
        assert bg.get_status("gpu_wait_b")["message"] == bg.GPU_WAIT_MESSAGE
        bg.clear_job("gpu_wait_b")


class TestStallDetection:
    def test_running_job_with_old_progress_is_flagged_without_changing_state(self):
        job = {"status": "running", "progress_at": 1000.0, "started_at": 900.0}
        assert bg.job_may_be_stalled(job, now=1000.0 + bg.JOB_STALL_SECONDS - 1) is False
        assert bg.job_may_be_stalled(job, now=1000.0 + bg.JOB_STALL_SECONDS + 1) is True
        assert job["status"] == "running"

    def test_stage_without_progress_gets_the_longer_allowance(self):
        job = {"status": "running", "progress_at": 1000.0, "can_report_progress": False}
        assert bg.job_may_be_stalled(job, now=1000.0 + bg.JOB_STALL_SECONDS + 1) is False
        assert bg.job_may_be_stalled(
            job, now=1000.0 + bg.JOB_STALL_NO_PROGRESS_SECONDS + 1) is True

    def test_only_running_jobs_can_be_stalled(self):
        assert bg.job_may_be_stalled({"status": "done", "progress_at": 1.0}, now=1e9) is False

    def test_stage_ticker_shows_elapsed_and_note_but_is_not_progress(self):
        bg._jobs["tick_a"] = {"status": "running", "progress": 0.0, "message": "",
                              "error": None, "cancel_requested": False, "result": None}
        with bg.stage_ticker("tick_a", "Loading model...", interval=0.05):
            first = bg._jobs["tick_a"]["progress_at"]
            time.sleep(0.2)
            msg = bg._jobs["tick_a"]["message"]
            assert msg.startswith("Loading model (elapsed ")
            assert "no progress is available" in msg
            assert bg._jobs["tick_a"]["progress_at"] == first
            assert bg._jobs["tick_a"]["can_report_progress"] is False
        bg.clear_job("tick_a")


class TestGpuParallelSlots:
    """gpu_max_parallel lets more than one GPU job run when nvidia-smi shows
    enough free VRAM; never more than the cap, and one at a time when free
    VRAM can't be read."""

    def setup_method(self):
        self._library_state = _isolate_library()
        bg.set_gpu_limit_enabled(True)
        self._releases = []

    def teardown_method(self):
        for ev in self._releases:
            ev.set()
        for jid in list(bg.list_all_jobs()):
            _wait(jid)
        bg.clear_all_jobs()
        _restore_library(*self._library_state)

    @pytest.fixture(autouse=True)
    def _gpu(self, monkeypatch):
        self.free_mb = {"value": 20000.0}
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
        monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: None if self.free_mb["value"] is None
                            else {"utilization_percent": 90.0, "memory_used_mb": 0.0,
                                  "memory_total_mb": 24000.0, "memory_free_mb": self.free_mb["value"]})
        monkeypatch.setattr(bg, "GPU_PARALLEL_SETTLE_SECONDS", 0)

    def _start(self, job_id):
        release = threading.Event()
        self._releases.append(release)
        assert bg.start_job(job_id, release.wait, 5, gpu_touching=True) is True
        return release

    def _status(self, job_id):
        return bg.get_status(job_id)["status"]

    def test_default_is_one_at_a_time(self):
        assert bg.get_gpu_max_parallel() == 1
        self._start("par_a")
        self._start("par_b")
        assert self._status("par_a") == "running"
        assert self._status("par_b") == "queued"

    def test_cap_is_never_exceeded(self):
        bg.set_gpu_max_parallel(2)
        self._start("par_a")
        self._start("par_b")
        self._start("par_c")
        assert [self._status(j) for j in ("par_a", "par_b", "par_c")] == ["running", "running", "queued"]
        assert db.gpu_lock_holder_count() == 2
        bg.recheck_gpu_queue()
        assert self._status("par_c") == "queued"

    def test_low_free_vram_holds_the_job(self):
        bg.set_gpu_max_parallel(3)
        self.free_mb["value"] = bg.GPU_PARALLEL_RESERVE_MB - 1
        self._start("par_a")
        self._start("par_b")
        assert self._status("par_b") == "queued"

    def test_no_nvidia_smi_runs_one_at_a_time(self):
        bg.set_gpu_max_parallel(4)
        self.free_mb["value"] = None
        self._start("par_a")
        self._start("par_b")
        assert self._status("par_b") == "queued"

    def test_settle_time_holds_a_second_job_until_the_first_has_loaded(self, monkeypatch):
        monkeypatch.setattr(bg, "GPU_PARALLEL_SETTLE_SECONDS", 3600)
        bg.set_gpu_max_parallel(2)
        self._start("par_a")
        self._start("par_b")
        assert self._status("par_b") == "queued"

    def test_queued_job_starts_when_vram_frees(self):
        bg.set_gpu_max_parallel(2)
        self.free_mb["value"] = 500.0
        self._start("par_a")
        self._start("par_b")
        assert self._status("par_b") == "queued"
        self.free_mb["value"] = 20000.0
        bg.recheck_gpu_queue()
        assert self._status("par_b") == "running"
        assert self._status("par_a") == "running"

    def test_promotion_is_first_come_first_served(self):
        bg.set_gpu_max_parallel(2)
        self.free_mb["value"] = 500.0
        release_a = self._start("par_a")
        self._start("par_b")
        self._start("par_c")
        # VRAM frees, but a new arrival still queues behind the waiting ones.
        self.free_mb["value"] = 20000.0
        self._start("par_d")
        assert [self._status(j) for j in ("par_b", "par_c", "par_d")] == ["queued"] * 3
        self.free_mb["value"] = 500.0
        release_a.set()  # a's slot frees: b (the head) gets it, not c or d
        assert _wait_for(lambda: self._status("par_b") == "running")
        assert self._status("par_c") == "queued" and self._status("par_d") == "queued"
        self.free_mb["value"] = 20000.0
        bg.recheck_gpu_queue()
        assert self._status("par_c") == "running"
        assert self._status("par_d") == "queued"

    def test_a_cli_holder_counts_toward_the_cap(self):
        bg.set_gpu_max_parallel(2)
        assert db.try_acquire_gpu_lock("cli:1", "CLI translate")
        self._start("par_a")
        self._start("par_b")
        assert self._status("par_a") == "running"
        assert self._status("par_b") == "queued"
        assert db.gpu_lock_holder_count() == 2

    def test_cli_takes_the_same_shared_slot_check(self):
        bg.set_gpu_max_parallel(2)
        self._start("par_a")
        assert bg.try_take_gpu_slot("cli:1", "CLI translate") is True
        assert bg.try_take_gpu_slot("cli:2", "CLI translate") is False  # cap of 2 reached
        db.release_gpu_lock("cli:1")
        self.free_mb["value"] = None
        assert bg.try_take_gpu_slot("cli:1", "CLI translate") is False  # no reading: one at a time

    def test_setting_is_clamped(self):
        bg.set_gpu_max_parallel(0)
        assert bg.get_gpu_max_parallel() == 1
        bg.set_gpu_max_parallel(9)
        assert bg.get_gpu_max_parallel() == 4
        db.set_app_setting("gpu_max_parallel", "lots")
        assert bg.get_gpu_max_parallel() == 1


class TestCancelQueued:
    """Step 9f item 3: a queued job's own Cancel button used to call
    clear_job() unconditionally, but _promote_next_queued_gpu_job() can
    promote it to "running" (and spawn its thread) in the gap between the
    render that showed Cancel and the click being processed -- clearing
    the record in that case would leave the now-genuinely-running job
    with nothing left for Stop/request_cancel to reach. cancel_queued()
    re-checks status under the lock before deciding what to do."""

    def test_a_still_queued_job_is_cleared_like_before(self):
        release = threading.Event()
        bg.start_job("cq_a", lambda: release.wait(timeout=2.0), gpu_touching=True)

        calls = []
        bg.start_job("cq_b", lambda: calls.append(1), gpu_touching=True)
        assert bg.get_status("cq_b")["status"] == "queued"

        assert bg.cancel_queued("cq_b") is True
        assert bg.get_status("cq_b") is None

        release.set()
        _wait("cq_a")
        time.sleep(0.1)  # give a wrongly-surviving queue entry a chance to fire
        assert calls == []
        bg.clear_job("cq_a")

    def test_a_job_promoted_to_running_in_the_gap_is_not_cleared(self):
        # Simulates the exact race: the record already flipped to
        # "running" (as _promote_next_queued_gpu_job() would do) by the
        # time the click is processed, even though the button that
        # produced this click was rendered while it was still "queued".
        with bg._lock:
            bg._jobs["cq_c"] = {"status": "running", "progress": 0.0, "message": "",
                                "error": None, "cancel_requested": False, "result": None,
                                "gpu_touching": True, "started_at": time.time()}

        assert bg.cancel_queued("cq_c") is False
        # The record must survive -- it's the only thing a real "Stop"
        # (request_cancel) has left to reach.
        assert bg.get_status("cq_c") is not None
        assert bg.get_status("cq_c")["status"] == "running"

        bg.request_cancel("cq_c")
        assert bg.is_cancel_requested("cq_c") is True
        bg.clear_job("cq_c")

    def test_a_job_that_no_longer_exists_is_a_no_op(self):
        bg.clear_job("cq_missing")
        assert bg.cancel_queued("cq_missing") is True  # nothing to reach either way


class TestGpuSlotRemoved:
    """Step 4k: gpu_slot() had no real callers left -- diarization and dub
    generation both moved onto start_job(gpu_touching=True) (Step 5c/8/9d
    era), so the standalone blocking context manager was dead code."""

    def test_gpu_slot_no_longer_exists(self):
        assert not hasattr(bg, "gpu_slot")


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

    def test_a_result_that_did_not_run_for_a_missing_package_ends_as_an_error(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        plain = "Transcription isn't installed yet. Open Diagnostics to install it."

        def fake_worker(result_queue):
            result_queue.put(("ok", {"failed_reason": "dependency_missing", "detail": plain}))

        job_id = "test_process_missing_package"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, fake_worker, args=())
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert status["error"] == plain
        assert status["result"]["failed_reason"] == "dependency_missing"
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
        bg.start_process_job(job_id, lambda q: q.put(("ok", {"v": 1})), args=(), on_done=hook)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "done"
        assert seen == [(job_id, {"v": 1}, "running")]
        bg.clear_job(job_id)

    def test_hook_raising_marks_error_with_redacted_message(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)

        def hook(jid, result):
            raise RuntimeError("boom key=sk-abcdefghijklmnopqrstuvwxyz123456")

        job_id = "test_ondone_raises"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(), on_done=hook)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error"
        assert "boom" in status["error"]
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in status["error"]
        bg.clear_job(job_id)

    def test_hook_not_called_on_subprocess_error(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        seen = []
        job_id = "test_ondone_err"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: q.put(("error", "RuntimeError", "x")), args=(),
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
        bg.start_process_job(job_id, lambda q: q.put(("error", "RuntimeError", f"401 bad key {key}")),
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
        bg.start_process_job(job_id, lambda q: None, args=(), on_done=lambda j, r: seen.append(j))
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
        bg.start_job("test_ondone_gpu_thread",
                     lambda: (started.set(), release.wait(timeout=2.0)), gpu_touching=True)
        started.wait(timeout=2.0)
        seen = []
        job_id = "test_ondone_queued"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: q.put(("ok", {"r": 2})), args=(),
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

    def test_hook_return_value_becomes_the_result(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        job_id = "test_ondone_returns"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: q.put(("ok", {"raw": 1})), args=(),
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
        monkeypatch.setattr(bg.multiprocessing, "Queue", CancelOnResultQueue)
        seen, finished = [], []
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: q.put(("ok", {"v": 1})), args=(),
                             on_done=lambda j, r: seen.append(j), on_finish=finished.append)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "cancelled"
        assert status["message"] == bg.CANCELLED_MESSAGE
        assert seen == []
        assert _wait_for(lambda: finished == [job_id])
        bg.clear_job(job_id)

    def test_finish_hook_runs_on_done_and_on_cancel(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        finished = []
        bg.clear_job("test_finish_done")
        bg.start_process_job("test_finish_done", lambda q: q.put(("ok", {})), args=(),
                             on_finish=finished.append)
        assert _wait_for_status("test_finish_done", "running")["status"] == "done"
        assert _wait_for(lambda: finished == ["test_finish_done"])
        bg.clear_job("test_finish_done")

        _install_fake_process(monkeypatch, alive_forever=True)
        bg.clear_job("test_finish_cancel")
        bg.start_process_job("test_finish_cancel", lambda q: None, args=(),
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
        monkeypatch.setattr(bg, "kill_tree", fake_kill_tree)
        job_id = "test_kill_whole_tree_cancel"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: None, args=(), kill_whole_tree=True)
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
        monkeypatch.setattr(bg, "kill_tree", fake_kill_tree)
        real_release = bg._release_gpu_slot
        monkeypatch.setattr(bg, "_release_gpu_slot",
                            lambda *a, **k: (events.append("release"), real_release(*a, **k)))
        job_id = "test_alive_after_result"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: None, args=(), gpu_touching=True,
                             kill_whole_tree=True, on_finish=lambda j: events.append("finish"))
        assert _wait_for(lambda: bg.is_running(job_id))
        instances[0]._args[-1].put(("ok", {"v": 1}))
        assert _wait_for(lambda: "finish" in events)
        assert bg.get_status(job_id)["status"] == "done"
        assert events == ["kill", "release", "finish"]
        assert instances[0].terminated is True
        assert db.gpu_lock_holder_count() == 0
        bg.clear_job(job_id)

    def test_a_worker_that_exited_is_not_killed_again(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        killed = []
        monkeypatch.setattr(bg, "kill_tree", killed.append)
        finished = []
        job_id = "test_exited_not_killed"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(), kill_whole_tree=True,
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
        monkeypatch.setattr(bg.multiprocessing, "Process", factory)
        real_notify = bg._notify_job_finished
        reran, finished = [], []

        def notify_then_rerun(*a, **k):
            real_notify(*a, **k)
            if not reran:
                reran.append(bg.start_process_job(job_id, lambda q: None, args=(),
                                                  gpu_touching=True))
        monkeypatch.setattr(bg, "_notify_job_finished", notify_then_rerun)
        bg.clear_job(job_id)

        bg.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(), gpu_touching=True,
                             on_finish=finished.append)
        assert _wait_for(lambda: finished == [job_id])
        assert reran == [True]
        assert bg.get_status(job_id)["status"] == "running"
        assert db.gpu_lock_holder_count() == 1

        bg.request_cancel(job_id)
        assert _wait_for(lambda: bg.get_status(job_id)["status"] == "cancelled")
        assert _wait_for(lambda: db.gpu_lock_holder_count() == 0)
        bg.clear_job(job_id)

    def test_start_method_picks_the_context_also_through_the_gpu_queue(self, monkeypatch):
        contexts = []

        class FakeContext:
            def Queue(self):
                return queue.Queue()

            def Process(self, target, args, daemon=True):
                return _FakeProcess(target, args, daemon=daemon, run_target_on_start=True)
        monkeypatch.setattr(bg.multiprocessing, "get_context",
                            lambda method: contexts.append(method) or FakeContext())
        _install_fake_process(monkeypatch, run_target_on_start=True)

        bg.clear_job("test_start_method_default")
        bg.start_process_job("test_start_method_default", lambda q: q.put(("ok", {})), args=())
        assert _wait_for_status("test_start_method_default", "running")["status"] == "done"
        assert contexts == []

        bg.clear_job("test_start_method_direct")
        bg.start_process_job("test_start_method_direct", lambda q: q.put(("ok", {})), args=(),
                             start_method="spawn")
        assert _wait_for_status("test_start_method_direct", "running")["status"] == "done"
        assert contexts == ["spawn"]

        release, started = threading.Event(), threading.Event()
        bg.start_job("test_start_method_gpu_thread",
                     lambda: (started.set(), release.wait(timeout=2.0)), gpu_touching=True)
        started.wait(timeout=2.0)
        job_id = "test_start_method_queued"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(), gpu_touching=True,
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
        bg.start_job("test_finish_holder", lambda: (started.set(), release.wait(timeout=5.0)),
                     gpu_touching=True)
        started.wait(timeout=2.0)
        bg.clear_job(job_id)
        assert bg.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(),
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
        assert bg.start_process_job("test_build_holder", lambda q: None, args=(),
                                    gpu_touching=True, on_finish=finished.append)
        assert _wait_for(lambda: bg.is_running("test_build_holder"))
        job_id = "test_build_promoted"
        bg.clear_job(job_id)
        assert bg.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(),
                                    gpu_touching=True, on_finish=finished.append) is True
        assert bg.get_status(job_id)["status"] == "queued"

        def no_process(target, args, daemon=True):
            raise OSError("Too many open files")
        monkeypatch.setattr(bg.multiprocessing, "Process", no_process)
        bg.request_cancel("test_build_holder")

        assert _wait_for(lambda: sorted(finished) == sorted(["test_build_holder", job_id]))
        status = bg.get_status(job_id)
        assert status["status"] == "error" and "Too many open files" in status["error"]
        assert _wait_for(lambda: db.gpu_lock_holder_count() == 0)
        assert len(instances) == 1
        time.sleep(0.1)
        assert sorted(finished) == sorted(["test_build_holder", job_id])

        _install_fake_process(monkeypatch, run_target_on_start=True)
        assert bg.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(),
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
        bg.start_process_job(job_id, lambda q: None, args=(), on_finish=finished.append)
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
        bg.start_process_job(job_id, lambda q: None, args=(), on_finish=slow_cleanup)
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
            bg.report_stage(q, "Loading the model")
            release.wait(timeout=2.0)
            bg.report_progress(q, 0.5, "Working... 50%")
            q.put(("ok", {}))
        _install_fake_process(monkeypatch, alive_forever=True)
        job_id = "test_reported_stage"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: None, args=(),
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
        assert bg.start_process_job(job_id, _large_result_worker, args=(200_000,)) is True

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
        assert bg.start_process_job(
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
        assert bg.start_process_job(job_id, _child_dies_mid_result_worker) is True

        status = _wait_for_status(job_id, "running", timeout=15.0)
        assert status["status"] == "error", status
        assert "exited unexpectedly" in status["error"]
        assert [n for n in os.listdir(storage.temp_root()) if "result-" in n] == []
        bg.clear_job(job_id)

    def test_the_result_file_is_removed_once_the_job_is_done(self, isolated_db):
        import storage
        job_id = "test_process_result_file_removed"
        bg.clear_job(job_id)
        assert bg.start_process_job(job_id, _large_result_worker_with_path,
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
            assert bg.start_process_job(job_id, _large_result_worker_with_path,
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
        bg.start_process_job(job_id, lambda q: q.put(("ok", {"v": 1})), args=(), on_done=on_done)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "cancelled"
        assert status["message"] == bg.CANCELLED_MESSAGE
        bg.clear_job(job_id)


class TestHeartbeatRefreshesRunningGpuRows:
    def test_a_running_gpu_job_that_reports_no_progress_keeps_its_row_fresh(self, monkeypatch):
        refreshed = []
        monkeypatch.setattr(db, "heartbeat_gpu_lock", lambda holder: refreshed.append(holder))
        monkeypatch.setattr(db, "touch_job_records", lambda ids: None)
        release = threading.Event()
        assert bg.start_job("hb_gpu", lambda: release.wait(timeout=5.0), gpu_touching=True)
        assert bg.start_job("hb_cpu", lambda: release.wait(timeout=5.0))
        try:
            bg._heartbeat_once()
            assert refreshed == ["ui:hb_gpu"]
        finally:
            release.set()
            assert bg.wait_for_job_threads(5.0)
            bg.clear_job("hb_gpu")
            bg.clear_job("hb_cpu")


class TestNotifyOnCompletion:
    """Step 23c item 4: an optional desktop notification when a
    background job finishes, gated behind set_notify_on_completion()
    (synced from the Settings toggle, off by default)."""

    def setup_method(self):
        self._library_state = _isolate_library()

    def teardown_method(self):
        bg.set_notify_on_completion(False)
        _restore_library(*self._library_state)

    def test_off_by_default_no_notification_attempted(self, monkeypatch):
        import sys
        import types
        fake_plyer = types.ModuleType("plyer")
        fake_notification = types.ModuleType("plyer.notification")
        calls = []
        fake_notification.notify = lambda **kw: calls.append(kw)
        fake_plyer.notification = fake_notification
        monkeypatch.setitem(sys.modules, "plyer", fake_plyer)
        monkeypatch.setitem(sys.modules, "plyer.notification", fake_notification)

        bg.start_job("t_notify_off", lambda: None, description="A job")  # off by default
        _wait("t_notify_off")
        # Give a late notification a fair chance to appear, or this would
        # pass without ever proving one didn't come.
        assert not _wait_for(lambda: calls, timeout=0.5)
        bg.clear_job("t_notify_off")

    def test_successful_job_notifies_when_enabled(self, monkeypatch):
        calls = []
        monkeypatch.setattr(bg, "_notify_job_finished", lambda *a, **k: calls.append(a))
        bg.set_notify_on_completion(True)
        bg.start_job("t_notify_ok", lambda: None, description="A translation job")
        _wait("t_notify_ok")
        assert _wait_for(lambda: calls), "the notification never arrived"
        assert calls == [("A translation job", "done")]
        bg.clear_job("t_notify_ok")

    def test_failed_job_notifies_with_error_status(self, monkeypatch):
        calls = []
        monkeypatch.setattr(bg, "_notify_job_finished", lambda *a, **k: calls.append(a))
        bg.set_notify_on_completion(True)
        bg.start_job("t_notify_err", lambda: (_ for _ in ()).throw(ValueError("boom")),
                     description="A doomed job")
        _wait("t_notify_err")
        assert _wait_for(lambda: calls), "the notification never arrived"
        assert calls == [("A doomed job", "error")]
        bg.clear_job("t_notify_err")

    def test_wait_for_genuinely_waits_rather_than_racing_the_notification(self, monkeypatch):
        """Step 49's own exit condition: prove `_wait_for` actually blocks
        on the notification landing rather than getting lucky, by
        artificially delaying `_notify_job_finished` past the point where
        `is_running()` already went False -- the exact window that made
        `test_failed_job_notifies_with_error_status` flaky before `_wait_for`
        existed (see `_wait_for`'s own docstring above)."""
        calls = []
        delay = 0.1

        def delayed_notify(*a, **k):
            time.sleep(delay)
            calls.append(a)

        monkeypatch.setattr(bg, "_notify_job_finished", delayed_notify)
        bg.set_notify_on_completion(True)
        bg.start_job("t_notify_delayed", lambda: (_ for _ in ()).throw(ValueError("boom")),
                     description="A doomed job")
        _wait("t_notify_delayed")
        assert not calls, (
            "notification landed before is_running() even went False -- the race "
            "window this test is supposed to exercise didn't happen, so this test "
            "isn't proving what it claims to")
        start = time.time()
        assert _wait_for(lambda: calls, timeout=2.0), "the notification never arrived"
        assert time.time() - start >= delay / 2, (
            "_wait_for returned before the delayed notification could plausibly "
            "have landed -- it isn't actually waiting on the notification")
        assert calls == [("A doomed job", "error")]
        bg.clear_job("t_notify_delayed")

    def test_notify_job_finished_is_a_no_op_without_plyer_installed(self, monkeypatch):
        """Core-only install (no `pip install plyer`): must never raise,
        matching every other optional-dependency fallback in this app."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "plyer":
                raise ImportError("No module named 'plyer'")
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        bg.set_notify_on_completion(True)
        bg._notify_job_finished("A job", "done")  # must not raise

    def test_notify_job_finished_calls_plyer_with_a_useful_message(self, monkeypatch):
        import sys
        import types
        fake_plyer = types.ModuleType("plyer")
        fake_notification = types.ModuleType("plyer.notification")
        calls = []
        fake_notification.notify = lambda **kw: calls.append(kw)
        fake_plyer.notification = fake_notification
        monkeypatch.setitem(sys.modules, "plyer", fake_plyer)
        monkeypatch.setitem(sys.modules, "plyer.notification", fake_notification)

        bg.set_notify_on_completion(True)
        bg._notify_job_finished("A translation job", "done")
        assert len(calls) == 1
        assert "A translation job" in calls[0]["message"]

        calls.clear()
        bg._notify_job_finished("A translation job", "error")
        assert len(calls) == 1
        assert "A translation job" in calls[0]["message"]
        assert "Fail" in calls[0]["message"]

    def test_notify_job_finished_does_nothing_when_disabled(self, monkeypatch):
        import sys
        import types
        fake_plyer = types.ModuleType("plyer")
        fake_notification = types.ModuleType("plyer.notification")
        calls = []
        fake_notification.notify = lambda **kw: calls.append(kw)
        fake_plyer.notification = fake_notification
        monkeypatch.setitem(sys.modules, "plyer", fake_plyer)
        monkeypatch.setitem(sys.modules, "plyer.notification", fake_notification)

        bg.set_notify_on_completion(False)
        bg._notify_job_finished("A job", "done")
        assert calls == []

    def test_process_based_job_notifies_too(self, monkeypatch):
        """The process-watcher path (start_process_job) is a separate
        code path from the thread-based runner above -- covered
        separately since it sets job status in its own place."""
        _install_fake_process(monkeypatch, run_target_on_start=True)
        calls = []
        monkeypatch.setattr(bg, "_notify_job_finished", lambda *a, **k: calls.append(a))
        bg.set_notify_on_completion(True)

        def fake_worker(result_queue):
            result_queue.put(("ok", {"sum": 1}))

        job_id = "test_process_notify"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, fake_worker, args=(), description="A process job")
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "done"
        assert calls == [("A process job", "done")]
        bg.clear_job(job_id)


class TestJobRecordsMirror:
    """Migration Slice 7: a job's status transitions mirror into db.py's
    cross-process job_records table -- the actual cross-process
    visibility gap this slice exists to close. Uses isolated_db so the
    mirror writes land in a throwaway library, not the real one."""

    def test_a_thread_job_appears_in_job_records_once_started_and_finished(self, isolated_db):
        import db
        done = threading.Event()
        bg.start_job("mirror_thread_job", lambda: done.set(), description="Mirror test")
        done.wait(timeout=2.0)
        _wait("mirror_thread_job")
        deadline = time.time() + 2.0
        rec = db.get_job_record("mirror_thread_job")
        while time.time() < deadline and (not rec or rec["status"] != "done"):
            time.sleep(0.01)
            rec = db.get_job_record("mirror_thread_job")
        assert rec is not None
        assert rec["status"] == "done"
        assert rec["description"] == "Mirror test"
        bg.clear_job("mirror_thread_job")

    def test_clearing_a_job_removes_its_job_record_too(self, isolated_db):
        import db
        done = threading.Event()
        bg.start_job("mirror_clear_job", lambda: done.set())
        done.wait(timeout=2.0)
        _wait("mirror_clear_job")
        _wait_for(lambda: db.get_job_record("mirror_clear_job") is not None)
        bg.clear_job("mirror_clear_job")
        assert db.get_job_record("mirror_clear_job") is None

    def test_a_failed_db_write_never_breaks_the_job_itself(self, isolated_db, monkeypatch):
        import db
        def boom(*a, **k):
            raise sqlite3.OperationalError("simulated failure")
        monkeypatch.setattr(db, "save_job_record", boom)
        done = threading.Event()
        ok = bg.start_job("mirror_db_down_job", lambda: done.set())
        assert ok is True
        assert done.wait(timeout=2.0)
        _wait("mirror_db_down_job")
        assert bg.get_status("mirror_db_down_job")["status"] == "done"
        bg.clear_job("mirror_db_down_job")


class TestGpuLimitAndNotifySettingsPersist:
    """Migration Slice 9 (D1 fix 2): these two used to be bare module
    globals, reset to a hardcoded default every restart and invisible to
    a separate process. Now backed by db.app_settings."""

    def setup_method(self):
        self._library_state = _isolate_library()

    def teardown_method(self):
        bg.set_gpu_limit_enabled(True)
        bg.set_notify_on_completion(False)
        _restore_library(*self._library_state)

    def test_gpu_limit_round_trips_through_the_db(self, isolated_db):
        bg.set_gpu_limit_enabled(False)
        assert bg.get_gpu_limit_enabled() is False
        assert db.get_app_setting("gpu_limit_enabled") is False

    def test_notify_on_completion_round_trips_through_the_db(self, isolated_db):
        bg.set_notify_on_completion(True)
        assert bg.get_notify_on_completion() is True
        assert db.get_app_setting("notify_on_completion") is True

    def test_gpu_limit_defaults_true_when_never_set(self, isolated_db):
        assert bg.get_gpu_limit_enabled() is True

    def test_notify_defaults_false_when_never_set(self, isolated_db):
        assert bg.get_notify_on_completion() is False

    def test_gpu_limit_read_fails_open_on_a_db_error(self, isolated_db, monkeypatch):
        def boom(*a, **k):
            raise sqlite3.OperationalError("simulated failure")
        monkeypatch.setattr(db, "get_app_setting", boom)
        assert bg.get_gpu_limit_enabled() is True  # fails open -- the safer default

    def test_notify_read_fails_closed_on_a_db_error(self, isolated_db, monkeypatch):
        def boom(*a, **k):
            raise sqlite3.OperationalError("simulated failure")
        monkeypatch.setattr(db, "get_app_setting", boom)
        assert bg.get_notify_on_completion() is False  # fails closed -- no notification


class TestProcessJobProgressTuples:
    """Process workers may send ("progress", frac, message) tuples before
    their final ("ok"/"error", ...) tuple."""

    def test_progress_tuples_update_job_state_then_final_result(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        seen = []

        def worker(q):
            bg.report_progress(q, 0.25, "Loading model")
            bg.report_progress(q, 0.5, "Transcribing key sk-abcdefghijklmnopqrstuvwx")
            q.put(("ok", {"v": 1}))

        real_update = bg.update_progress
        monkeypatch.setattr(bg, "update_progress",
                            lambda j, f, m="": (seen.append((f, m)), real_update(j, f, m)))
        job_id = "test_progress_tuples"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, worker, args=(), on_done=lambda j, r: seen.append("hook"))
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
            bg.report_progress(q, 0.4, "Aligning...")
            q.put(("ok", {}))

        job_id = "test_progress_message_kept"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, worker, args=())
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "done"
        assert status["message"] == "Aligning..."
        bg.clear_job(job_id)

    def test_error_after_progress_still_reported(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)

        def worker(q):
            bg.report_progress(q, 0.1, "Loading")
            q.put(("error", "RuntimeError", "boom"))

        job_id = "test_progress_then_error"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, worker, args=(), on_done=lambda j, r: pytest.fail("no hook"))
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "error" and "boom" in status["error"]
        bg.clear_job(job_id)

    def test_cancel_still_terminates_a_running_process_job(self, monkeypatch):
        procs = _install_fake_process(monkeypatch, alive_forever=True)
        job_id = "test_progress_cancel"
        bg.clear_job(job_id)
        bg.start_process_job(job_id, lambda q: None, args=())
        bg.request_cancel(job_id)
        status = _wait_for_status(job_id, "running")
        assert status["status"] == "cancelled"
        assert procs[0].terminated is True
        bg.clear_job(job_id)

    def test_malformed_progress_tuple_is_ignored(self):
        assert bg._apply_progress_item("nope", ("progress", "x")) is True
        assert bg._apply_progress_item("nope", ("ok", {})) is False


class _WorkerExited(BaseException):
    pass


class TestOrphanedWorkerExits:
    """A worker that left its parent's process group (start_own_process_group)
    ends itself once its parent dies, instead of running on unseen."""

    def _orphan(self, monkeypatch):
        killed = []
        monkeypatch.setattr(bg, "_worker_parent_pid", 4242)
        monkeypatch.setattr(bg.os, "getppid", lambda: 1)
        monkeypatch.setattr(bg.os, "getpgid", lambda pid: os.getpid())
        monkeypatch.setattr(bg.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

        def fake_exit(code):
            raise _WorkerExited(code)
        monkeypatch.setattr(bg.os, "_exit", fake_exit)
        return killed

    @pytest.mark.parametrize("report", [lambda q: bg.report_progress(q, 0.5, "x"),
                                        lambda q: bg.report_stage(q, "Loading")])
    def test_a_report_after_the_parent_died_kills_the_group_and_exits(self, monkeypatch, report):
        import signal
        killed = self._orphan(monkeypatch)
        q = queue.Queue()
        with pytest.raises(_WorkerExited):
            report(q)
        assert killed == [(0, signal.SIGKILL)]
        assert q.empty()

    def test_a_live_parent_changes_nothing(self, monkeypatch):
        monkeypatch.setattr(bg, "_worker_parent_pid", os.getppid())
        q = queue.Queue()
        bg.report_progress(q, 0.5, "x")
        assert q.get_nowait() == ("progress", 0.5, "x")

    def test_no_check_outside_a_worker(self, monkeypatch):
        monkeypatch.setattr(bg, "_worker_parent_pid", None)
        monkeypatch.setattr(bg.os, "getppid", lambda: 1)
        bg.exit_if_parent_gone()

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
        monkeypatch.setattr(bg, "_worker_parent", None)
        monkeypatch.setattr(bg, "_worker_parent_pid", None)
        monkeypatch.setattr(bg.multiprocessing, "parent_process", lambda: self._FakeParent(4242))
        monkeypatch.setattr(bg, "_parent_watchdog", lambda: None)
        monkeypatch.setattr(bg.os, "setsid", lambda: None)
        monkeypatch.setattr(bg.os, "getppid", lambda: 1)
        bg.start_own_process_group()
        assert bg._worker_parent_pid == 4242
        killed = self._orphan(monkeypatch)
        monkeypatch.setattr(bg, "_worker_parent_pid", 4242)
        with pytest.raises(_WorkerExited):
            bg.exit_if_parent_gone()
        assert len(killed) == 1

    def test_without_parent_process_data_it_falls_back_to_getppid(self, monkeypatch):
        monkeypatch.setattr(bg, "_worker_parent", None)
        monkeypatch.setattr(bg, "_worker_parent_pid", None)
        monkeypatch.setattr(bg.multiprocessing, "parent_process", lambda: None)
        monkeypatch.setattr(bg, "_parent_watchdog", lambda: None)
        monkeypatch.setattr(bg.os, "setsid", lambda: None)
        monkeypatch.setattr(bg.os, "getppid", lambda: 777)
        bg.start_own_process_group()
        assert bg._worker_parent is None and bg._worker_parent_pid == 777

    def test_a_dead_parent_sentinel_exits_even_with_the_same_ppid(self, monkeypatch):
        killed = self._orphan(monkeypatch)
        monkeypatch.setattr(bg.os, "getppid", lambda: 4242)
        monkeypatch.setattr(bg, "_worker_parent", self._FakeParent(4242, alive=False))
        with pytest.raises(_WorkerExited):
            bg.report_progress(queue.Queue(), 0.5, "x")
        assert len(killed) == 1

    def test_on_windows_only_the_sentinel_is_checked_and_nothing_raises(self, monkeypatch):
        def no_getppid():
            raise OSError("getppid failed")
        monkeypatch.setattr(bg.os, "name", "nt")
        monkeypatch.setattr(bg.os, "getppid", no_getppid)
        monkeypatch.setattr(bg, "_worker_parent_pid", 4242)
        monkeypatch.setattr(bg, "_worker_parent", self._FakeParent(4242))
        q = queue.Queue()
        bg.report_progress(q, 0.5, "x")
        bg.report_stage(q, "Loading")
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
            "import background_jobs as bg\n"
            "def worker():\n"
            "    bg.start_own_process_group()\n"
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
                             timeout=4 * bg.PARENT_CHECK_INTERVAL + 2)
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
    bg.start_own_process_group()
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
            assert bg.start_process_job(job_id, _worker_leaving_a_child, args=(pid_file,),
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


class TestRunCancellable:
    """B-05: a thread job running an external command can be stopped."""

    def test_cancel_kills_the_command_and_marks_cancelled(self, isolated_db):
        started = bg.start_job("rc1", lambda: bg.run_cancellable(
            "rc1", [sys.executable, "-c", "import time; time.sleep(30)"], poll_interval=0.05))
        assert started
        time.sleep(0.3)
        t0 = time.time()
        bg.request_cancel("rc1")
        assert _wait_for(lambda: bg.get_status("rc1")["status"] == "cancelled", timeout=5)
        assert time.time() - t0 < 5
        assert bg.get_status("rc1")["status"] != "done"

    def test_nonzero_exit_raises_called_process_error(self, isolated_db):
        import subprocess
        with pytest.raises(subprocess.CalledProcessError):
            bg.run_cancellable("rc2", [sys.executable, "-c", "raise SystemExit(3)"])


    @pytest.mark.skipif(os.name == "nt", reason="POSIX process-group kill")
    def test_cancel_kills_grandchild_holding_the_pipes(self, isolated_db):
        """A wrapper (shim) whose child inherits stdout/stderr: killing only
        the wrapper would leave communicate() waiting on the grandchild."""
        wrapper = ("import subprocess, sys, time; "
                   "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                   "time.sleep(60)")
        assert bg.start_job("rc3", lambda: bg.run_cancellable(
            "rc3", [sys.executable, "-c", wrapper], poll_interval=0.05, kill_timeout=5))
        time.sleep(0.5)
        t0 = time.time()
        bg.request_cancel("rc3")
        assert _wait_for(lambda: bg.get_status("rc3")["status"] == "cancelled", timeout=8)
        assert time.time() - t0 < 4


class TestStartFailure:
    """A job is marked "running" before its thread/process starts; if the
    start raises, the record must not stay "running" forever (blocking a
    restart, acquire_exclusive and the GPU lock)."""

    def _fail_thread_starts(self, monkeypatch, name=None):
        real = bg._start_job_thread

        def flaky(target, thread_name, *args, **kwargs):
            if name is None or thread_name == name:
                raise RuntimeError("can't start new thread key=AIzaSyFAKESECRETVALUE12345")
            return real(target, thread_name, *args, **kwargs)

        monkeypatch.setattr(bg, "_start_job_thread", flaky)
        return real

    def test_thread_start_failure_marks_error_and_frees_everything(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
        real = self._fail_thread_starts(monkeypatch)
        with pytest.raises(RuntimeError):
            bg.start_job("sf_thread", lambda: None, gpu_touching=True)
        status = bg.get_status("sf_thread")
        assert status["status"] == "error"
        assert "AIzaSyFAKESECRETVALUE12345" not in status["error"]
        assert db.try_acquire_gpu_lock("someone_else")
        db.release_gpu_lock("someone_else")
        assert bg.acquire_exclusive("test")
        bg.release_exclusive()
        monkeypatch.setattr(bg, "_start_job_thread", real)
        assert bg.start_job("sf_thread", lambda: None) is True
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

        monkeypatch.setattr(bg.multiprocessing, "Process", make_proc)
        monkeypatch.setattr(bg.multiprocessing, "Queue", make_queue)
        with pytest.raises(TypeError):
            bg.start_process_job("sf_proc", lambda q: None, gpu_touching=True)
        assert bg.get_status("sf_proc")["status"] == "error"
        assert procs[0].closed and queues[0].closed   # no leaked pipe fds
        assert db.try_acquire_gpu_lock("someone_else")
        db.release_gpu_lock("someone_else")
        assert bg.acquire_exclusive("test")
        bg.release_exclusive()

    def test_promoted_job_failing_to_start_errors_and_the_next_one_runs(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
        bg.set_gpu_limit_enabled(True)
        release = threading.Event()
        assert bg.start_job("sf_a", lambda: release.wait(5), gpu_touching=True)
        assert bg.start_job("sf_b", lambda: None, gpu_touching=True)
        assert bg.start_job("sf_c", lambda: None, gpu_touching=True)
        assert bg.get_status("sf_b")["status"] == "queued"
        self._fail_thread_starts(monkeypatch, name="job:sf_b")
        release.set()
        assert _wait_for(lambda: bg.get_status("sf_c")["status"] == "done", timeout=5)
        assert bg.get_status("sf_b")["status"] == "error"


class TestSystemExitInJob:
    def test_system_exit_marks_the_job_errored(self):
        def leave():
            raise SystemExit(2)

        bg.start_job("sysexit", leave)
        assert _wait_for(lambda: bg.get_status("sysexit")["status"] != "running")
        assert bg.get_status("sysexit")["status"] == "error"
        assert "SystemExit" in bg.get_status("sysexit")["error"]


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
    with bg._lock:
        bg._jobs[job_id] = {
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
        bg._process_watcher("w_torn", proc, q, poll_interval=0.01)
        status = bg.get_status("w_torn")
        assert status["status"] == "error"
        assert "UnpicklingError" in status["error"]
        assert "AIzaSyFAKESECRETVALUE12345" not in status["error"]
        assert proc.killed
        assert q.closed

    def test_cancel_kills_a_child_that_ignores_terminate(self):
        proc = _register_fake_process_job("w_stubborn")
        bg.request_cancel("w_stubborn")
        bg._process_watcher("w_stubborn", proc, _SpyQueue(), poll_interval=0.01)
        assert bg.get_status("w_stubborn")["status"] == "cancelled"
        assert proc.terminated and proc.killed
        assert not proc.is_alive()

    def test_normal_exit_reaps_the_child_and_closes_the_queue(self):
        proc = _register_fake_process_job("w_normal")
        q = _SpyQueue(items=[("ok", {"n": 1})])
        bg._process_watcher("w_normal", proc, q, poll_interval=0.01)
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
        bg._process_watcher("w_order", proc, _SpyQueue(items=[("ok", {"n": 1})]),
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
        bg._process_watcher("w_linger", proc, _SpyQueue(items=[("ok", {"n": 1})]),
                            poll_interval=0.01)
        assert proc.terminated and proc.killed
        assert not proc.is_alive()
        assert set(seen) == {"running"}
        assert bg.get_status("w_linger")["status"] == "done"


_FAKE_KEY = "AIzaSyFAKESECRETVALUE12345"


def _log_text():
    import applog
    return "\n".join(applog.tail(200))


def _boom(*a, **k):
    raise sqlite3.OperationalError(f"disk I/O error key={_FAKE_KEY}")


class TestSwallowedFailuresAreVisible:
    def test_gpu_lock_db_error_queues_the_job_and_logs(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
        bg.set_gpu_limit_enabled(True)
        real = db.try_acquire_gpu_lock
        monkeypatch.setattr(db, "try_acquire_gpu_lock", _boom)
        ran = threading.Event()
        assert bg.start_job("gl_err", ran.set, gpu_touching=True) is True
        assert bg.get_status("gl_err")["status"] == "queued"
        assert not ran.is_set()
        log = _log_text()
        assert "could not take the GPU lock" in log and _FAKE_KEY not in log
        monkeypatch.setattr(db, "try_acquire_gpu_lock", real)
        bg.recheck_gpu_queue()
        assert ran.wait(5)

    def test_external_gpu_check_error_is_logged_and_ignored(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", _boom)
        assert bg.start_job("ext_err", lambda: None, gpu_touching=True) is True
        _wait("ext_err")
        assert bg.get_status("ext_err")["status"] == "done"
        log = _log_text()
        assert "external GPU load check failed" in log and _FAKE_KEY not in log

    def test_gpu_limit_setting_error_is_logged(self, monkeypatch):
        monkeypatch.setattr(db, "get_app_setting", _boom)
        assert bg.get_gpu_limit_enabled() is True
        assert "could not read the GPU-limit setting" in _log_text()

    def test_db_cancel_check_error_is_logged_once_and_retried(self, monkeypatch):
        calls = []

        def failing(job_id):
            calls.append(job_id)
            raise sqlite3.OperationalError(f"database is locked key={_FAKE_KEY}")

        from jobs import job_store
        monkeypatch.setattr(job_store, "row_cancel_time", failing)
        # Set the log handler up before the job thread logs: two threads
        # doing it at once can attach it twice and write every line twice.
        import applog
        applog.get_logger()
        release = threading.Event()
        bg.start_job("dc_err", lambda: release.wait(5))
        try:
            assert bg.is_cancel_requested("dc_err") is False
            assert bg.is_cancel_requested("dc_err") is False
            assert len(calls) == 2    # not held back by the check interval
            log = _log_text()
            assert log.count("could not read a cancel request") == 1
            assert _FAKE_KEY not in log
        finally:
            release.set()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX process-group kill")
    def test_kill_tree_failure_is_logged(self, monkeypatch):
        def denied(pid, sig):
            raise PermissionError("not permitted")

        class _Proc:
            pid = 999999

            def kill(self):
                raise OSError("kill failed")

        monkeypatch.setattr(os, "killpg", denied)
        bg.kill_tree(_Proc())
        log = _log_text()
        assert "could not kill process tree 999999" in log
        assert "could not kill process 999999" in log


class TestCancelIsVisibleAndQueuedJobsStopAtOnce:
    """Owner report: Cancel "did nothing" in the Jobs panel. A queued job
    stayed queued (and would still run once promoted), and a running one
    kept showing its old progress text until it reached a checkpoint."""

    def setup_method(self):
        self._library_state = _isolate_library()
        bg.set_gpu_limit_enabled(True)

    def teardown_method(self):
        bg.set_gpu_limit_enabled(True)
        _restore_library(*self._library_state)

    def test_cancelling_a_queued_job_ends_it_at_once_and_it_never_runs(self):
        release = threading.Event()
        started = threading.Event()

        def slow():
            started.set()
            release.wait(timeout=5.0)

        ran = []
        try:
            assert bg.start_job("cq_a", slow, gpu_touching=True)
            started.wait(timeout=2.0)
            assert bg.start_job("cq_b", lambda: ran.append(1), gpu_touching=True)
            assert bg.get_status("cq_b")["status"] == "queued"
            bg.request_cancel("cq_b")
            assert bg.get_status("cq_b")["status"] == "cancelled"
            assert db.get_job_record("cq_b")["status"] == "cancelled"
        finally:
            release.set()
            _wait("cq_a")
        assert bg.wait_for_job_threads(5.0)
        assert ran == []
        assert bg.get_status("cq_b")["status"] == "cancelled"
        bg.clear_job("cq_a")
        bg.clear_job("cq_b")

    def test_a_running_job_says_cancelling_until_it_stops(self):
        release = threading.Event()
        started = threading.Event()

        def work():
            started.set()
            release.wait(timeout=5.0)
            # A late progress write from a worker that hasn't noticed yet
            # must not hide that a cancel is on its way.
            bg.update_progress("cr_a", 0.5, "Transcribing... 50%")
            if bg.is_cancel_requested("cr_a"):
                raise bg.JobCancelled("cr_a")

        assert bg.start_job("cr_a", work)
        started.wait(timeout=2.0)
        bg.request_cancel("cr_a")
        assert bg.get_status("cr_a")["message"] == bg.CANCELLING_MESSAGE
        assert db.get_job_record("cr_a")["message"] == bg.CANCELLING_MESSAGE
        release.set()
        assert _wait_for(lambda: bg.get_status("cr_a")["status"] == "cancelled")
        assert bg.get_status("cr_a")["message"] == bg.CANCELLED_MESSAGE
        assert bg.wait_for_job_threads(5.0)
        bg.clear_job("cr_a")


class TestDeadWorkerIsReconciled:
    """A running job whose worker thread is gone must not stay "running"
    forever (owner report: a transcription still Running long after it had
    stopped)."""

    def test_a_running_job_whose_thread_died_is_marked_interrupted(self, monkeypatch):
        release = threading.Event()
        assert bg.start_job("dw_a", lambda: release.wait(5.0))
        try:
            dead = threading.Thread(target=lambda: None)
            dead.start()
            dead.join()
            with bg._lock:
                # as if the runner thread had vanished without a word
                bg._workers["dw_a"] = (bg._jobs["dw_a"], dead)
            assert bg.reconcile_dead_workers() == ["dw_a"]
            status = bg.get_status("dw_a")
            assert status["status"] == "error"
            assert status["error"] == bg.WORKER_LOST_MESSAGE
            assert db.get_job_record("dw_a")["status"] == "error"
        finally:
            release.set()
            assert bg.wait_for_job_threads(5.0)
        # The real runner finishing later can't turn it back into "done".
        assert bg.get_status("dw_a")["status"] == "error"
        bg.clear_job("dw_a")

    def test_a_live_worker_is_left_alone(self):
        release = threading.Event()
        assert bg.start_job("dw_b", lambda: release.wait(5.0))
        try:
            assert bg.reconcile_dead_workers() == []
            assert bg.get_status("dw_b")["status"] == "running"
        finally:
            release.set()
            assert bg.wait_for_job_threads(5.0)
        assert bg.get_status("dw_b")["status"] == "done"
        bg.clear_job("dw_b")


def test_a_thread_job_that_did_not_run_for_a_missing_package_ends_as_an_error():
    job_id = "test_thread_missing_package"
    bg.clear_job(job_id)

    def work():
        bg.update_progress(job_id, 0.0, "Loading Whisper model large-v3-turbo... 0%")
        bg.set_result(job_id, {"failed_reason": "dependency_missing",
                               "detail": "Transcription isn't installed yet."})

    assert bg.start_job(job_id, work) is True
    _wait(job_id)
    status = _wait_for_status(job_id, "running")
    assert status["status"] == "error"
    assert status["error"] == "Transcription isn't installed yet."
    assert not status["message"]
    bg.clear_job(job_id)
