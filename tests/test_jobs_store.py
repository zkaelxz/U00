"""jobs/job_store.py: the invariants that keep a job's row in library.db honest.

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
from jobs import job_store

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
            "thread", job_store.INSTANCE_ID, None)
        release.set()
        assert _wait_for(lambda: bg.get_status("st_done")["status"] == "done")
        _finish()
        assert [s for j, s in writes if j == "st_done"] == ["running", "done"]
        _assert_row_matches_job("st_done")
        assert "st_done" not in job_store._last_write
        bg.clear_job("st_done")

    def test_running_cancelling_then_cancelled(self, writes):
        heard, stop = threading.Event(), threading.Event()

        def work():
            while not bg.is_cancel_requested("st_cancel"):
                time.sleep(0.01)
            heard.set()
            stop.wait(5.0)
            raise bg.JobCancelled("st_cancel")
        assert bg.start_job("st_cancel", work)
        bg.request_cancel("st_cancel")
        assert heard.wait(5.0)
        _assert_row_matches_job("st_cancel")
        assert db.get_job_record("st_cancel")["cancel_requested_at"] == pytest.approx(
            bg.get_status("st_cancel")["cancel_requested_at"])
        stop.set()
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
        assert "st_fail" in job_store._pending

        monkeypatch.setattr(db, "save_job_record", real)
        bg._heartbeat_once()
        job = bg.get_status("st_fail")
        assert "sync_error" not in job and "detail_state" not in job
        assert "st_fail" not in job_store._pending
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
            job_store.write_transition("st_log", bg._jobs["st_log"])
        mine = [m for m in logged if "st_log" in m and "could not save" in m]
        assert len(mine) == 1
        assert "SECRET1234567890" not in mine[0]
        bg.clear_job("st_log")
        bg._heartbeat_once()   # a cleared job's retry is dropped, never written
        assert "st_log" not in job_store._pending
        assert db.get_job_record("st_log") is None

    def test_database_is_locked_once_is_retried_and_lands(self, isolated_db, monkeypatch):
        real = db.save_job_record
        failed = []

        def locked_once(job_id, *a, **k):
            if k.get("status") == "done" and not failed:
                failed.append(1)
                raise sqlite3.OperationalError("database is locked")
            return real(job_id, *a, **k)
        monkeypatch.setattr(db, "save_job_record", locked_once)
        assert bg.start_job("st_locked", lambda: None)
        assert _wait_for(lambda: bg.get_status("st_locked")["status"] == "done")
        _finish()
        # One try under background_jobs._lock; the retry runs outside it.
        assert failed == [1] and "st_locked" in job_store._pending
        bg._heartbeat_once()
        assert "sync_error" not in bg.get_status("st_locked")
        assert db.get_job_record("st_locked")["status"] == "done"
        assert "st_locked" not in job_store._pending
        bg.clear_job("st_locked")


class TestAWriteUnderTheJobsLockNeverWaitsLong:
    @pytest.mark.parametrize("failure", ["locked", "disk"])
    def test_a_database_held_by_another_connection_fails_fast(
            self, isolated_db, monkeypatch, failure):
        release = threading.Event()
        assert bg.start_job("st_held", lambda: release.wait(10.0))
        if failure == "disk":
            # A non-lock failure must not follow up with its own write under the lock.
            def boom(*a, **k):
                raise sqlite3.OperationalError("disk I/O error")
            monkeypatch.setattr(db, "save_job_record", boom)
        holding, done = threading.Event(), threading.Event()

        def hold():
            with contextlib.closing(db.get_conn()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                holding.set()
                done.wait(10.0)
                conn.rollback()
        holder = threading.Thread(target=hold)
        holder.start()
        try:
            assert holding.wait(5.0)
            t0 = time.monotonic()
            with bg._lock:
                ok = job_store.write_transition("st_held", bg._jobs["st_held"])
            assert time.monotonic() - t0 < 2.0
            assert ok is False
            assert bg.get_status("st_held")["sync_error"]
            assert "st_held" in job_store._pending
        finally:
            done.set()
            holder.join(10)
            monkeypatch.undo()
            release.set()
            _finish()
        bg._heartbeat_once()
        assert "st_held" not in job_store._pending
        bg.clear_job("st_held")


class TestRowsOfGoneOwners:
    def test_my_pid_with_another_instance_is_swept_a_live_other_pid_is_not(self, isolated_db):
        db.save_job_record("st_earlier", "running", owner_pid=os.getpid())
        _set_row("st_earlier", owner_instance="an-earlier-server")
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            db.save_job_record("st_other", "running", owner_pid=other.pid)
            _set_row("st_other", owner_instance="another-server")
            assert job_store.sweep_dead_owners(bg.STALE_JOB_SECONDS) == 1
            assert db.get_job_record("st_other")["status"] == "running"
        finally:
            other.kill()
            other.wait(timeout=10)
        row = db.get_job_record("st_earlier")
        assert (row["status"], row["detail_state"], row["error"]) == (
            "cancelled", "interrupted", bg.INTERRUPTED_MESSAGE)

    def test_my_own_row_with_a_write_still_pending_is_not_closed(self, isolated_db):
        """A job cleared from memory while its last write is owed: the row
        is this process's to write, not an earlier server's to close."""
        db.save_job_record("st_owed", "running", owner_pid=os.getpid())
        _set_row("st_owed", owner_instance=job_store.INSTANCE_ID)
        with bg._lock:
            job_store._pending.add("st_owed")
        try:
            assert job_store.owner_gone(db.get_job_record("st_owed")) is False
            assert job_store.sweep_dead_owners(bg.STALE_JOB_SECONDS) == 0
            assert db.get_job_record("st_owed")["status"] == "running"
        finally:
            with bg._lock:
                job_store._pending.discard("st_owed")
        assert job_store.close_if_owner_gone(db.get_job_record("st_owed")) is True

    def test_a_close_loses_to_a_new_run_by_another_instance(self, isolated_db):
        db.save_job_record("st_race", "running", owner_pid=os.getpid())
        _set_row("st_race", owner_instance="an-earlier-server")
        seen = db.get_job_record("st_race")
        _set_row("st_race", owner_instance="a-new-server")   # a new run in between
        assert job_store.close_if_owner_gone(seen) is False
        assert db.get_job_record("st_race")["status"] == "running"

    def test_cancel_time_is_kept_and_does_not_refresh_staleness(self, isolated_db):
        db.save_job_record("st_flag", "running")
        _set_row("st_flag", updated_at=time.time() - 3600)
        before = db.get_job_record("st_flag")
        assert job_store.request_cancel("st_flag")
        first = db.get_job_record("st_flag")
        assert first["cancel_requested"] == 1 and first["cancel_requested_at"]
        assert first["updated_at"] == before["updated_at"]
        assert job_store.request_cancel("st_flag")
        assert db.get_job_record("st_flag")["cancel_requested_at"] == first["cancel_requested_at"]

    def test_force_stop_is_not_offered_for_a_job_not_live_here(self, isolated_db):
        """force_stop knows only this process's jobs: offering it for another
        process's job would answer 409."""
        from services import jobs_service
        db.save_job_record("st_hung", "running", owner_pid=os.getppid())
        _set_row("st_hung", kind="thread", cancel_requested_at=time.time() - 90)
        out = jobs_service.get_job("st_hung")
        assert out["can_force_stop"] is False
        for internal in ("owner_pid", "owner_instance", "detail_state", "sync_error",
                         "cancel_requested_at"):
            assert internal not in out
        assert out["kind"] == jobs_service.job_kind("st_hung")

    def test_a_sweep_between_the_two_writes_cannot_close_a_new_run(self, isolated_db, monkeypatch):
        db.save_job_record("st_mid", "running", owner_pid=os.getpid())
        _set_row("st_mid", owner_instance="an-earlier-server")
        seen = db.get_job_record("st_mid")
        real = db.save_job_record
        sweeps = []

        def sweep_in_between(job_id, *a, **k):
            real(job_id, *a, **k)
            if job_id == "st_mid" and not sweeps:
                t = threading.Thread(
                    target=lambda: sweeps.append(job_store.close_if_owner_gone(seen)))
                sweeps.append(t)
                t.start()
                time.sleep(0.2)   # the sweep's UPDATE is ready to run now
        monkeypatch.setattr(db, "save_job_record", sweep_in_between)
        release = threading.Event()
        assert bg.start_job("st_mid", lambda: release.wait(5.0))
        try:
            sweeps[0].join(10)
            assert sweeps[1:] == [False]
            row = db.get_job_record("st_mid")
            assert (row["status"], row["owner_instance"]) == ("running", job_store.INSTANCE_ID)
        finally:
            release.set()
            _finish("st_mid")

    def test_the_requesters_cancel_time_stands_and_the_owner_adopts_it(self, isolated_db):
        release = threading.Event()
        assert bg.start_job("st_adopt", lambda: release.wait(5.0))
        try:
            asked = time.time() - 30
            _set_row("st_adopt", cancel_requested=1, cancel_requested_at=asked)
            bg._last_db_cancel_check.clear()
            assert bg.is_cancel_requested("st_adopt")
            assert bg.get_status("st_adopt")["cancel_requested_at"] == pytest.approx(asked)
            with bg._lock:
                bg._jobs["st_adopt"]["cancel_requested_at"] = time.time()
                job_store.write_transition("st_adopt", bg._jobs["st_adopt"])
            assert db.get_job_record("st_adopt")["cancel_requested_at"] == pytest.approx(asked)
        finally:
            release.set()
            _finish("st_adopt")


class TestRetriesNeverHoldTheJobsLock:
    def test_a_blocked_retry_never_blocks_get_status(self, isolated_db, monkeypatch):
        real = db.save_job_record

        def fail_final(job_id, *a, **k):
            if k.get("status") == "done":
                raise sqlite3.OperationalError("disk I/O error")
            return real(job_id, *a, **k)
        monkeypatch.setattr(db, "save_job_record", fail_final)
        assert bg.start_job("st_slow", lambda: None)
        assert _wait_for(lambda: bg.get_status("st_slow")["status"] == "done")
        _finish()
        assert "st_slow" in job_store._pending
        blocked = []

        def locked_for_2s(job_id, *a, **k):
            if not blocked:
                blocked.append(1)
                time.sleep(2.0)
                raise sqlite3.OperationalError("database is locked")
            return real(job_id, *a, **k)
        monkeypatch.setattr(db, "save_job_record", locked_for_2s)
        beat = threading.Thread(target=bg._heartbeat_once)
        beat.start()
        assert _wait_for(lambda: blocked)
        worst = 0.0
        while beat.is_alive():
            t0 = time.monotonic()
            bg.get_status("st_slow")
            worst = max(worst, time.monotonic() - t0)
            time.sleep(0.005)
        beat.join()
        assert worst < 0.05
        assert db.get_job_record("st_slow")["status"] == "done"
        assert "st_slow" not in job_store._pending
        bg.clear_job("st_slow")

    def test_a_retry_that_raced_a_new_transition_is_written_again(self, isolated_db, monkeypatch):
        real = db.save_job_record
        release = threading.Event()
        assert bg.start_job("st_race2", lambda: release.wait(5.0))
        with bg._lock:
            job_store._pending.add("st_race2")
        moved = []

        def transition_meanwhile(job_id, *a, **k):
            real(job_id, *a, **k)
            if not moved:
                moved.append(1)
                with bg._lock:   # what a progress message change does
                    bg._jobs["st_race2"]["message"] = "newer"
                    job_store.write_transition("st_race2", bg._jobs["st_race2"])
                real(job_id, *a, **k)   # the older copy lands last
        monkeypatch.setattr(db, "save_job_record", transition_meanwhile)
        try:
            job_store.retry_pending()
            assert db.get_job_record("st_race2")["message"] == "newer"
            assert "st_race2" not in job_store._pending
        finally:
            release.set()
            _finish("st_race2")


class TestExitFlush:
    def test_a_running_row_is_closed_as_interrupted_and_its_gpu_slot_freed(self, isolated_db):
        bg.set_gpu_limit_enabled(True)
        release = threading.Event()
        assert bg.start_job("st_exit", lambda: release.wait(5.0), gpu_touching=True)
        try:
            assert "ui:st_exit" in _gpu_holders()
            job_store.flush_at_exit()
            row = db.get_job_record("st_exit")
            assert (row["status"], row["detail_state"], row["error"]) == (
                "cancelled", "interrupted", bg.INTERRUPTED_MESSAGE)
            assert "ui:st_exit" not in _gpu_holders()
        finally:
            release.set()
            _finish("st_exit")

    def test_a_queued_gpu_jobs_slot_name_is_left_alone(self, isolated_db):
        bg.set_gpu_limit_enabled(True)
        release = threading.Event()
        assert bg.start_job("st_ex_a", lambda: release.wait(5.0), gpu_touching=True)
        assert bg.start_job("st_ex_b", lambda: None, gpu_touching=True)
        try:
            assert bg.get_status("st_ex_b")["status"] == "queued"
            with contextlib.closing(db.get_conn()) as conn:   # another process's job
                conn.execute("INSERT INTO gpu_lock VALUES (2, 'ui:st_ex_b', 'x', ?, ?)",
                             (time.time(), time.time()))
                conn.commit()
            job_store.flush_at_exit()
            assert "ui:st_ex_b" in _gpu_holders()
            assert "ui:st_ex_a" not in _gpu_holders()
        finally:
            release.set()
            _finish("st_ex_a")

    def test_flush_keeps_to_its_budget_on_a_locked_database(self, isolated_db, monkeypatch):
        real = db.save_job_record

        def fail_final(job_id, *a, **k):
            if k.get("status") == "done":
                raise sqlite3.OperationalError("disk I/O error")
            return real(job_id, *a, **k)
        monkeypatch.setattr(db, "save_job_record", fail_final)
        assert bg.start_job("st_ex_pending", lambda: None)
        assert _wait_for(lambda: bg.get_status("st_ex_pending")["status"] == "done")
        _finish()
        release = threading.Event()
        assert bg.start_job("st_ex_live", lambda: release.wait(5.0))

        class Locked:
            """Waits out its busy timeout (sqlite's 5 s unless set), then fails."""
            def __init__(self):
                self.wait = 5.0

            def execute(self, sql, *a):
                if sql.startswith("PRAGMA busy_timeout"):
                    self.wait = int(sql.rsplit("=", 1)[1]) / 1000
                    return
                time.sleep(self.wait)
                raise sqlite3.OperationalError("database is locked")

            def close(self):
                pass
        real_conn = db.get_conn
        monkeypatch.setattr(db, "get_conn", Locked)
        monkeypatch.setattr(job_store, "EXIT_FLUSH_SECONDS", 0.5)
        try:
            t0 = time.monotonic()
            job_store.flush_at_exit()
            assert time.monotonic() - t0 < 0.8
        finally:
            monkeypatch.setattr(db, "get_conn", real_conn)
            release.set()
            _finish("st_ex_live")
        assert "st_ex_pending" in job_store._pending
        bg.clear_job("st_ex_pending")

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
        job_store.flush_at_exit()
        assert db.get_job_record("st_exit_pending")["status"] == "done"
        assert "st_exit_pending" not in job_store._pending
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
        assert job_store.sweep_dead_owners(bg.STALE_JOB_SECONDS) == 1
        row = db.get_job_record("st_owner")
    assert (row["status"], row["detail_state"], row["error"]) == (
        "cancelled", "interrupted", bg.INTERRUPTED_MESSAGE)


@pytest.mark.parametrize("module", ["jobs", "jobs.job_store"])
def test_each_jobs_module_imports_alone(module):
    proc = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=REPO,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
