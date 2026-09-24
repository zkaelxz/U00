"""
tests/test_background_jobs.py -- the background job runner that lets long
operations (translation, dubbing) survive Streamlit's script lifecycle.

The property that matters isn't "does the function run" -- it's "does it
keep running with nobody polling it," since that's exactly the scenario
of switching to another tab and coming back later.
"""
import sys
import os
import time
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


class TestLineWritingJobConflictGuard:
    """Translate, flag, and fix-flagged-lines jobs each save their own
    full copy of a drama's lines through db.save_lines(), which replaces
    every line for the drama. Since they can run concurrently (job ids
    are per-type, so start_job()'s own dedup doesn't catch this), the
    last one to save silently undoes whatever the other one wrote --
    e.g. starting "Find lines to flag" mid-translation wipes the
    translations done so far. other_line_writing_job() is the guard the
    UI checks before starting any of these three."""

    def test_no_conflict_when_nothing_is_running(self):
        assert bg.other_line_writing_job(999, "translate_") is None

    def test_translate_running_blocks_flag_for_the_same_drama(self):
        bg.start_job("translate_501", lambda: time.sleep(0.05))
        assert bg.other_line_writing_job(501, "flag_") == "translate"
        _wait("translate_501")
        bg.clear_job("translate_501")

    def test_flag_running_blocks_fixflag_for_the_same_drama(self):
        bg.start_job("flag_502", lambda: time.sleep(0.05))
        assert bg.other_line_writing_job(502, "fixflag_") == "flag"
        _wait("flag_502")
        bg.clear_job("flag_502")

    def test_fixflag_running_blocks_translate_for_the_same_drama(self):
        bg.start_job("fixflag_503", lambda: time.sleep(0.05))
        assert bg.other_line_writing_job(503, "translate_") == "fixflag"
        _wait("fixflag_503")
        bg.clear_job("fixflag_503")

    def test_a_running_job_for_a_different_drama_is_not_a_conflict(self):
        bg.start_job("translate_504", lambda: time.sleep(0.05))
        assert bg.other_line_writing_job(505, "flag_") is None
        _wait("translate_504")
        bg.clear_job("translate_504")

    def test_same_type_running_is_not_reported_as_a_conflict(self):
        """start_job()'s own job_id dedup already refuses a same-type
        duplicate -- this guard is only for a DIFFERENT job type stepping
        on the same drama, so it must exclude its own prefix."""
        bg.start_job("translate_506", lambda: time.sleep(0.05))
        assert bg.other_line_writing_job(506, "translate_") is None
        _wait("translate_506")
        bg.clear_job("translate_506")

    def test_finished_job_is_no_longer_a_conflict(self):
        bg.start_job("translate_507", lambda: None)
        _wait("translate_507")
        assert bg.other_line_writing_job(507, "flag_") is None
        bg.clear_job("translate_507")


class TestAnyLineWritingJob:
    """other_line_writing_job() above only stops the three line-writing
    jobs from clashing with EACH OTHER. A person's own manual edit in
    Workspace's Review & edit section (save, merge, a per-line fix,
    undo/restore) still races a running one of those jobs the same way --
    any_line_writing_job() is the guard workspace_tab.py checks to lock
    those controls while any of the three is running for the drama."""

    def test_false_when_nothing_is_running(self):
        assert bg.any_line_writing_job(601) is False

    def test_true_while_translate_is_running(self):
        bg.start_job("translate_602", lambda: time.sleep(0.05))
        assert bg.any_line_writing_job(602) is True
        _wait("translate_602")
        bg.clear_job("translate_602")

    def test_true_while_flag_is_running(self):
        bg.start_job("flag_603", lambda: time.sleep(0.05))
        assert bg.any_line_writing_job(603) is True
        _wait("flag_603")
        bg.clear_job("flag_603")

    def test_true_while_fixflag_is_running(self):
        bg.start_job("fixflag_604", lambda: time.sleep(0.05))
        assert bg.any_line_writing_job(604) is True
        _wait("fixflag_604")
        bg.clear_job("fixflag_604")

    def test_false_for_a_different_drama(self):
        bg.start_job("translate_605", lambda: time.sleep(0.05))
        assert bg.any_line_writing_job(606) is False
        _wait("translate_605")
        bg.clear_job("translate_605")

    def test_false_once_the_job_has_finished(self):
        bg.start_job("translate_607", lambda: None)
        _wait("translate_607")
        assert bg.any_line_writing_job(607) is False
        bg.clear_job("translate_607")

    def test_false_for_a_non_line_writing_job(self):
        """emotion/consistency/notes jobs don't call db.save_lines() --
        no race to guard against, so they must not lock editing."""
        bg.start_job("emotion_608", lambda: time.sleep(0.05))
        assert bg.any_line_writing_job(608) is False
        _wait("emotion_608")
        bg.clear_job("emotion_608")


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
