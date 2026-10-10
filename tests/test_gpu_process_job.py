"""services/gpu_process_job.run_in_child with real spawned children: a large
result, a raising body, the deadline, Cancel, per-line items, and the
parent-side deadline for a child that never starts its own timer. The
retranscribe_timeout_s formula is pinned here too."""
import os
import threading
import time

import pytest

import background_jobs
import storage
from services import gpu_process_job, retranscribe_worker


def hang_body(marker, scratch_dir, result_queue):
    """Worker body whose model call never returns."""
    with open(marker, "w") as f:
        f.write(str(os.getpid()))
    time.sleep(600)


def big_body(size, scratch_dir, result_queue):
    result_queue.put(("ok", {"blob": "x" * size}))


def raising_body(secret, scratch_dir, result_queue):
    raise ValueError(f"boom token={secret}")


def items_then_hang_body(marker, scratch_dir, result_queue):
    result_queue.put(("item", {"n": 1}))
    result_queue.put(("item", {"n": 2}))
    hang_body(marker, scratch_dir, result_queue)


def wait_until(predicate, what, timeout=60):
    deadline = time.time() + timeout
    while not predicate():
        assert time.time() < deadline, what
        time.sleep(0.05)


def pid_gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def scratch_dirs(job_id):
    return [d for d in os.listdir(storage.temp_root()) if d.startswith(job_id)]


def test_a_large_result_comes_back_through_the_result_file(isolated_db):
    result = gpu_process_job.run_in_child(
        "gpujob_big", big_body, (50_000,), timeout_s=60)
    assert len(result["blob"]) == 50_000
    assert not scratch_dirs("gpujob_big")


def test_a_raising_body_becomes_a_plain_redacted_child_failed(isolated_db):
    secret = "sk-ant-api03-" + "a" * 40
    with pytest.raises(gpu_process_job.ChildFailed) as err:
        gpu_process_job.run_in_child("gpujob_raise", raising_body, (secret,), timeout_s=60)
    assert "boom" in str(err.value) and secret not in str(err.value)
    assert not scratch_dirs("gpujob_raise")


def test_the_worker_deadline_ends_the_run_and_the_worker_is_gone(isolated_db, tmp_path):
    marker = str(tmp_path / "started")
    started = time.monotonic()
    with pytest.raises(gpu_process_job.ChildFailed) as err:
        gpu_process_job.run_in_child("gpujob_timeout", hang_body, (marker,), timeout_s=2)
    assert time.monotonic() - started < 30
    assert str(err.value) == gpu_process_job.TIMEOUT_MESSAGE
    assert str(tmp_path) not in str(err.value)
    wait_until(lambda: pid_gone(int(open(marker).read())), "the worker outlived the run", 10)
    assert not scratch_dirs("gpujob_timeout")


def test_the_parent_deadline_frees_a_child_that_never_starts_its_timer(
        isolated_db, tmp_path, monkeypatch):
    # With no grace and a long worker timeout only the parent's deadline can fire.
    monkeypatch.setattr(gpu_process_job, "PARENT_GRACE_S", -298)
    marker = str(tmp_path / "started")
    started = time.monotonic()
    with pytest.raises(gpu_process_job.ChildFailed) as err:
        gpu_process_job.run_in_child("gpujob_parent", hang_body, (marker,), timeout_s=300)
    assert time.monotonic() - started < 30
    assert str(err.value) == gpu_process_job.TIMEOUT_MESSAGE
    wait_until(lambda: pid_gone(int(open(marker).read())), "the worker outlived the run", 10)


def test_cancel_kills_the_worker_and_keeps_the_items_already_sent(isolated_db, tmp_path):
    marker = str(tmp_path / "started")
    items, outcome = [], []

    def job():
        try:
            gpu_process_job.run_in_child(
                "gpujob_cancel", items_then_hang_body, (marker,), timeout_s=300,
                on_item=items.append)
        except background_jobs.JobCancelled:
            outcome.append("cancelled")
    assert background_jobs.start_job("gpujob_cancel", job, description="Hanging")
    wait_until(lambda: os.path.exists(marker) and len(items) == 2, "worker never got going")
    pid = int(open(marker).read())
    background_jobs.request_cancel("gpujob_cancel")
    background_jobs.wait_for_job_threads(30)
    assert outcome == ["cancelled"] and items == [{"n": 1}, {"n": 2}]
    wait_until(lambda: pid_gone(pid), "Cancel did not kill the worker", 10)
    assert not scratch_dirs("gpujob_cancel")


def test_timeout_formula_has_a_per_line_term_and_scales_with_audio():
    fn = retranscribe_worker.retranscribe_timeout_s
    assert fn(0.0) >= 1800
    assert fn(100.0, 1) - fn(0.0, 1) == 100 * retranscribe_worker._PER_AUDIO_S
    # Whisper pads each clip to 30 s, so 200 one-second lines need far more
    # than their 200 s of audio suggests.
    assert fn(200.0, 200) - fn(200.0, 1) == 199 * retranscribe_worker._PER_WINDOW_S
    assert fn(200.0, 200) >= 1800 + 200 * 30
