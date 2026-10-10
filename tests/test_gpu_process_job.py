"""services/gpu_process_job.run_in_child with real spawned children: a large
result, a raising body, the deadline, Cancel, per-line items, and the
parent-side deadline for a child that never starts its own timer. The
retranscribe_timeout_s formula is pinned here too."""
import os
import queue
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


class _FakeProc:
    def __init__(self, alive_until):
        self._alive_until = alive_until

    def is_alive(self):
        return time.monotonic() < self._alive_until


class _LateChannel:
    """Delivers one item at a set time, like a feeder thread that is slow."""

    def __init__(self, item, at):
        self._item, self._at = item, at

    def get(self, timeout):
        time.sleep(timeout)
        if self._item is not None and time.monotonic() >= self._at:
            item, self._item = self._item, None
            return item
        raise queue.Empty


def test_a_result_landing_after_the_child_exits_is_not_lost(isolated_db):
    now = time.monotonic()
    result = gpu_process_job._await_child(
        "gpujob_late", _FakeProc(now), _LateChannel(("ok", {"n": 1}), now + 0.6),
        0.1, now + 30, None)
    assert result == {"n": 1}


def test_a_child_that_exits_with_nothing_is_lost_after_the_grace(isolated_db, monkeypatch):
    monkeypatch.setattr(gpu_process_job, "EXIT_DRAIN_GRACE_S", 0.3)
    now = time.monotonic()
    with pytest.raises(gpu_process_job.ChildFailed) as err:
        gpu_process_job._await_child(
            "gpujob_lost", _FakeProc(now), _LateChannel(None, 0), 0.1, now + 30, None)
    assert str(err.value) == background_jobs.WORKER_LOST_MESSAGE


def test_a_raising_on_item_becomes_a_plain_redacted_child_failed(isolated_db):
    secret = "sk-ant-api03-" + "a" * 40

    def on_item(_item):
        raise RuntimeError(f"db write failed {secret}")

    now = time.monotonic()
    channel = _LateChannel(("item", {"n": 1}), now)
    with pytest.raises(gpu_process_job.ChildFailed) as err:
        gpu_process_job._await_child(
            "gpujob_onitem", _FakeProc(now + 30), channel, 0.01, now + 30, on_item)
    assert "db write failed" in str(err.value) and secret not in str(err.value)


def test_give_up_ends_the_process_even_if_the_queue_flush_blocks(monkeypatch, tmp_path):
    release = threading.Event()
    exited = []

    class BlockedQueue:
        def put(self, item):
            pass

        def close(self):
            pass

        def join_thread(self):
            release.wait(30)

    monkeypatch.setattr(gpu_process_job, "FLUSH_TIMEOUT_S", 0.2)
    monkeypatch.setattr(gpu_process_job.background_jobs, "start_own_process_group", lambda: None)
    monkeypatch.setattr(gpu_process_job.os, "_exit", lambda code: exited.append(code))

    def body(scratch_dir, result_queue):
        # Outlives the 0.05 s deadline so give_up runs while the body is busy.
        deadline = time.monotonic() + 3
        while not exited and time.monotonic() < deadline:
            time.sleep(0.02)

    try:
        gpu_process_job.run_worker(body, 0.05, str(tmp_path / "scratch"), BlockedQueue())
    finally:
        release.set()
    assert exited == [0]


def test_timeout_formula_has_a_per_line_term_and_scales_with_audio():
    fn = retranscribe_worker.retranscribe_timeout_s
    assert fn(0.0) >= 1800
    assert fn(100.0, 1) - fn(0.0, 1) == 100 * retranscribe_worker._PER_AUDIO_S
    # Whisper pads each clip to 30 s, so 200 one-second lines need far more
    # than their 200 s of audio suggests.
    assert fn(200.0, 200) - fn(200.0, 1) == 199 * retranscribe_worker._PER_WINDOW_S
    assert fn(200.0, 200) >= 1800 + 200 * 30
