"""
Tests for services/jobs_service.py -- Migration Slice 8's read-only,
cross-process job list, reading db.job_records (Migration Slice 7's
mirror) rather than background_jobs.py's own in-memory state.
"""

import db
from services import jobs_service
from services.service_errors import NotFoundError

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

    def test_done_job_without_result_is_ok(self, isolated_db):
        db.save_job_record("j1", status="done")
        job = jobs_service.list_jobs()[0]
        assert job["result"] is None
        assert job["outcome"] == "ok"


def _run_job_with_result(job_id, result):
    import time
    import background_jobs
    background_jobs.start_job(job_id, lambda: background_jobs.set_result(job_id, result))
    deadline = time.time() + 3
    while (background_jobs.get_status(job_id) or {}).get("status") != "done":
        assert time.time() < deadline
        time.sleep(0.01)


class TestJobResults:
    def test_failed_transcribe_result_is_failed_and_redacted(self, isolated_db):
        _run_job_with_result("transcribe_1", {
            "failed_reason": "model_download",
            "detail": "could not fetch /home/someone/models/large-v3 with key sk-ABCDEFGHIJKLMNOP12345"})
        job = jobs_service.get_job("transcribe_1")
        assert job["status"] == "done"
        assert job["outcome"] == "failed"
        assert "sk-ABCDEFGHIJKLMNOP12345" not in job["outcome_message"]
        assert "/home/someone" not in job["outcome_message"]
        assert "downloaded" in job["outcome_message"]

    def test_result_survives_a_restart(self, isolated_db):
        import background_jobs
        _run_job_with_result("translate_2", {"errors": ["batch 1: timeout"], "cap_reached": None,
                                             "lines_replaced": True})
        background_jobs._jobs.clear()  # the owning process is gone
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

    def test_cancelled_bulk_run_is_cancelled(self):
        assert jobs_service.derive_outcome("done", None, {"status": "cancelled"})[0] == "cancelled"

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
        from services.service_errors import ConflictError
        db.save_job_record("act", status=status)
        with pytest.raises(ConflictError):
            jobs_service.delete_job("act", confirm=True)
        assert db.get_job_record("act") is not None

    def test_unknown_and_unconfirmed(self, isolated_db):
        from services.service_errors import InvalidInputError
        with pytest.raises(NotFoundError):
            jobs_service.delete_job("nope", confirm=True)
        db.save_job_record("f", status="error")
        with pytest.raises(InvalidInputError):
            jobs_service.delete_job("f")
        assert db.get_job_record("f") is not None

    def test_clear_finished_leaves_active(self, isolated_db):
        for jid, st in (("a", "done"), ("b", "error"), ("c", "cancelled"),
                        ("d", "running"), ("e", "queued")):
            db.save_job_record(jid, status=st)
        assert jobs_service.clear_finished_jobs(confirm=True) == {"deleted_count": 3}
        assert {r["job_id"] for r in db.list_job_records()} == {"d", "e"}
