"""services/gpu_process_job: the shared GPU process-job shape, with real
spawned workers (Cancel ends the job and the worker is gone; the deadline
ends it with a plain message)."""
import os
import time

import background_jobs
import storage
from services import gpu_process_job


def hang_body(marker, scratch_dir, result_queue):
    """Worker body whose model call never returns."""
    with open(marker, "w") as f:
        f.write(str(os.getpid()))
    time.sleep(600)


def quick_body(value, scratch_dir, result_queue):
    with open(os.path.join(scratch_dir, "x.tmp"), "w") as f:
        f.write("scratch")
    result_queue.put(("ok", {"value": value}))


def wait_until(predicate, what, timeout=60):
    deadline = time.time() + timeout
    while not predicate():
        assert time.time() < deadline, what
        time.sleep(0.05)


def ended(job_id):
    return background_jobs.get_status(job_id)["status"] not in ("running", "queued")


def scratch_dirs(job_id):
    return [d for d in os.listdir(storage.temp_root()) if d.startswith(job_id)]


def test_cancel_kills_the_worker_within_bounded_time(isolated_db, tmp_path):
    marker = str(tmp_path / "started")
    assert gpu_process_job.start_gpu_process_job(
        "gpujob_1", hang_body, (marker,), drama_id=1, kind="Hanging", timeout_s=300)
    wait_until(lambda: os.path.exists(marker), "the worker never started")
    proc = background_jobs.get_status("gpujob_1")["process"]
    background_jobs.request_cancel("gpujob_1")
    wait_until(lambda: ended("gpujob_1"), "Cancel did not end the job")
    assert background_jobs.get_status("gpujob_1")["status"] == "cancelled"
    proc.join(10)
    assert not proc.is_alive()
    background_jobs.wait_for_job_threads(10)
    wait_until(lambda: not scratch_dirs("gpujob_1"), "scratch folder left behind", 10)


def test_the_worker_deadline_ends_the_job_with_a_plain_message(isolated_db, tmp_path):
    marker = str(tmp_path / "started")
    assert gpu_process_job.start_gpu_process_job(
        "gpujob_2", hang_body, (marker,), kind="Hanging", timeout_s=2)
    wait_until(lambda: ended("gpujob_2"), "the deadline did not end the job")
    job = background_jobs.get_status("gpujob_2")
    assert job["status"] == "error"
    assert gpu_process_job.TIMEOUT_MESSAGE in job["error"]
    assert str(tmp_path) not in job["error"]
    proc = job["process"]
    proc.join(10)
    assert not proc.is_alive()


def test_a_result_reaches_on_done_and_scratch_is_removed(isolated_db):
    seen, finished = [], []
    assert gpu_process_job.start_gpu_process_job(
        "gpujob_3", quick_body, (7,), kind="Quick", timeout_s=60,
        on_done=lambda job_id, result: seen.append(result),
        on_finish=finished.append)
    wait_until(lambda: ended("gpujob_3"), "the job never ended")
    background_jobs.wait_for_job_threads(10)
    assert background_jobs.get_status("gpujob_3")["status"] == "done"
    assert seen == [{"value": 7}] and finished == ["gpujob_3"]
    assert not scratch_dirs("gpujob_3")


def test_a_refused_start_leaves_no_scratch_folder(isolated_db, tmp_path):
    marker = str(tmp_path / "started")
    assert gpu_process_job.start_gpu_process_job(
        "gpujob_4", hang_body, (marker,), kind="Hanging", timeout_s=300)
    assert not gpu_process_job.start_gpu_process_job(
        "gpujob_4", hang_body, (marker,), kind="Hanging", timeout_s=300)
    assert len(scratch_dirs("gpujob_4")) == 1
    background_jobs.request_cancel("gpujob_4")
    wait_until(lambda: ended("gpujob_4"), "Cancel did not end the job")
    background_jobs.wait_for_job_threads(10)
