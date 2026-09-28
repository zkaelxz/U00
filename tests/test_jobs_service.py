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

    def test_result_field_is_never_present(self, isolated_db):
        # job_records has no result column at all -- confirms the schema
        # itself, not just the service, keeps arbitrary job output out.
        db.save_job_record("j1", status="done")
        assert "result" not in jobs_service.list_jobs()[0]


class TestGetJob:
    def test_returns_a_recorded_job(self, isolated_db):
        db.save_job_record("j1", status="queued")
        assert jobs_service.get_job("j1")["status"] == "queued"

    def test_raises_not_found_for_an_unknown_job(self, isolated_db):
        with pytest.raises(NotFoundError):
            jobs_service.get_job("nope")
