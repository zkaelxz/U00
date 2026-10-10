"""jobs/gpu_slots.py: the one cross-process GPU acquire/release path. Fully
mocked: isolated_db, no GPU, a patched pid check."""
import contextlib
import os
import pathlib
import re
import threading
import time

import pytest

import background_jobs as bg
import db
import diagnostics_torch
from jobs import gpu_slots, job_store

DEAD_PID = 2 ** 22 + 12345
OTHER_PID = 2 ** 22 + 23456


@pytest.fixture(autouse=True)
def _pid_alive(monkeypatch):
    # Only DEAD_PID counts as exited; every other pid is a running process.
    monkeypatch.setattr(bg, "owner_process_alive", lambda pid: pid != DEAD_PID)


def _wait(job_id, timeout=2.0):
    start = time.time()
    while bg.is_running(job_id) and time.time() - start < timeout:
        time.sleep(0.01)


def _wait_for(predicate, timeout=2.0):
    end = time.time() + timeout
    while not predicate() and time.time() < end:
        time.sleep(0.01)
    return predicate()


def _row(slot, holder, pid=None, instance=None, age=0.0):
    """A row another process (or an older build, with pid None) wrote."""
    now = time.time() - age
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("INSERT INTO gpu_lock (id, holder, description, acquired_at, heartbeat_at, "
                     "owner_pid, owner_instance) VALUES (?, ?, 'private description', ?, ?, ?, ?)",
                     (slot, holder, now, now, pid, instance))
        conn.commit()


def _holders():
    with contextlib.closing(db.get_conn()) as conn:
        return {r["holder"] for r in conn.execute("SELECT holder FROM gpu_lock")}


def _heartbeat_at(holder):
    with contextlib.closing(db.get_conn()) as conn:
        return conn.execute("SELECT heartbeat_at FROM gpu_lock WHERE holder = ?",
                            (holder,)).fetchone()[0]


class TestGpuParallelSlots:
    """gpu_max_parallel lets more than one GPU job run when nvidia-smi shows
    enough free VRAM; never more than the cap, and one at a time when free
    VRAM can't be read."""

    @pytest.fixture(autouse=True)
    def _library(self, isolated_db):
        bg.set_gpu_limit_enabled(True)
        self._releases = []
        yield
        for ev in self._releases:
            ev.set()
        for jid in list(bg.list_all_jobs()):
            _wait(jid)
        bg.clear_all_jobs()

    @pytest.fixture(autouse=True)
    def _gpu(self, monkeypatch):
        self.free_mb = {"value": 20000.0}
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
        monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: None if self.free_mb["value"] is None
                            else {"utilization_percent": 90.0, "memory_used_mb": 0.0,
                                  "memory_total_mb": 24000.0, "memory_free_mb": self.free_mb["value"]})
        monkeypatch.setattr(gpu_slots, "GPU_PARALLEL_SETTLE_SECONDS", 0)

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
        assert gpu_slots.holder_count() == 2
        bg.recheck_gpu_queue()
        assert self._status("par_c") == "queued"

    def test_low_free_vram_holds_the_job(self):
        bg.set_gpu_max_parallel(3)
        self.free_mb["value"] = gpu_slots.GPU_PARALLEL_RESERVE_MB - 1
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
        monkeypatch.setattr(gpu_slots, "GPU_PARALLEL_SETTLE_SECONDS", 3600)
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
        assert gpu_slots.take("cli:1", "CLI translate")
        self._start("par_a")
        self._start("par_b")
        assert self._status("par_a") == "running"
        assert self._status("par_b") == "queued"
        assert gpu_slots.holder_count() == 2

    def test_cli_takes_the_same_shared_slot_check(self):
        bg.set_gpu_max_parallel(2)
        self._start("par_a")
        assert gpu_slots.acquire("cli:1", "CLI translate") is True
        assert gpu_slots.acquire("cli:2", "CLI translate") is False  # cap of 2 reached
        gpu_slots.release("cli:1")
        self.free_mb["value"] = None
        assert gpu_slots.acquire("cli:1", "CLI translate") is False  # no reading: one at a time

    def test_setting_is_clamped(self):
        bg.set_gpu_max_parallel(0)
        assert bg.get_gpu_max_parallel() == 1
        bg.set_gpu_max_parallel(9)
        assert bg.get_gpu_max_parallel() == 4
        db.set_app_setting("gpu_max_parallel", "lots")
        assert bg.get_gpu_max_parallel() == 1

    def test_cli_gpu_lock_waits_for_a_ui_job_and_releases(self):
        import cli
        release = self._start("par_a")
        got = threading.Event()

        def run_cli():
            with cli._gpu_lock("CLI translate", poll_interval=0.01) as holder:
                got.set()
                assert gpu_slots.status()[0] == holder
        t = threading.Thread(target=run_cli)
        t.start()
        assert not got.wait(0.2)
        release.set()
        t.join(5)
        assert got.is_set() and not t.is_alive()
        assert gpu_slots.status() == (None, None)



class TestTwoProcesses:
    def test_cap_is_never_exceeded_with_holders_from_two_other_pids(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: {"memory_free_mb": 20000.0})
        monkeypatch.setattr(gpu_slots, "GPU_PARALLEL_SETTLE_SECONDS", 0)
        _row(1, f"cli:{OTHER_PID}", OTHER_PID, "aaaa")
        _row(2, f"cli:{OTHER_PID + 1}", OTHER_PID + 1, "bbbb")
        bg.set_gpu_max_parallel(2)
        assert gpu_slots.acquire("ui:third", "x") is False
        bg.set_gpu_max_parallel(3)
        assert gpu_slots.acquire("ui:third", "x") is True
        assert gpu_slots.acquire("ui:fourth", "x") is False
        assert gpu_slots.holder_count() == 3

    def test_a_dead_owners_row_is_taken_over(self, isolated_db):
        _row(1, f"cli:{DEAD_PID}", DEAD_PID, "aaaa")
        assert gpu_slots.status() == (None, None)
        assert gpu_slots.take("ui:next") is True
        assert _holders() == {"ui:next"}

    def test_another_servers_row_of_the_same_job_id_is_neither_reused_nor_freed(self, isolated_db):
        _row(1, "ui:transcribe_5", OTHER_PID, "aaaa")
        assert gpu_slots.take("ui:transcribe_5") is False   # cap 1: their row is live
        assert gpu_slots.take("ui:transcribe_5", max_holders=2) is True
        gpu_slots.release("ui:transcribe_5")
        with contextlib.closing(db.get_conn()) as conn:
            rows = conn.execute("SELECT owner_pid FROM gpu_lock").fetchall()
        assert [r[0] for r in rows] == [OTHER_PID]

    def test_acquire_records_the_owner_and_job(self, isolated_db):
        assert gpu_slots.acquire("ui:job_1", "Transcription", job_id="job_1")
        with contextlib.closing(db.get_conn()) as conn:
            row = conn.execute("SELECT owner_pid, owner_instance, job_id FROM gpu_lock").fetchone()
        assert tuple(row) == (os.getpid(), job_store.INSTANCE_ID, "job_1")


class TestReap:
    def test_frees_dead_pid_other_instance_and_stale_rows_only(self, isolated_db):
        _row(1, f"cli:{DEAD_PID}", DEAD_PID, "aaaa")
        _row(2, "ui:old_server", os.getpid(), "an-earlier-server")
        _row(3, f"cli:{OTHER_PID}", OTHER_PID, "bbbb", age=gpu_slots.GPU_LOCK_STALE_SECONDS + 1)
        _row(4, f"cli:{OTHER_PID + 1}", OTHER_PID + 1, "cccc")
        assert gpu_slots.reap() == 3
        assert _holders() == {f"cli:{OTHER_PID + 1}"}

    def test_this_servers_own_rows_stay(self, isolated_db):
        assert gpu_slots.take("ui:live_now")
        assert gpu_slots.reap() == 0
        assert _holders() == {"ui:live_now"}

    def test_live_whisper_claims_follow_the_same_rule(self, isolated_db):
        _row(1, f"live-whisper:{DEAD_PID}", DEAD_PID, "aaaa")
        _row(2, f"live-whisper:{OTHER_PID}", OTHER_PID, "bbbb")
        _row(3, "cli:123", 123, "cccc")
        assert gpu_slots.reap() == 1
        assert _holders() == {f"live-whisper:{OTHER_PID}", "cli:123"}

    def test_legacy_rows_follow_the_heartbeat_rule(self, isolated_db):
        _row(1, "ui:legacy_fresh")
        _row(2, "cli:old", age=gpu_slots.GPU_LOCK_STALE_SECONDS + 1)
        assert gpu_slots.holder_count() == 1
        assert gpu_slots.take("ui:next") is False   # the fresh legacy row still counts
        assert gpu_slots.reap() == 1
        assert _holders() == {"ui:legacy_fresh"}


class TestHeartbeatAndRelease:
    def test_heartbeat_refreshes_an_aging_row(self, isolated_db):
        assert gpu_slots.take("ui:quiet")
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute("UPDATE gpu_lock SET heartbeat_at = heartbeat_at - ?",
                         (gpu_slots.GPU_LOCK_STALE_SECONDS + 60,))
            conn.commit()
        assert gpu_slots.holder_count() == 0
        gpu_slots.heartbeat(["ui:quiet", "ui:absent"])
        assert gpu_slots.holder_count() == 1
        assert time.time() - _heartbeat_at("ui:quiet") < 5

    def test_heartbeat_never_refreshes_another_processes_row(self, isolated_db):
        _row(1, "ui:shared_id", OTHER_PID, "aaaa", age=100)
        gpu_slots.heartbeat(["ui:shared_id"])
        assert time.time() - _heartbeat_at("ui:shared_id") >= 100

    def test_abandoned_workers_rows_are_refreshed(self, isolated_db, monkeypatch):
        import job_force_stop
        beats = []
        monkeypatch.setattr(gpu_slots, "heartbeat", lambda holders: beats.extend(holders))
        release = threading.Event()
        worker = threading.Thread(target=release.wait, args=(5,))
        worker.baihe_abandoned_gpu = True
        worker.start()
        monkeypatch.setitem(job_force_stop._abandoned, "ab_1", worker)
        try:
            job_force_stop.refresh_abandoned_gpu_rows()
        finally:
            release.set()
            worker.join(5)
        assert beats == ["ui:ab_1"]


class TestPreviousRunWaitMessage:
    def _ghost(self, job_id, pid=DEAD_PID, status="cancelled", age=0.0):
        _row(1, f"ui:{job_id}", age=age)   # written before the owner columns existed
        db.save_job_record(job_id, status, owner_pid=pid, gpu_touching=True)

    def test_none_when_nothing_holds_the_gpu(self, isolated_db):
        assert gpu_slots.previous_run_wait_message() is None

    def test_legacy_ghost_row_says_a_previous_run_is_being_released(self, isolated_db):
        self._ghost("old_job", age=120)
        msg = gpu_slots.previous_run_wait_message()
        assert msg == "Waiting for a previous run to be released (up to 8 min)"
        assert "old_job" not in msg and "private" not in msg

    def test_live_cli_row_keeps_the_generic_wording(self, isolated_db):
        gpu_slots.take("cli:5")
        assert gpu_slots.previous_run_wait_message() is None

    def test_other_live_server_job_keeps_the_generic_wording(self, isolated_db):
        self._ghost("elsewhere", pid=OTHER_PID, status="running")
        assert gpu_slots.previous_run_wait_message() is None

    def test_queued_job_shows_it(self, isolated_db):
        self._ghost("ghost")
        try:
            bg.start_job("waiter", lambda: None, gpu_touching=True)
            assert bg.get_status("waiter")["status"] == "queued"
            assert bg.get_status("waiter")["message"].startswith("Waiting for a previous run")
        finally:
            bg.clear_all_jobs()


class TestOnlyThisModuleTouchesTheTable:
    """A second path to the gpu_lock table is how the old per-caller sweeps
    and holder rules drifted apart. Retire this once the table is private to
    a module nothing else can import from."""

    NAMES = re.compile(r"try_acquire_gpu_lock|release_gpu_lock|heartbeat_gpu_lock|FROM gpu_lock")

    def test_no_other_module_names_the_table_or_its_old_helpers(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        skip = {"__pycache__", "tests", "node_modules", "frontend", "venv", ".venv"}
        allowed = {root / "jobs" / "gpu_slots.py", root / "db.py"}
        offenders = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root).parts
            if any(p in skip or p.startswith(".") for p in rel[:-1]) or path in allowed:
                continue
            if self.NAMES.search(path.read_text(encoding="utf-8", errors="replace")):
                offenders.append("/".join(rel))
        assert offenders == []
