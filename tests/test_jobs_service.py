"""
Tests for services/jobs_service.py -- Migration Slice 8's read-only,
cross-process job list, reading db.job_records (Migration Slice 7's
mirror) rather than background_jobs.py's own in-memory state.
"""

import os

import db
from services import jobs_service
from lib.errors import NotFoundError

import pytest


class TestListJobs:
    def test_empty_when_no_jobs_recorded(self, isolated_db):
        assert jobs_service.list_jobs() == []

    def test_lists_a_recorded_job(self, isolated_db):
        db.save_job_record("j1", status="running", progress=0.5, description="Translating")
        jobs = jobs_service.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "j1"
        assert jobs[0]["status"] == "running"
        assert jobs[0]["description"] == "Translating"

    def test_redacts_a_secret_in_the_error_field(self, isolated_db):
        db.save_job_record("j1", status="error", error="failed with key sk-ABCDEFGHIJKLMNOP12345")
        jobs = jobs_service.list_jobs()
        assert "sk-ABCDEFGHIJKLMNOP12345" not in jobs[0]["error"]

    def test_scrubs_urls_from_the_error_and_message_fields(self, isolated_db):
        db.save_job_record("j1", status="error",
                           error="GET https://internal.example/x?token=abc failed",
                           message="fetching http://10.0.0.5/secret")
        job = jobs_service.list_jobs()[0]
        assert job["error"] == "GET [URL] failed"
        assert job["message"] == "fetching [URL]"

    def test_done_job_without_result_is_ok(self, isolated_db):
        db.save_job_record("j1", status="done")
        job = jobs_service.list_jobs()[0]
        assert job["result"] is None
        assert job["outcome"] == "ok"


def _run_job_with_result(job_id, result):
    import time
    import background_jobs
    from jobs import job_launch
    job_launch.start_job(job_id, lambda: background_jobs.set_result(job_id, result))
    deadline = time.time() + 3
    while (background_jobs.get_status(job_id) or {}).get("status") not in ("done", "error"):
        assert time.time() < deadline
        time.sleep(0.01)


def _wait_until_finished(job_id):
    import time
    import background_jobs
    deadline = time.time() + 3
    while (background_jobs.get_status(job_id) or {}).get("status") in ("queued", "running"):
        assert time.time() < deadline
        time.sleep(0.01)


def test_a_job_cancelled_while_queued_still_lists_as_cancelled(isolated_db):
    """A bulk run, a URL download and a Live session stop a child still
    waiting for the GPU with cancel_queued; the cancelled run must stay
    listed (latest run per id), not vanish from the Jobs page."""
    import threading
    import background_jobs
    from jobs import job_launch, job_runner
    release = threading.Event()
    job_launch.start_job("transcribe_1", lambda: release.wait(5), gpu_touching=True)
    try:
        job_launch.start_job("transcribe_2", lambda: None, gpu_touching=True)
        assert background_jobs.get_status("transcribe_2")["status"] == "queued"
        assert background_jobs.cancel_queued("transcribe_2") is True
        listed = {j["job_id"]: j for j in jobs_service.list_jobs()}
        assert listed["transcribe_2"]["status"] == "cancelled"
        assert listed["transcribe_2"]["outcome"] == "cancelled"
    finally:
        release.set()
        job_runner.wait_for_job_threads(5)


class TestJobResults:
    def test_failed_transcribe_result_is_failed_and_redacted(self, isolated_db):
        _run_job_with_result("transcribe_1", {
            "failed_reason": "model_download",
            "detail": "could not fetch /home/someone/models/large-v3 with key sk-ABCDEFGHIJKLMNOP12345"})
        job = jobs_service.get_job("transcribe_1")
        assert job["status"] == "error"
        assert job["outcome"] == "failed"
        for text in (job["outcome_message"], job["error"]):
            assert "sk-ABCDEFGHIJKLMNOP12345" not in text
            assert "/home/someone" not in text
            assert "downloaded" in text

    def test_result_survives_a_restart(self, isolated_db):
        from jobs import job_table
        _run_job_with_result("translate_2", {"errors": ["batch 1: timeout"], "cap_reached": None,
                                             "lines_replaced": True})
        job_table._jobs.clear()  # the owning process is gone
        job = jobs_service.get_job("translate_2")
        assert job["result"]["lines_replaced"] is True
        assert job["result"]["errors"] == ["batch 1: timeout"]
        assert job["outcome"] == "partial"

    def test_no_path_url_or_unlisted_key_in_result(self, isolated_db):
        _run_job_with_result("dub_3", {
            "failed_reason": "empty", "detail": r"C:\Users\kae\Videos\ep1.mp4 at https://x.example/a?k=1",
            "output_path": "/srv/library/1/out.wav", "segments": [{"start": 0}]})
        result = jobs_service.get_job("dub_3")["result"]
        text = str(result)
        assert "output_path" not in result and "segments" not in result
        assert "Users" not in text and "/srv" not in text and "x.example" not in text

    def test_outcomes_per_job_shape(self):
        d = jobs_service.derive_outcome
        assert d("done", None, {"failed_reason": "cancelled"})[0] == "cancelled"
        assert d("done", None, {"failed_reason": "empty_kept_existing",
                                "existing_line_count": 4})[0] == "kept_existing"
        assert d("done", None, {"fixed_count": 2, "total_flagged": 3, "errors": ["x"],
                                "cap_reached": None})[0] == "partial"
        assert d("done", None, {"fixed_count": 3, "total_flagged": 3, "errors": [],
                                "cap_reached": None}) == ("ok", "Fixed 3 of 3 flagged line(s).")
        assert d("done", None, {"errors": [], "cap_reached": 1.5})[0] == "partial"
        assert d("done", None, {"stats": {}, "partial": True})[0] == "partial"
        assert d("cancelled", None, None)[0] == "cancelled"
        assert d("error", "boom", None) == ("failed", "boom")
        assert d("running", None, None) == (None, None)

    def test_a_translate_run_that_wrote_nothing_after_errors_is_failed_not_done(self):
        d = jobs_service.derive_outcome
        projected = jobs_service.project_result({"errors": ["batch 1: rate limited"], "translated_count": 0})
        assert d("done", None, projected) == ("failed", "Nothing was translated. First problem: batch 1: rate limited")
        some = jobs_service.project_result({"errors": ["batch 2: rate limited"], "translated_count": 7})
        assert d("done", None, some)[0] == "partial"
        assert d("done", None, jobs_service.project_result({"errors": [], "translated_count": 0}))[0] == "ok"

    def test_transcribe_warnings_are_partial(self):
        for key in ("gpu_fallback", "word_align_error", "forced_align_error",
                    "coverage_warning"):
            result = jobs_service.project_result({"line_count": 5, key: "boom"})
            assert result[key] == "boom"
            outcome, message = jobs_service.derive_outcome("done", None, result)
            assert outcome == "partial" and "boom" in message
        assert "CPU" in jobs_service.derive_outcome("done", None, {"gpu_fallback": "x"})[1]
        outcome, message = jobs_service.derive_outcome(
            "done", None, {"flags_needing_recheck": [7]})
        assert outcome == "partial" and "recheck" in message
        assert jobs_service.derive_outcome("done", None, {"line_count": 5, "gpu_fallback": None})[0] == "ok"
        notice = "Transcription ran on the CPU because the GPU couldn't be used (x). This was slower than on the GPU."
        result = jobs_service.project_result({"line_count": 5, "gpu_fallback": "x", "device_notice": notice})
        assert jobs_service.derive_outcome("done", None, result) == ("partial", notice)

    def test_transcribe_stage_seconds_keep_only_the_recorded_known_stages(self):
        result = jobs_service.project_result({"line_count": 5, "stage_seconds": {
            "separate": 12.345, "transcribe": 80, "align": True, "load": -1, "C:/secret": 3, "decode_vad": "x"}})
        assert result["stage_seconds"] == {"separate": 12.35, "transcribe": 80}
        assert "stage_seconds" not in jobs_service.project_result({"line_count": 5, "stage_seconds": {}})

    def test_cancelled_bulk_run_is_cancelled(self):
        assert jobs_service.derive_outcome("done", None, {"status": "cancelled"})[0] == "cancelled"

    def test_a_cancelled_bulk_review_pass_is_not_called_a_translation(self, isolated_db):
        for job_id, text in (("bulk_translate_4", "The bulk translation was cancelled."),
                             ("bulk_flag_4", "The job was cancelled."),
                             ("bulk_consistency_4", "The job was cancelled.")):
            db.save_job_record(job_id, status="done",
                               result_json=jobs_service.project_result_json({"status": "cancelled"}))
            job = jobs_service.get_job(job_id)
            assert (job["outcome"], job["outcome_message"]) == ("cancelled", text)

    def test_translate_error_dicts_are_formatted(self):
        result = jobs_service.project_result({"errors": [
            {"batch_index": 0, "lines": [2, 3], "error": "timeout with key sk-ABCDEFGHIJKLMNOP12345"},
            {"batch_index": 1, "lines": [7], "error": "bad json"}]})
        assert result["errors"][0].startswith("lines 3-4: timeout")
        assert "sk-ABCDEFGHIJKLMNOP12345" not in result["errors"][0]
        assert result["errors"][1] == "line 8: bad json"

    def test_redaction_survives_getuser_failure(self, monkeypatch):
        import getpass

        def boom():
            raise OSError("no user")
        monkeypatch.setattr(getpass, "getuser", boom)
        result = jobs_service.project_result({"detail": "at /home/x/models/m.bin sk-ABCDEFGHIJKLMNOP12345"})
        assert "/home/x" not in result["detail"] and "sk-ABCDEFGHIJKLMNOP12345" not in result["detail"]

    def test_projection_failure_still_mirrors_status(self, isolated_db, monkeypatch):
        def broken(_result):
            raise RuntimeError("projection broke")
        monkeypatch.setattr(jobs_service, "project_result_json", broken)
        _run_job_with_result("j_broken", {"line_count": 1})
        job = jobs_service.get_job("j_broken")
        assert job["status"] == "done" and job["result"] is None

    def test_oversized_result_is_capped(self):
        out = jobs_service.project_result({"errors": ["e" * 400] * 500})
        assert len(str(out)) <= 9000


class TestFallbackAndBulkProjection:
    SECRET = "sk-ABCDEFGHIJKLMNOP12345"

    def test_fallbacks_projected_as_engine_names_and_counts_only(self):
        events = [{"from": "claude", "to": "deepseek", "reason": "RateLimitError",
                   "detail": f"429 with {self.SECRET}"}] * 2
        out = jobs_service.project_result({"errors": [], "fallbacks": events})
        assert out["fallbacks"] == [{"from": "claude", "to": "deepseek", "count": 2}]
        assert self.SECRET not in str(out) and "429" not in str(out)
        # Stable on re-projection (a stored row is re-projected on read).
        assert jobs_service.project_result(out)["fallbacks"] == out["fallbacks"]

    def test_fallback_shows_in_outcome_message(self):
        out = jobs_service.project_result({"errors": [],
                                           "fallbacks": [{"from": "claude", "to": "gemini"}]})
        outcome, msg = jobs_service.derive_outcome("done", None, out)
        assert outcome == "ok" and "claude to gemini" in msg

    def test_speaker_count_hint_shows_in_outcome_without_making_it_partial(self):
        out = jobs_service.project_result({"speaker_count_hint": "Only 2 speakers found."})
        outcome, msg = jobs_service.derive_outcome("done", None, out)
        assert outcome == "ok" and "Only 2 speakers found." in msg

    def _bulk(self, **kw):
        base = {"translated": [], "skipped_running": [], "skipped_no_key": [],
                "skipped_no_lines": [], "skipped_cap": [], "skipped_engine_changed": [],
                "errors": {}}
        base.update(kw)
        return base

    def test_bulk_every_drama_failed_is_failed(self):
        out = jobs_service.project_result(self._bulk(errors={3: f"bad key {self.SECRET}"}))
        assert out["bulk"]["failed"][0]["drama_id"] == 3
        assert self.SECRET not in str(out)
        assert jobs_service.derive_outcome("done", None, out)[0] == "failed"

    def test_bulk_all_skipped_is_failed(self):
        out = jobs_service.project_result(self._bulk(skipped_no_key=[1, 2]))
        assert out["bulk"]["skipped"]["no_key"] == {"count": 2, "drama_ids": [1, 2]}
        assert jobs_service.derive_outcome("done", None, out)[0] == "failed"

    def test_bulk_some_failed_is_partial(self):
        out = jobs_service.project_result(self._bulk(translated=[1], errors={2: "boom"}))
        outcome, msg = jobs_service.derive_outcome("done", None, out)
        assert outcome == "partial" and "1 failed" in msg

    def test_bulk_all_translated_is_ok(self):
        out = jobs_service.project_result(self._bulk(translated=[1, 2]))
        assert jobs_service.derive_outcome("done", None, out)[0] == "ok"

    def test_bulk_projection_is_bounded_and_survives_storage(self, isolated_db):
        result = self._bulk(translated=list(range(100)),
                            errors={i: "x" * 900 for i in range(100, 150)})
        db.save_job_record("b1", status="done",
                           result_json=jobs_service.project_result_json(result))
        job = jobs_service.get_job("b1")
        bulk = job["result"]["bulk"]
        assert bulk["translated_count"] == 100 and len(bulk["translated_ids"]) <= 20
        assert bulk["failed_count"] == 50
        assert all(len(f["error"]) <= 120 for f in bulk["failed"])
        assert job["outcome"] == "partial"

    def test_bulk_with_chinese_errors_is_trimmed_never_dropped(self, isolated_db):
        result = self._bulk(errors={i: "密钥已被撤销" * 20 for i in range(20)},
                            skipped_no_key=list(range(100, 120)))
        out = jobs_service.project_result(result)
        import json
        assert len(json.dumps(out, ensure_ascii=False).encode("utf-8")) <= 8000
        assert out["bulk"]["failed_count"] == 20
        assert all(len(f["error"].encode("utf-8")) <= 120 for f in out["bulk"]["failed"])
        assert jobs_service.derive_outcome("done", None, out)[0] == "failed"
        db.save_job_record("b2", status="done", result_json=jobs_service.project_result_json(result))
        assert jobs_service.get_job("b2")["outcome"] == "failed"

    def test_bulk_partly_translated_drama_is_partial(self):
        out = jobs_service.project_result(self._bulk(partial={4: "batch errors"}))
        assert out["bulk"]["partial"] == [{"drama_id": 4, "reason": "batch errors"}]
        assert jobs_service.derive_outcome("done", None, out)[0] == "partial"

    def test_bulk_stopped_by_a_failure_streak_says_why(self, isolated_db):
        result = self._bulk(errors={1: "bad key", 2: "bad key", 3: "bad key"},
                            stopped="Stopped: 3 titles in a row failed")
        out = jobs_service.project_result(result)
        outcome, msg = jobs_service.derive_outcome("done", None, out)
        assert outcome == "failed" and msg.endswith("Stopped: 3 titles in a row failed.")
        # Also once stored and read back.
        db.save_job_record("b3", status="done", result_json=jobs_service.project_result_json(result))
        assert jobs_service.get_job("b3")["outcome_message"] == msg

    def test_kept_your_edit_as_a_count_or_a_list_of_ids(self):
        # Re-segment and the off-peak batch store a count; Fix flagged stores line ids.
        for kept in (2, [7, 9]):
            outcome, msg = jobs_service.derive_outcome("done", None, {"kept_your_edit": kept})
            assert outcome == "ok" and msg.startswith("2 line(s) edited while it ran")

    def test_bulk_cancelled(self):
        out = jobs_service.project_result(self._bulk(cancelled=True))
        assert out["bulk"]["cancelled"] is True
        assert jobs_service.derive_outcome("done", None, out)[0] == "cancelled"
        out = jobs_service.project_result(self._bulk(translated=[1], cancelled=True))
        assert jobs_service.derive_outcome("done", None, out)[0] == "partial"


class TestGetJob:
    def test_returns_a_recorded_job(self, isolated_db):
        db.save_job_record("j1", status="queued")
        assert jobs_service.get_job("j1")["status"] == "queued"

    def test_raises_not_found_for_an_unknown_job(self, isolated_db):
        with pytest.raises(NotFoundError):
            jobs_service.get_job("nope")


class TestDeleteJobs:
    def test_deletes_finished_job_memory_and_row(self, isolated_db):
        import background_jobs
        _run_job_with_result("fin", {})
        db.save_job_record("fin", status="done")
        assert jobs_service.delete_job("fin", confirm=True) == {"job_id": "fin", "deleted": True}
        assert db.get_job_record("fin") is None
        assert background_jobs.get_status("fin") is None

    @pytest.mark.parametrize("status", ["queued", "running"])
    def test_active_job_refused(self, isolated_db, status):
        from lib.errors import ConflictError
        db.save_job_record("act", status=status)
        with pytest.raises(ConflictError):
            jobs_service.delete_job("act", confirm=True)
        assert db.get_job_record("act") is not None

    def test_unknown_and_unconfirmed(self, isolated_db):
        from lib.errors import InvalidInputError
        with pytest.raises(NotFoundError):
            jobs_service.delete_job("nope", confirm=True)
        db.save_job_record("f", status="error")
        with pytest.raises(InvalidInputError):
            jobs_service.delete_job("f")
        assert db.get_job_record("f") is not None

    def test_a_run_started_after_the_check_is_not_deleted(self, isolated_db, monkeypatch):
        """Between delete_job's "is it finished?" check and the delete, a new
        run of the same id starts: it must stay listed, running and
        cancellable."""
        import threading
        import background_jobs
        from jobs import job_launch
        from lib.errors import ConflictError
        _run_job_with_result("race", {})
        go = threading.Event()
        real = jobs_service._is_finished

        def finished_then_restarted(record):
            seen = real(record)
            job_launch.start_job("race", lambda: go.wait(5))
            return seen

        monkeypatch.setattr(jobs_service, "_is_finished", finished_then_restarted)
        try:
            with pytest.raises(ConflictError):
                jobs_service.delete_job("race", confirm=True)
            assert background_jobs.get_status("race")["status"] == "running"
            assert db.get_job_record("race")["status"] == "running"
        finally:
            go.set()
            _wait_until_finished("race")

    def test_a_run_that_also_finished_after_the_check_keeps_its_result(self, isolated_db,
                                                                        monkeypatch):
        import time
        import background_jobs
        from lib.errors import ConflictError
        _run_job_with_result("race3", {"run": 1})
        real = jobs_service._is_finished

        def finished_then_rerun(record):
            seen = real(record)
            time.sleep(0.01)
            _run_job_with_result("race3", {"run": 2})
            return seen

        monkeypatch.setattr(jobs_service, "_is_finished", finished_then_rerun)
        with pytest.raises(ConflictError):
            jobs_service.delete_job("race3", confirm=True)
        assert background_jobs.get_status("race3")["result"] == {"run": 2}
        assert db.get_job_record("race3") is not None

    def test_clear_finished_skips_a_run_restarted_after_the_check(self, isolated_db, monkeypatch):
        import threading
        import background_jobs
        from jobs import job_launch
        _run_job_with_result("race2", {})
        go = threading.Event()
        real = jobs_service._is_finished

        def finished_then_restarted(record):
            seen = real(record)
            job_launch.start_job("race2", lambda: go.wait(5))
            return seen

        monkeypatch.setattr(jobs_service, "_is_finished", finished_then_restarted)
        try:
            assert jobs_service.clear_finished_jobs(confirm=True) == {"deleted_count": 0}
            assert background_jobs.get_status("race2")["status"] == "running"
            assert db.get_job_record("race2")["status"] == "running"
        finally:
            go.set()
            _wait_until_finished("race2")

    def test_clear_finished_leaves_active(self, isolated_db):
        for jid, st in (("a", "done"), ("b", "error"), ("c", "cancelled"),
                        ("d", "running"), ("e", "queued")):
            db.save_job_record(jid, status=st)
        assert jobs_service.clear_finished_jobs(confirm=True) == {"deleted_count": 3}
        assert {r["job_id"] for r in db.list_job_records()} == {"d", "e"}


class TestJobKindAndDramaId:
    def test_every_drama_prefix_has_a_kind(self):
        import background_jobs
        assert set(jobs_service.JOB_KIND_BY_PREFIX) == set(background_jobs.DRAMA_JOB_PREFIXES)

    @pytest.mark.parametrize("prefix", sorted(jobs_service.JOB_KIND_BY_PREFIX))
    def test_prefix_table(self, prefix):
        expected = jobs_service.JOB_KIND_BY_PREFIX[prefix]
        assert expected in ("transcribe", "translate", "align", "dub", "export",
                            "review", "import", "other")
        assert jobs_service.job_kind(f"{prefix}12") == expected

    @pytest.mark.parametrize("job_id, kind", [
        ("translate_5", "translate"), ("bulk_translate_5", "translate"),
        ("retranscribe_5", "transcribe"), ("transcribe_5", "transcribe"),
        ("fixflag_5", "review"), ("burned_video_5", "export"),
        ("dubbed_video_5", "export"),
        ("sourceimport_5", "import"), ("resplit_5", "align"),
    ])
    def test_known_ids(self, job_id, kind):
        assert jobs_service.job_kind(job_id) == kind

    @pytest.mark.parametrize("job_id", ["mystery_5", "translate_", "translate_x", "xtranslate_5",
                                        "discover_bulk_extract", "", None])
    def test_unknown_ids_are_other(self, job_id):
        assert jobs_service.job_kind(job_id) == "other"

    def test_record_carries_drama_id_and_kind(self, isolated_db):
        db.save_job_record("translate_7", status="done")
        db.save_job_record("mystery_7", status="done")
        by_id = {j["job_id"]: j for j in jobs_service.list_jobs()}
        assert (by_id["translate_7"]["drama_id"], by_id["translate_7"]["kind"]) == (7, "translate")
        assert (by_id["mystery_7"]["drama_id"], by_id["mystery_7"]["kind"]) == (None, "other")

    def test_schema_round_trip(self, isolated_db):
        from api.schemas import JobRecord
        db.save_job_record("dub_9", status="running")
        data = JobRecord(**jobs_service.get_job("dub_9")).model_dump(mode="json")
        assert (data["drama_id"], data["kind"]) == (9, "dub")
        assert JobRecord(**data).model_dump(mode="json") == data
        legacy = JobRecord(job_id="x", status="done", updated_at=1.0)
        assert (legacy.drama_id, legacy.kind) == (None, "other")


class TestJobPage:
    @pytest.mark.parametrize("prefix", sorted(jobs_service.JOB_KIND_BY_PREFIX))
    def test_every_drama_prefix_is_a_title_job(self, prefix):
        assert jobs_service.job_page(f"{prefix}12") == "title"

    @pytest.mark.parametrize("job_id, page", [
        ("sources_search", "sources"), ("sources_series_nyaa", "sources"),
        ("sources_url_preview", "sources"), ("sources_save", "sources"),
        ("sources_signin_site", "sources"), ("sources_tiertest_site", "sources"),
        ("sources_url_preflight", "sources"), ("sources_url_identify", "sources"),
        ("sources_chapter_check", "sources"),
        ("discover_bulk_extract", "discover"), ("discover_navigation_help", "discover"),
        ("live_0123abcd", "live"),
        ("library_backup", "settings"), ("library_db_backup", "settings"),
        ("library_user_backup", "settings"), ("library_auto_backup", "settings"),
        ("deno_install", "diagnostics"), ("upgrade_check", "diagnostics"),
        ("benchmark_lab", None), ("bulk_series_translate", None),
        ("library_export_zip", None), ("mystery_5", None), ("translate_x", None), ("", None),
        (None, None),
    ])
    def test_ids(self, job_id, page):
        assert jobs_service.job_page(job_id) == page

    def test_record_and_schema_carry_page_only(self, isolated_db):
        from api.schemas import JobRecord
        db.save_job_record("sources_search", status="done")
        db.save_job_record("translate_7", status="done")
        by_id = {j["job_id"]: j for j in jobs_service.list_jobs()}
        assert by_id["sources_search"]["page"] == "sources"
        assert by_id["translate_7"]["page"] == "title"
        data = JobRecord(**by_id["sources_search"]).model_dump(mode="json")
        assert data["page"] == "sources" and JobRecord(**data).model_dump(mode="json") == data
        assert JobRecord(job_id="x", status="done", updated_at=1.0).page is None


def test_cancel_reaches_another_processes_run_past_a_finished_one_here(isolated_db):
    """A member's run of this id finished here; the CLI (another process,
    no starter) now runs it. Cancelling the CLI's run must flag its row,
    not be refused because the finished run here had another starter."""
    from jobs import job_table
    from jobs import job_store
    job_id = "translate_77"
    with job_table._lock:
        job_table._jobs[job_id] = {"status": "done", "owner_user_id": 5,
                                         "started_at": 1.0, "finished_at": 2.0}
    try:
        db.save_job_record(job_id, "running", started_at=3.0, owner_user_id=None,
                           owner_pid=os.getppid())
        out = jobs_service.cancel_job(job_id, principal=None)
        assert out["cancel_requested"] is True
        assert db.get_job_record(job_id)["cancel_requested"]
        assert job_store.row_cancel_time(job_id) is not None
    finally:
        with job_table._lock:
            job_table._jobs.pop(job_id, None)


class TestListJobsReadsTheRecordsOnce:
    """GET /api/jobs is polled while the Jobs page is open: one job_records
    scan per call, whatever the job count, and none for the stale sweep
    when nothing is queued or running."""

    @staticmethod
    def _count_scans(monkeypatch):
        scans = []
        real = db.list_job_records
        monkeypatch.setattr(db, "list_job_records", lambda *a: (scans.append(1), real(*a))[1])
        return scans

    def test_finished_jobs_cost_one_scan_and_no_sweep(self, isolated_db, monkeypatch):
        from jobs import job_store
        for n in range(25):
            db.save_job_record(f"j{n}", status="done")
        swept = []
        real_sweep = job_store.sweep_dead_owners
        monkeypatch.setattr(job_store, "sweep_dead_owners",
                            lambda *a, **k: (swept.append(1), real_sweep(*a, **k))[1])
        scans = self._count_scans(monkeypatch)
        assert len(jobs_service.list_jobs()) == 25
        assert scans == [1] and swept == []

    def test_a_live_running_job_is_swept_from_the_same_scan(self, isolated_db, monkeypatch):
        import threading
        import background_jobs
        from jobs import job_launch, job_store
        gate = threading.Event()
        assert job_launch.start_job("live_one", gate.wait, 10)
        try:
            for n in range(10):
                db.save_job_record(f"old{n}", status="done")
            scanned, swept = [], []
            real_scan, real_sweep = db.list_job_records, job_store.sweep_dead_owners
            monkeypatch.setattr(db, "list_job_records",
                                lambda *a: scanned.append(real_scan(*a)) or scanned[-1])
            monkeypatch.setattr(job_store, "sweep_dead_owners", lambda stale, records=None: (
                swept.append(records), real_sweep(stale, records=records))[1])
            listed = {j["job_id"]: j["status"] for j in jobs_service.list_jobs()}
            assert listed["live_one"] == "running" and len(listed) == 11
            assert len(scanned) == 1 and len(swept) == 1 and swept[0] is scanned[0]
        finally:
            gate.set()
            background_jobs.clear_job("live_one")

    def test_a_job_whose_worker_died_does_not_list_as_running(self, isolated_db):
        """The sweep marks this process's lost worker failed after the rows
        were read, so the list reads them again."""
        import threading
        import background_jobs
        from jobs import job_launch, job_runner, job_table
        release = threading.Event()
        assert job_launch.start_job("lost_one", release.wait, 10)
        try:
            dead = threading.Thread(target=lambda: None)
            dead.start()
            dead.join()
            with job_table._lock:
                job_table._workers["lost_one"] = (job_table._jobs["lost_one"], dead)
            assert db.get_job_record("lost_one")["status"] == "running"
            job = {j["job_id"]: j for j in jobs_service.list_jobs()}["lost_one"]
            assert (job["status"], job["error"]) == ("error", job_runner.WORKER_LOST_MESSAGE)
        finally:
            release.set()
            assert job_runner.wait_for_job_threads(5.0)
            background_jobs.clear_job("lost_one")

    def test_a_row_another_poll_closed_after_this_read_is_not_listed_running(
            self, isolated_db, monkeypatch):
        """Two polls at once: this one reads the rows, the other's sweep closes
        a dead owner's row, and this one's sweep then finds nothing to close.
        It must still not list the row as running."""
        import subprocess
        import sys
        from jobs import job_store, job_table
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait(timeout=30)
        db.save_job_record("dead_two", "running", owner_pid=gone.pid)
        real_scan = db.list_job_records
        scans = []

        def scan_then_other_poll_sweeps(*a):
            rows = real_scan(*a)
            scans.append(1)
            if len(scans) == 1:
                assert job_store.sweep_dead_owners(job_table.STALE_JOB_SECONDS) == 1
            return rows
        monkeypatch.setattr(db, "list_job_records", scan_then_other_poll_sweeps)
        job = jobs_service.list_jobs()[0]
        assert (job["job_id"], job["status"]) == ("dead_two", "cancelled")

    def test_a_worker_another_poll_reconciled_after_this_read_is_not_listed_running(
            self, isolated_db, monkeypatch):
        import threading
        import background_jobs
        from jobs import job_launch, job_runner, job_table
        release = threading.Event()
        assert job_launch.start_job("lost_two", release.wait, 10)
        try:
            dead = threading.Thread(target=lambda: None)
            dead.start()
            dead.join()
            real_scan = db.list_job_records
            scans = []

            def scan_then_other_poll_reconciles(*a):
                rows = real_scan(*a)
                if not scans:
                    with job_table._lock:
                        job_table._workers["lost_two"] = (job_table._jobs["lost_two"], dead)
                    assert job_runner.reconcile_dead_workers() == ["lost_two"]
                scans.append(1)
                return rows
            monkeypatch.setattr(db, "list_job_records", scan_then_other_poll_reconciles)
            job = {j["job_id"]: j for j in jobs_service.list_jobs()}["lost_two"]
            assert job["status"] == "error"
        finally:
            release.set()
            assert job_runner.wait_for_job_threads(5.0)
            background_jobs.clear_job("lost_two")

    def test_a_dead_owners_row_still_lists_as_interrupted(self, isolated_db, monkeypatch):
        """The sweep closing a row is the one case that reads the rows again,
        so the list never shows a row the sweep just closed as running."""
        import subprocess
        import sys
        import background_jobs
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait(timeout=30)
        db.save_job_record("dead_one", "running", owner_pid=gone.pid)
        scans = self._count_scans(monkeypatch)
        job = jobs_service.list_jobs()[0]
        assert (job["job_id"], job["status"]) == ("dead_one", "cancelled")
        assert job["error"] == background_jobs.INTERRUPTED_MESSAGE
        assert len(scans) == 2

    def test_the_account_names_are_read_once_per_list_and_still_redacted(
            self, isolated_db, monkeypatch):
        import getpass
        for n in range(20):
            db.save_job_record(f"e{n}", status="error", error=f"run {n} failed for zoya",
                               message="zoya was here")
        calls = []
        monkeypatch.setattr(getpass, "getuser", lambda: calls.append(1) or "zoya")
        jobs = jobs_service.list_jobs()
        assert len(jobs) == 20 and len(calls) == 1
        assert all("zoya" not in (j["error"] + j["message"]) for j in jobs)
        # Outside a list, each redaction reads the names afresh.
        monkeypatch.setattr(getpass, "getuser", lambda: calls.append(1) or "yuki")
        assert jobs_service.get_job("e0")["message"] == "zoya was here"


def test_an_unchanged_job_list_is_not_swept_again_at_once(isolated_db, monkeypatch):
    """GET /api/jobs is polled: rows unchanged since a sweep that closed
    nothing are not probed again until they change."""
    sweeps = []
    monkeypatch.setattr(jobs_service, "_quiet_sweep", (None, 0.0))
    monkeypatch.setattr(jobs_service, "sweep_stale_job_records",
                        lambda records=None: sweeps.append(1) or 0)
    db.save_job_record("elsewhere", status="running", owner_pid=os.getppid())
    jobs_service.list_jobs()
    jobs_service.list_jobs()
    assert len(sweeps) == 1
    db.save_job_record("elsewhere", status="running", progress=0.5, owner_pid=os.getppid())
    jobs_service.list_jobs()
    assert len(sweeps) == 2
    monkeypatch.setattr(jobs_service, "_SWEEP_REPEAT_SECONDS", 0.0)
    jobs_service.list_jobs()
    assert len(sweeps) == 3


def test_polled_job_list_caps_finished_rows_but_keeps_active_ones(isolated_db, monkeypatch):
    monkeypatch.setattr(jobs_service, "_LISTED_FINISHED_JOBS", 2)
    isolated_db.save_job_record("recent-old-active", "running", started_at=1.0,
                                owner_pid=os.getpid())
    for n in range(4):
        isolated_db.save_job_record(f"done-{n}", "done", started_at=10.0 + n, finished_at=20.0 + n)
    listed = {j["job_id"] for j in jobs_service.list_jobs()}
    assert {"done-3", "done-2"} <= listed and not {"done-0", "done-1"} & listed
    assert len(isolated_db.list_job_records()) == 5
