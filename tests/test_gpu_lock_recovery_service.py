import os
import threading
import time

import pytest

import background_jobs as bg
import db
from services import gpu_lock_recovery_service as recovery

DEAD_PID = 2 ** 22 + 12345


@pytest.fixture(autouse=True)
def _pid_alive(monkeypatch):
    # Only DEAD_PID counts as exited; every other pid is a running process.
    monkeypatch.setattr(bg, "owner_process_alive", lambda pid: pid != DEAD_PID)


def _holders():
    with db.get_conn() as conn:
        return {r["holder"] for r in conn.execute("SELECT holder FROM gpu_lock")}


def _ghost(job_id, pid=DEAD_PID, status="cancelled"):
    db.save_job_record(job_id, status, owner_pid=pid, gpu_touching=True)
    assert db.try_acquire_gpu_lock(f"ui:{job_id}", "private description")


def _age(holder, seconds):
    with db.get_conn() as conn:
        conn.execute("UPDATE gpu_lock SET heartbeat_at = heartbeat_at - ? WHERE holder = ?",
                     (seconds, holder))


class TestReleaseOrphanedServerHolders:
    def test_ghost_server_row_of_a_dead_owner_is_released(self, isolated_db):
        _ghost("live_old")
        assert recovery.release_orphaned_server_holders() == 1
        assert _holders() == set()
        assert db.try_acquire_gpu_lock("ui:next")

    def test_row_of_an_earlier_run_with_this_pid_is_released(self, isolated_db):
        _ghost("live_same_pid", pid=os.getpid())
        assert recovery.release_orphaned_server_holders() == 1

    def test_fresh_cli_holder_is_untouched(self, isolated_db):
        assert db.try_acquire_gpu_lock(f"cli:{DEAD_PID}", "CLI batch")
        assert recovery.release_orphaned_server_holders() == 0
        assert _holders() == {f"cli:{DEAD_PID}"}

    def test_server_row_owned_by_another_live_process_is_untouched(self, isolated_db):
        _ghost("live_other", pid=os.getpid() + 1, status="running")
        assert recovery.release_orphaned_server_holders() == 0
        assert _holders() == {"ui:live_other"}

    def test_row_without_a_record_is_left_to_expire(self, isolated_db):
        db.try_acquire_gpu_lock("ui:no_record")
        assert recovery.release_orphaned_server_holders() == 0

    def test_row_whose_record_has_no_pid_is_left_to_expire(self, isolated_db):
        _ghost("no_pid", pid=None)
        assert recovery.release_orphaned_server_holders() == 0

    def test_stale_rows_still_expire_as_before(self, isolated_db):
        db.try_acquire_gpu_lock("cli:1")
        _age("cli:1", db.GPU_LOCK_STALE_SECONDS + 1)
        assert recovery.release_orphaned_server_holders() == 0
        assert db.try_acquire_gpu_lock("ui:next")

    def test_a_live_job_of_this_process_keeps_its_slot(self, isolated_db):
        started, release = threading.Event(), threading.Event()
        bg.start_job("live_now", lambda: (started.set(), release.wait(timeout=5)),
                     gpu_touching=True)
        assert started.wait(timeout=2)
        # A record naming this pid is what would mark an old run's row as a
        # ghost; the job being live here must still win.
        db.save_job_record("live_now", "running", owner_pid=os.getpid())
        assert recovery.release_orphaned_server_holders() == 0
        assert "ui:live_now" in _holders()
        release.set()
        for _ in range(100):
            if bg.get_status("live_now")["status"] != "running":
                break
            time.sleep(0.02)

    def test_a_job_taking_the_slot_after_the_read_keeps_it(self, isolated_db, monkeypatch):
        _ghost("racer")
        real = recovery._owner_is_gone

        def gone_then_job_starts(job_id):
            gone = real(job_id)
            # The new run takes the same holder name between the check and the delete.
            time.sleep(0.01)
            assert db.try_acquire_gpu_lock(f"ui:{job_id}", "new run")
            return gone

        monkeypatch.setattr(recovery, "_owner_is_gone", gone_then_job_starts)
        assert recovery.release_orphaned_server_holders() == 0
        assert _holders() == {"ui:racer"}


class TestPreviousRunWaitMessage:
    def test_none_when_nothing_holds_the_gpu(self, isolated_db):
        assert recovery.previous_run_wait_message() is None

    def test_ghost_row_says_a_previous_run_is_being_released(self, isolated_db):
        _ghost("old_job")
        _age("ui:old_job", 120)
        msg = recovery.previous_run_wait_message()
        assert msg == "Waiting for a previous run to be released (up to 8 min)"
        assert "old_job" not in msg and "private" not in msg

    def test_live_cli_or_foreign_server_row_keeps_the_generic_wording(self, isolated_db):
        db.try_acquire_gpu_lock("cli:5")
        assert recovery.previous_run_wait_message() is None

    def test_other_live_server_job_keeps_the_generic_wording(self, isolated_db):
        _ghost("elsewhere", pid=os.getpid() + 1, status="running")
        assert recovery.previous_run_wait_message() is None

    def test_queued_job_shows_it_and_start_after_release(self, isolated_db):
        _ghost("ghost")
        bg.start_job("waiter", lambda: None, gpu_touching=True)
        assert bg.get_status("waiter")["status"] == "queued"
        assert bg.get_status("waiter")["message"].startswith("Waiting for a previous run")
        recovery.release_orphaned_server_holders()
        bg.recheck_gpu_queue()
        for _ in range(100):
            if bg.get_status("waiter")["status"] == "done":
                break
            time.sleep(0.02)
        assert bg.get_status("waiter")["status"] == "done"
