"""
Tests for services/diagnostics_service.py -- Migration Slice 5's
read-only Diagnostics overview, shared by the FastAPI /api/diagnostics
route and (later) the Streamlit Diagnostics tab.

No admin action is exposed by this service (install/upgrade/delete stay
Streamlit-only) -- these tests exist to confirm the overview's shape and
that job/log data is redacted before leaving the service, the same
scope guarantee tests/test_reader_service.py checks for Slice 4.
"""

import background_jobs
from services import diagnostics_service


def test_overview_has_every_expected_section(isolated_db):
    overview = diagnostics_service.get_diagnostics_overview()
    assert set(overview) == {
        "dependencies", "file_completeness", "library_writable", "gpu",
        "model_engine_versions", "running_jobs", "recent_log_lines",
    }
    assert isinstance(overview["dependencies"], dict)
    assert isinstance(overview["model_engine_versions"], list)
    assert isinstance(overview["running_jobs"], list)
    assert isinstance(overview["recent_log_lines"], list)


def test_library_writable_is_true_for_a_real_isolated_library(isolated_db):
    assert diagnostics_service.get_diagnostics_overview()["library_writable"] is True


def test_running_jobs_reflects_real_job_state(isolated_db):
    background_jobs.start_job("diag-svc-test-job", lambda: None, description="test job")
    overview = diagnostics_service.get_diagnostics_overview()
    job_ids = [j["job_id"] for j in overview["running_jobs"]]
    assert "diag-svc-test-job" in job_ids or not background_jobs.is_running("diag-svc-test-job")
    background_jobs.clear_job("diag-svc-test-job")


def test_job_message_and_error_are_redacted(isolated_db, monkeypatch):
    fake_job = {
        "status": "running", "progress": 0.5,
        "message": "calling with key sk-ABCDEFGHIJKLMNOP12345", "error": None,
        "description": "d", "started_at": 0.0, "finished_at": None,
        "cancel_requested": False, "result": None, "gpu_touching": False, "kind": "thread",
    }
    monkeypatch.setattr(background_jobs, "list_running_jobs", lambda: {"j1": fake_job})
    overview = diagnostics_service.get_diagnostics_overview()
    assert len(overview["running_jobs"]) == 1
    assert "sk-ABCDEFGHIJKLMNOP12345" not in overview["running_jobs"][0]["message"]


def test_result_field_is_never_exposed(isolated_db, monkeypatch):
    fake_job = {
        "status": "finished", "progress": 1.0, "message": "done", "error": None,
        "description": "d", "started_at": 0.0, "finished_at": 1.0,
        "cancel_requested": False, "result": {"secret": "should never leave the service"},
        "gpu_touching": False, "kind": "thread",
    }
    monkeypatch.setattr(background_jobs, "list_running_jobs", lambda: {"j1": fake_job})
    overview = diagnostics_service.get_diagnostics_overview()
    assert "result" not in overview["running_jobs"][0]
