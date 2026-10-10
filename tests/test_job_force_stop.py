"""Force stop for a thread job that ignores Cancel (job_force_stop), and the
nvidia-smi reading that no longer runs under background_jobs._lock (gpu_probe)."""
import threading
import time

import pytest

import background_jobs as bg
import db
import diagnostics_torch
import job_force_stop
from services import jobs_service
from services.service_errors import ConflictError, NotFoundError


def _wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


@pytest.fixture
def quiet_gpu(isolated_db, monkeypatch):
    monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
    monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: None)


class HungJob:
    """A thread job stuck past Cancel until release() lets it go."""

    def __init__(self, job_id, gpu=False):
        self.job_id = job_id
        self.gate = threading.Event()
        self.late_writes_done = threading.Event()
        assert bg.start_job(job_id, self._work, gpu_touching=gpu, description="Hung")

    def _work(self):
        self.gate.wait(10)
        # What a stuck worker does once it wakes up: report into its old record.
        bg.update_progress(self.job_id, 0.9, "late progress")
        bg.set_result(self.job_id, {"late": True})
        self.late_writes_done.set()

    def release(self):
        self.gate.set()


def _cancelling_for(job_id, seconds):
    bg.request_cancel(job_id)
    with bg._lock:
        bg._jobs[job_id]["cancel_requested_at"] = time.time() - seconds


def _slot_held(job_id):
    return db.gpu_lock_holder_count() > 0


class TestForceStop:
    def test_refused_until_cancel_has_been_pending_a_minute(self, quiet_gpu):
        hung = HungJob("hung_1")
        with pytest.raises(ConflictError, match="Cancel the job first"):
            job_force_stop.force_stop("hung_1")
        _cancelling_for("hung_1", 5)
        assert job_force_stop.can_force_stop(bg.get_status("hung_1")) is False
        with pytest.raises(ConflictError):
            job_force_stop.force_stop("hung_1")
        hung.release()

    def test_a_stalled_but_not_cancelled_job_is_refused(self, quiet_gpu):
        hung = HungJob("hung_s")
        with bg._lock:
            bg._jobs["hung_s"]["progress_at"] = time.time() - bg.JOB_STALL_SECONDS - 5
        with pytest.raises(ConflictError, match="Cancel the job first"):
            job_force_stop.force_stop("hung_s")
        hung.release()

    def test_closes_the_record_and_keeps_the_gpu_claim_while_the_worker_lives(self, quiet_gpu):
        hung = HungJob("hung_2", gpu=True)
        assert _slot_held("hung_2")
        _cancelling_for("hung_2", 61)
        assert job_force_stop.can_force_stop(bg.get_status("hung_2")) is True

        result = job_force_stop.force_stop("hung_2")

        assert result == {"status": "cancelled", "worker_still_running": True}
        status = bg.get_status("hung_2")
        assert status["status"] == "cancelled"
        assert status["message"] == job_force_stop.FORCE_STOPPED_MESSAGE
        assert "detail_state" not in status  # nothing reads it; the message already says Force stopped
        assert db.get_job_record("hung_2")["status"] == "cancelled"
        assert _slot_held("hung_2"), "the abandoned worker may still be using the GPU"

        with pytest.raises(ConflictError, match="not running"):
            job_force_stop.force_stop("hung_2")   # no second free

        hung.release()
        assert hung.late_writes_done.wait(3)
        assert _wait_for(lambda: not _slot_held("hung_2")), "freed once the worker ends"
        late = bg.get_status("hung_2")
        assert late["status"] == "cancelled" and late["message"] == job_force_stop.FORCE_STOPPED_MESSAGE
        assert late["result"] is None and late["progress"] != 0.9

    def test_an_abandoned_gpu_worker_still_counts_and_its_row_stays_fresh(self, quiet_gpu):
        hung = HungJob("hung_g", gpu=True)
        _cancelling_for("hung_g", 61)
        job_force_stop.force_stop("hung_g")
        conn = db.get_conn()   # the row has aged past GPU_LOCK_STALE_SECONDS
        conn.execute("UPDATE gpu_lock SET heartbeat_at = heartbeat_at - ?",
                     (db.GPU_LOCK_STALE_SECONDS + 60,))
        conn.commit()
        conn.close()
        assert db.gpu_lock_holder_count() == 0, "stale rows are ignored without the heartbeat"
        bg._heartbeat_once()
        assert _slot_held("hung_g"), "the heartbeat keeps the abandoned worker's row live"

        second = threading.Event()
        assert bg.start_job("second_g", second.set, gpu_touching=True) is True
        assert not second.wait(0.3), "gpu_max_parallel=1: must wait for the abandoned worker"

        hung.release()
        assert hung.late_writes_done.wait(3)
        assert second.wait(5), "promoted by the abandoned thread's own finally, without the poller"

    def test_an_abandoned_worker_blocks_exclusive_holds_and_drama_checks(self, quiet_gpu):
        hung = HungJob("dub_77")
        _cancelling_for("dub_77", 61)
        job_force_stop.force_stop("dub_77")
        assert bg.acquire_exclusive("Model cache delete") is False
        assert bg.any_job_running_for_drama(77) is True
        hung.release()
        assert hung.late_writes_done.wait(3)
        assert _wait_for(lambda: not job_force_stop._live_abandoned("dub_77"))
        assert bg.acquire_exclusive("Model cache delete") is True
        bg.release_exclusive()

    def test_the_same_id_cannot_restart_until_the_abandoned_worker_ends(self, quiet_gpu):
        hung = HungJob("hung_3")
        _cancelling_for("hung_3", 61)
        job_force_stop.force_stop("hung_3")

        with pytest.raises(ConflictError, match="still finishing"):
            bg.start_job("hung_3", lambda: None)
        with pytest.raises(ConflictError, match="still finishing"):
            bg.start_process_job("hung_3", lambda *a: None)
        assert bg.start_job("another_id", lambda: None) is True   # other ids are unaffected

        hung.release()
        assert hung.late_writes_done.wait(3)
        assert _wait_for(lambda: not job_force_stop._live_abandoned("hung_3"))
        assert bg.start_job("hung_3", lambda: bg.set_result("hung_3", {"fresh": True})) is True
        assert _wait_for(lambda: (bg.get_status("hung_3") or {}).get("status") == "done")
        assert bg.get_status("hung_3")["result"] == {"fresh": True}

    def test_a_dead_worker_frees_the_gpu_slot_at_once(self, quiet_gpu):
        assert bg.try_take_gpu_slot("ui:dead_1", "x")
        dead = threading.Thread(target=lambda: None)
        dead.start()
        dead.join()
        run = {"status": "running", "kind": "thread", "gpu_touching": True, "message": "",
               "cancel_requested": True, "cancel_requested_at": time.time() - 90,
               "started_at": time.time()}
        with bg._lock:
            bg._jobs["dead_1"] = run
            bg._workers["dead_1"] = (run, dead)

        result = job_force_stop.force_stop("dead_1")

        assert result["worker_still_running"] is False
        assert not _slot_held("dead_1")
        with pytest.raises(ConflictError):
            job_force_stop.force_stop("dead_1")

    def test_a_queued_or_process_job_is_not_force_stoppable(self, quiet_gpu):
        with bg._lock:
            bg._jobs["proc_1"] = {"status": "running", "kind": "process", "cancel_requested_at": 1.0}
            bg._jobs["queued_1"] = {"status": "queued", "kind": "thread", "cancel_requested_at": 1.0}
        for job_id in ("proc_1", "queued_1", "missing"):
            with pytest.raises(ConflictError):
                job_force_stop.force_stop(job_id)


class TestForceStopService:
    def test_record_offers_it_only_after_a_minute_of_cancelling(self, quiet_gpu):
        hung = HungJob("hung_4")
        assert jobs_service.get_job("hung_4")["can_force_stop"] is False
        _cancelling_for("hung_4", 10)
        assert jobs_service.get_job("hung_4")["can_force_stop"] is False
        _cancelling_for("hung_4", 90)
        with bg._lock:
            bg._jobs["hung_4"]["cancel_requested_at"] = time.time() - 90
        assert jobs_service.get_job("hung_4")["can_force_stop"] is True

        result = jobs_service.force_stop_job("hung_4")

        assert result == {"job_id": "hung_4", "force_stopped": True, "status": "cancelled",
                          "worker_still_running": True}
        assert jobs_service.get_job("hung_4")["can_force_stop"] is False
        hung.release()

    def test_unknown_job_is_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            jobs_service.force_stop_job("nope")


class TestGpuProbeOutsideTheLock:
    def test_a_slow_nvidia_smi_does_not_block_status_reads(self, isolated_db, monkeypatch):
        def slow(*_):
            time.sleep(0.4)
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: slow() or False)
        monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: slow())
        bg.start_job("other", lambda: time.sleep(0.01))
        starter = threading.Thread(
            target=lambda: bg.start_job("gpu_job", lambda: None, gpu_touching=True, description="g"))
        starter.start()
        slowest = 0.0
        while starter.is_alive():
            t0 = time.monotonic()
            bg.get_status("other")
            bg.is_cancel_requested("other")
            slowest = max(slowest, time.monotonic() - t0)
            time.sleep(0.01)
        starter.join()
        assert slowest < 0.2, f"a status read waited {slowest:.2f}s behind the GPU probe"
        assert _wait_for(lambda: (bg.get_status("gpu_job") or {}).get("status") == "done")

    def test_the_prefetched_reading_still_decides_the_slot(self, isolated_db, monkeypatch):
        busy = {"value": True}
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: busy["value"])
        monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: None)
        bg.start_job("gpu_a", lambda: None, gpu_touching=True, description="a")
        assert bg.get_status("gpu_a")["status"] == "queued"
        bg.clear_job("gpu_a")
        busy["value"] = False
        bg.start_job("gpu_b", lambda: None, gpu_touching=True, description="b")
        assert _wait_for(lambda: (bg.get_status("gpu_b") or {}).get("status") == "done")

    def test_a_reading_is_used_once_and_never_when_old(self, monkeypatch):
        import gpu_probe
        calls = []
        monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: calls.append(1) or {"n": len(calls)})
        monkeypatch.setattr(diagnostics_torch, "external_gpu_is_busy", lambda *_a: False)
        gpu_probe.prefetch()
        assert gpu_probe.external_gpu_load() == {"n": 1}
        assert gpu_probe.external_gpu_load() == {"n": 2}   # second read is live
        gpu_probe.prefetch()
        monkeypatch.setattr(gpu_probe, "MAX_AGE_SECONDS", -1.0)
        assert gpu_probe.external_gpu_load() == {"n": 4}   # too old: live


def test_the_cancellable_m4b_encode_has_a_deadline(tmp_path, monkeypatch):
    import dub_narration
    from dub import M4B_ENCODE_TIMEOUT_SECONDS
    (tmp_path / "narration_track.wav").write_bytes(b"RIFF")
    seen = {}
    monkeypatch.setattr(bg, "run_cancellable", lambda job_id, cmd, **kw: seen.update(kw))
    monkeypatch.setattr(dub_narration, "_wav_duration_ms", lambda path: 1000)
    monkeypatch.setattr(dub_narration, "narration_paragraph_ends", lambda *a, **k: [])
    dub_narration.export_narration_m4b([], str(tmp_path), cancel_job_id="narr_1")
    assert seen == {"timeout": M4B_ENCODE_TIMEOUT_SECONDS}
