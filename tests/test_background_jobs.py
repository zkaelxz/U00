"""
tests/test_background_jobs.py -- the background job runner that lets long
operations (translation, dubbing) survive Streamlit's script lifecycle.

The property that matters isn't "does the function run" -- it's "does it
keep running with nobody polling it," since that's exactly the scenario
of switching to another tab and coming back later.
"""
import sys
import os
import sqlite3
import tempfile
import threading
import time

import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs as bg
from jobs import job_launch, job_runner, job_settings, job_table
import db
import diagnostics_torch
from jobs import gpu_slots
from tests.conftest import rmtree_retrying


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
    rmtree_retrying(temp_dir)


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
        job_launch.start_job("t1", lambda: time.sleep(0.05))
        assert bg.is_running("t1")
        _wait("t1")
        bg.clear_job("t1")

    def test_completed_job_reports_done_with_full_progress(self):
        job_launch.start_job("t2", lambda: None)
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

        assert job_launch.start_job("t3", slow) is True
        assert job_launch.start_job("t3", slow) is False  # must not launch a second one
        _wait("t3")
        assert len(calls) == 1
        bg.clear_job("t3")

    def test_can_restart_after_previous_run_finished(self):
        job_launch.start_job("t4", lambda: None)
        _wait("t4")
        assert job_launch.start_job("t4", lambda: None) is True  # fine once t4 isn't running
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

        job_launch.start_job("t5", work)
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

        job_launch.start_job("t6", work)
        assert returned.wait(10)
        assert _wait_for(lambda: bg.get_status("t6")["status"] == "done", timeout=10)
        assert bg.get_status("t6")["status"] == "done"
        bg.clear_job("t6")


class TestErrorHandling:
    def test_exception_is_captured_not_raised_to_the_caller(self):
        job_launch.start_job("t7", lambda: (_ for _ in ()).throw(ValueError("boom")))
        _wait("t7")
        status = bg.get_status("t7")
        assert status["status"] == "error"
        assert "ValueError" in status["error"]
        assert "boom" in status["error"]
        bg.clear_job("t7")

    def test_traceback_is_available_for_debugging(self):
        job_launch.start_job("t8", lambda: 1 / 0)
        _wait("t8")
        status = bg.get_status("t8")
        assert "ZeroDivisionError" in status["traceback"]
        bg.clear_job("t8")


class TestResultPassing:
    def test_set_result_is_readable_after_completion(self):
        def work():
            bg.set_result("t9", {"errors": [], "count": 42})

        job_launch.start_job("t9", work)
        _wait("t9")
        assert bg.get_status("t9")["result"] == {"errors": [], "count": 42}
        bg.clear_job("t9")

    def test_result_defaults_to_none(self):
        job_launch.start_job("t10", lambda: None)
        _wait("t10")
        assert bg.get_status("t10")["result"] is None
        bg.clear_job("t10")


class TestCancellationFlag:
    def test_flag_is_off_by_default(self):
        job_launch.start_job("t11", lambda: time.sleep(0.02))
        assert bg.is_cancel_requested("t11") is False
        _wait("t11")
        bg.clear_job("t11")

    def test_request_cancel_sets_the_flag(self):
        job_launch.start_job("t12", lambda: time.sleep(0.05))
        bg.request_cancel("t12")
        assert bg.is_cancel_requested("t12") is True
        _wait("t12")
        bg.clear_job("t12")


class TestCleanup:
    def test_clear_job_removes_the_record(self):
        job_launch.start_job("t13", lambda: None)
        _wait("t13")
        bg.clear_job("t13")
        assert bg.get_status("t13") is None

    def test_clear_all_jobs_wipes_everything(self):
        job_launch.start_job("t14", lambda: time.sleep(0.02))
        job_launch.start_job("t15", lambda: None)
        time.sleep(0.01)
        bg.clear_all_jobs()
        assert bg.get_status("t14") is None
        assert bg.get_status("t15") is None

    def test_list_running_jobs_only_includes_active_ones(self):
        # t16 holds until released: a fixed sleep(0.05) raced the start of
        # t17 plus _wait's polling, and lost on a loaded machine.
        release = threading.Event()
        job_launch.start_job("t16", lambda: release.wait(10))
        try:
            job_launch.start_job("t17", lambda: None)
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
        from engine_backends import translate_pipeline
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

        translate_pipeline.translate_lines_with_engine(
            lines, engine, {}, batch_size=2, cancel_check_cb=cancel_check)

        translated = sum(1 for l in lines if l.en)
        assert translated < len(lines), "must have stopped early, not run to completion"
        assert translated == 2, f"exactly the first batch should have completed, got {translated}"

    def test_no_cancel_callback_runs_to_completion_as_before(self):
        from engine_backends import translate_pipeline
        from core import Line
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]

        class Engine:
            supports_reference = False
            def translate_batch(self, zh_lines, context):
                return [f"EN:{z}" for z in zh_lines]

        translate_pipeline.translate_lines_with_engine(lines, Engine(), {}, batch_size=2)
        assert all(l.en for l in lines)  # unaffected when nobody asks to cancel

    def test_full_reset_race_does_not_corrupt_a_reused_id(self, isolated_db):
        """Regression test for a real, serious bug: an in-flight background
        job's stale writes landing on a brand-new drama that reused the id
        of the one the job's original drama was deleted from. Reproduces
        the exact scenario, with the fix in place: cancel + wait before
        the destructive reset proceeds."""
        import time
        from engine_backends import translate_pipeline
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
            translate_pipeline.translate_lines_with_engine(
                lines, engine, {}, batch_size=2,
                save_cb=lambda ls: isolated_db.save_lines(drama_id, ls),
                cancel_check_cb=lambda: bg.is_cancel_requested(job_id))

        import background_jobs as bg
        from jobs import job_launch, job_runner
        job_id = f"translate_{did_old}"
        job_launch.start_job(job_id, run_job, job_id, did_old, lines_old, SlowEngine())
        time.sleep(0.03)  # mid-flight

        # The fixed sequence: cancel + wait, THEN reset. Waiting means the
        # job's thread has exited, not just that it stopped reading as
        # running: its tail still writes to the database after that.
        running = bg.list_running_jobs()
        for jid in running:
            bg.request_cancel(jid)
        assert job_runner.wait_for_job_threads(5)
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

        monkeypatch.setattr(job_runner, "_notify_job_finished", blocked_notify)
        job_launch.start_job("t_tail", lambda: None)
        try:
            assert in_tail.wait(10)
            assert bg.get_status("t_tail")["status"] == "done"
            assert bg.is_running("t_tail") is False
            assert job_runner.wait_for_job_threads(0.05) is False
        finally:
            release.set()
        assert job_runner.wait_for_job_threads(10) is True


class TestCancelLineJobs:
    """Step 2: translate/flag/fix-flagged jobs now write only their own
    fields by permanent line id, so the old "one at a time" guard is gone.
    The one thing that still has to stop them is the drama's lines being
    replaced wholesale (a new transcription) -- cancel_line_jobs asks each
    running one to stop."""

    def test_cancels_every_running_line_job_for_that_drama_only(self):
        release = threading.Event()
        for jid in ("translate_601", "flag_601", "fixflag_601", "translate_602"):
            job_launch.start_job(jid, release.wait)
        bg.cancel_line_jobs(601)
        assert bg.is_cancel_requested("translate_601")
        assert bg.is_cancel_requested("flag_601")
        assert bg.is_cancel_requested("fixflag_601")
        assert not bg.is_cancel_requested("translate_602")
        release.set()
        for jid in ("translate_601", "flag_601", "fixflag_601", "translate_602"):
            _wait(jid)
            bg.clear_job(jid)

    def test_notes_and_emotion_jobs_are_cancelled_too(self):
        release = threading.Event()
        for jid in ("notes_611", "emotion_611"):
            job_launch.start_job(jid, release.wait)
        bg.cancel_line_jobs(611)
        assert bg.is_cancel_requested("notes_611") and bg.is_cancel_requested("emotion_611")
        release.set()
        for jid in ("notes_611", "emotion_611"):
            _wait(jid)
            bg.clear_job(jid)

    def test_a_dub_job_is_cancelled_too(self):
        release = threading.Event()
        job_launch.start_job("dub_621", release.wait)
        bg.cancel_line_jobs(621)
        assert bg.is_cancel_requested("dub_621")
        release.set()
        _wait("dub_621")
        bg.clear_job("dub_621")

    def test_a_queued_job_is_cancelled_before_it_starts(self):
        with bg._lock:
            bg._jobs["translate_612"] = {"status": "queued", "progress": 0, "message": ""}
        try:
            bg.cancel_line_jobs(612)
            assert bg.get_status("translate_612")["status"] == "cancelled"
        finally:
            bg.clear_job("translate_612")

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
        job_launch.start_job("translate_701", release.wait)
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
        job_launch.start_job("transcribe_702", release.wait)
        try:
            assert bg.any_job_running_for_drama(702) is True
        finally:
            release.set()
            _wait("transcribe_702")
            bg.clear_job("transcribe_702")

    def test_true_while_queued_not_just_running(self, isolated_db):
        job_settings.set_gpu_limit_enabled(True)
        release = threading.Event()
        job_launch.start_job("gpu_busy_703", release.wait, gpu_touching=True)
        try:
            job_launch.start_job("transcribe_703", release.wait, gpu_touching=True)  # queues behind it
            assert bg.get_status("transcribe_703")["status"] == "queued"
            assert bg.any_job_running_for_drama(703) is True
        finally:
            release.set()
            _wait("gpu_busy_703")
            bg.clear_job("gpu_busy_703")
            bg.clear_job("transcribe_703")

    def test_false_for_a_different_drama(self):
        release = threading.Event()
        job_launch.start_job("translate_704", release.wait)
        try:
            assert bg.any_job_running_for_drama(705) is False
        finally:
            release.set()
            _wait("translate_704")
            bg.clear_job("translate_704")

    def test_false_once_the_job_is_done(self):
        job_launch.start_job("translate_706", lambda: None)
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

        job_launch.start_job("t_log_1", lambda: 1 / 0)
        _wait("t_log_1")
        bg.clear_job("t_log_1")

        lines = applog.tail(50)
        joined = "\n".join(lines)
        assert "t_log_1" in joined
        assert "ZeroDivisionError" in joined

    def test_a_full_disk_is_stored_as_a_fixed_sentence(self, isolated_db):
        import errno
        import applog

        def boom():
            raise OSError(errno.ENOSPC, "No space left on device", "/home/x/library/dramas/3/a.wav")

        job_launch.start_job("t_disk_full", boom)
        _wait("t_disk_full")
        status = bg.get_status("t_disk_full")
        bg.clear_job("t_disk_full")
        assert status["status"] == "error" and status["error"] == "The disk is full."
        assert "a.wav" in "\n".join(applog.tail(50))

    def test_logged_error_has_no_api_key_in_it(self, isolated_db):
        import applog

        def boom():
            raise RuntimeError("400 Client Error: Bad Request for url: "
                                "https://generativelanguage.googleapis.com/v1beta/"
                                "models/x:generateContent?key=AIzaSyFAKESECRETVALUE12345")

        job_launch.start_job("t_log_2", boom)
        _wait("t_log_2")
        status = bg.get_status("t_log_2")
        bg.clear_job("t_log_2")

        assert "AIzaSyFAKESECRETVALUE12345" not in status["error"]
        assert "AIzaSyFAKESECRETVALUE12345" not in status["traceback"]
        joined = "\n".join(applog.tail(50))
        assert "AIzaSyFAKESECRETVALUE12345" not in joined


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
        job_table._jobs["tick_a"] = {"status": "running", "progress": 0.0, "message": "",
                              "error": None, "cancel_requested": False, "result": None}
        with bg.stage_ticker("tick_a", "Loading model...", interval=0.05):
            first = job_table._jobs["tick_a"]["progress_at"]
            time.sleep(0.2)
            msg = job_table._jobs["tick_a"]["message"]
            assert msg.startswith("Loading model (elapsed ")
            assert "no progress is available" in msg
            assert job_table._jobs["tick_a"]["progress_at"] == first
            assert job_table._jobs["tick_a"]["can_report_progress"] is False
        bg.clear_job("tick_a")


class TestCancelQueued:
    """Step 9f item 3: a queued job's own Cancel button used to call
    clear_job() unconditionally, but _promote_next_queued_gpu_job() can
    promote it to "running" (and spawn its thread) in the gap between the
    render that showed Cancel and the click being processed -- clearing
    the record in that case would leave the now-genuinely-running job
    with nothing left for Stop/request_cancel to reach. cancel_queued()
    re-checks status under the lock before deciding what to do."""

    def test_a_still_queued_job_ends_cancelled_and_never_runs(self):
        release = threading.Event()
        job_launch.start_job("cq_a", lambda: release.wait(timeout=2.0), gpu_touching=True)

        calls = []
        job_launch.start_job("cq_b", lambda: calls.append(1), gpu_touching=True)
        assert bg.get_status("cq_b")["status"] == "queued"

        assert bg.cancel_queued("cq_b") is True
        assert bg.get_status("cq_b")["status"] == "cancelled"

        release.set()
        _wait("cq_a")
        time.sleep(0.1)  # give a wrongly-surviving queue entry a chance to fire
        assert calls == []
        bg.clear_job("cq_a")
        bg.clear_job("cq_b")

    def test_a_priority_job_queues_ahead_of_jobs_already_waiting(self):
        release, started = threading.Event(), []
        job_launch.start_job("pq_a", lambda: release.wait(timeout=5.0), gpu_touching=True)
        job_launch.start_job("pq_b", lambda: started.append("b"), gpu_touching=True)
        job_launch.start_job("pq_c", lambda: started.append("c"), gpu_touching=True, priority=True)
        assert bg.get_status("pq_b")["status"] == bg.get_status("pq_c")["status"] == "queued"
        release.set()
        deadline = time.time() + 5
        while len(started) < 2 and time.time() < deadline:
            time.sleep(0.02)
        assert started == ["c", "b"]
        for job in ("pq_a", "pq_b", "pq_c"):
            bg.clear_job(job)

    def test_priority_jobs_keep_their_own_order(self):
        release, started = threading.Event(), []
        job_launch.start_job("po_a", lambda: release.wait(timeout=5.0), gpu_touching=True)
        job_launch.start_job("po_b", lambda: started.append("b"), gpu_touching=True)
        job_launch.start_job("po_c", lambda: started.append("c"), gpu_touching=True, priority=True)
        job_launch.start_job("po_d", lambda: started.append("d"), gpu_touching=True, priority=True)
        release.set()
        deadline = time.time() + 5
        while len(started) < 3 and time.time() < deadline:
            time.sleep(0.02)
        assert started == ["c", "d", "b"]
        for job in ("po_a", "po_b", "po_c", "po_d"):
            bg.clear_job(job)

    def test_a_job_promoted_to_running_in_the_gap_is_not_cleared(self):
        # Simulates the exact race: the record already flipped to
        # "running" (as _promote_next_queued_gpu_job() would do) by the
        # time the click is processed, even though the button that
        # produced this click was rendered while it was still "queued".
        with job_table._lock:
            job_table._jobs["cq_c"] = {"status": "running", "progress": 0.0, "message": "",
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

    def test_the_row_delete_does_not_hold_the_jobs_lock(self, monkeypatch):
        """A delete waiting on a busy database must not stall every other
        job's updates."""
        free = []

        def try_lock():
            free.append(job_table._lock.acquire(timeout=0.5))
            if free[-1]:
                job_table._lock.release()

        def delete(job_id):
            t = threading.Thread(target=try_lock)
            t.start()
            t.join()
        monkeypatch.setattr(db, "delete_job_record", delete)
        assert bg.cancel_queued("cq_unlocked") is True
        assert free == [True]


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
    """Patches job_runner.multiprocessing.Process with a factory building
    _FakeProcess(**kwargs) instances, and returns the list of instances
    it creates (in order) so a test can inspect e.g. .terminated after
    the fact."""
    instances = []

    def factory(target, args, daemon=True):
        proc = _FakeProcess(target, args, daemon=daemon, **kwargs)
        instances.append(proc)
        return proc

    monkeypatch.setattr(job_runner.multiprocessing, "Process", factory)
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


class TestHeartbeatRefreshesRunningGpuRows:
    def test_a_running_gpu_job_that_reports_no_progress_keeps_its_row_fresh(self, monkeypatch):
        refreshed = []
        monkeypatch.setattr(gpu_slots, "heartbeat", lambda holders: refreshed.extend(holders))
        monkeypatch.setattr(db, "touch_job_records", lambda ids: None)
        release = threading.Event()
        assert job_launch.start_job("hb_gpu", lambda: release.wait(timeout=5.0), gpu_touching=True)
        assert job_launch.start_job("hb_cpu", lambda: release.wait(timeout=5.0))
        try:
            job_table._heartbeat_once()
            assert refreshed == ["ui:hb_gpu"]
        finally:
            release.set()
            assert job_runner.wait_for_job_threads(5.0)
            bg.clear_job("hb_gpu")
            bg.clear_job("hb_cpu")


class TestCancelDuringTheFinalStep:
    def test_a_job_that_finishes_despite_a_cancel_stops_saying_cancelling(self):
        release = threading.Event()
        started = threading.Event()

        def work():
            started.set()
            release.wait(timeout=5.0)    # finishes without noticing the cancel

        assert job_launch.start_job("cd_done", work)
        started.wait(timeout=2.0)
        bg.request_cancel("cd_done")
        assert db.get_job_record("cd_done")["message"] == bg.CANCELLING_MESSAGE
        release.set()
        assert job_runner.wait_for_job_threads(5.0)
        assert bg.get_status("cd_done")["status"] == "done"
        assert bg.get_status("cd_done")["message"] == job_runner.FINISHED_MESSAGE
        assert db.get_job_record("cd_done")["message"] == job_runner.FINISHED_MESSAGE
        bg.clear_job("cd_done")


class TestCancelCheckStateIsPerRun:
    def test_a_finished_run_leaves_no_throttle_or_logged_once_mark(self, monkeypatch):
        from jobs import job_store

        def failing(job_id):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(job_store, "row_cancel_time", failing)
        release = threading.Event()
        assert job_launch.start_job("cc_run", lambda: release.wait(5))
        try:
            assert bg.is_cancel_requested("cc_run") is False
            assert "cc_run" in bg._db_cancel_check_failed
        finally:
            release.set()
        assert job_runner.wait_for_job_threads(5.0)
        assert "cc_run" not in bg._db_cancel_check_failed
        assert "cc_run" not in bg._last_db_cancel_check
        bg.clear_job("cc_run")


    def test_a_finished_run_leaves_a_newer_runs_throttle_alone(self, monkeypatch):
        """The old run's thread forgets its throttle state after its status is
        final, when a new run of the same id may already be checking."""
        from jobs import job_store
        reads = []
        monkeypatch.setattr(job_store, "row_cancel_time", lambda job_id: reads.append(job_id))
        old_finishing, old_go, old_forgot = threading.Event(), threading.Event(), threading.Event()
        real_timing_finish, real_forget = job_runner._timing_finish, job_runner._forget_cancel_check

        def timing_finish(job_id, token, thread_job=True):
            if not old_finishing.is_set():
                old_finishing.set()
                old_go.wait(5)
            real_timing_finish(job_id, token, thread_job)

        def forget(job_id, *args):
            real_forget(job_id, *args)
            old_forgot.set()
        monkeypatch.setattr(job_runner, "_timing_finish", timing_finish)
        monkeypatch.setattr(job_runner, "_forget_cancel_check", forget)

        checked, again, seen = threading.Event(), threading.Event(), []

        def new_run():
            seen.append(bg.is_cancel_requested("cc_same"))
            checked.set()
            again.wait(5)
            seen.append(bg.is_cancel_requested("cc_same"))

        assert job_launch.start_job("cc_same", lambda: None)
        assert old_finishing.wait(5)
        assert job_launch.start_job("cc_same", new_run)
        assert checked.wait(5)
        old_go.set()
        assert old_forgot.wait(5)
        again.set()
        assert job_runner.wait_for_job_threads(5.0)
        assert seen == [False, False]
        # One read: the second check is inside the new run's interval.
        assert reads == ["cc_same"]
        bg.clear_job("cc_same")


class TestNotifyOnCompletion:
    """Step 23c item 4: an optional desktop notification when a
    background job finishes, gated behind set_notify_on_completion()
    (synced from the Settings toggle, off by default)."""

    def setup_method(self):
        self._library_state = _isolate_library()

    def teardown_method(self):
        job_settings.set_notify_on_completion(False)
        _restore_library(*self._library_state)

    def test_turned_off_no_notification_attempted(self, monkeypatch):
        import sys
        import types
        fake_plyer = types.ModuleType("plyer")
        fake_notification = types.ModuleType("plyer.notification")
        calls = []
        fake_notification.notify = lambda **kw: calls.append(kw)
        fake_plyer.notification = fake_notification
        monkeypatch.setitem(sys.modules, "plyer", fake_plyer)
        monkeypatch.setitem(sys.modules, "plyer.notification", fake_notification)

        job_settings.set_notify_on_completion(False)
        job_launch.start_job("t_notify_off", lambda: None, description="A job")
        _wait("t_notify_off")
        # Give a late notification a fair chance to appear, or this would
        # pass without ever proving one didn't come.
        assert not _wait_for(lambda: calls, timeout=0.5)
        bg.clear_job("t_notify_off")

    def test_no_toast_is_attempted_without_a_desktop(self, monkeypatch):
        import sys
        import types
        fake_plyer = types.ModuleType("plyer")
        fake_notification = types.ModuleType("plyer.notification")
        calls = []
        fake_notification.notify = lambda **kw: calls.append(kw)
        fake_plyer.notification = fake_notification
        monkeypatch.setitem(sys.modules, "plyer", fake_plyer)
        monkeypatch.setitem(sys.modules, "plyer.notification", fake_notification)
        job_settings.set_notify_on_completion(True)

        monkeypatch.setattr(job_settings.desktop_session, "has_desktop", lambda: False)
        job_settings._notify_job_finished("A job", "done")
        assert calls == []

        monkeypatch.setattr(job_settings.desktop_session, "has_desktop", lambda: True)
        job_settings._notify_job_finished("A job", "done")
        assert len(calls) == 1

    def test_failed_popup_says_why_it_failed(self, monkeypatch):
        import sys
        import types
        fake_plyer = types.ModuleType("plyer")
        fake_notification = types.ModuleType("plyer.notification")
        calls = []
        fake_notification.notify = lambda **kw: calls.append(kw)
        fake_plyer.notification = fake_notification
        monkeypatch.setitem(sys.modules, "plyer", fake_plyer)
        monkeypatch.setitem(sys.modules, "plyer.notification", fake_notification)
        monkeypatch.setattr(job_settings.desktop_session, "has_desktop", lambda: True)
        job_settings.set_notify_on_completion(True)
        job_settings._notify_job_finished("Dub generation", "error", reason="The disk is full.")
        assert calls[-1]["message"] == "Failed: Dub generation: The disk is full."
        job_settings._notify_job_finished("Dub generation", "error")
        assert calls[-1]["message"] == "Failed: Dub generation"

    def test_has_desktop_is_true_off_windows(self, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "platform", "linux")
        from lib import desktop_session
        assert desktop_session.has_desktop() is True

    def test_successful_job_notifies_when_enabled(self, monkeypatch):
        calls = []
        monkeypatch.setattr(job_runner, "_notify_job_finished", lambda *a, **k: calls.append(a))
        job_settings.set_notify_on_completion(True)
        job_launch.start_job("t_notify_ok", lambda: None, description="A translation job")
        _wait("t_notify_ok")
        assert _wait_for(lambda: calls), "the notification never arrived"
        assert calls == [("A translation job", "done")]
        bg.clear_job("t_notify_ok")

    def test_failed_job_notifies_with_error_status(self, monkeypatch):
        calls = []
        monkeypatch.setattr(job_runner, "_notify_job_finished", lambda *a, **k: calls.append(a))
        job_settings.set_notify_on_completion(True)
        job_launch.start_job("t_notify_err", lambda: (_ for _ in ()).throw(ValueError("boom")),
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

        monkeypatch.setattr(job_runner, "_notify_job_finished", delayed_notify)
        job_settings.set_notify_on_completion(True)
        job_launch.start_job("t_notify_delayed", lambda: (_ for _ in ()).throw(ValueError("boom")),
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
        job_settings.set_notify_on_completion(True)
        job_settings._notify_job_finished("A job", "done")  # must not raise

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

        job_settings.set_notify_on_completion(True)
        job_settings._notify_job_finished("A translation job", "done")
        assert len(calls) == 1
        assert "A translation job" in calls[0]["message"]

        calls.clear()
        job_settings._notify_job_finished("A translation job", "error")
        assert len(calls) == 1
        assert "A translation job" in calls[0]["message"]
        assert "Fail" in calls[0]["message"]

    def test_a_waiting_job_pops_up_the_bells_sentence(self, monkeypatch):
        import sys
        import types
        fake_plyer = types.ModuleType("plyer")
        fake_notification = types.ModuleType("plyer.notification")
        calls = []
        fake_notification.notify = lambda **kw: calls.append(kw)
        fake_plyer.notification = fake_notification
        monkeypatch.setitem(sys.modules, "plyer", fake_plyer)
        monkeypatch.setitem(sys.modules, "plyer.notification", fake_notification)
        monkeypatch.setattr(job_settings.desktop_session, "has_desktop", lambda: True)
        job_settings.set_notify_on_completion(True)
        did = db.create_drama(title_en="Moonlit Garden", owner_user_id=5, is_private=1)
        desc = f"Glossary from lines (title #{did})"

        job_settings._notify_job_finished(desc, "done", waiting={
            "text": "12 glossary names to review", "target": {"page": "glossary", "drama_id": did}})
        # The PC's own pop-up names even a private title, as the bell does there.
        assert calls[-1]["message"] == "Waiting for you: 12 glossary names to review - Moonlit Garden"
        job_settings._notify_job_finished(f"Translation (drama #{did})", "done",
                                          waiting={"cap": True})
        assert calls[-1]["message"] == \
            "Waiting for you: Translation stopped at the spending cap - Moonlit Garden"
        job_settings._notify_job_finished(f"Translation (drama #{did})", "done",
                                          waiting={"reason": "3 requests failed in a row"})
        assert calls[-1]["message"] == \
            "Waiting for you: Translation stopped - Moonlit Garden. 3 requests failed in a row"
        # On the Guest profile the screen hides private titles, so the pop-up leaves the name out.
        from services import pc_profile_service
        monkeypatch.setattr(pc_profile_service, "guest_active", lambda: True)
        job_settings._notify_job_finished(desc, "done", waiting={
            "text": "12 glossary names to review", "target": {"page": "glossary", "drama_id": did}})
        assert calls[-1]["message"] == "Waiting for you: 12 glossary names to review"
        monkeypatch.setattr(pc_profile_service, "guest_active", lambda: False)
        # A failed job keeps "Failed"; a broken note falls back to "Finished", never raising.
        job_settings._notify_job_finished(desc, "error", waiting={"cap": True})
        assert calls[-1]["message"] == f"Failed: {desc}"
        from services import notification_service

        def boom(*a, **k):
            raise RuntimeError("down")
        monkeypatch.setattr(notification_service, "waiting_job_message", boom)
        job_settings._notify_job_finished(desc, "done", waiting={"cap": True})
        assert calls[-1]["message"] == f"Finished: {desc}"

    def test_a_long_pop_up_is_cut_to_fit_the_windows_balloon(self, monkeypatch):
        import sys
        import types
        fake_plyer = types.ModuleType("plyer")
        fake_notification = types.ModuleType("plyer.notification")
        calls = []
        fake_notification.notify = lambda **kw: calls.append(kw)
        fake_plyer.notification = fake_notification
        monkeypatch.setitem(sys.modules, "plyer", fake_plyer)
        monkeypatch.setitem(sys.modules, "plyer.notification", fake_notification)
        monkeypatch.setattr(job_settings.desktop_session, "has_desktop", lambda: True)
        job_settings.set_notify_on_completion(True)
        did = db.create_drama(title_en="Moonlit Garden " + "x" * 75)   # 90 characters
        job_settings._notify_job_finished(f"Translation (drama #{did})", "done",
                                          waiting={"reason": "r" * 200})
        message = calls[-1]["message"]
        assert len(message) <= job_settings.POPUP_MAX_UNITS == 255
        assert message.startswith("Waiting for you: Translation stopped - Moonlit Garden")
        assert message.endswith("…")
        # The field counts UTF-16 units: each of these takes two.
        did = db.create_drama(title_en="🌙" * 60)
        job_settings._notify_job_finished(f"Translation (drama #{did})", "done",
                                          waiting={"reason": "r" * 200})
        message = calls[-1]["message"]
        assert len(message.encode("utf-16-le")) // 2 <= 255
        assert message.startswith("Waiting for you: Translation stopped - 🌙") and message.endswith("…")

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

        job_settings.set_notify_on_completion(False)
        job_settings._notify_job_finished("A job", "done")
        assert calls == []

    def test_process_based_job_notifies_too(self, monkeypatch):
        """The process-watcher path (start_process_job) is a separate
        code path from the thread-based runner above -- covered
        separately since it sets job status in its own place."""
        _install_fake_process(monkeypatch, run_target_on_start=True)
        calls = []
        monkeypatch.setattr(job_runner, "_notify_job_finished", lambda *a, **k: calls.append(a))
        job_settings.set_notify_on_completion(True)

        def fake_worker(result_queue):
            result_queue.put(("ok", {"sum": 1}))

        job_id = "test_process_notify"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, fake_worker, args=(), description="A process job")
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
        job_launch.start_job("mirror_thread_job", lambda: done.set(), description="Mirror test")
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
        job_launch.start_job("mirror_clear_job", lambda: done.set())
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
        ok = job_launch.start_job("mirror_db_down_job", lambda: done.set())
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
        job_settings.set_gpu_limit_enabled(True)
        job_settings.set_notify_on_completion(False)
        _restore_library(*self._library_state)

    def test_gpu_limit_round_trips_through_the_db(self, isolated_db):
        job_settings.set_gpu_limit_enabled(False)
        assert job_settings.get_gpu_limit_enabled() is False
        assert db.get_app_setting("gpu_limit_enabled") is False

    def test_notify_on_completion_round_trips_through_the_db(self, isolated_db):
        job_settings.set_notify_on_completion(True)
        assert job_settings.get_notify_on_completion() is True
        assert db.get_app_setting("notify_on_completion") is True

    def test_gpu_limit_defaults_true_when_never_set(self, isolated_db):
        assert job_settings.get_gpu_limit_enabled() is True

    def test_notify_defaults_true_when_never_set(self, isolated_db):
        assert job_settings.get_notify_on_completion() is True

    def test_gpu_limit_read_fails_open_on_a_db_error(self, isolated_db, monkeypatch):
        def boom(*a, **k):
            raise sqlite3.OperationalError("simulated failure")
        monkeypatch.setattr(db, "get_app_setting", boom)
        assert job_settings.get_gpu_limit_enabled() is True  # fails open -- the safer default

    def test_notify_read_fails_closed_on_a_db_error(self, isolated_db, monkeypatch):
        def boom(*a, **k):
            raise sqlite3.OperationalError("simulated failure")
        monkeypatch.setattr(db, "get_app_setting", boom)
        assert job_settings.get_notify_on_completion() is False  # fails closed -- no notification


class TestRunCancellable:
    """B-05: a thread job running an external command can be stopped."""

    def test_cancel_kills_the_command_and_marks_cancelled(self, isolated_db):
        started = job_launch.start_job("rc1", lambda: bg.run_cancellable(
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
        assert job_launch.start_job("rc3", lambda: bg.run_cancellable(
            "rc3", [sys.executable, "-c", wrapper], poll_interval=0.05, kill_timeout=5))
        time.sleep(0.5)
        t0 = time.time()
        bg.request_cancel("rc3")
        assert _wait_for(lambda: bg.get_status("rc3")["status"] == "cancelled", timeout=8)
        assert time.time() - t0 < 4


class TestSystemExitInJob:
    def test_system_exit_marks_the_job_errored(self):
        def leave():
            raise SystemExit(2)

        job_launch.start_job("sysexit", leave)
        assert _wait_for(lambda: bg.get_status("sysexit")["status"] != "running")
        assert bg.get_status("sysexit")["status"] == "error"
        assert "SystemExit" in bg.get_status("sysexit")["error"]


_FAKE_KEY = "AIzaSyFAKESECRETVALUE12345"


def _log_text():
    import applog
    return "\n".join(applog.tail(200))


def _boom(*a, **k):
    raise sqlite3.OperationalError(f"disk I/O error key={_FAKE_KEY}")


class TestSwallowedFailuresAreVisible:
    def test_gpu_lock_db_error_queues_the_job_and_logs(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
        job_settings.set_gpu_limit_enabled(True)
        real = gpu_slots.take
        monkeypatch.setattr(gpu_slots, "take", _boom)
        ran = threading.Event()
        assert job_launch.start_job("gl_err", ran.set, gpu_touching=True) is True
        assert bg.get_status("gl_err")["status"] == "queued"
        assert not ran.is_set()
        log = _log_text()
        assert "could not take the GPU lock" in log and _FAKE_KEY not in log
        monkeypatch.setattr(gpu_slots, "take", real)
        job_runner.recheck_gpu_queue()
        assert ran.wait(5)

    def test_external_gpu_check_error_is_logged_and_ignored(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", _boom)
        assert job_launch.start_job("ext_err", lambda: None, gpu_touching=True) is True
        _wait("ext_err")
        assert bg.get_status("ext_err")["status"] == "done"
        log = _log_text()
        assert "external GPU load check failed" in log and _FAKE_KEY not in log

    def test_gpu_limit_setting_error_is_logged(self, monkeypatch):
        monkeypatch.setattr(db, "get_app_setting", _boom)
        assert job_settings.get_gpu_limit_enabled() is True
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
        job_launch.start_job("dc_err", lambda: release.wait(5))
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
        job_settings.set_gpu_limit_enabled(True)

    def teardown_method(self):
        job_settings.set_gpu_limit_enabled(True)
        _restore_library(*self._library_state)

    def test_cancelling_a_queued_job_ends_it_at_once_and_it_never_runs(self):
        release = threading.Event()
        started = threading.Event()

        def slow():
            started.set()
            release.wait(timeout=5.0)

        ran = []
        try:
            assert job_launch.start_job("cq_a", slow, gpu_touching=True)
            started.wait(timeout=2.0)
            assert job_launch.start_job("cq_b", lambda: ran.append(1), gpu_touching=True)
            assert bg.get_status("cq_b")["status"] == "queued"
            bg.request_cancel("cq_b")
            assert bg.get_status("cq_b")["status"] == "cancelled"
            assert db.get_job_record("cq_b")["status"] == "cancelled"
        finally:
            release.set()
            _wait("cq_a")
        assert job_runner.wait_for_job_threads(5.0)
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

        assert job_launch.start_job("cr_a", work)
        started.wait(timeout=2.0)
        bg.request_cancel("cr_a")
        assert bg.get_status("cr_a")["message"] == bg.CANCELLING_MESSAGE
        assert db.get_job_record("cr_a")["message"] == bg.CANCELLING_MESSAGE
        release.set()
        assert _wait_for(lambda: bg.get_status("cr_a")["status"] == "cancelled")
        assert bg.get_status("cr_a")["message"] == job_runner.CANCELLED_MESSAGE
        assert job_runner.wait_for_job_threads(5.0)
        bg.clear_job("cr_a")


class TestDeadWorkerIsReconciled:
    """A running job whose worker thread is gone must not stay "running"
    forever (owner report: a transcription still Running long after it had
    stopped)."""

    def test_a_running_job_whose_thread_died_is_marked_interrupted(self, monkeypatch):
        release = threading.Event()
        assert job_launch.start_job("dw_a", lambda: release.wait(5.0))
        try:
            dead = threading.Thread(target=lambda: None)
            dead.start()
            dead.join()
            with job_table._lock:
                # as if the runner thread had vanished without a word
                job_table._workers["dw_a"] = (job_table._jobs["dw_a"], dead)
            assert job_runner.reconcile_dead_workers() == ["dw_a"]
            status = bg.get_status("dw_a")
            assert status["status"] == "error"
            assert status["error"] == job_runner.WORKER_LOST_MESSAGE
            assert db.get_job_record("dw_a")["status"] == "error"
        finally:
            release.set()
            assert job_runner.wait_for_job_threads(5.0)
        # The real runner finishing later can't turn it back into "done".
        assert bg.get_status("dw_a")["status"] == "error"
        bg.clear_job("dw_a")

    def test_a_live_worker_is_left_alone(self):
        release = threading.Event()
        assert job_launch.start_job("dw_b", lambda: release.wait(5.0))
        try:
            assert job_runner.reconcile_dead_workers() == []
            assert bg.get_status("dw_b")["status"] == "running"
        finally:
            release.set()
            assert job_runner.wait_for_job_threads(5.0)
        assert bg.get_status("dw_b")["status"] == "done"
        bg.clear_job("dw_b")


def test_a_thread_job_that_did_not_run_for_a_missing_package_ends_as_an_error():
    job_id = "test_thread_missing_package"
    bg.clear_job(job_id)

    def work():
        bg.update_progress(job_id, 0.0, "Loading Whisper model large-v3-turbo... 0%")
        bg.set_result(job_id, {"failed_reason": "dependency_missing",
                               "detail": "Transcription isn't installed yet."})

    assert job_launch.start_job(job_id, work) is True
    _wait(job_id)
    status = _wait_for_status(job_id, "running")
    assert status["status"] == "error"
    assert status["error"] == "Transcription isn't installed yet."
    assert not status["message"]
    bg.clear_job(job_id)


@pytest.mark.parametrize("reason,detail,text", [
    ("empty", None, "Nothing was produced: no speech or text was found."),
    ("qwen3_asr", "CUDA out of memory", "Qwen3-ASR failed on this audio. CUDA out of memory"),
    ("groq", "HTTP 503", "The Groq transcription request failed. HTTP 503"),
    ("vocal_separation", "separator crashed", "Separating the vocals failed. separator crashed"),
])
def test_a_thread_job_whose_worker_reports_a_failure_ends_as_an_error(monkeypatch, reason,
                                                                      detail, text):
    """The bell and the Jobs list read the server's status: a worker that
    returned a failure reason must not read as Finished."""
    from services import jobs_service
    calls = []
    monkeypatch.setattr(job_runner, "_notify_job_finished", lambda *a, **k: calls.append((a, k)))
    job_id = f"test_thread_failed_{reason}"
    bg.clear_job(job_id)
    result = {"failed_reason": reason, **({"detail": detail} if detail else {})}
    assert job_launch.start_job(job_id, lambda: bg.set_result(job_id, result)) is True
    _wait(job_id)
    status = _wait_for_status(job_id, "running")
    assert status["status"] == "error"
    assert status["error"] == text
    assert status["result"]["failed_reason"] == reason
    assert jobs_service.derive_outcome(status["status"], status["error"], status["result"]) == (
        "failed", text)
    _wait_for(lambda: calls)
    (args, kwargs), = calls
    assert args[1] == "error" and kwargs["reason"] == text
    bg.clear_job(job_id)


def test_a_process_job_whose_worker_reports_a_failure_ends_as_an_error(monkeypatch):
    _install_fake_process(monkeypatch, run_target_on_start=True)

    def fake_worker(result_queue):
        result_queue.put(("ok", {"line_id": 4, "failed_reason": "empty"}))

    job_id = "test_process_failed_empty"
    bg.clear_job(job_id)
    job_launch.start_process_job(job_id, fake_worker, args=())
    status = _wait_for_status(job_id, "running")
    assert status["status"] == "error"
    assert status["error"] == "Nothing was produced: no speech or text was found."
    assert status["result"] == {"line_id": 4, "failed_reason": "empty"}
    bg.clear_job(job_id)


def test_a_done_job_with_an_unlisted_failure_reason_notifies_as_failed(monkeypatch):
    """The Jobs list reads such a job as Failed (derive_outcome), so the bell
    and the toast must too, though the stored status stays done for the stage panels."""
    calls = []
    monkeypatch.setattr(job_runner, "_notify_job_finished", lambda *a, **k: calls.append((a, k)))
    job_id = "test_thread_soft_failed_unlisted"
    bg.clear_job(job_id)
    assert job_launch.start_job(job_id, lambda: bg.set_result(job_id, {"failed_reason": "a_reason_with_no_sentence"})) is True
    _wait(job_id)
    assert _wait_for_status(job_id, "running")["status"] == "done"
    _wait_for(lambda: calls)
    (args, kwargs), = calls
    assert args[1] == "error" and kwargs["reason"] == "The job did not finish."
    bg.clear_job(job_id)


def test_a_process_job_with_an_unlisted_failure_reason_notifies_as_failed(monkeypatch):
    _install_fake_process(monkeypatch, run_target_on_start=True)
    calls = []
    monkeypatch.setattr(job_runner, "_notify_job_finished", lambda *a, **k: calls.append((a, k)))

    def fake_worker(result_queue):
        result_queue.put(("ok", {"failed_reason": "audio_slice", "detail": "Couldn't cut this line's audio."}))

    job_id = "test_process_soft_failed_slice"
    bg.clear_job(job_id)
    job_launch.start_process_job(job_id, fake_worker, args=())
    assert _wait_for_status(job_id, "running")["status"] == "done"
    _wait_for(lambda: calls)
    (args, kwargs), = calls
    assert args[1] == "error" and "Couldn't cut this line's audio." in kwargs["reason"]
    bg.clear_job(job_id)


def test_a_process_job_stopped_at_its_cap_passes_a_waiting_note(monkeypatch):
    _install_fake_process(monkeypatch, run_target_on_start=True)
    calls = []
    monkeypatch.setattr(job_runner, "_notify_job_finished", lambda *a, **k: calls.append((a, k)))

    def fake_worker(result_queue):
        result_queue.put(("ok", {"cap_reached": 0.5, "translated_count": 2}))

    job_id = "test_process_cap_waiting"
    bg.clear_job(job_id)
    job_launch.start_process_job(job_id, fake_worker, args=())
    assert _wait_for_status(job_id, "running")["status"] == "done"
    _wait_for(lambda: calls)
    (args, kwargs), = calls
    assert args[1] == "done" and kwargs["waiting"] == {"cap": True, "target": {"page": "jobs"}}
    bg.clear_job(job_id)


def test_a_thread_job_passes_its_own_waiting_note_but_a_failed_one_does_not(monkeypatch):
    calls = []
    monkeypatch.setattr(job_runner, "_notify_job_finished", lambda *a, **k: calls.append((a, k)))
    note = {"text": "2 glossary names to review", "target": {"page": "glossary", "drama_id": 3}}
    for job_id, result in (("test_thread_waiting_ok", {"waiting": note}),
                           ("test_thread_waiting_failed", {"waiting": note, "failed_reason": "timeout"})):
        bg.clear_job(job_id)
        assert job_launch.start_job(job_id, lambda j=job_id, r=result: bg.set_result(j, r)) is True
        _wait(job_id)
    _wait_for(lambda: len(calls) == 2)
    by_id = {k["job_id"]: (a, k) for a, k in calls}
    assert by_id["test_thread_waiting_ok"][1]["waiting"] == note
    assert by_id["test_thread_waiting_ok"][0][1] == "done"
    assert by_id["test_thread_waiting_failed"][0][1] == "error"
    assert by_id["test_thread_waiting_failed"][1]["waiting"] is None
    for job_id in by_id:
        bg.clear_job(job_id)


@pytest.mark.parametrize("reason", ["no_lines", "no_text", "empty_kept_existing"])
def test_a_partial_worker_outcome_still_ends_done(reason):
    """Zero lines is "Finished but found no lines" (a partial), not a failure."""
    job_id = f"test_thread_partial_{reason}"
    bg.clear_job(job_id)
    assert job_launch.start_job(job_id, lambda: bg.set_result(job_id, {"failed_reason": reason})) is True
    _wait(job_id)
    status = _wait_for_status(job_id, "running")
    assert status["status"] == "done" and status["progress"] == 1.0
    bg.clear_job(job_id)


class TestAClearedRunNeverWritesIntoTheNextRun:
    """A run cleared while it still works (a library reset's clear_all_jobs)
    can be followed by a new run of the same id. The old run's progress,
    result and outcome belong to it alone, never to the new run."""

    def test_old_thread_run_leaves_the_new_run_alone(self, monkeypatch):
        told = []
        monkeypatch.setattr(job_runner, "_notify_job_finished",
                            lambda *a, **k: told.append(k.get("job_id")))
        job_id = "rerun_thread"
        go_old, go_new = threading.Event(), threading.Event()
        old_thread = []

        def old():
            old_thread.append(threading.current_thread())
            go_old.wait(5)
            bg.update_progress(job_id, 0.9, "old run")
            bg.set_result(job_id, "old result")

        job_launch.start_job(job_id, old)
        assert _wait_for(lambda: old_thread)
        # Held across both calls so the old run cannot notice the gap.
        with job_table._lock:
            bg.clear_job(job_id)
            job_launch.start_job(job_id, lambda: go_new.wait(5))
        try:
            go_old.set()
            old_thread[0].join(5)
            assert not old_thread[0].is_alive()
            new = bg.get_status(job_id)
            assert new["status"] == "running"
            assert new["progress"] == 0.0 and new["message"] != "old run"
            assert new["result"] is None
            assert told == []   # the cleared run's outcome is nobody's news
        finally:
            go_new.set()
            _wait(job_id)
            bg.clear_job(job_id)

    def test_a_cleared_thread_run_is_told_to_stop(self):
        job_id = "rerun_cancel"
        go_old, go_new = threading.Event(), threading.Event()
        heard = []

        def old():
            go_old.wait(5)
            heard.append(bg.is_cancel_requested(job_id))

        job_launch.start_job(job_id, old)
        with job_table._lock:
            bg.clear_job(job_id)
            job_launch.start_job(job_id, lambda: go_new.wait(5))
        try:
            go_old.set()
            assert _wait_for(lambda: heard)
            assert heard == [True]
            assert bg.is_cancel_requested(job_id) is False   # the new run was not asked
        finally:
            go_new.set()
            _wait(job_id)
            bg.clear_job(job_id)

    def test_a_caller_outside_any_job_is_not_told_to_stop(self):
        # Pipelines run directly (tests, the CLI) pass job_id None from an
        # untagged thread; that must not read as a cleared run.
        assert bg.is_cancel_requested(None) is False
        bg.update_progress(None, 0.5, "ok")   # still a no-op, not an error

    def test_old_process_watcher_stops_its_own_process_not_the_new_run(self, monkeypatch):
        instances = _install_fake_process(monkeypatch, alive_forever=True)
        job_id = "rerun_process"
        job_launch.start_process_job(job_id, lambda result_queue: None, args=())
        assert _wait_for(lambda: bg.is_running(job_id))
        with job_table._lock:
            bg.clear_job(job_id)
            job_launch.start_process_job(job_id, lambda result_queue: None, args=())
        try:
            assert _wait_for(lambda: instances[0].terminated)
            time.sleep(0.5)     # several watcher polls
            assert bg.get_status(job_id)["status"] == "running"
            assert instances[1].terminated is False
        finally:
            bg.request_cancel(job_id)
            _wait_for_status(job_id, "running")
            bg.clear_job(job_id)


class TestClearFinishedJob:
    def test_a_running_run_is_kept(self):
        go = threading.Event()
        job_launch.start_job("cf_live", lambda: go.wait(5))
        try:
            assert bg.clear_finished_job("cf_live") is False
            assert bg.get_status("cf_live")["status"] == "running"
            assert db.get_job_record("cf_live")["status"] == "running"
        finally:
            go.set()
            _wait("cf_live")
        assert bg.clear_finished_job("cf_live") is True
        assert bg.get_status("cf_live") is None and db.get_job_record("cf_live") is None

    def test_another_processes_new_run_keeps_its_row(self):
        db.save_job_record("cf_other", status="running", started_at=2.0)
        assert bg.clear_finished_job("cf_other", started_at=1.0) is False
        assert db.get_job_record("cf_other")["status"] == "running"

    def test_a_newer_finished_run_here_keeps_its_unread_result(self):
        job_launch.start_job("cf_rerun", lambda: bg.set_result("cf_rerun", "first"))
        assert _wait_for(lambda: bg.get_status("cf_rerun")["status"] == "done")
        seen = db.get_job_record("cf_rerun")["started_at"]
        time.sleep(0.01)    # a distinct started_at for the second run
        job_launch.start_job("cf_rerun", lambda: bg.set_result("cf_rerun", "second"))
        assert _wait_for(lambda: bg.get_status("cf_rerun")["result"] == "second"
                         and bg.get_status("cf_rerun")["status"] == "done")
        assert bg.clear_finished_job("cf_rerun", started_at=seen) is False
        assert bg.get_status("cf_rerun")["result"] == "second"
        assert db.get_job_record("cf_rerun") is not None
        bg.clear_job("cf_rerun")

    def test_a_newer_finished_run_keeps_its_row(self):
        db.save_job_record("cf_newer", status="done", started_at=2.0)
        assert bg.clear_finished_job("cf_newer", started_at=1.0) is False
        assert db.get_job_record("cf_newer") is not None
        assert bg.clear_finished_job("cf_newer", started_at=2.0) is True
        assert db.get_job_record("cf_newer") is None


class TestJobsWriteOnlyTheirDeclaredLineFields:
    """A job names at launch the line columns it writes; its saves are
    refused any other column, and a full sync unless it declared one."""

    @staticmethod
    def _title():
        from core import Line, line_from_row
        did = db.create_drama(title_en="Declared")
        db.save_lines(did, [Line(idx=0, start=0, end=1, zh="原文", en="old")])
        return did, [line_from_row(r) for r in db.load_lines(did)]

    def _run(self, job_id, target, line_fields):
        bg.clear_job(job_id)
        assert job_launch.start_job(job_id, target, line_fields=line_fields) is True
        status = _wait_for_status(job_id, "running")
        bg.clear_job(job_id)
        return status

    def test_a_declared_field_is_written(self):
        did, lines = self._title()

        def job():
            lines[0].en = "new"
            db.save_lines(did, lines, fields=("en",))
        assert self._run("declared_en", job, ("en", "flag"))["status"] == "done"
        assert db.load_lines(did)[0]["en"] == "new"

    def test_an_undeclared_field_fails_the_job_and_writes_nothing(self):
        did, lines = self._title()

        def job():
            lines[0].en, lines[0].flag = "new", "check"
            db.save_lines(did, lines, fields=("en", "flag"))
        status = self._run("undeclared_flag", job, ("en",))
        assert status["status"] == "error"
        assert "UndeclaredLineWrite" in status["error"] and "flag" in status["error"]
        row = db.load_lines(did)[0]
        assert (row["en"], row["flag"]) == ("old", None)

    def test_a_full_sync_needs_the_full_sync_declaration(self):
        did, lines = self._title()

        def job():
            lines[0].en = "replaced"
            db.save_lines(did, lines)
        status = self._run("no_full_sync", job, ("zh", "en"))
        assert status["status"] == "error" and "full sync" in status["error"]
        assert db.load_lines(did)[0]["en"] == "old"
        assert self._run("full_sync", job, db.FULL_SYNC)["status"] == "done"
        assert db.load_lines(did)[0]["en"] == "replaced"

    def test_a_job_declaring_no_fields_cannot_compare_and_set_either(self):
        did, lines = self._title()
        lid = lines[0].id

        def one():
            db.update_line_fields_if(did, lid, {"en": "x"}, {"en": "old"})

        def many():
            db.update_lines_fields_if_many(did, [(lid, {"en": "x"}, {"en": "old"})])
        assert self._run("cas_one", one, ())["status"] == "error"
        assert self._run("cas_many", many, ())["status"] == "error"
        assert db.load_lines(did)[0]["en"] == "old"

    def test_a_misspelt_column_refuses_the_start(self):
        bg.clear_job("bad_declaration")
        with pytest.raises(ValueError, match="englsh"):
            job_launch.start_job("bad_declaration", lambda: None, line_fields=("englsh",))
        with pytest.raises(ValueError):
            job_launch.start_job("bad_declaration", lambda: None, line_fields="en")
        assert bg.get_status("bad_declaration") is None

    def test_the_declaration_ends_with_the_job(self):
        did, lines = self._title()
        assert self._run("noop", lambda: None, ())["status"] == "done"
        lines[0].flag = "check"
        db.save_lines(did, lines, fields=("flag",))   # this thread is not a job
        assert db.load_lines(did)[0]["flag"] == "check"

    def test_a_process_jobs_on_done_is_held_to_the_declaration(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        did, lines = self._title()

        def apply(_jid, result):
            lines[0].speaker = result["speaker"]
            db.save_lines(did, lines, fields=("speaker",))
        job_id = "declared_process"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {"speaker": "A"})), args=(),
                                     on_done=apply, line_fields=("en",))
        status = _wait_for_status(job_id, "running")
        bg.clear_job(job_id)
        assert status["status"] == "error" and "speaker" in status["error"]
        assert db.load_lines(did)[0]["speaker"] in ("", None)

    def test_a_queued_gpu_job_keeps_its_declaration(self, monkeypatch):
        _install_fake_process(monkeypatch, run_target_on_start=True)
        did, lines = self._title()
        release, started = threading.Event(), threading.Event()
        job_launch.start_job("declared_gpu_blocker",
                             lambda: (started.set(), release.wait(timeout=2.0)),
                             gpu_touching=True, line_fields=())
        started.wait(timeout=2.0)

        def apply(_jid, _result):
            lines[0].en = "queued"
            db.save_lines(did, lines, fields=("en",))
        job_id = "declared_queued"
        bg.clear_job(job_id)
        job_launch.start_process_job(job_id, lambda q: q.put(("ok", {})), args=(),
                                     gpu_touching=True, on_done=apply, line_fields=("flag",))
        assert bg.get_status(job_id)["status"] == "queued"
        release.set()
        deadline = time.time() + 3.0
        status = bg.get_status(job_id)
        while time.time() < deadline and status["status"] in ("queued", "running"):
            time.sleep(0.01)
            status = bg.get_status(job_id)
        _wait("declared_gpu_blocker")
        bg.clear_job("declared_gpu_blocker")
        bg.clear_job(job_id)
        assert status["status"] == "error" and "en" in status["error"]
        assert db.load_lines(did)[0]["en"] == "old"
