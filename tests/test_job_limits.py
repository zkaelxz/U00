"""Tests for what holds a job back: the one-GPU-job guard, external GPU load, the GPU slot under the job lock and per-user caps."""

import sys
import os
import sqlite3
import threading
import time

import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs as bg
from jobs import gpu_queue, job_launch, job_runner, job_settings, job_table
import db
import diagnostics_torch
from jobs import gpu_slots, user_caps
from tests.test_background_jobs import (  # noqa: F401  (_private_library is the autouse fixture)
    _boom, _isolate_library, _private_library, _restore_library, _wait, _wait_for,
)


class TestGpuJobGuard:
    """Step 5c: a soft, global "one GPU job at a time" guard, prompted by a
    shared review flagging that nothing today stops two independent
    GPU-touching jobs (different job_ids, different dramas) from running
    concurrently and competing for the same VRAM. gpu_touching=True jobs
    queue behind each other instead of starting immediately; non-GPU jobs
    are unaffected either way."""

    def setup_method(self):
        self._library_state = _isolate_library()
        job_settings.set_gpu_limit_enabled(True)

    def teardown_method(self):
        job_settings.set_gpu_limit_enabled(True)  # never leak into other tests
        _restore_library(*self._library_state)

    def test_a_second_gpu_job_queues_instead_of_starting(self):
        release = threading.Event()
        started = threading.Event()

        def slow():
            started.set()
            release.wait(timeout=2.0)

        assert job_launch.start_job("gpu_a", slow, gpu_touching=True) is True
        started.wait(timeout=2.0)
        assert bg.get_status("gpu_a")["status"] == "running"

        calls = []
        assert job_launch.start_job("gpu_b", lambda: calls.append(1), gpu_touching=True) is True
        assert bg.get_status("gpu_b")["status"] == "queued"
        assert calls == []  # queued, not started

        release.set()
        _wait("gpu_a")
        bg.clear_job("gpu_a")
        bg.clear_job("gpu_b")

    def test_a_non_gpu_job_is_unaffected_by_a_running_gpu_job(self):
        release = threading.Event()
        job_launch.start_job("gpu_c", lambda: release.wait(timeout=2.0), gpu_touching=True)

        calls = []
        assert job_launch.start_job("cpu_a", lambda: calls.append(1), gpu_touching=False) is True
        _wait("cpu_a")
        assert calls == [1]  # ran immediately, no queueing

        release.set()
        _wait("gpu_c")
        bg.clear_job("gpu_c")
        bg.clear_job("cpu_a")

    def test_queued_gpu_job_starts_automatically_once_the_running_one_finishes(self):
        release = threading.Event()
        started = threading.Event()
        job_launch.start_job("gpu_d", lambda: (started.set(), release.wait(timeout=2.0)),
                     gpu_touching=True)
        started.wait(timeout=2.0)

        second_ran = threading.Event()
        job_launch.start_job("gpu_e", lambda: second_ran.set(), gpu_touching=True)
        assert bg.get_status("gpu_e")["status"] == "queued"

        release.set()
        assert second_ran.wait(timeout=2.0), "queued job never started after the first finished"
        _wait("gpu_e")
        assert bg.get_status("gpu_e")["status"] == "done"
        bg.clear_job("gpu_d")
        bg.clear_job("gpu_e")

    def test_toggle_off_allows_two_gpu_jobs_to_run_concurrently(self):
        job_settings.set_gpu_limit_enabled(False)
        release = threading.Event()
        started_f = threading.Event()
        job_launch.start_job("gpu_f", lambda: (started_f.set(), release.wait(timeout=2.0)),
                     gpu_touching=True)
        started_f.wait(timeout=2.0)

        started_g = threading.Event()
        job_launch.start_job("gpu_g", lambda: started_g.set(), gpu_touching=True)
        assert started_g.wait(timeout=2.0), "second GPU job should start immediately when the limit is off"
        assert bg.get_status("gpu_g")["status"] != "queued"

        release.set()
        _wait("gpu_f")
        _wait("gpu_g")
        bg.clear_job("gpu_f")
        bg.clear_job("gpu_g")

    def test_queued_message_never_names_the_running_job(self):
        # Auth B2 (review M-1): the busy job may be another user's private
        # drama, and whoever can see the waiting job reads its message.
        release = threading.Event()
        started = threading.Event()
        job_launch.start_job("gpu_h", lambda: (started.set(), release.wait(timeout=2.0)),
                     gpu_touching=True, description="Transcription for Test Drama")
        started.wait(timeout=2.0)
        job_launch.start_job("gpu_h2", lambda: None, gpu_touching=True, description="Mine")

        queued = bg.get_status("gpu_h2")
        assert queued["status"] == "queued"
        assert queued["message"] == gpu_queue.GPU_WAIT_MESSAGE
        assert "Test Drama" not in queued["message"] and "gpu_h" not in queued["message"]
        assert "Transcription for Test Drama" in bg.get_status("gpu_h")["description"]

        release.set()
        _wait("gpu_h")
        _wait("gpu_h2")
        bg.clear_job("gpu_h")
        bg.clear_job("gpu_h2")

    def test_clearing_a_queued_job_drops_it_from_the_queue(self):
        release = threading.Event()
        job_launch.start_job("gpu_i", lambda: release.wait(timeout=2.0), gpu_touching=True)

        calls = []
        job_launch.start_job("gpu_j", lambda: calls.append(1), gpu_touching=True)
        assert bg.get_status("gpu_j")["status"] == "queued"

        bg.clear_job("gpu_j")  # the user navigated away / reset before it ever ran
        release.set()
        _wait("gpu_i")
        time.sleep(0.1)  # give a wrongly-surviving queue entry a chance to fire
        assert bg.get_status("gpu_j") is None
        assert calls == []
        bg.clear_job("gpu_i")


class TestExternalGpuLoadGuard:
    """Step 26d: the GPU guard also respects real load from nvidia-smi
    (diagnostics_torch.external_gpu_is_busy), not just its own two locks --
    so a completely different application on the same GPU (Jellyfin
    transcoding on the same card, say) is respected too, not just other
    Baihe jobs."""

    def setup_method(self):
        self._library_state = _isolate_library()
        job_settings.set_gpu_limit_enabled(True)

    def teardown_method(self):
        job_settings.set_gpu_limit_enabled(True)  # never leak into other tests
        _restore_library(*self._library_state)

    def test_queues_when_gpu_is_externally_busy_even_with_no_baihe_job_running(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: True)
        calls = []
        assert job_launch.start_job("gpu_ext_a", lambda: calls.append(1), gpu_touching=True) is True
        assert bg.get_status("gpu_ext_a")["status"] == "queued"
        assert calls == []  # queued, not started -- nothing Baihe-side is even running
        bg.clear_job("gpu_ext_a")

    def test_recheck_gpu_queue_promotes_once_external_load_clears(self, monkeypatch):
        busy = {"value": True}
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: busy["value"])
        started = threading.Event()
        job_launch.start_job("gpu_ext_b", lambda: started.set(), gpu_touching=True)
        assert bg.get_status("gpu_ext_b")["status"] == "queued"
        assert not started.is_set()

        busy["value"] = False  # e.g. Jellyfin's transcode finished
        job_runner.recheck_gpu_queue()
        assert started.wait(timeout=2.0), "queued job never started after external load cleared"
        _wait("gpu_ext_b")
        assert bg.get_status("gpu_ext_b")["status"] == "done"
        bg.clear_job("gpu_ext_b")

    def test_a_non_gpu_job_is_unaffected_by_external_gpu_load(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: True)
        calls = []
        assert job_launch.start_job("cpu_ext", lambda: calls.append(1), gpu_touching=False) is True
        _wait("cpu_ext")
        assert calls == [1]  # non-GPU jobs never consult the GPU guard at all
        bg.clear_job("cpu_ext")

    def test_queued_message_for_external_load_is_generic(self, monkeypatch):
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: True)
        job_launch.start_job("gpu_ext_c", lambda: None, gpu_touching=True)
        assert bg.get_status("gpu_ext_c")["message"] == gpu_queue.GPU_WAIT_MESSAGE
        bg.clear_job("gpu_ext_c")


class TestExternalGpuWaitMessage:
    """A job held back by another program's GPU use says so, with numbers
    only (no process names, paths or command lines)."""

    def setup_method(self):
        self._library_state = _isolate_library()
        job_settings.set_gpu_limit_enabled(True)

    def teardown_method(self):
        job_settings.set_gpu_limit_enabled(True)
        _restore_library(*self._library_state)

    def test_queued_message_names_external_use_and_the_fix(self, monkeypatch):
        load = {"utilization_percent": 20.0, "memory_used_mb": 9216.0,
                "memory_total_mb": 10240.0, "memory_free_mb": 1024.0 - 1}
        monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: load)
        job_launch.start_job("gpu_wait_a", lambda: None, gpu_touching=True)
        status = bg.get_status("gpu_wait_a")
        assert status["status"] == "queued"
        assert status["message"].startswith("Waiting for the GPU: another program is using it")
        assert "9.0 GB of 10.0 GB in use" in status["message"]
        assert "Close GPU-heavy apps" in status["message"] and "Settings" in status["message"]
        bg.clear_job("gpu_wait_a")

    def test_message_goes_back_to_generic_when_load_is_not_external(self, monkeypatch):
        load = {"utilization_percent": 5.0, "memory_used_mb": 100.0,
                "memory_total_mb": 10240.0, "memory_free_mb": 10140.0}
        monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: load)
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: True)
        job_launch.start_job("gpu_wait_b", lambda: None, gpu_touching=True)
        assert bg.get_status("gpu_wait_b")["message"] == gpu_queue.GPU_WAIT_MESSAGE
        bg.clear_job("gpu_wait_b")


class TestGpuSlotRemoved:
    """Step 4k: gpu_slot() had no real callers left -- diarization and dub
    generation both moved onto start_job(gpu_touching=True) (Step 5c/8/9d
    era), so the standalone blocking context manager was dead code."""

    def test_gpu_slot_no_longer_exists(self):
        assert not hasattr(bg, "gpu_slot")


class TestPerUserJobCaps:
    """A member can't fill the GPU queue or the job threads: jobs/user_caps.py
    caps the running and queued jobs they started, whatever the job ids.
    Starter None (the PC owner, auth off, legacy records) is never capped."""

    def setup_method(self):
        self._library_state = _isolate_library()
        job_settings.set_gpu_limit_enabled(True)
        self.release = threading.Event()
        self.gates = [self.release]
        self.acting = {"uid": None}
        self.before = set(bg.list_all_jobs())

    def teardown_method(self):
        # Every gate a test made, not just self.release: a job blocked on one that is left
        # closed outlives the join below and holds up the jobs queued behind it.
        for gate in self.gates:
            gate.set()
        # Only this test's jobs: another test in the same worker may leave its own.
        mine = [j for j in bg.list_all_jobs() if j not in self.before]
        for job_id in mine:
            if (bg.get_status(job_id) or {}).get("status") == "queued":
                bg.request_cancel(job_id)      # it may never get a GPU slot here
        assert job_runner.wait_for_job_threads(5.0, job_ids=mine)
        bg.clear_all_jobs()
        job_settings.set_gpu_limit_enabled(True)
        _restore_library(*self._library_state)

    def _as(self, monkeypatch, uid):
        self.acting["uid"] = uid
        monkeypatch.setattr(job_launch, "_acting_user_id", lambda: self.acting["uid"])

    def _gate(self):
        gate = threading.Event()
        self.gates.append(gate)
        return gate

    def _block(self):
        self.release.wait(timeout=5.0)

    def test_a_member_with_a_daily_limit_cannot_start_more_jobs_today_and_the_owner_is_never_counted(self, monkeypatch):
        from lib.errors import RateLimitedError
        user_caps.set_jobs_per_day(7, 2)
        self._as(monkeypatch, 7)
        # Short jobs that finish at once, so only the daily count (not the running cap) can refuse the third.
        for n in range(2):
            assert job_launch.start_job(f"translate_{n}", lambda: None) is True
            _wait(f"translate_{n}")
        assert user_caps.jobs_today(7) == 2
        with pytest.raises(RateLimitedError) as err:
            job_launch.start_job("translate_9", lambda: None)
        assert "limit of 2 jobs for today" in err.value.message
        assert bg.get_status("translate_9") is None
        assert user_caps.jobs_today(7) == 2          # a refused start is not counted

        self._as(monkeypatch, None)
        for n in range(3):
            assert job_launch.start_job(f"owner_{n}", lambda: None) is True
            _wait(f"owner_{n}")
        assert user_caps.jobs_today(None) == 0

    def test_a_new_day_starts_the_count_again_and_clearing_the_limit_lifts_it(self):
        user_caps.set_jobs_per_day(7, 1)
        user_caps.count_daily_start(7, today="2026-11-24")
        user_caps.count_daily_start(7, today="2026-11-25")      # a new day: the old count is not carried
        assert user_caps.jobs_today(7, "2026-11-25") == 1 and user_caps.jobs_today(7, "2026-11-24") == 0
        from lib.errors import RateLimitedError
        with pytest.raises(RateLimitedError):
            user_caps.count_daily_start(7, today="2026-11-25")
        user_caps.set_jobs_per_day(7, None)
        user_caps.count_daily_start(7, today="2026-11-25")      # no limit: nothing counted, nothing refused
        assert user_caps.jobs_today(7, "2026-11-25") == 1

    def test_a_member_past_the_running_cap_gets_rate_limited_whatever_the_job_ids(self, monkeypatch):
        from lib.errors import RateLimitedError
        self._as(monkeypatch, 7)
        assert job_launch.start_job("translate_1", self._block) is True
        assert job_launch.start_job("transcribe_2", self._block) is True
        with pytest.raises(RateLimitedError) as err:
            job_launch.start_job("dub_3", self._block)
        assert "2 jobs running" in err.value.message
        assert bg.get_status("dub_3") is None
        with pytest.raises(RateLimitedError):
            job_launch.start_process_job("speakers_4", _boom)
        assert bg.get_status("speakers_4") is None

        self._as(monkeypatch, 8)
        assert job_launch.start_job("translate_5", self._block) is True
        self._as(monkeypatch, None)
        for n in range(3):
            assert job_launch.start_job(f"owner_{n}", self._block) is True

    def test_a_finished_or_cancelled_job_frees_its_place(self, monkeypatch):
        self._as(monkeypatch, 7)
        assert job_launch.start_job("quick_1", lambda: None) is True
        _wait("quick_1")
        assert job_launch.start_job("slow_1", self._block) is True
        assert job_launch.start_job("slow_2", self._block) is True
        bg.request_cancel("slow_2")
        job_table._jobs["slow_2"]["status"] = "cancelled"   # as a cooperative stop records it
        assert job_launch.start_job("slow_3", self._block) is True

    def test_starts_at_the_same_moment_cannot_both_pass_the_count(self, monkeypatch):
        from lib.errors import RateLimitedError
        self._as(monkeypatch, 7)
        barrier = threading.Barrier(8)
        outcomes = []

        def go(n):
            barrier.wait()
            try:
                outcomes.append(job_launch.start_job(f"race_{n}", self._block))
            except RateLimitedError:
                outcomes.append("limited")

        threads = [threading.Thread(target=go, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        assert outcomes.count(True) == user_caps.MAX_RUNNING_JOBS_PER_USER
        assert outcomes.count("limited") == 8 - user_caps.MAX_RUNNING_JOBS_PER_USER

    def test_a_member_past_the_queued_cap_gets_rate_limited(self, monkeypatch):
        from lib.errors import RateLimitedError
        self._as(monkeypatch, None)
        owner_gate = self._gate()
        assert job_launch.start_job("owner_gpu", owner_gate.wait, 5.0, gpu_touching=True) is True
        self._as(monkeypatch, 7)
        for n in range(user_caps.MAX_QUEUED_JOBS_PER_USER):
            assert job_launch.start_job(f"gpu_wait_{n}", lambda: None, gpu_touching=True) is True
            assert bg.get_status(f"gpu_wait_{n}")["status"] == "queued"
        with pytest.raises(RateLimitedError) as err:
            job_launch.start_job("gpu_wait_more", lambda: None, gpu_touching=True)
        assert f"{user_caps.MAX_QUEUED_JOBS_PER_USER} jobs waiting" in err.value.message
        assert all(e["job_id"] != "gpu_wait_more" for e in gpu_queue._gpu_queue)
        self._as(monkeypatch, 8)
        assert job_launch.start_job("other_gpu", lambda: None, gpu_touching=True) is True

    def test_a_gpu_job_at_the_running_cap_waits_without_holding_up_others_then_starts(
            self, monkeypatch):
        self._as(monkeypatch, 7)
        own_release = self._gate()
        assert job_launch.start_job("cpu_1", lambda: own_release.wait(timeout=5.0)) is True
        assert job_launch.start_job("cpu_2", self._block) is True
        ran = threading.Event()
        assert job_launch.start_job("gpu_held", ran.set, gpu_touching=True) is True
        assert bg.get_status("gpu_held")["status"] == "queued"

        # The GPU is free, so someone else's GPU job starts rather than
        # queueing behind the held one.
        self._as(monkeypatch, 8)
        other_ran = threading.Event()
        assert job_launch.start_job("gpu_other", other_ran.set, gpu_touching=True) is True
        assert other_ran.wait(timeout=2.0)
        _wait("gpu_other")
        assert bg.get_status("gpu_held")["status"] == "queued"

        own_release.set()
        assert ran.wait(timeout=2.0), "the member's queued job never started once under the cap"
        _wait("gpu_held")
        assert bg.get_status("gpu_held")["status"] == "done"

    def test_the_queue_runner_is_refused_at_the_cap_and_its_jobs_never_wait_for_it(
            self, monkeypatch):
        from lib.errors import RateLimitedError
        self._as(monkeypatch, 7)
        assert job_launch.start_job("cpu_a", self._block) is True
        assert job_launch.start_job("cpu_b", self._block) is True
        with user_caps.refuse_at_cap():
            with pytest.raises(RateLimitedError):
                job_launch.start_job("gpu_runner", lambda: None, gpu_touching=True)
        assert bg.get_status("gpu_runner") is None and not gpu_queue._gpu_queue
        assert job_launch.start_job("gpu_direct", lambda: None, gpu_touching=True) is True
        assert bg.get_status("gpu_direct")["status"] == "queued"      # a direct start waits

        # A runner start that queues behind someone else's GPU job is promoted
        # even if its starter reaches the cap meanwhile.
        bg.clear_job("gpu_direct")
        self.release.set()
        assert job_runner.wait_for_job_threads(5.0, job_ids=["cpu_a", "cpu_b"])
        self.release.clear()
        self._as(monkeypatch, None)
        owner_gate = self._gate()
        assert job_launch.start_job("owner_gpu", owner_gate.wait, 5.0, gpu_touching=True) is True
        self._as(monkeypatch, 7)
        ran = threading.Event()
        with user_caps.refuse_at_cap():
            assert job_launch.start_job("gpu_runner", ran.set, gpu_touching=True) is True
        assert bg.get_status("gpu_runner")["status"] == "queued"
        assert job_launch.start_job("cpu_c", self._block) is True
        assert job_launch.start_job("cpu_d", self._block) is True
        owner_gate.set()
        assert ran.wait(2.0), "the runner's job was held for its starter's cap"


class TestGpuSlotWorkUnderTheJobLock:
    """The slot row is taken and freed under job_table._lock, which every
    get_status waits on: another process holding the database's write lock
    may stall that only briefly, and a release that fails is retried."""

    def setup_method(self):
        self._library_state = _isolate_library()
        job_settings.set_gpu_limit_enabled(True)

    def teardown_method(self):
        job_settings.set_gpu_limit_enabled(True)
        _restore_library(*self._library_state)

    def test_a_locked_database_queues_a_gpu_job_without_a_long_wait(self):
        other = db.get_conn()
        other.execute("BEGIN IMMEDIATE")
        try:
            started = time.monotonic()
            assert job_launch.start_job("busy_db", lambda: None, gpu_touching=True) is True
            assert time.monotonic() - started < gpu_queue.GPU_ROW_BUSY_SECONDS + 2.0
            assert bg.get_status("busy_db")["status"] == "queued"
        finally:
            other.rollback()
            other.close()
        job_runner.recheck_gpu_queue()
        assert _wait_for(lambda: bg.get_status("busy_db")["status"] == "done")
        bg.clear_job("busy_db")

    def test_a_failed_release_is_retried_by_the_heartbeat(self, monkeypatch):
        real_release = gpu_slots.release

        def locked(*a, **k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(gpu_slots, "release", locked)
        assert job_launch.start_job("rel_retry", lambda: None, gpu_touching=True) is True
        assert job_runner.wait_for_job_threads(5.0, job_ids={"rel_retry"})
        assert gpu_slots.holder_count() == 1
        monkeypatch.setattr(gpu_slots, "release", real_release)
        job_table._heartbeat_once()
        assert gpu_slots.holder_count() == 0
        assert "rel_retry" not in gpu_queue._unreleased
        bg.clear_job("rel_retry")

    def test_a_rerun_holding_the_slot_keeps_it_on_retry(self):
        go = threading.Event()
        assert job_launch.start_job("rel_rerun", lambda: go.wait(5), gpu_touching=True)
        try:
            gpu_queue._unreleased.add("rel_rerun")
            gpu_queue.retry_unreleased_gpu_slots()
            assert gpu_slots.holder_count() == 1
            assert "rel_rerun" not in gpu_queue._unreleased
        finally:
            go.set()
            _wait("rel_rerun")
            bg.clear_job("rel_rerun")

    def test_an_abandoned_live_worker_keeps_its_row_until_it_is_gone(self, monkeypatch):
        import job_force_stop
        real_release = gpu_slots.release

        def locked(*a, **k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(gpu_slots, "release", locked)
        assert job_launch.start_job("rel_abandoned", lambda: None, gpu_touching=True) is True
        assert job_runner.wait_for_job_threads(5.0, job_ids={"rel_abandoned"})
        monkeypatch.setattr(gpu_slots, "release", real_release)
        monkeypatch.setattr(job_force_stop, "abandoned_alive_locked",
                            lambda job_id: job_id == "rel_abandoned")
        gpu_queue.retry_unreleased_gpu_slots()
        assert gpu_slots.holder_count() == 1
        assert "rel_abandoned" in gpu_queue._unreleased
        monkeypatch.setattr(job_force_stop, "abandoned_alive_locked", lambda job_id: False)
        gpu_queue.retry_unreleased_gpu_slots()
        assert gpu_slots.holder_count() == 0
        assert "rel_abandoned" not in gpu_queue._unreleased
        bg.clear_job("rel_abandoned")

    def test_a_new_run_taking_the_slot_ends_the_old_retry(self):
        gpu_queue._unreleased.add("rel_taken")
        go = threading.Event()
        assert job_launch.start_job("rel_taken", lambda: go.wait(5), gpu_touching=True)
        try:
            assert bg.get_status("rel_taken")["status"] == "running"
            assert "rel_taken" not in gpu_queue._unreleased
        finally:
            go.set()
            _wait("rel_taken")
            # The slot is freed after the status reads final; join the thread first.
            assert job_runner.wait_for_job_threads(5.0, job_ids={"rel_taken"})
            bg.clear_job("rel_taken")
        assert gpu_slots.holder_count() == 0

    def test_progress_refreshes_the_gpu_row_once_per_heartbeat_interval(self, monkeypatch):
        beats = []
        monkeypatch.setattr(gpu_slots, "heartbeat", lambda holders: beats.append(list(holders)))
        go = threading.Event()

        def work():
            for i in range(5):
                bg.update_progress("beat_throttle", i / 5, "working")
            go.wait(5)

        assert job_launch.start_job("beat_throttle", work, gpu_touching=True)
        try:
            assert _wait_for(lambda: (bg.get_status("beat_throttle") or {}).get("progress") == 0.8)
            assert beats == [["ui:beat_throttle"]]
        finally:
            go.set()
            _wait("beat_throttle")
            bg.clear_job("beat_throttle")
