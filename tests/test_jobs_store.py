"""jobs/store.py: the invariants that keep a job's row in library.db honest.

Every transition is written once and the row then matches the job; a failed
write is visible (sync_error, the "could not be saved" suffix) and retried; a
row is closed by anyone but its worker only through one conditional UPDATE;
a server that exits, cleanly or not, leaves no row saying it still runs.
"""
import contextlib
import os
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

import background_jobs as bg
import db
from jobs import store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _finish(*job_ids):
    assert bg.wait_for_job_threads(5.0)
    for job_id in job_ids:
        bg.clear_job(job_id)


def _set_row(job_id, **cols):
    with contextlib.closing(db.get_conn()) as conn:
        for col, value in cols.items():
            conn.execute(f"UPDATE job_records SET {col} = ? WHERE job_id = ?", (value, job_id))
        conn.commit()


def _gpu_holders():
    with contextlib.closing(db.get_conn()) as conn:
        return {r[0] for r in conn.execute("SELECT holder FROM gpu_lock").fetchall()}


def _assert_row_matches_job(job_id):
    job, row = bg.get_status(job_id), db.get_job_record(job_id)
    for field in ("status", "progress", "message", "error"):
        assert row[field] == job.get(field), field


@pytest.fixture
def writes(isolated_db, monkeypatch):
    """(job id, status) of every save_job_record call."""
    calls = []
    real = db.save_job_record

    def spy(job_id, *a, **k):
        calls.append((job_id, k.get("status")))
        return real(job_id, *a, **k)
    monkeypatch.setattr(db, "save_job_record", spy)
    return calls


class TestEveryTransitionIsWrittenOnce:
    def test_running_then_done(self, writes):
        release = threading.Event()
        assert bg.start_job("st_done", lambda: release.wait(5.0), description="Store test")
        _assert_row_matches_job("st_done")
        row = db.get_job_record("st_done")
        assert (row["kind"], row["owner_instance"], row["detail_state"]) == (
            "thread", store.INSTANCE_ID, None)
        release.set()
        assert _wait_for(lambda: bg.get_status("st_done")["status"] == "done")
        _finish()
        assert [s for j, s in writes if j == "st_done"] == ["running", "done"]
        _assert_row_matches_job("st_done")
        bg.clear_job("st_done")

    def test_running_cancelling_then_cancelled(self, writes):
        def work():
            while not bg.is_cancel_requested("st_cancel"):
                time.sleep(0.01)
            raise bg.JobCancelled("st_cancel")
        assert bg.start_job("st_cancel", work)
        bg.request_cancel("st_cancel")
        _assert_row_matches_job("st_cancel")
        assert db.get_job_record("st_cancel")["cancel_requested_at"] == pytest.approx(
            bg.get_status("st_cancel")["cancel_requested_at"])
        assert _wait_for(lambda: bg.get_status("st_cancel")["status"] == "cancelled")
        _finish()
        # The second "running" write is the Cancelling... message, not a new status.
        assert [s for j, s in writes if j == "st_cancel"] == ["running", "running", "cancelled"]
        _assert_row_matches_job("st_cancel")
        row = db.get_job_record("st_cancel")
        assert row["cancel_requested_at"] is None and row["detail_state"] is None
        bg.clear_job("st_cancel")

    def test_queued_then_cancelled(self, writes):
        bg.set_gpu_limit_enabled(True)
        release = threading.Event()
        try:
            assert bg.start_job("st_gpu_a", lambda: release.wait(5.0), gpu_touching=True)
            assert bg.start_job("st_gpu_b", lambda: None, gpu_touching=True)
            assert bg.get_status("st_gpu_b")["status"] == "queued"
            _assert_row_matches_job("st_gpu_b")
            bg.request_cancel("st_gpu_b")
            _assert_row_matches_job("st_gpu_b")
        finally:
            release.set()
            _finish("st_gpu_a")
        assert [s for j, s in writes if j == "st_gpu_b"][-1] == "cancelled"
        assert [s for j, s in writes if j == "st_gpu_b"].count("cancelled") == 1
        bg.clear_job("st_gpu_b")


@pytest.fixture
def client(isolated_db):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


class TestAFailedWriteIsVisibleAndRetried:
    def test_failed_final_write_shows_on_the_job_and_heals_on_the_heartbeat(
            self, client, monkeypatch):
        real = db.save_job_record

        def fail_final(job_id, *a, **k):
            if k.get("status") == "done":
                raise sqlite3.OperationalError("disk I/O error, key sk-ant-SECRET1234567890abcdef")
            return real(job_id, *a, **k)
        monkeypatch.setattr(db, "save_job_record", fail_final)
        assert bg.start_job("st_fail", lambda: None)
        assert _wait_for(lambda: bg.get_status("st_fail")["status"] == "done")
        _finish()
        job = bg.get_status("st_fail")
        assert job["sync_error"] and "SECRET1234567890" not in job["sync_error"]
        assert job["detail_state"] == "unknown"
        # The last written status stands, and the list says why.
        listed = {j["job_id"]: j for j in client.get("/api/jobs").json()["items"]}["st_fail"]
        assert listed["status"] == "running"
        assert "Job state could not be saved (" in listed["message"]
        assert "showing the in-memory state" in listed["message"]
        assert "SECRET1234567890" not in str(listed)
        assert "st_fail" in store._pending

        monkeypatch.setattr(db, "save_job_record", real)
        bg._heartbeat_once()
        job = bg.get_status("st_fail")
        assert "sync_error" not in job and "detail_state" not in job
        assert "st_fail" not in store._pending
        row = db.get_job_record("st_fail")
        assert row["status"] == "done" and row["sync_error"] is None
        listed = client.get("/api/jobs/st_fail").json()
        assert listed["status"] == "done"
        assert "could not be saved" not in (listed["message"] or "")
        bg.clear_job("st_fail")

    def test_the_failure_is_logged_once_and_redacted(self, isolated_db, monkeypatch):
        import applog
        logged = []
        monkeypatch.setattr(applog, "get_logger", lambda: type(
            "L", (), {"warning": lambda self, m, **k: logged.append(m),
                      "info": lambda self, m, **k: None,
                      "error": lambda self, m, **k: None})())

        def boom(*a, **k):
            raise sqlite3.OperationalError("disk I/O error sk-ant-SECRET1234567890abcdef")
        monkeypatch.setattr(db, "save_job_record", boom)
        assert bg.start_job("st_log", lambda: None)
        assert _wait_for(lambda: bg.get_status("st_log")["status"] == "done")
        _finish()
        with bg._lock:
            store.write_transition("st_log", bg._jobs["st_log"])
        mine = [m for m in logged if "st_log" in m and "could not save" in m]
        assert len(mine) == 1
        assert "SECRET1234567890" not in mine[0]
        bg.clear_job("st_log")
        bg._heartbeat_once()   # a cleared job's retry is dropped, never written
        assert "st_log" not in store._pending
        assert db.get_job_record("st_log") is None

    def test_database_is_locked_once_is_retried_and_lands(self, isolated_db, monkeypatch):
        real = db.save_job_record
        failed = []

        def locked_once(job_id, *a, **k):
            if not failed:
                failed.append(1)
                raise sqlite3.OperationalError("database is locked")
            return real(job_id, *a, **k)
        monkeypatch.setattr(db, "save_job_record", locked_once)
        assert bg.start_job("st_locked", lambda: None)
        assert _wait_for(lambda: bg.get_status("st_locked")["status"] == "done")
        _finish()
        assert failed == [1]
        assert "sync_error" not in bg.get_status("st_locked")
        assert db.get_job_record("st_locked")["status"] == "done"
        assert "st_locked" not in store._pending
        bg.clear_job("st_locked")


class TestRowsOfGoneOwners:
    def test_my_pid_with_another_instance_is_swept_a_live_other_pid_is_not(self, isolated_db):
        db.save_job_record("st_earlier", "running", owner_pid=os.getpid())
        _set_row("st_earlier", owner_instance="an-earlier-server")
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            db.save_job_record("st_other", "running", owner_pid=other.pid)
            _set_row("st_other", owner_instance="another-server")
            assert store.sweep_dead_owners(bg.STALE_JOB_SECONDS) == 1
            assert db.get_job_record("st_other")["status"] == "running"
        finally:
            other.kill()
            other.wait(timeout=10)
        row = db.get_job_record("st_earlier")
        assert (row["status"], row["detail_state"], row["error"]) == (
            "cancelled", "interrupted", bg.INTERRUPTED_MESSAGE)

    def test_a_close_loses_to_a_new_run_by_another_instance(self, isolated_db):
        db.save_job_record("st_race", "running", owner_pid=os.getpid())
        _set_row("st_race", owner_instance="an-earlier-server")
        seen = db.get_job_record("st_race")
        _set_row("st_race", owner_instance="a-new-server")   # a new run in between
        assert store.close_if_owner_gone(seen) is False
        assert db.get_job_record("st_race")["status"] == "running"

    def test_cancel_time_is_kept_and_does_not_refresh_staleness(self, isolated_db):
        db.save_job_record("st_flag", "running")
        _set_row("st_flag", updated_at=time.time() - 3600)
        before = db.get_job_record("st_flag")
        assert store.request_cancel("st_flag")
        first = db.get_job_record("st_flag")
        assert first["cancel_requested"] == 1 and first["cancel_requested_at"]
        assert first["updated_at"] == before["updated_at"]
        assert store.request_cancel("st_flag")
        assert db.get_job_record("st_flag")["cancel_requested_at"] == first["cancel_requested_at"]

    def test_force_stop_is_offered_from_the_row_when_the_job_is_not_live_here(self, isolated_db):
        from services import jobs_service
        db.save_job_record("st_hung", "running", owner_pid=os.getppid())
        _set_row("st_hung", kind="thread", cancel_requested_at=time.time() - 5)
        assert jobs_service.get_job("st_hung")["can_force_stop"] is False
        _set_row("st_hung", cancel_requested_at=time.time() - 90)
        out = jobs_service.get_job("st_hung")
        assert out["can_force_stop"] is True
        for internal in ("owner_pid", "owner_instance", "detail_state", "sync_error",
                         "cancel_requested_at"):
            assert internal not in out
        assert out["kind"] == jobs_service.job_kind("st_hung")


class TestExitFlush:
    def test_a_running_row_is_closed_as_interrupted_and_its_gpu_slot_freed(self, isolated_db):
        bg.set_gpu_limit_enabled(True)
        release = threading.Event()
        assert bg.start_job("st_exit", lambda: release.wait(5.0), gpu_touching=True)
        try:
            assert "ui:st_exit" in _gpu_holders()
            store.flush_at_exit()
            row = db.get_job_record("st_exit")
            assert (row["status"], row["detail_state"], row["error"]) == (
                "cancelled", "interrupted", bg.INTERRUPTED_MESSAGE)
            assert "ui:st_exit" not in _gpu_holders()
        finally:
            release.set()
            _finish("st_exit")

    def test_flush_retries_a_pending_write_first(self, isolated_db, monkeypatch):
        real = db.save_job_record

        def fail_final(job_id, *a, **k):
            if k.get("status") == "done":
                raise sqlite3.OperationalError("disk I/O error")
            return real(job_id, *a, **k)
        monkeypatch.setattr(db, "save_job_record", fail_final)
        assert bg.start_job("st_exit_pending", lambda: None)
        assert _wait_for(lambda: bg.get_status("st_exit_pending")["status"] == "done")
        _finish()
        monkeypatch.setattr(db, "save_job_record", real)
        store.flush_at_exit()
        assert db.get_job_record("st_exit_pending")["status"] == "done"
        assert "st_exit_pending" not in store._pending
        bg.clear_job("st_exit_pending")


_OWNER_SCRIPT = r"""
import os, sys, threading
sys.path.insert(0, sys.argv[2])
import db, background_jobs as bg
db.configure_library_dir(sys.argv[1])
bg.start_job("st_owner", lambda: threading.Event().wait(60))
assert db.get_job_record("st_owner")["status"] == "running"
if sys.argv[3] == "kill":
    os._exit(1)   # what SIGKILL or a crash leaves: no atexit
"""


@pytest.mark.parametrize("how", ["exit", "kill"])
def test_an_exited_server_leaves_no_running_row(isolated_db, how):
    """A clean exit closes the row itself (atexit); a hard kill skips atexit,
    so the next start's sweep closes it. Either way it ends interrupted."""
    proc = subprocess.run([sys.executable, "-c", _OWNER_SCRIPT, db.LIBRARY_DIR, REPO, how],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == (1 if how == "kill" else 0), proc.stderr
    row = db.get_job_record("st_owner")
    if how == "kill":
        assert row["status"] == "running"
        assert store.sweep_dead_owners(bg.STALE_JOB_SECONDS) == 1
        row = db.get_job_record("st_owner")
    assert (row["status"], row["detail_state"], row["error"]) == (
        "cancelled", "interrupted", bg.INTERRUPTED_MESSAGE)


@pytest.mark.parametrize("module", ["jobs", "jobs.store"])
def test_each_jobs_module_imports_alone(module):
    proc = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=REPO,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
